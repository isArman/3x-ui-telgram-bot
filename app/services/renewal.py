"""VPN account renewal logic (reset semantics)."""

from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import Order, VPNAccount


def compute_reset_expiry(days: int) -> datetime:
    """Fresh expiry from now — a renewal never carries leftover time."""
    return datetime.utcnow() + timedelta(days=days)


async def extend_vpn_account(
    session: AsyncSession,
    vpn_account: VPNAccount,
    order: Order,
    subscription_url: str | None = None,
) -> VPNAccount:
    """
    Reset an existing VPN account to the order's plan (admin gift flow).

    The new plan replaces the old subscription entirely: expiry restarts from
    now and traffic is reset to the plan's allowance. A traffic of 0 means
    "leave the traffic allowance unchanged" (admin gift only sets days).
    """
    vpn_account.expires_at = compute_reset_expiry(order.days)
    if order.traffic_gb > 0:
        vpn_account.traffic_limit_gb = order.traffic_gb
    if subscription_url:
        vpn_account.subscription_path = subscription_url
    vpn_account.is_active = True
    vpn_account.expiry_notified = False
    vpn_account.traffic_low_notified = False
    vpn_account.last_renewed_at = datetime.utcnow()
    await session.flush()
    return vpn_account
