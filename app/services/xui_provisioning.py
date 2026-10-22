from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Optional

from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import Order, PanelSettings, User, VPNAccount
from app.services.panel_settings import (
    get_selected_inbound_ids,
    prune_selected_inbound_ids,
    xui_client_for_panel,
)
from app.services.traffic_usage import client_record
from app.utils.client_identity import build_client_comment, panel_client_email
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
    values instead of recomputing them — a repeat purchase extends the client
    on the panel, and the local row has to follow.
    """

    subscription_url: str
    client_email: str
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


async def provision_subscription_detailed(
    session: AsyncSession,
    settings: PanelSettings,
    user: User,
    order: Order,
    existing_account: VPNAccount | None = None,
) -> Optional[ProvisionResult]:
    """
    Create or extend a 3x-ui client and return the resulting panel state.

    New client: created with the order's quota/expiry.
    Existing client (renewal or repeat purchase): extended with an atomic
    server-side delta via bulkAdjust, which also re-enables a client the panel
    had auto-disabled for being depleted.
    """
    email = panel_client_email(user)
    comment = build_client_comment(user)
    now_ms = int(time.time() * 1000)
    add_bytes = _gb_to_bytes(order.traffic_gb)
    add_ms = order.days * 24 * 60 * 60 * 1000
    device_limit = max(int(getattr(order, "device_limit", 1) or 1), 1)

    try:
        async with xui_client_for_panel(settings) as client:
            inbound_ids = await resolve_live_inbound_ids(
                client, settings, persist_prune=True
            )
            if not inbound_ids:
                logger.error("Auto provisioning skipped: no valid inbounds selected")
                return None

            existing_client = await client.get_client(email)
            client_data = client_record(existing_client or {})

            if client_data:
                # Extend rather than reset: bulkAdjust is an atomic
                # read-modify-write on the panel (no client-side race) and
                # re-enables a client that was auto-disabled for being depleted.
                adjusted = await client.bulk_adjust(
                    [email],
                    add_days=order.days,
                    add_bytes=add_bytes,
                )
                if _bulk_adjust_skipped(adjusted, email):
                    logger.error(
                        "bulkAdjust skipped client %s for order %s: %s",
                        email,
                        order.id,
                        adjusted.get("skipped"),
                    )
                    return None
                # bulkAdjust does not touch limitIp, so a plan with a different
                # device count needs an explicit update. The full record is sent
                # because the panel replaces the row.
                if int(client_data.get("limitIp") or 0) != device_limit:
                    # The update replaces the stored quota and expiry, so it has
                    # to carry the values the panel holds *now* — the snapshot
                    # taken before bulkAdjust would roll the renewal back.
                    current = await client.get_client(email) or existing_client
                    payload = prepare_client_update_payload(
                        current,
                        email=email,
                        limit_ip=device_limit,
                    )
                    detail = await client.update_client(email, payload, inbound_ids)
                    logger.info(
                        "Updated device limit of client %s to %s", email, device_limit
                    )
                else:
                    # Ensure inbound membership matches the selected set.
                    detail = await client.sync_client_inbounds(email, inbound_ids)
                logger.info(
                    "Extended 3x-ui client %s for order %s (+%s days, +%s GB)",
                    email,
                    order.id,
                    order.days,
                    order.traffic_gb,
                )
            else:
                detail = await client.add_client(
                    email,
                    inbound_ids,
                    add_bytes,
                    now_ms + add_ms,
                    comment,
                    limit_ip=device_limit,
                )
                logger.info(
                    "Created 3x-ui client %s for order %s", email, order.id
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
