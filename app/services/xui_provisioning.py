from __future__ import annotations

import time
from typing import Optional

from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import Order, PanelSettings, User
from app.services.panel_settings import get_selected_inbound_ids, xui_client_for_panel
from app.utils.client_identity import build_client_comment, panel_client_email
from app.utils.logger import logger
from app.xui.client import XUIError, build_subscription_url


async def provision_subscription_for_order(
    session: AsyncSession,
    settings: PanelSettings,
    user: User,
    order: Order,
) -> Optional[str]:
    """
    Create or update a 3x-ui client and return the subscription URL.
    Returns None on failure (caller should fall back to manual inventory).
    """
    inbound_ids = get_selected_inbound_ids(settings)
    if not inbound_ids:
        logger.error("Auto provisioning skipped: no inbounds selected")
        return None

    email = panel_client_email(user)
    comment = build_client_comment(user)
    total_bytes = order.traffic_gb * 1024 * 1024 * 1024
    expiry_ms = int(time.time() * 1000) + order.days * 24 * 60 * 60 * 1000

    try:
        async with xui_client_for_panel(settings) as client:
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
