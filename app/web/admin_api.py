"""Admin API for the Mini App.

Everything here requires an admin Telegram id; the auth middleware already
rejects unauthenticated callers, and these routes check the admin list.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from aiohttp import web
from sqlalchemy import func, select

from app.config.settings import settings
from app.database.models import Order, Payment, PlanConfig, PromoCode, User, VPNAccount, WalletTopUp
from app.database.session import AsyncSessionLocal
from app.services.bot_settings import set_bot_settings
from app.services.broadcast import (
    BROADCAST_TARGET_ALL,
    BROADCAST_TARGET_USER,
    create_broadcast,
    send_broadcast,
)
from app.services.config_inventory import add_config, count_available
from app.services.plans_catalog import (
    create_plan,
    delete_plan,
    get_plan,
    list_all_plans,
    set_plan_active,
    update_plan_fields,
)
from app.services.promocodes import (
    create_promo,
    delete_promo,
    get_promo,
    list_promos,
    set_promo_active,
)
from app.services.users import find_user, get_user_summary, set_user_blocked
from app.services.wallet import credit_balance
from app.utils.logger import logger
from app.utils.statistics import get_dashboard_stats
from app.web.api import _error, _json, _require_admin, routes


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
        "traffic_gb": plan.traffic_gb,
        "price": plan.price,
        "devices": plan.devices,
        "description": plan.description or "",
        "is_active": plan.is_active,
        "sort_order": plan.sort_order,
    }


@routes.get("/api/admin/dashboard")
async def admin_dashboard(request: web.Request) -> web.Response:
    await _require_admin(request)
    async with AsyncSessionLocal() as session:
        stats = await get_dashboard_stats(session)
        plans = await list_all_plans(session)
        stock = {
            plan.id: await count_available(session, plan.id) for plan in plans
        }
    return _json({"ok": True, "stats": stats, "stock": stock})


@routes.get("/api/admin/pending")
async def admin_pending(request: web.Request) -> web.Response:
    await _require_admin(request)
    async with AsyncSessionLocal() as session:
        payments = (
            await session.execute(
                select(Payment)
                .where(Payment.status == "pending")
                .order_by(Payment.created_at.desc())
                .limit(50)
            )
        ).scalars().all()
        topups = (
            await session.execute(
                select(WalletTopUp)
                .where(WalletTopUp.status == "pending")
                .order_by(WalletTopUp.created_at.desc())
                .limit(50)
            )
        ).scalars().all()

        items = []
        for payment in payments:
            order = (
                await session.execute(
                    select(Order).where(Order.id == payment.order_id)
                )
            ).scalar_one_or_none()
            items.append(
                {
                    "kind": "order",
                    "id": payment.id,
                    "order_id": payment.order_id,
                    "user_id": payment.user_id,
                    "method": payment.method,
                    "amount": order.price if order else 0,
                    "wallet_debit": order.wallet_debit if order else 0,
                    "days": order.days if order else 0,
                    "traffic_gb": order.traffic_gb if order else 0,
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
    return _json({"ok": True, "items": items})


@routes.post("/api/admin/payments/{payment_id}/approve")
async def admin_approve_payment(request: web.Request) -> web.Response:
    await _require_admin(request)
    payment_id = int(request.match_info["payment_id"])
    bot = request.app["bot"]

    async with AsyncSessionLocal() as session:
        payment = (
            await session.execute(select(Payment).where(Payment.id == payment_id))
        ).scalar_one_or_none()
        if not payment or payment.status != "pending":
            return _error("پرداخت پیدا نشد یا قبلاً بررسی شده.")
        order = (
            await session.execute(select(Order).where(Order.id == payment.order_id))
        ).scalar_one_or_none()
        if not order:
            return _error("سفارش پیدا نشد.", status=404)
        plan = await get_plan(session, order.plan_id) if order.plan_id else None
        from app.services.order_fulfillment import fulfill_paid_order

        fulfill = await fulfill_paid_order(
            session, payment, order, None, plan["name"] if plan else None
        )

    if fulfill.kind in ("new_auto", "new_inventory") and fulfill.config_text:
        await bot.send_message(
            chat_id=order.user_id,
            text=(
                "پرداخت شما تایید شد. اکانت شما آماده است:\n"
                f"{fulfill.config_text}"
            ),
        )
    elif fulfill.kind in ("renewal_auto", "renewal_db") and fulfill.vpn_account:
        await bot.send_message(
            chat_id=order.user_id,
            text=(
                "تمدید تایید شد.\n"
                f"انقضای جدید: {fulfill.vpn_account.expires_at:%Y-%m-%d}\n"
                f"لینک اشتراک: {fulfill.vpn_account.subscription_path}"
            ),
        )
    else:
        from app.bot.handlers.admin import notify_admins_wallet_manual

        await notify_admins_wallet_manual(bot, payment, order)

    return _json({"ok": True, "kind": fulfill.kind})


@routes.post("/api/admin/payments/{payment_id}/reject")
async def admin_reject_payment(request: web.Request) -> web.Response:
    await _require_admin(request)
    payment_id = int(request.match_info["payment_id"])
    bot = request.app["bot"]

    async with AsyncSessionLocal() as session:
        payment = (
            await session.execute(select(Payment).where(Payment.id == payment_id))
        ).scalar_one_or_none()
        if not payment or payment.status != "pending":
            return _error("پرداخت پیدا نشد یا قبلاً بررسی شده.")
        order = (
            await session.execute(select(Order).where(Order.id == payment.order_id))
        ).scalar_one_or_none()
        payment.status = "rejected"
        payment.reviewed_at = datetime.utcnow()
        if order:
            order.status = "rejected"
            if order.wallet_debit:
                await credit_balance(session, order.user_id, order.wallet_debit)
                order.wallet_debit = 0
        await session.commit()

    if order:
        await bot.send_message(
            chat_id=order.user_id,
            text="پرداخت شما تایید نشد. اگر فکر می‌کنید اشتباه شده با پشتیبانی تماس بگیرید.",
        )

    return _json({"ok": True})


@routes.post("/api/admin/topups/{topup_id}/approve")
async def admin_approve_topup(request: web.Request) -> web.Response:
    await _require_admin(request)
    topup_id = int(request.match_info["topup_id"])
    bot = request.app["bot"]

    async with AsyncSessionLocal() as session:
        topup = (
            await session.execute(
                select(WalletTopUp).where(WalletTopUp.id == topup_id)
            )
        ).scalar_one_or_none()
        if not topup or topup.status != "pending":
            return _error("درخواست پیدا نشد یا قبلاً بررسی شده.")
        amount = int(request.query.get("amount") or topup.requested_amount)
        topup.status = "approved"
        topup.credited_amount = amount
        balance = await credit_balance(session, topup.user_id, amount)
        await session.commit()

    await bot.send_message(
        chat_id=topup.user_id,
        text=f"شارژ کیف پول تایید شد.\nمبلغ: {amount:,} تومان\nموجودی: {balance:,} تومان",
    )
    return _json({"ok": True, "balance": balance})


@routes.post("/api/admin/topups/{topup_id}/reject")
async def admin_reject_topup(request: web.Request) -> web.Response:
    await _require_admin(request)
    topup_id = int(request.match_info["topup_id"])
    bot = request.app["bot"]

    async with AsyncSessionLocal() as session:
        topup = (
            await session.execute(
                select(WalletTopUp).where(WalletTopUp.id == topup_id)
            )
        ).scalar_one_or_none()
        if not topup or topup.status != "pending":
            return _error("درخواست پیدا نشد یا قبلاً بررسی شده.")
        topup.status = "rejected"
        await session.commit()

    await bot.send_message(
        chat_id=topup.user_id, text="درخواست شارژ کیف پول شما تایید نشد."
    )
    return _json({"ok": True})


# --- plans -----------------------------------------------------------------


@routes.get("/api/admin/plans")
async def admin_plans(request: web.Request) -> web.Response:
    await _require_admin(request)
    async with AsyncSessionLocal() as session:
        plans = await list_all_plans(session)
        stock = {p.id: await count_available(session, p.id) for p in plans}
    return _json(
        {"ok": True, "plans": [_plan_dict(p) for p in plans], "stock": stock}
    )


@routes.post("/api/admin/plans")
async def admin_create_plan(request: web.Request) -> web.Response:
    await _require_admin(request)
    body = await request.json()
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
    except ValueError as exc:
        return _error(str(exc))
    return _json({"ok": True, "plan": _plan_dict(plan)})


@routes.post("/api/admin/plans/{plan_id}")
async def admin_update_plan(request: web.Request) -> web.Response:
    await _require_admin(request)
    plan_id = request.match_info["plan_id"]
    body = await request.json()
    async with AsyncSessionLocal() as session:
        plan = await get_plan(session, plan_id)
        if not plan:
            return _error("پلن پیدا نشد.", status=404)
        fields: dict[str, Any] = {}
        for key in ("name", "description"):
            if body.get(key) is not None:
                fields[key] = str(body[key]).strip()
        for key in ("days", "traffic_gb", "price", "devices"):
            if body.get(key) is not None:
                try:
                    fields[key] = int(body[key])
                except (TypeError, ValueError):
                    return _error("مقادیر عددی نامعتبرند.")
        if "devices" in fields and not 1 <= fields["devices"] <= 50:
            return _error("تعداد دستگاه باید بین ۱ تا ۵۰ باشد.")
        if body.get("is_active") is not None:
            await set_plan_active(session, plan, bool(body["is_active"]))
        if fields:
            await update_plan_fields(session, plan, **fields)
    return _json({"ok": True, "plan": _plan_dict(plan)})


@routes.delete("/api/admin/plans/{plan_id}")
async def admin_delete_plan(request: web.Request) -> web.Response:
    await _require_admin(request)
    plan_id = request.match_info["plan_id"]
    async with AsyncSessionLocal() as session:
        plan = await get_plan(session, plan_id)
        if not plan:
            return _error("پلن پیدا نشد.", status=404)
        await delete_plan(session, plan)
    return _json({"ok": True})


# --- promo codes -----------------------------------------------------------


@routes.get("/api/admin/promos")
async def admin_promos(request: web.Request) -> web.Response:
    await _require_admin(request)
    async with AsyncSessionLocal() as session:
        promos = await list_promos(session)
    return _json({"ok": True, "promos": [_promo_dict(p) for p in promos]})


@routes.post("/api/admin/promos")
async def admin_create_promo(request: web.Request) -> web.Response:
    await _require_admin(request)
    body = await request.json()
    try:
        async with AsyncSessionLocal() as session:
            promo = await create_promo(
                session,
                code=(body.get("code") or "").strip(),
                kind=(body.get("kind") or "days").strip(),
                value=int(body.get("value") or 0),
                max_uses=int(body.get("max_uses") or 0),
                created_by=(await _admin_id(request)),
            )
            await session.commit()
            await session.refresh(promo)
    except (ValueError, TypeError) as exc:
        return _error(str(exc))
    return _json({"ok": True, "promo": _promo_dict(promo)})


async def _admin_id(request: web.Request) -> int:
    user = request.get("telegram_user") or {}
    return int(user.get("id") or 0)


@routes.post("/api/admin/promos/{promo_id}/toggle")
async def admin_toggle_promo(request: web.Request) -> web.Response:
    await _require_admin(request)
    promo_id = int(request.match_info["promo_id"])
    async with AsyncSessionLocal() as session:
        promo = (
            await session.execute(select(PromoCode).where(PromoCode.id == promo_id))
        ).scalar_one_or_none()
        if not promo:
            return _error("کد پرومو پیدا نشد.", status=404)
        await set_promo_active(session, promo, not promo.is_active)
    return _json({"ok": True, "promo": _promo_dict(promo)})


@routes.delete("/api/admin/promos/{promo_id}")
async def admin_delete_promo(request: web.Request) -> web.Response:
    await _require_admin(request)
    promo_id = int(request.match_info["promo_id"])
    async with AsyncSessionLocal() as session:
        promo = (
            await session.execute(select(PromoCode).where(PromoCode.id == promo_id))
        ).scalar_one_or_none()
        if not promo:
            return _error("کد پرومو پیدا نشد.", status=404)
        await delete_promo(session, promo)
    return _json({"ok": True})


# --- users -----------------------------------------------------------------


@routes.get("/api/admin/users")
async def admin_users(request: web.Request) -> web.Response:
    await _require_admin(request)
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
    return _json({"ok": True, "users": summaries})


@routes.get("/api/admin/users/{user_id}")
async def admin_user_detail(request: web.Request) -> web.Response:
    await _require_admin(request)
    user_id = int(request.match_info["user_id"])
    async with AsyncSessionLocal() as session:
        summary = await get_user_summary(session, user_id)
        if not summary:
            return _error("کاربر پیدا نشد.", status=404)
        result = await session.execute(
            select(Order)
            .where(Order.user_id == user_id)
            .order_by(Order.created_at.desc())
            .limit(20)
        )
        orders = [
            {
                "id": o.id,
                "price": o.price,
                "status": o.status,
                "days": o.days,
                "traffic_gb": o.traffic_gb,
                "created_at": o.created_at.strftime("%Y-%m-%d %H:%M"),
            }
            for o in result.scalars().all()
        ]
    return _json(
        {
            "ok": True,
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
    await _require_admin(request)
    user_id = int(request.match_info["user_id"])
    body = await request.json()
    blocked = bool(body.get("blocked", True))
    async with AsyncSessionLocal() as session:
        user = await set_user_blocked(session, user_id, blocked)
        if not user:
            return _error("کاربر پیدا نشد.", status=404)
    return _json({"ok": True, "blocked": blocked})


@routes.post("/api/admin/users/{user_id}/credit")
async def admin_credit_user(request: web.Request) -> web.Response:
    await _require_admin(request)
    user_id = int(request.match_info["user_id"])
    body = await request.json()
    try:
        amount = int(body.get("amount"))
    except (TypeError, ValueError):
        return _error("مبلغ نامعتبر است.")
    if amount < 1:
        return _error("مبلغ باید بزرگ‌تر از صفر باشد.")
    async with AsyncSessionLocal() as session:
        balance = await credit_balance(session, user_id, amount)
        await session.commit()
    return _json({"ok": True, "balance": balance})


@routes.post("/api/admin/users/{user_id}/renew")
async def admin_renew_user(request: web.Request) -> web.Response:
    await _require_admin(request)
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
            return _error("اکانت فعالی برای این کاربر نیست.")

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
    return _json({"ok": True, "expires_at": account.expires_at.strftime("%Y-%m-%d")})


# --- shop settings / broadcast / backup ------------------------------------


@routes.post("/api/admin/maintenance")
async def admin_maintenance(request: web.Request) -> web.Response:
    await _require_admin(request)
    body = await request.json()
    async with AsyncSessionLocal() as session:
        row = await set_bot_settings(
            session, maintenance_mode=bool(body.get("enabled"))
        )
    return _json({"ok": True, "maintenance": bool(row.maintenance_mode)})


@routes.post("/api/admin/trial")
async def admin_trial_settings(request: web.Request) -> web.Response:
    await _require_admin(request)
    body = await request.json()
    async with AsyncSessionLocal() as session:
        row = await set_bot_settings(
            session,
            trial_enabled=body.get("enabled"),
            trial_days=body.get("days"),
            trial_gb=body.get("traffic_gb"),
        )
    return _json(
        {
            "ok": True,
            "trial": {
                "enabled": bool(row.trial_enabled),
                "days": row.trial_days,
                "traffic_gb": row.trial_gb,
            },
        }
    )


@routes.post("/api/admin/broadcast")
async def admin_broadcast(request: web.Request) -> web.Response:
    await _require_admin(request)
    body = await request.json()
    text = (body.get("text") or "").strip()
    if not text:
        return _error("متن پیام خالی است.")
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
            created_by=await _admin_id(request),
        )
    async with AsyncSessionLocal() as session:
        result = await send_broadcast(request.app["bot"], session, broadcast)
    return _json({"ok": True, "summary": result.summary, "sent": result.sent, "failed": result.failed})


@routes.get("/api/admin/backup")
async def admin_backup(request: web.Request) -> web.Response:
    await _require_admin(request)
    from app.services.backup import create_backup_bytes

    try:
        payload = await create_backup_bytes()
    except Exception as exc:
        logger.error("Database backup failed: %s", exc)
        return _error("پشتیبان‌گیری ناموفق بود.", status=500)
    return web.Response(
        body=payload,
        headers={"Content-Disposition": 'attachment; filename="db-backup.sqlite3"'},
        content_type="application/x-sqlite3",
    )


@routes.get("/api/admin/inventory")
async def admin_inventory(request: web.Request) -> web.Response:
    await _require_admin(request)
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
    return _json({"ok": True, "entries": entries})


@routes.post("/api/admin/inventory")
async def admin_add_inventory(request: web.Request) -> web.Response:
    await _require_admin(request)
    body = await request.json()
    plan_id = (body.get("plan_id") or "").strip()
    config_text = (body.get("config_text") or "").strip()
    if not plan_id or not config_text:
        return _error("پلن و متن کانفیگ لازم است.")
    async with AsyncSessionLocal() as session:
        plan = await get_plan(session, plan_id)
        if not plan:
            return _error("پلن پیدا نشد.", status=404)
        entry = await add_config(
            session, plan_id, config_text, await _admin_id(request)
        )
        available = await count_available(session, plan_id)
    return _json({"ok": True, "id": entry.id, "available": available})
