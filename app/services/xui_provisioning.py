from __future__ import annotations

import time
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import Order, PanelSettings, User, VPNAccount
from app.services.panel_settings import (
    get_selected_inbound_ids,
    prune_selected_inbound_ids,
    xui_client_for_panel,
)
from app.utils.client_identity import build_client_comment, panel_client_email
from app.utils.logger import logger
from app.xui.client import XUIClient, XUIError, build_subscription_url


def _gb_to_bytes(gb: int) -> int:
    return gb * 1024 * 1024 * 1024


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
    Re-sync inbound membership for all active VPN users to the selected set.
    Returns (ok_count, fail_count).
    """
    result = await session.execute(
        select(User)
        .join(VPNAccount, VPNAccount.user_id == User.id)
        .where(VPNAccount.is_active.is_(True))
        .distinct()
    )
    users = list(result.scalars().all())
    if not users:
        return 0, 0

    ok = 0
    fail = 0
    async with xui_client_for_panel(settings) as client:
        for user in users:
            email = panel_client_email(user)
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


async def provision_subscription_for_order(
    session: AsyncSession,
    settings: PanelSettings,
    user: User,
    order: Order,
    existing_account: VPNAccount | None = None,
) -> Optional[str]:
    """
    Create or update a 3x-ui client and return the subscription URL.
    For renewals, extends expiry and traffic from current panel/DB values.
    """
    email = panel_client_email(user)
    comment = build_client_comment(user)
    now_ms = int(time.time() * 1000)
    add_bytes = _gb_to_bytes(order.traffic_gb)
    add_ms = order.days * 24 * 60 * 60 * 1000

    try:
        async with xui_client_for_panel(settings) as client:
            inbound_ids = await resolve_live_inbound_ids(
                client, settings, persist_prune=True
            )
            if not inbound_ids:
                logger.error("Auto provisioning skipped: no valid inbounds selected")
                return None

            existing_client = await client.get_client(email)
            client_data = (existing_client or {}).get("client") or {}

            # Always stack onto an existing panel client (same Telegram email).
            # Replacing quota on a second "new" purchase wiped remaining traffic.
            if client_data:
                current_expiry = int(client_data.get("expiryTime") or 0)
                expiry_ms = max(now_ms, current_expiry) + add_ms
                total_bytes = int(client_data.get("totalGB") or 0) + add_bytes
                # #region agent log
                from app.utils.debug_ndjson import agent_log

                agent_log(
                    "D",
                    "xui_provisioning.py:provision",
                    "extending existing panel client",
                    {
                        "order_id": order.id,
                        "is_renewal": bool(existing_account),
                        "add_gb": order.traffic_gb,
                    },
                    run_id="post-fix",
                )
                # #endregion
            else:
                expiry_ms = now_ms + add_ms
                total_bytes = add_bytes
                # #region agent log
                from app.utils.debug_ndjson import agent_log

                agent_log(
                    "D",
                    "xui_provisioning.py:provision",
                    "creating fresh panel client",
                    {"order_id": order.id},
                    run_id="post-fix",
                )
                # #endregion

            detail = await client.upsert_client(
                email=email,
                inbound_ids=inbound_ids,
                total_bytes=total_bytes,
                expiry_ms=expiry_ms,
                comment=comment,
            )
    except XUIError as exc:
        logger.error("3x-ui provisioning failed for order %s: %s", order.id, exc)
        return None
    except Exception as exc:
        logger.error("Unexpected provisioning error for order %s: %s", order.id, exc)
        return None

    client_data = detail.get("client") or {}
    sub_id = client_data.get("subId")
    if not sub_id:
        logger.error("3x-ui client has no subId for order %s", order.id)
        return None

    return build_subscription_url(settings.subscription_base_url, sub_id)
