"""Shared order provisioning after payment is confirmed (card or wallet)."""

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import Order, Payment, PlanConfig, User, VPNAccount
from app.services.config_inventory import assign_config
from app.services.panel_settings import get_panel_settings, is_auto_provisioning_ready
from app.services.renewal import extend_vpn_account
from app.services.xui_provisioning import (
    ProvisionResult,
    provision_subscription_detailed,
)

GB = 1024**3
AUTO_CONFIG_REF = "xui-auto"


@dataclass
class FulfillResult:
    """Result of attempting to fulfill a paid order."""

    kind: str  # renewal_auto | renewal_db | new_auto | new_inventory | needs_manual
    config_text: str | None = None
    vpn_account: VPNAccount | None = None
    plan_config_id: int | None = None
    config_ref: str | None = None
    plan_name: str | None = None
    referral_cashback: tuple[int, int] | None = None  # (referrer_id, amount)


def _expiry_from_ms(expiry_ms: int) -> datetime | None:
    """Panel expiry (ms since epoch) as a naive UTC datetime, like the rest of the DB."""
    if expiry_ms <= 0:
        return None
    return datetime.fromtimestamp(expiry_ms / 1000, tz=timezone.utc).replace(tzinfo=None)


async def _upsert_auto_account(
    session: AsyncSession,
    order: Order,
    subscription_url: str,
    provision: ProvisionResult | None,
) -> VPNAccount:
    """
    Keep exactly one database row per user for panel-backed accounts.

    The 3x-ui panel keeps a single client per Telegram user and *extends* it on
    every repeat purchase, so inserting a new row per order would leave stale
    rows behind: duplicated expiry alerts and a renewal that extends a row the
    panel client no longer matches. The panel owns the quota/expiry, so the row
    mirrors the panel instead of recomputing it.
    """
    expires_at = _expiry_from_ms(provision.expiry_ms) if provision else None
    if expires_at is None:
        expires_at = datetime.utcnow() + timedelta(days=order.days)

    result = await session.execute(
        select(VPNAccount)
        .where(
            VPNAccount.user_id == order.user_id,
            VPNAccount.config_ref == AUTO_CONFIG_REF,
        )
        .order_by(VPNAccount.created_at.desc())
        .limit(1)
    )
    account = result.scalar_one_or_none()

    if account is not None:
        account.subscription_path = subscription_url
        account.expires_at = expires_at
        if provision and provision.total_bytes > 0:
            account.traffic_limit_gb = provision.total_bytes // GB
        account.is_active = True
        account.expiry_notified = False
        account.traffic_low_notified = False
        account.last_renewed_at = datetime.utcnow()
        await _deactivate_duplicate_auto_accounts(session, order.user_id, account.id)
        await session.flush()
        return account

    traffic_gb = order.traffic_gb
    if provision and provision.total_bytes > 0:
        traffic_gb = provision.total_bytes // GB

    account = VPNAccount(
        order_id=order.id,
        user_id=order.user_id,
        config_ref=AUTO_CONFIG_REF,
        subscription_path=subscription_url,
        expires_at=expires_at,
        traffic_limit_gb=traffic_gb,
        is_active=True,
    )
    session.add(account)
    await session.flush()
    return account


async def _deactivate_duplicate_auto_accounts(
    session: AsyncSession, user_id: int, keep_id: int
) -> None:
    """
    Older releases inserted one account row per order even though the panel
    keeps a single client per user. Park the leftovers so they stop producing
    duplicate expiry/traffic alerts.
    """
    result = await session.execute(
        select(VPNAccount).where(
            VPNAccount.user_id == user_id,
            VPNAccount.config_ref == AUTO_CONFIG_REF,
            VPNAccount.id != keep_id,
        )
    )
    for duplicate in result.scalars().all():
        duplicate.is_active = False


async def _try_auto_provision(
    session: AsyncSession,
    order: Order,
    user: User,
    vpn_account: VPNAccount | None = None,
) -> ProvisionResult | None:
    panel = await get_panel_settings(session)
    if not is_auto_provisioning_ready(panel):
        return None
    return await provision_subscription_detailed(
        session, panel, user, order, existing_account=vpn_account
    )


async def complete_payment_approval(
    session: AsyncSession,
    payment: Payment,
    order: Order,
    admin_id: int | None,
    config_text: str,
    plan_config_id: int | None = None,
    config_ref: str | None = None,
    provision: ProvisionResult | None = None,
) -> tuple[int, int] | None:
    """Complete new-order payment. Returns referral cashback (referrer_id, amount) if any."""
    from app.services.referral import grant_referral_cashback

    payment.status = "approved"
    payment.reviewed_at = datetime.utcnow()
    payment.reviewed_by = admin_id
    order.status = "completed"

    if config_ref == AUTO_CONFIG_REF:
        vpn_account = await _upsert_auto_account(
            session, order, config_text, provision
        )
    elif order.renew_vpn_account_id:
        # A renewal must extend the existing account, never create a second row
        # for the same service.
        result = await session.execute(
            select(VPNAccount).where(VPNAccount.id == order.renew_vpn_account_id)
        )
        vpn_account = result.scalar_one_or_none()
        if vpn_account is not None:
            vpn_account.subscription_path = config_text
            vpn_account.expires_at = datetime.utcnow() + timedelta(days=order.days)
            vpn_account.traffic_limit_gb += order.traffic_gb
            vpn_account.is_active = True
            await session.flush()
        else:
            vpn_account = VPNAccount(
                order_id=order.id,
                user_id=order.user_id,
                config_ref=config_ref or "manual",
                subscription_path=config_text,
                expires_at=datetime.utcnow() + timedelta(days=order.days),
                traffic_limit_gb=order.traffic_gb,
                is_active=True,
            )
            session.add(vpn_account)
    else:
        vpn_account = VPNAccount(
            order_id=order.id,
            user_id=order.user_id,
            config_ref=config_ref or (str(plan_config_id) if plan_config_id else "manual"),
            subscription_path=config_text,
            expires_at=datetime.utcnow() + timedelta(days=order.days),
            traffic_limit_gb=order.traffic_gb,
            is_active=True,
        )
        session.add(vpn_account)
    cashback = await grant_referral_cashback(session, order)
    await session.commit()
    return cashback


async def complete_renewal_approval(
    session: AsyncSession,
    payment: Payment,
    order: Order,
    admin_id: int | None,
    vpn_account: VPNAccount,
    subscription_url: str | None = None,
    provision: ProvisionResult | None = None,
) -> VPNAccount:
    payment.status = "approved"
    payment.reviewed_at = datetime.utcnow()
    payment.reviewed_by = admin_id
    order.status = "completed"
    await extend_vpn_account(session, vpn_account, order, subscription_url)
    if provision is not None:
        # The panel is authoritative: mirror its quota, and its expiry whenever
        # the client really is still alive after the adjustment.
        if provision.total_bytes > 0:
            vpn_account.traffic_limit_gb = provision.total_bytes // GB
        panel_expiry = _expiry_from_ms(provision.expiry_ms)
        if panel_expiry is not None and panel_expiry > datetime.utcnow():
            vpn_account.expires_at = panel_expiry
    await session.commit()
    await session.refresh(vpn_account)
    return vpn_account


async def fulfill_paid_order(
    session: AsyncSession,
    payment: Payment,
    order: Order,
    reviewer_id: int | None,
    plan_name: str | None = None,
) -> FulfillResult:
    """
    Attempt auto/inventory fulfillment for a pending payment.
    Returns needs_manual if admin must paste a subscription link.
    """
    user_result = await session.execute(select(User).where(User.id == order.user_id))
    user = user_result.scalar_one_or_none()
    if not user:
        raise ValueError(f"user {order.user_id} not found")

    # Renewal
    if order.renew_vpn_account_id:
        acc_result = await session.execute(
            select(VPNAccount).where(VPNAccount.id == order.renew_vpn_account_id)
        )
        vpn_account = acc_result.scalar_one_or_none()
        if not vpn_account or vpn_account.user_id != order.user_id:
            raise ValueError("renewal vpn account not found")

        sub_url = await _try_auto_provision(session, order, user, vpn_account)
        if sub_url:
            vpn_account = await complete_renewal_approval(
                session, payment, order, reviewer_id, vpn_account, sub_url.subscription_url,
                provision=sub_url,
            )
            return FulfillResult(
                kind="renewal_auto",
                config_text=sub_url.subscription_url,
                vpn_account=vpn_account,
                plan_name=plan_name,
            )

        vpn_account = await complete_renewal_approval(
            session, payment, order, reviewer_id, vpn_account, None
        )
        return FulfillResult(
            kind="renewal_db",
            config_text=vpn_account.subscription_path,
            vpn_account=vpn_account,
            plan_name=plan_name,
        )

    # New account — auto 3x-ui
    sub_url = await _try_auto_provision(session, order, user)
    if sub_url:
        cashback = await complete_payment_approval(
            session,
            payment,
            order,
            reviewer_id,
            sub_url.subscription_url,
            config_ref=AUTO_CONFIG_REF,
            provision=sub_url,
        )
        return FulfillResult(
            kind="new_auto",
            config_text=sub_url.subscription_url,
            config_ref=AUTO_CONFIG_REF,
            plan_name=plan_name,
            referral_cashback=cashback,
        )

    # Inventory fallback
    if order.plan_id:
        config_entry: PlanConfig | None = await assign_config(
            session, order.plan_id, order.id
        )
        if config_entry:
            cashback = await complete_payment_approval(
                session,
                payment,
                order,
                reviewer_id,
                config_entry.config_text,
                config_entry.id,
            )
            return FulfillResult(
                kind="new_inventory",
                config_text=config_entry.config_text,
                plan_config_id=config_entry.id,
                plan_name=plan_name,
                referral_cashback=cashback,
            )

    return FulfillResult(kind="needs_manual", plan_name=plan_name)
