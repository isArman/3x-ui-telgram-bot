"""Shared order provisioning after payment is confirmed (card or wallet)."""

from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import Order, Payment, PlanConfig, User, VPNAccount
from app.services.config_inventory import assign_config
from app.services.panel_settings import get_panel_settings, is_auto_provisioning_ready
from app.services.renewal import extend_vpn_account
from app.services.xui_provisioning import provision_subscription_for_order


@dataclass
class FulfillResult:
    """Result of attempting to fulfill a paid order."""

    kind: str  # renewal_auto | renewal_db | new_auto | new_inventory | needs_manual
    config_text: str | None = None
    vpn_account: VPNAccount | None = None
    plan_config_id: int | None = None
    config_ref: str | None = None
    plan_name: str | None = None


async def _try_auto_provision(
    session: AsyncSession,
    order: Order,
    user: User,
    vpn_account: VPNAccount | None = None,
) -> str | None:
    panel = await get_panel_settings(session)
    if not is_auto_provisioning_ready(panel):
        return None
    return await provision_subscription_for_order(
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
) -> None:
    payment.status = "approved"
    payment.reviewed_at = datetime.utcnow()
    payment.reviewed_by = admin_id
    order.status = "completed"

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
    await session.commit()


async def complete_renewal_approval(
    session: AsyncSession,
    payment: Payment,
    order: Order,
    admin_id: int | None,
    vpn_account: VPNAccount,
    subscription_url: str | None = None,
) -> VPNAccount:
    payment.status = "approved"
    payment.reviewed_at = datetime.utcnow()
    payment.reviewed_by = admin_id
    order.status = "completed"
    await extend_vpn_account(session, vpn_account, order, subscription_url)
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
                session, payment, order, reviewer_id, vpn_account, sub_url
            )
            return FulfillResult(
                kind="renewal_auto",
                config_text=sub_url,
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
        await complete_payment_approval(
            session,
            payment,
            order,
            reviewer_id,
            sub_url,
            config_ref="xui-auto",
        )
        return FulfillResult(
            kind="new_auto",
            config_text=sub_url,
            config_ref="xui-auto",
            plan_name=plan_name,
        )

    # Inventory fallback
    if order.plan_id:
        config_entry: PlanConfig | None = await assign_config(
            session, order.plan_id, order.id
        )
        if config_entry:
            await complete_payment_approval(
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
            )

    return FulfillResult(kind="needs_manual", plan_name=plan_name)
