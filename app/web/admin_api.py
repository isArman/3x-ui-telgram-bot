"""Admin API for the Mini App.

Everything here needs an admin Telegram id; the auth middleware already
rejects unauthenticated callers, and these routes check the admin list.
"""

from __future__ import annotations

from datetime import datetime
import time
from typing import Any

from aiohttp import web
from sqlalchemy import select, update

from app.config.settings import settings
from app.database.models import (
    Order,
    Payment,
    PlanConfig,
    PromoCode,
    User,
    VPNAccount,
    WalletTopUp,
)
from app.database.session import AsyncSessionLocal
from app.services.bot_settings import set_bot_settings
from app.services.broadcast import (
    BROADCAST_TARGET_ALL,
    BROADCAST_TARGET_USER,
    create_broadcast,
    send_broadcast,
)
from app.services.config_inventory import add_config, count_available
from app.services.fulfillment import deliver_paid_order
from app.services.panel_settings import (
    get_panel_api_token,
    get_panel_password,
    get_panel_settings,
    get_selected_inbound_ids,
    set_panel_api_token,
    set_selected_inbound_ids,
    update_panel_settings,
    xui_client_for_panel,
)
from app.services.plans_catalog import (
    create_plan,
    delete_plan,
    get_plan,
    get_pricing,
    list_all_plans,
    set_plan_active,
    set_pricing,
    update_plan_fields,
)
from app.services.promocodes import (
    create_promo,
    delete_promo,
    list_promos,
    set_promo_active,
)
from app.services.users import find_user, get_user_summary, set_user_blocked
from app.services.wallet import credit_balance
from app.utils.logger import logger
from app.utils.statistics import get_dashboard_stats
from app.utils.validation import is_valid_config_text
from app.web.api import _json_body
from app.web.fields import FieldError, optional_bool, optional_int, optional_text
from app.web.responses import ApiError, fail, not_found, ok

routes = web.RouteTableDef()


def _admin(request: web.Request) -> int:
    """The admin's Telegram id, or raise an error the client understands."""
    user = request.get("telegram_user") or {}
    user_id = int(user.get("id") or 0)
    if not user_id:
        raise ApiError("دسترسی نامعتبر است.", status=401, code="unauthorized")
    if user_id not in settings.ADMIN_IDS:
        raise ApiError("دسترسی ندارید.", status=403, code="forbidden")
    return user_id


def _promo_dict(promo: PromoCode) -> dict[str, Any]:
    return {
        "id": promo.id,
        "code": promo.code,
        "kind": promo.kind,
        "value": promo.value,
        "max_uses": promo.max_uses,
        "used_count": promo.used_count,
        "is_active": promo.is_active,
        "expires_at": promo.expires_at.strftime("%Y-%m-%d") if promo.expires_at else None,
    }


def _plan_dict(plan) -> dict[str, Any]:
    return {
        "id": plan.id,
        "name": plan.name,
        "days": plan.days,
        "traffic": plan.traffic_gb,
        "traffic_gb": plan.traffic_gb,
        "price": plan.price,
        "devices": plan.devices,
        "description": plan.description or "",
        "is_active": plan.is_active,
        "sort_order": plan.sort_order,
    }


def _panel_dict(panel) -> dict[str, Any]:
    """The stored panel connection, without the secrets."""
    return {
        "url": panel.panel_url or "",
        "base_path": panel.panel_base_path or "",
        "username": panel.panel_username or "",
        "has_password": bool(panel.panel_password),
        "has_token": bool(get_panel_api_token(panel)),
        "mode": panel.provisioning_mode,
        "verified": bool(panel.is_verified),
        "subscription_base_url": panel.subscription_base_url or "",
        "selected_inbound_ids": get_selected_inbound_ids(panel),
    }


def _order_dict(order: Order) -> dict[str, Any]:
    return {
        "id": order.id,
        "user_id": order.user_id,
        "days": order.days,
        "traffic_gb": order.traffic_gb,
        "price": order.price,
        "original_price": order.original_price,
        "plan_id": order.plan_id,
        "promo_code": order.promo_code,
        "device_limit": order.device_limit,
        "status": order.status,
        "created_at": order.created_at.strftime("%Y-%m-%d %H:%M"),
    }


# --- dashboard -------------------------------------------------------------


@routes.get("/api/admin/dashboard")
async def admin_dashboard(request: web.Request) -> web.Response:
    _admin(request)
    async with AsyncSessionLocal() as session:
        stats = await get_dashboard_stats(session)
        plans = await list_all_plans(session)
        stock = {plan.id: await count_available(session, plan.id) for plan in plans}
    return ok({"stats": stats, "stock": stock})


@routes.get("/api/admin/payments")
async def admin_payments(request: web.Request) -> web.Response:
    """The payment history, newest first."""
    _admin(request)
    try:
        limit = min(int(request.query.get("limit", 50)), 200)
    except (TypeError, ValueError):
        limit = 50

    async with AsyncSessionLocal() as session:
        rows = (
            await session.execute(
                select(Payment, Order)
                .join(Order, Order.id == Payment.order_id)
                .order_by(Payment.created_at.desc())
                .limit(limit)
            )
        ).all()
        items = [
            {
                "id": payment.id,
                "order_id": payment.order_id,
                "user_id": payment.user_id,
                "method": payment.method,
                "status": payment.status,
                "amount": order.price,
                "days": order.days,
                "traffic_gb": order.traffic_gb,
                "receipt_file_id": payment.receipt_file_id,
                "admin_note": payment.admin_note,
                "created_at": payment.created_at.strftime("%Y-%m-%d %H:%M"),
            }
            for payment, order in rows
        ]
    return ok({"payments": items})


@routes.get("/api/admin/pending")
async def admin_pending(request: web.Request) -> web.Response:
    _admin(request)
    async with AsyncSessionLocal() as session:
        rows = (
            await session.execute(
                select(Payment, Order)
                .join(Order, Order.id == Payment.order_id)
                .where(Payment.status == "pending")
                .order_by(Payment.created_at.desc())
                .limit(50)
            )
        ).all()
        topups = (
            await session.execute(
                select(WalletTopUp)
                .where(WalletTopUp.status == "pending")
                .order_by(WalletTopUp.created_at.desc())
                .limit(50)
            )
        ).scalars().all()

        items = []
        for payment, order in rows:
            items.append(
                {
                    "kind": "order",
                    "id": payment.id,
                    "order_id": payment.order_id,
                    "user_id": payment.user_id,
                    "method": payment.method,
                    "amount": order.price,
                    "wallet_debit": order.wallet_debit,
                    "days": order.days,
                    "traffic_gb": order.traffic_gb,
                    "receipt_file_id": payment.receipt_file_id,
                    "created_at": payment.created_at.strftime("%Y-%m-%d %H:%M"),
                }
            )
        for topup in topups:
            items.append(
                {
                    "kind": "topup",
                    "id": topup.id,
                    "user_id": topup.user_id,
                    "amount": topup.requested_amount,
                    "created_at": topup.created_at.strftime("%Y-%m-%d %H:%M"),
                }
            )
    return ok({"items": items})


@routes.post("/api/admin/payments/{payment_id}/approve")
async def admin_approve_payment(request: web.Request) -> web.Response:
    """Approve a card payment and deliver the order."""
    _admin(request)
    payment_id = int(request.match_info["payment_id"])
    bot = request.app["bot"]

    async with AsyncSessionLocal() as session:
        # Claim the payment first: only one request can move it out of
        # "pending", so a double click cannot deliver the order twice.
        claimed = await session.execute(
            update(Payment)
            .where(Payment.id == payment_id, Payment.status == "pending")
            .values(status="approved")
            .execution_options(synchronize_session=False)
        )
        if claimed.rowcount != 1:
            await session.rollback()
            return fail("پرداخت پیدا نشد یا قبلاً بررسی شده.", code="not_pending")
        order_id = (
            await session.execute(select(Payment.order_id).where(Payment.id == payment_id))
        ).scalar_one()
        await session.commit()

    delivery = await deliver_paid_order(bot, order_id, payment_id)
    return ok({"delivery": delivery.kind})


@routes.post("/api/admin/payments/{payment_id}/reject")
async def admin_reject_payment(request: web.Request) -> web.Response:
    """Reject a card payment and refund any wallet debit."""
    _admin(request)
    payment_id = int(request.match_info["payment_id"])
    bot = request.app["bot"]

    async with AsyncSessionLocal() as session:
        order_id = (
            await session.execute(
                select(Payment.order_id).where(Payment.id == payment_id)
            )
        ).scalar_one_or_none()
        if order_id is None:
            return not_found("پرداخت پیدا نشد.")

        claimed = await session.execute(
            update(Payment)
            .where(Payment.id == payment_id, Payment.status == "pending")
            .values(status="rejected")
            .execution_options(synchronize_session=False)
        )
        if claimed.rowcount != 1:
            await session.rollback()
            return fail("پرداخت پیدا نشد یا قبلاً بررسی شده.", code="not_pending")

        order = (
            await session.execute(select(Order).where(Order.id == order_id))
        ).scalar_one_or_none()

        payment = (
            await session.execute(select(Payment).where(Payment.id == payment_id))
        ).scalar_one_or_none()
        payment.reviewed_at = datetime.utcnow()
        refunded = 0
        if order:
            order.status = "rejected"
            if order.wallet_debit:
                refunded = int(order.wallet_debit)
                await credit_balance(session, order.user_id, refunded)
                order.wallet_debit = 0
        await session.commit()
        user_id = order.user_id if order else 0

    if user_id:
        try:
            await bot.send_message(
                chat_id=user_id,
                text=(
                    "پرداخت شما تایید نشد."
                    + (f"\n{refunded:,} تومان به کیف پول شما برگشت." if refunded else "")
                ),
            )
        except Exception as exc:
            logger.warning("Could not notify the customer: %s", exc)

    return ok({"refunded": refunded})


@routes.post("/api/admin/topups/{topup_id}/approve")
async def admin_approve_topup(request: web.Request) -> web.Response:
    _admin(request)
    topup_id = int(request.match_info["topup_id"])
    bot = request.app["bot"]

    async with AsyncSessionLocal() as session:
        topup = (
            await session.execute(
                select(WalletTopUp).where(WalletTopUp.id == topup_id)
            )
        ).scalar_one_or_none()
        if not topup or topup.status != "pending":
            return fail("درخواست پیدا نشد یا قبلاً بررسی شده.", code="not_pending")
        amount = int(topup.requested_amount)
        topup.status = "approved"
        topup.credited_amount = amount
        balance = await credit_balance(session, topup.user_id, amount)
        await session.commit()

    try:
        await bot.send_message(
            chat_id=topup.user_id,
            text=(
                f"شارژ کیف پول تایید شد.\n"
                f"مبلغ: {amount:,} تومان\nموجودی: {balance:,} تومان"
            ),
        )
    except Exception as exc:
        logger.warning("Could not notify the customer: %s", exc)

    return ok({"balance": balance})


@routes.post("/api/admin/topups/{topup_id}/reject")
async def admin_reject_topup(request: web.Request) -> web.Response:
    _admin(request)
    topup_id = int(request.match_info["topup_id"])
    bot = request.app["bot"]

    async with AsyncSessionLocal() as session:
        topup = (
            await session.execute(
                select(WalletTopUp).where(WalletTopUp.id == topup_id)
            )
        ).scalar_one_or_none()
        if not topup or topup.status != "pending":
            return fail("درخواست پیدا نشد یا قبلاً بررسی شده.", code="not_pending")
        topup.status = "rejected"
        await session.commit()

    try:
        await bot.send_message(
            chat_id=topup.user_id, text="درخواست شارژ کیف پول شما تایید نشد."
        )
    except Exception as exc:
        logger.warning("Could not notify the customer: %s", exc)

    return ok({})


# --- plans -----------------------------------------------------------------


@routes.get("/api/admin/plans")
async def admin_plans(request: web.Request) -> web.Response:
    _admin(request)
    async with AsyncSessionLocal() as session:
        plans = await list_all_plans(session)
        stock = {p.id: await count_available(session, p.id) for p in plans}
    return ok({"plans": [_plan_dict(p) for p in plans], "stock": stock})


@routes.post("/api/admin/plans")
async def admin_create_plan(request: web.Request) -> web.Response:
    _admin(request)
    body = await _json_body(request)
    try:
        async with AsyncSessionLocal() as session:
            plan = await create_plan(
                session,
                plan_id=(body.get("id") or "").strip().lower().replace(" ", "_"),
                name=(body.get("name") or "").strip(),
                days=int(body.get("days") or 0),
                traffic_gb=int(body.get("traffic_gb") or 0),
                price=int(body.get("price") or 0),
                description=(body.get("description") or "").strip(),
                devices=int(body.get("devices") or 1),
            )
            await session.commit()
            await session.refresh(plan)
    except (ValueError, TypeError) as exc:
        return fail(str(exc), code="invalid_plan")
    return ok({"plan": _plan_dict(plan)})


@routes.post("/api/admin/plans/{plan_id}")
async def admin_update_plan(request: web.Request) -> web.Response:
    _admin(request)
    plan_id = request.match_info["plan_id"]
    body = await _json_body(request)

    async with AsyncSessionLocal() as session:
        plan = await get_plan(session, plan_id)
        if not plan:
            return not_found("پلن پیدا نشد.")
        fields: dict[str, Any] = {}
        for key in ("name", "description"):
            if body.get(key) is not None:
                fields[key] = str(body[key]).strip()
        for key in ("days", "traffic_gb", "price", "devices"):
            if body.get(key) is not None:
                try:
                    fields[key] = int(body[key])
                except (TypeError, ValueError):
                    return fail("مقادیر عددی نامعتبرند.", code="invalid_plan")
        if "devices" in fields and not 1 <= fields["devices"] <= 50:
            return fail("تعداد دستگاه باید بین ۱ تا ۵۰ باشد.", code="invalid_plan")
        if body.get("is_active") is not None:
            await set_plan_active(session, plan, bool(body["is_active"]))
        if fields:
            await update_plan_fields(session, plan, **fields)
    return ok({"plan": _plan_dict(plan)})


@routes.delete("/api/admin/plans/{plan_id}")
async def admin_delete_plan(request: web.Request) -> web.Response:
    _admin(request)
    plan_id = request.match_info["plan_id"]
    async with AsyncSessionLocal() as session:
        plan = await get_plan(session, plan_id)
        if not plan:
            return not_found("پلن پیدا نشد.")
        try:
            await delete_plan(session, plan)
        except ValueError as exc:
            return fail(str(exc), code="plan_in_use")
    return ok({})


# --- promo codes -----------------------------------------------------------


@routes.get("/api/admin/promos")
async def admin_promos(request: web.Request) -> web.Response:
    _admin(request)
    async with AsyncSessionLocal() as session:
        promos = await list_promos(session)
    return ok({"promos": [_promo_dict(p) for p in promos]})


@routes.post("/api/admin/promos")
async def admin_create_promo(request: web.Request) -> web.Response:
    _admin(request)
    body = await _json_body(request)
    try:
        async with AsyncSessionLocal() as session:
            promo = await create_promo(
                session,
                code=(body.get("code") or "").strip(),
                kind=(body.get("kind") or "days").strip(),
                value=int(body.get("value") or 0),
                max_uses=int(body.get("max_uses") or 0),
                created_by=_admin(request),
            )
            await session.commit()
            await session.refresh(promo)
    except (ValueError, TypeError) as exc:
        return fail(str(exc), code="invalid_promo")
    return ok({"promo": _promo_dict(promo)})


@routes.post("/api/admin/promos/{promo_id}/toggle")
async def admin_toggle_promo(request: web.Request) -> web.Response:
    _admin(request)
    promo_id = int(request.match_info["promo_id"])
    async with AsyncSessionLocal() as session:
        promo = (
            await session.execute(select(PromoCode).where(PromoCode.id == promo_id))
        ).scalar_one_or_none()
        if not promo:
            return not_found("کد پرومو پیدا نشد.")
        await set_promo_active(session, promo, not promo.is_active)
    return ok({"promo": _promo_dict(promo)})


@routes.delete("/api/admin/promos/{promo_id}")
async def admin_delete_promo(request: web.Request) -> web.Response:
    _admin(request)
    promo_id = int(request.match_info["promo_id"])
    async with AsyncSessionLocal() as session:
        promo = (
            await session.execute(select(PromoCode).where(PromoCode.id == promo_id))
        ).scalar_one_or_none()
        if not promo:
            return not_found("کد پرومو پیدا نشد.")
        await delete_promo(session, promo)
    return ok({})


# --- users -----------------------------------------------------------------


@routes.get("/api/admin/users")
async def admin_users(request: web.Request) -> web.Response:
    _admin(request)
    query = (request.query.get("q") or "").strip()
    async with AsyncSessionLocal() as session:
        if query:
            user = await find_user(session, query)
            users = [user] if user else []
        else:
            result = await session.execute(
                select(User).order_by(User.id.desc()).limit(50)
            )
            users = list(result.scalars().all())
        summaries = []
        for user in users:
            summary = await get_user_summary(session, user.id)
            if summary:
                summaries.append(
                    {
                        "id": summary.user.id,
                        "name": summary.display_name,
                        "balance": summary.balance,
                        "orders": summary.total_orders,
                        "spent": summary.total_spent,
                        "blocked": bool(summary.user.is_blocked),
                        "referrals": summary.referrals,
                    }
                )
    return ok({"users": summaries})


@routes.get("/api/admin/users/{user_id}")
async def admin_user_detail(request: web.Request) -> web.Response:
    _admin(request)
    user_id = int(request.match_info["user_id"])
    async with AsyncSessionLocal() as session:
        summary = await get_user_summary(session, user_id)
        if not summary:
            return not_found("کاربر پیدا نشد.")
        result = await session.execute(
            select(Order)
            .where(Order.user_id == user_id)
            .order_by(Order.created_at.desc())
            .limit(20)
        )
        orders = [_order_dict(o) for o in result.scalars().all()]
    return ok(
        {
            "user": {
                "id": summary.user.id,
                "name": summary.display_name,
                "username": summary.user.username or "",
                "balance": summary.balance,
                "total_orders": summary.total_orders,
                "completed_orders": summary.completed_orders,
                "pending_payments": summary.pending_payments,
                "total_spent": summary.total_spent,
                "active_accounts": summary.active_accounts,
                "referrals": summary.referrals,
                "referred_by": summary.referred_by,
                "blocked": bool(summary.user.is_blocked),
            },
            "orders": orders,
        }
    )


@routes.post("/api/admin/users/{user_id}/block")
async def admin_block_user(request: web.Request) -> web.Response:
    _admin(request)
    user_id = int(request.match_info["user_id"])
    body = await _json_body(request)
    async with AsyncSessionLocal() as session:
        user = await set_user_blocked(session, user_id, bool(body.get("blocked", True)))
        if not user:
            return not_found("کاربر پیدا نشد.")
    return ok({"blocked": bool(user.is_blocked)})


@routes.post("/api/admin/users/{user_id}/credit")
async def admin_credit_user(request: web.Request) -> web.Response:
    _admin(request)
    user_id = int(request.match_info["user_id"])
    body = await _json_body(request)
    try:
        amount = int(body.get("amount"))
    except (TypeError, ValueError):
        return fail("مبلغ نامعتبر است.", code="invalid_amount")
    if amount < 1:
        return fail("مبلغ باید بزرگ‌تر از صفر باشد.", code="invalid_amount")
    async with AsyncSessionLocal() as session:
        balance = await credit_balance(session, user_id, amount)
        await session.commit()
    return ok({"balance": balance})


@routes.post("/api/admin/users/{user_id}/renew")
async def admin_renew_user(request: web.Request) -> web.Response:
    _admin(request)
    user_id = int(request.match_info["user_id"])
    async with AsyncSessionLocal() as session:
        account = (
            await session.execute(
                select(VPNAccount)
                .where(VPNAccount.user_id == user_id, VPNAccount.is_active.is_(True))
                .order_by(VPNAccount.created_at.desc())
                .limit(1)
            )
        ).scalar_one_or_none()
        if not account:
            return fail("اکانت فعالی برای این کاربر نیست.", code="no_account")

        from app.services.renewal import extend_vpn_account

        order = Order(
            user_id=user_id,
            days=30,
            traffic_gb=0,
            price=0,
            original_price=0,
            renew_vpn_account_id=account.id,
            status="completed",
        )
        session.add(order)
        await session.flush()
        await extend_vpn_account(session, account, order)
        await session.commit()
        await session.refresh(account)
    return ok({"expires_at": account.expires_at.strftime("%Y-%m-%d")})


# --- shop settings ---------------------------------------------------------


@routes.get("/api/admin/settings")
async def admin_settings(request: web.Request) -> web.Response:
    """Everything the shop settings screen needs."""
    _admin(request)
    async with AsyncSessionLocal() as session:
        from app.services.bot_settings import get_bot_settings

        shop = await get_bot_settings(session)
        panel = await get_panel_settings(session)
        pricing = await get_pricing(session)

    return ok(
        {
            "card_number": shop.card_number or "",
            "card_holder": shop.card_holder or "",
            "maintenance": bool(shop.maintenance_mode),
            "trial": {
                "enabled": bool(shop.trial_enabled),
                "days": shop.trial_days,
                "traffic_gb": shop.trial_gb,
            },
            "stars_rate": shop.stars_rate,
            "pricing": pricing,
            "panel": _panel_dict(panel),
        }
    )


@routes.post("/api/admin/settings")
async def admin_update_settings(request: web.Request) -> web.Response:
    """Update the card details, the trial, the Stars rate or maintenance."""
    _admin(request)
    body = await _json_body(request)

    from app.services.bot_settings import set_card_holder, set_card_number

    try:
        card_number = optional_text(body, "card_number", maximum=64)
        card_holder = optional_text(body, "card_holder", maximum=255)
        maintenance = optional_bool(body, "maintenance")
        trial_enabled = optional_bool(body, "trial_enabled")
        trial_days = optional_int(body, "trial_days", minimum=0, maximum=365)
        trial_gb = optional_int(body, "trial_gb", minimum=0, maximum=5000)
        stars_rate = optional_int(body, "stars_rate", minimum=1, maximum=100000)
        pricing = body.get("pricing") if isinstance(body.get("pricing"), dict) else {}
        per_day = optional_int(pricing, "per_day", minimum=0, maximum=1000000)
        per_gb = optional_int(pricing, "per_gb", minimum=0, maximum=1000000)
    except FieldError as exc:
        return fail(str(exc), code="invalid_settings")

    async with AsyncSessionLocal() as session:
        if card_number is not None:
            await set_card_number(session, card_number)
        if card_holder is not None:
            await set_card_holder(session, card_holder)
        await set_bot_settings(
            session,
            maintenance_mode=maintenance,
            trial_enabled=trial_enabled,
            trial_days=trial_days,
            trial_gb=trial_gb,
            stars_rate=stars_rate,
        )
        if per_day is not None or per_gb is not None:
            await set_pricing(session, per_day=per_day, per_gb=per_gb)
    return ok({})


# --- 3x-ui panel -----------------------------------------------------------


@routes.get("/api/admin/panel")
async def admin_panel(request: web.Request) -> web.Response:
    """The stored panel connection and the inbounds it offers."""
    _admin(request)
    async with AsyncSessionLocal() as session:
        panel = await get_panel_settings(session)
        stored = _panel_dict(panel)
        inbounds: list[dict[str, Any]] = []
        error = ""
        if panel.panel_url and panel.panel_username:
            try:
                async with xui_client_for_panel(panel) as client:
                    for inbound in await client.list_inbounds():
                        inbounds.append(
                            {
                                "id": int(inbound.get("id") or 0),
                                "remark": inbound.get("remark") or "",
                                "protocol": inbound.get("protocol") or "",
                                "enable": bool(inbound.get("enable")),
                            }
                        )
            except Exception as exc:
                error = str(exc)
                logger.warning("Could not list the panel inbounds: %s", exc)

    return ok({"panel": stored, "inbounds": inbounds, "error": error})


@routes.post("/api/admin/panel")
async def admin_update_panel(request: web.Request) -> web.Response:
    """Store the panel connection."""
    _admin(request)
    body = await _json_body(request)
    url = (body.get("url") or "").strip()
    if url and not url.startswith("https://"):
        return fail("آدرس پنل باید با https:// شروع شود.", code="invalid_url")

    async with AsyncSessionLocal() as session:
        panel = await update_panel_settings(
            session,
            panel_url=url or None,
            panel_base_path=body.get("base_path"),
            panel_username=body.get("username"),
            panel_password=body.get("password"),
            api_token=body.get("api_token"),
            provisioning_mode=body.get("mode"),
        )
        stored = _panel_dict(panel)
    return ok({"panel": stored})


@routes.post("/api/admin/panel/test")
async def admin_test_panel(request: web.Request) -> web.Response:
    """Test the connection and read the subscription base from the panel."""
    _admin(request)
    async with AsyncSessionLocal() as session:
        panel = await get_panel_settings(session)
        if not panel.panel_url or not panel.panel_username:
            return fail("پنل تنظیم نشده است.", code="not_configured")
        try:
            async with xui_client_for_panel(panel) as client:
                summary = await client.test_connection()
        except Exception as exc:
            logger.warning("Panel test failed: %s", exc)
            return fail(f"اتصال ناموفق بود: {exc}", status=502, code="panel_error")

        sub_base = summary.get("subscription_base_url")
        await update_panel_settings(
            session,
            subscription_base_url=sub_base,
            is_verified=True,
        )
        panel = await get_panel_settings(session)
        stored = _panel_dict(panel)

    return ok(
        {
            "panel": stored,
            "total_inbounds": summary.get("total_inbounds"),
            "enabled_inbounds": summary.get("enabled_inbounds"),
            "subscription_base_url": sub_base,
        }
    )


@routes.post("/api/admin/panel/inbounds")
async def admin_set_inbounds(request: web.Request) -> web.Response:
    """Choose the inbounds new clients are attached to."""
    _admin(request)
    body = await _json_body(request)
    raw = body.get("inbound_ids")
    if not isinstance(raw, list):
        return fail("فهرست inbound نامعتبر است.", code="invalid_inbounds")
    try:
        ids = [int(x) for x in raw]
    except (TypeError, ValueError):
        return fail("فهرست inbound نامعتبر است.", code="invalid_inbounds")

    async with AsyncSessionLocal() as session:
        panel = await get_panel_settings(session)
        set_selected_inbound_ids(panel, ids)
        await session.commit()
        selected = get_selected_inbound_ids(panel)
    return ok({"selected_inbound_ids": selected})


@routes.post("/api/admin/panel/token")
async def admin_mint_panel_token(request: web.Request) -> web.Response:
    """Mint a fresh API token for the bot and store it."""
    _admin(request)
    async with AsyncSessionLocal() as session:
        panel = await get_panel_settings(session)
        if not panel.panel_url or not panel.panel_username:
            return fail("پنل تنظیم نشده است.", code="not_configured")
        password = get_panel_password(panel)
        if not password:
            return fail("رمز پنل برای ساخت توکن لازم است.", code="no_password")

        from app.xui.client import XUIClient

        # The panel refuses a duplicate token name, so every mint gets its own
        # name — the button always works.
        token_name = f"miniapp-{int(time.time())}"
        try:
            async with XUIClient(
                panel.panel_url,
                panel.panel_username,
                password,
                base_path=panel.panel_base_path or "",
            ) as client:
                token = await client.create_api_token(token_name)
        except Exception as exc:
            logger.error("Could not mint a panel token: %s", exc)
            return fail(f"ساخت توکن ناموفق بود: {exc}", status=502, code="panel_error")

        set_panel_api_token(panel, token)
        await session.commit()

    return ok({"has_token": True})


# --- inventory, broadcast, backup -----------------------------------------


@routes.get("/api/admin/inventory")
async def admin_inventory(request: web.Request) -> web.Response:
    _admin(request)
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(PlanConfig).order_by(PlanConfig.created_at.desc()).limit(100)
        )
        entries = [
            {
                "id": c.id,
                "plan_id": c.plan_id,
                "is_assigned": c.is_assigned,
                "order_id": c.order_id,
                "created_at": c.created_at.strftime("%Y-%m-%d %H:%M"),
            }
            for c in result.scalars().all()
        ]
    return ok({"entries": entries})


@routes.post("/api/admin/inventory")
async def admin_add_inventory(request: web.Request) -> web.Response:
    _admin(request)
    body = await _json_body(request)
    plan_id = (body.get("plan_id") or "").strip()
    config_text = (body.get("config_text") or "").strip()
    if not plan_id or not config_text:
        return fail("پلن و متن کانفیگ لازم است.", code="invalid_config")
    if not is_valid_config_text(config_text):
        return fail(
            "متن کانفیگ معتبر نیست. یک لینک اشتراک یا کانفیگ معتبر بفرستید.",
            code="invalid_config",
        )
    async with AsyncSessionLocal() as session:
        plan = await get_plan(session, plan_id)
        if not plan:
            return not_found("پلن پیدا نشد.")
        entry = await add_config(session, plan_id, config_text, _admin(request))
        available = await count_available(session, plan_id)
    return ok({"id": entry.id, "available": available})


@routes.post("/api/admin/broadcast")
async def admin_broadcast(request: web.Request) -> web.Response:
    _admin(request)
    body = await _json_body(request)
    text = (body.get("text") or "").strip()
    if not text:
        return fail("متن پیام خالی است.", code="empty_text")
    use_html = bool(body.get("use_html"))
    target = BROADCAST_TARGET_USER if body.get("user_id") else BROADCAST_TARGET_ALL
    target_chat_id = body.get("user_id")

    async with AsyncSessionLocal() as session:
        broadcast = await create_broadcast(
            session,
            text,
            use_html=use_html,
            target=target,
            target_chat_id=int(target_chat_id) if target_chat_id else None,
            created_by=_admin(request),
        )
    async with AsyncSessionLocal() as session:
        result = await send_broadcast(request.app["bot"], session, broadcast)
    return ok(
        {
            "summary": result.summary,
            "sent": result.sent,
            "failed": result.failed,
        }
    )


@routes.get("/api/admin/backup")
async def admin_backup(request: web.Request) -> web.Response:
    _admin(request)
    from app.services.backup import create_backup_bytes

    try:
        payload = await create_backup_bytes()
    except Exception as exc:
        logger.error("Database backup failed: %s", exc)
        return fail("پشتیبان‌گیری ناموفق بود.", status=500, code="backup_failed")
    return web.Response(
        body=payload,
        headers={"Content-Disposition": 'attachment; filename="db-backup.sqlite3"'},
        content_type="application/x-sqlite3",
    )
