"""VPN account renewal logic."""

from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import Order, VPNAccount


def compute_extended_expiry(current_expires_at: datetime, extra_days: int) -> datetime:
    """Add days to expiry; if already expired, extend from now."""
    now = datetime.utcnow()
    base = current_expires_at if current_expires_at > now else now
    return base + timedelta(days=extra_days)


async def extend_vpn_account(
    session: AsyncSession,
    vpn_account: VPNAccount,
    order: Order,
    subscription_url: str | None = None,
) -> VPNAccount:
    """Extend an existing VPN account after renewal payment approval."""
    vpn_account.expires_at = compute_extended_expiry(vpn_account.expires_at, order.days)
    vpn_account.traffic_limit_gb += order.traffic_gb
    if subscription_url:
        vpn_account.subscription_path = subscription_url
    vpn_account.is_active = True
    vpn_account.expiry_notified = False
    vpn_account.traffic_low_notified = False
    vpn_account.last_renewed_at = datetime.utcnow()
    await session.flush()
    return vpn_account
