from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import Order, PanelSettings, User, VPNAccount
from app.services.panel_settings import (
    get_selected_inbound_ids,
    prune_selected_inbound_ids,
    xui_client_for_panel,
)
from app.services.traffic_usage import client_record
from app.utils.client_identity import (
    build_client_comment,
    panel_client_email,
    panel_client_email_for_order,
)
from app.utils.logger import logger
from app.xui.client import (
    XUIClient,
    XUIError,
    build_subscription_url,
    prepare_client_update_payload,
)


def _gb_to_bytes(gb: int) -> int:
    return gb * 1024 * 1024 * 1024


@dataclass
class ProvisionResult:
    """
    Authoritative panel state after provisioning.

    The panel owns the real quota/expiry, so the database must mirror these
    values instead of recomputing them.
    """

    subscription_url: str
    client_email: str
    panel_email: str = ""
    total_bytes: int = 0
    expiry_ms: int = 0


def _bulk_adjust_skipped(result: dict, email: str) -> bool:
    """True when the panel reported this email as skipped by bulkAdjust."""
    for entry in result.get("skipped") or []:
        if isinstance(entry, dict) and entry.get("email") == email:
            return True
    return False


async def resolve_live_inbound_ids(
    client: XUIClient,
    settings: PanelSettings,
    *,
    persist_prune: bool = False,
) -> list[int]:
    """
    Intersect admin-selected inbound IDs with inbounds that still exist on the panel.
    Stale IDs (deleted on panel) break add/attach with 'record not found'.
    """
    selected = get_selected_inbound_ids(settings)
    if not selected:
        return []

    inbounds = await client.list_inbounds()
    live_ids = [int(ib["id"]) for ib in inbounds if ib.get("id") is not None]
    live_set = set(live_ids)
    valid = [i for i in selected if i in live_set]
    stale = [i for i in selected if i not in live_set]
    if stale:
        logger.warning(
            "Dropping stale selected inbound ids no longer on panel: %s",
            stale,
        )
        if persist_prune:
            prune_selected_inbound_ids(settings, live_ids)
    return valid


async def sync_active_clients_inbounds(
    session: AsyncSession,
    settings: PanelSettings,
    inbound_ids: list[int],
) -> tuple[int, int]:
    """
    Re-sync inbound membership for all active VPN clients to the selected set.
    Returns (ok_count, fail_count).
    """
    result = await session.execute(
        select(VPNAccount).where(
            VPNAccount.is_active.is_(True),
            VPNAccount.panel_email.isnot(None),
        )
    )
    accounts = list(result.scalars().all())
    if not accounts:
        return 0, 0

    ok = 0
    fail = 0
    async with xui_client_for_panel(settings) as client:
        for account in accounts:
            email = account.panel_email
            try:
                existing = await client.get_client(email)
                if not existing:
                    continue
                await client.sync_client_inbounds(email, inbound_ids)
                ok += 1
            except Exception as exc:
                fail += 1
                logger.error(
                    "Failed to sync inbounds for client %s: %s",
                    email,
                    exc,
                )
    return ok, fail


async def disable_panel_client(client: XUIClient, email: str) -> None:
    """Disable (not delete) a panel client so its subscription stops working."""
    detail = await client.get_client(email)
    if not detail:
        return
    payload = prepare_client_update_payload(detail, email=email)
    payload["enable"] = False
    inbound_ids = [int(i) for i in (detail.get("inboundIds") or [])]
    await client.update_client(email, payload, inbound_ids)


async def provision_subscription_detailed(
    session: AsyncSession,
    settings: PanelSettings,
    user: User,
    order: Order,
    existing_account: VPNAccount | None = None,
) -> Optional[ProvisionResult]:
    """
    Create a fresh 3x-ui client for this order.

    Every order gets its own panel client with exactly the plan's quota and
    expiry — nothing is carried over from previous orders. When the order is a
    renewal, the replaced client is disabled so the old subscription stops
    working.
    """
    email = panel_client_email_for_order(user, order.id)
    comment = build_client_comment(user)
    now_ms = int(time.time() * 1000)
    total_bytes = _gb_to_bytes(order.traffic_gb)
    expiry_ms = now_ms + order.days * 24 * 60 * 60 * 1000
    device_limit = max(int(getattr(order, "device_limit", 1) or 1), 1)

    if order.days <= 0 or order.traffic_gb <= 0:
        logger.error(
            "Auto provisioning skipped for order %s: invalid plan limits "
            "(days=%s, traffic_gb=%s)",
            order.id,
            order.days,
            order.traffic_gb,
        )
        return None

    replaced_email: str | None = None
    if order.renew_vpn_account_id and existing_account is not None:
        replaced_email = existing_account.panel_email or panel_client_email(user)

    try:
        async with xui_client_for_panel(settings) as client:
            inbound_ids = await resolve_live_inbound_ids(
                client, settings, persist_prune=True
            )
            if not inbound_ids:
                logger.error("Auto provisioning skipped: no valid inbounds selected")
                return None

            detail = await client.add_client(
                email,
                inbound_ids,
                total_bytes,
                expiry_ms,
                comment,
                limit_ip=device_limit,
            )
            logger.info(
                "Created 3x-ui client %s for order %s (%s days, %s GB)",
                email,
                order.id,
                order.days,
                order.traffic_gb,
            )

            if replaced_email:
                try:
                    await disable_panel_client(client, replaced_email)
                    logger.info(
                        "Disabled replaced 3x-ui client %s (order %s)",
                        replaced_email,
                        order.id,
                    )
                except Exception as exc:
                    logger.warning(
                        "Could not disable replaced client %s: %s",
                        replaced_email,
                        exc,
                    )
    except XUIError as exc:
        logger.error("3x-ui provisioning failed for order %s: %s", order.id, exc)
        return None
    except Exception as exc:
        logger.error("Unexpected provisioning error for order %s: %s", order.id, exc)
        return None

    client_data = client_record(detail)
    sub_id = client_data.get("subId")
    if not sub_id:
        logger.error("3x-ui client has no subId for order %s", order.id)
        return None

    return ProvisionResult(
        subscription_url=build_subscription_url(settings.subscription_base_url, sub_id),
        client_email=email,
        panel_email=email,
        total_bytes=int(client_data.get("totalGB") or 0),
        expiry_ms=int(client_data.get("expiryTime") or 0),
    )


async def provision_subscription_for_order(
    session: AsyncSession,
    settings: PanelSettings,
    user: User,
    order: Order,
    existing_account: VPNAccount | None = None,
) -> Optional[str]:
    """Backwards-compatible wrapper returning only the subscription URL."""
    result = await provision_subscription_detailed(
        session, settings, user, order, existing_account=existing_account
    )
    return result.subscription_url if result else None
