"""JSON API for the Telegram Mini App.

Every request is authenticated with Telegram's signed launch data, so the Mini
App can act on behalf of the signed-in customer without a password.
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any, Optional

from aiohttp import web
from sqlalchemy import select

from app.bot.auth import is_admin
from app.config.settings import settings
from app.config.texts import get_text
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
from app.services.bot_settings import get_bot_settings, get_card_details
from app.services.broadcast import (
    BROADCAST_TARGET_ALL,
    BROADCAST_TARGET_USER,
    create_broadcast,
    send_broadcast,
)
from app.services.config_inventory import add_config, count_available
from app.services.order_fulfillment import fulfill_paid_order
from app.services.panel_settings import get_panel_settings, xui_client_for_panel
from app.services.plans_catalog import (
    create_plan,
    get_plan,
    get_pricing,
    list_all_plans,
    list_active_plans,
    set_plan_active,
    update_plan_fields,
)
from app.services.promocodes import (
    create_promo,
    delete_promo,
    get_promo,
    list_promos,
    normalize_code,
    reserve_promo,
    set_promo_active,
    validate_promo,
)
from app.services.qr import subscription_qr_png
from app.services.referral import (
    apply_purchase_discount,
    ensure_referral_code,
    is_discount_eligible,
    referral_link,
)
from app.services.telegram_payments import build_stars_invoice, send_stars_invoice
from app.services.trial import (
    create_trial_order,
    get_trial_settings,
    mark_trial_used,
    trial_available,
)
from app.services.users import find_user, get_user_summary, set_user_blocked
from app.services.wallet import credit_balance, debit_balance, get_balance
from app.utils.logger import logger
from app.web.auth import WebAuthError, parse_user, validate_init_data

routes = web.RouteTableDef()


# --- helpers ---------------------------------------------------------------


def _json(data: Any, status: int = 200) -> web.Response:
    return web.json_response(data, status=status, dumps=lambda o: json.dumps(o, ensure_ascii=False))


def _error(message: str, status: int = 400) -> web.Response:
    return _json({"ok": False, "error": message}, status=status)


def _init_data(request: web.Request) -> str:
    header = request.headers.get("X-Telegram-Init-Data", "")
    if header:
        return header
    auth = request.headers.get("Authorization", "")
    if auth.startswith("tma "):
        return auth[4:].strip()
    return ""


async def _current_user(request: web.Request) -> Optional[dict[str, Any]]:
    """The signed-in Telegram user, or None when the launch data is invalid."""
    data = validate_init_data(_init_data(request), settings.BOT_TOKEN)
    return parse_user(data)


def _require_user(request: web.Request) -> dict[str, Any]:
    user = request.get("telegram_user")
    if not user:
        raise web.HTTPUnauthorized(text="unauthorized")
    return user


async def _require_admin(request: web.Request) -> dict[str, Any]:
    user = _require_user(request)
    if not is_admin(user["id"]):
        raise web.HTTPForbidden(text="forbidden")
    return user


@web.middleware
async def auth_middleware(request: web.Request, handler):
    """Validate the launch data once per request."""
    if request.path.startswith("/api/"):
        try:
            request["telegram_user"] = await _current_user(request)
        except WebAuthError as exc:
            logger.warning("Rejected Mini App request: %s", exc)
            return _error("دسترسی نامعتبر است.", status=401)
    return await handler(request)


# --- customer --------------------------------------------------------------


@routes.get("/api/me")
async def api_me(request: web.Request) -> web.Response:
    tg_user = _require_user(request)
    async with AsyncSessionLocal() as session:
        user, _ = await _ensure_user(session, tg_user)
        await session.commit()
        balance = await get_balance(session, user.id)
        code = await ensure_referral_code(session, user)
        await session.commit()
        discount_ok = await is_discount_eligible(session, user)
        settings_row = await get_bot_settings(session)
        can_trial, trial_reason = await trial_available(session, user.id)
        trial = await get_trial_settings(session)
        account = await _primary_account(session, user.id)

    username = await request.app["bot"].get_me()
    return _json(
        {
            "ok": True,
            "is_admin": is_admin(tg_user["id"]),
            "user": {
                "id": tg_user["id"],
                "username": tg_user["username"],
                "first_name": tg_user["first_name"],
                "last_name": tg_user["last_name"],
            },
            "bot_username": username.username,
            "balance": balance,
            "referral_code": code,
            "referral_link": referral_link(username.username, code),
            "referral_discount_available": discount_ok,
            "maintenance": bool(settings_row.maintenance_mode),
            "trial": {
                "available": can_trial,
                "reason": trial_reason,
                "days": trial.days,
                "traffic": trial.gb,
            },
            "account": account,
        }
    )


async def _ensure_user(session, tg_user: dict[str, Any]):
    from app.services.users import get_or_create_user

    class _Telegram:
        id = tg_user["id"]
        username = tg_user["username"]
        first_name = tg_user["first_name"]
        last_name = tg_user["last_name"]

    return await get_or_create_user(session, _Telegram())


async def _primary_account(session, user_id: int) -> Optional[dict[str, Any]]:
    """The customer's live account with its traffic and expiry."""
    result = await session.execute(
        select(VPNAccount)
        .where(VPNAccount.user_id == user_id, VPNAccount.is_active.is_(True))
        .order_by(VPNAccount.created_at.desc())
        .limit(1)
    )
    account = result.scalar_one_or_none()
    if not account:
        return None

    total_bytes = int(account.traffic_limit_gb or 0) * 1024**3
    used_bytes = 0
    try:
        panel = await get_panel_settings(session)
        if panel.panel_url:
            async with xui_client_for_panel(panel) as client:
                detail = await client.get_client(str(user_id))
            if detail:
                from app.services.traffic_usage import used_bytes as read_used

                used_bytes = read_used(detail)
    except Exception as exc:
        logger.warning("Could not read panel traffic for user %s: %s", user_id, exc)

    return {
        "order_id": account.order_id,
        "expires_at": account.expires_at.strftime("%Y-%m-%d"),
        "days_left": max((account.expires_at - datetime.utcnow()).days, 0),
        "traffic_gb": account.traffic_limit_gb,
        "used_gb": round(used_bytes / 1024**3, 2),
        "subscription_url": account.subscription_path,
    }


@routes.get("/api/plans")
async def api_plans(request: web.Request) -> web.Response:
    _require_user(request)
    async with AsyncSessionLocal() as session:
        plans = await list_active_plans(session)
        pricing = await get_pricing(session)
    return _json(
        {
            "ok": True,
            "plans": plans,
            "custom_pricing": {
                "per_day": pricing["per_day"],
                "per_gb": pricing["per_gb"],
            },
        }
    )


@routes.post("/api/quote")
async def api_quote(request: web.Request) -> web.Response:
    """Price a plan (optionally with a promo code) before ordering."""
    tg_user = _require_user(request)
    body = await request.json()
    async with AsyncSessionLocal() as session:
        user, _ = await _ensure_user(session, tg_user)
        quote = await _quote(session, user, body)
    if quote.get("error"):
        return _error(quote["error"])
    return _json({"ok": True, **quote})


async def _quote(session, user, body: dict[str, Any]) -> dict[str, Any]:
    plan_id = (body.get("plan_id") or "").strip()
    promo_code = normalize_code(body.get("promo_code") or "")

    if plan_id:
        plan = await get_plan(session, plan_id, active_only=True)
        if not plan:
            return {"error": "این پلن در دسترس نیست."}
        days = int(plan["days"])
        traffic = int(plan["traffic"])
        base_price = int(plan["price"])
        devices = int(plan.get("devices") or 1)
        name = plan["name"]
    else:
        try:
            days = int(body.get("days"))
            traffic = int(body.get("traffic"))
        except (TypeError, ValueError):
            return {"error": "مدت و حجم را وارد کنید."}
        if not 1 <= days <= 365 or not 1 <= traffic <= 500:
            return {"error": "مدت باید بین ۱ تا ۳۶۵ روز و حجم بین ۱ تا ۵۰۰ گیگابایت باشد."}
        pricing = await get_pricing(session)
        base_price = days * pricing["per_day"] + traffic * pricing["per_gb"]
        devices = 1
        name = "پلن سفارشی"

    promo = None
    promo_error = ""
    if promo_code:
        promo, promo_error = await validate_promo(
            session, promo_code, user_id=user.id, price=base_price
        )

    discount = promo.discount if promo else 0
    bonus_days = promo.bonus_days if promo else 0
    bonus_gb = promo.bonus_gb if promo else 0
    payable = max(base_price - discount, 0)

    referral_applied = False
    if await is_discount_eligible(session, user):
        payable, _, referral_applied = apply_purchase_discount(payable)

    return {
        "name": name,
        "plan_id": plan_id or None,
        "days": days + bonus_days,
        "traffic": traffic + bonus_gb,
        "devices": devices,
        "base_price": base_price,
        "promo_code": promo.code if promo else None,
        "promo_discount": discount,
        "promo_bonus_days": bonus_days,
        "promo_bonus_gb": bonus_gb,
        "promo_error": promo_error,
        "referral_discount": referral_applied,
        "total": payable,
    }


@routes.post("/api/orders")
async def api_create_order(request: web.Request) -> web.Response:
    """Create an order and return how much to pay."""
    tg_user = _require_user(request)
    body = await request.json()
    async with AsyncSessionLocal() as session:
        user, _ = await _ensure_user(session, tg_user)
        settings_row = await get_bot_settings(session)
        if settings_row.maintenance_mode:
            return _error("فروش موقتاً متوقف است.", status=503)

        quote = await _quote(session, user, body)
        if quote.get("error"):
            return _error(quote["error"])

        original_price = quote["base_price"]
        order = Order(
            user_id=user.id,
            days=quote["days"],
            traffic_gb=quote["traffic"],
            price=quote["total"],
            original_price=original_price,
            plan_id=quote["plan_id"],
            referral_discount_applied=quote["referral_discount"],
            device_limit=quote["devices"],
            promo_code=quote["promo_code"],
            promo_discount=quote["promo_discount"],
            promo_bonus_days=quote["promo_bonus_days"],
            promo_bonus_gb=quote["promo_bonus_gb"],
            status="pending",
        )
        session.add(order)
        if quote["promo_code"]:
            promo = await get_promo(session, quote["promo_code"])
            if promo:
                await reserve_promo(session, promo)
        await session.commit()
        await session.refresh(order)
        card_number, card_holder = await get_card_details(session)

    return _json(
        {
            "ok": True,
            "order": _order_dict(order),
            "card_number": card_number,
            "card_holder": card_holder,
        }
    )


async def _card(session) -> tuple[str, str]:
    """Card details for the customer."""
    return await get_card_details(session)


def _order_dict(order: Order) -> dict[str, Any]:
    return {
        "id": order.id,
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


@routes.get("/api/orders")
async def api_orders(request: web.Request) -> web.Response:
    tg_user = _require_user(request)
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(Order)
            .where(Order.user_id == tg_user["id"])
            .order_by(Order.created_at.desc())
            .limit(30)
        )
        orders = [_order_dict(o) for o in result.scalars().all()]
    return _json({"ok": True, "orders": orders})


@routes.post("/api/orders/{order_id}/pay/wallet")
async def api_pay_wallet(request: web.Request) -> web.Response:
    """Pay for an order from the wallet balance."""
    tg_user = _require_user(request)
    order_id = int(request.match_info["order_id"])
    bot = request.app["bot"]

    async with AsyncSessionLocal() as session:
        order = (
            await session.execute(select(Order).where(Order.id == order_id))
        ).scalar_one_or_none()
        if not order or order.user_id != tg_user["id"]:
            return _error("سفارش پیدا نشد.", status=404)
        if order.status != "pending":
            return _error("این سفارش قابل پرداخت نیست.")

        balance = await get_balance(session, tg_user["id"])
        if balance < order.price:
            return _error("موجودی کیف پول کافی نیست.")
        if order.price <= 0:
            new_balance = balance
        else:
            new_balance = await debit_balance(session, tg_user["id"], order.price)
        order.wallet_debit = order.price
        order.status = "paid"
        payment = Payment(
            order_id=order.id,
            user_id=tg_user["id"],
            receipt_file_id="wallet",
            status="pending",
            method="wallet",
        )
        session.add(payment)
        await session.commit()
        await session.refresh(payment)
        await session.refresh(order)

    await _fulfill(bot, order.id, payment.id, tg_user["id"])
    return _json({"ok": True, "balance": new_balance, "order": _order_dict(order)})


@routes.post("/api/orders/{order_id}/pay/stars")
async def api_pay_stars(request: web.Request) -> web.Response:
    """Send a Telegram Stars invoice for an order."""
    tg_user = _require_user(request)
    order_id = int(request.match_info["order_id"])
    bot = request.app["bot"]

    async with AsyncSessionLocal() as session:
        order = (
            await session.execute(select(Order).where(Order.id == order_id))
        ).scalar_one_or_none()
        if not order or order.user_id != tg_user["id"]:
            return _error("سفارش پیدا نشد.", status=404)
        if order.status != "pending":
            return _error("این سفارش قابل پرداخت نیست.")
        stars, rate = await build_stars_invoice(session, order)
        plan = await get_plan(session, order.plan_id) if order.plan_id else None

    if stars <= 0:
        return _error("این سفارش مبلغی ندارد.")

    try:
        await send_stars_invoice(
            bot,
            tg_user["id"],
            order,
            stars=stars,
            plan_name=plan["name"] if plan else "",
        )
    except Exception as exc:
        logger.error("Could not send the stars invoice: %s", exc)
        return _error("ارسال فاکتور ناموفق بود.", status=500)

    return _json({"ok": True, "stars": stars, "toman": stars * rate})


@routes.post("/api/orders/{order_id}/pay/card")
async def api_pay_card(request: web.Request) -> web.Response:
    """Mark an order as paid by card and return the card details."""
    tg_user = _require_user(request)
    order_id = int(request.match_info["order_id"])
    async with AsyncSessionLocal() as session:
        order = (
            await session.execute(select(Order).where(Order.id == order_id))
        ).scalar_one_or_none()
        if not order or order.user_id != tg_user["id"]:
            return _error("سفارش پیدا نشد.", status=404)
        if order.status != "pending":
            return _error("این سفارش قابل پرداخت نیست.")
        card_number, card_holder = await get_card_details(session)
        if not card_number or not card_holder:
            return _error("اطلاعات کارت بانکی هنوز تنظیم نشده است.", status=503)

    return _json(
        {
            "ok": True,
            "card_number": card_number,
            "card_holder": card_holder,
            "amount": order.price,
        }
    )


@routes.post("/api/orders/{order_id}/receipt")
async def api_upload_receipt(request: web.Request) -> web.Response:
    """Upload the payment receipt photo for a card order."""
    tg_user = _require_user(request)
    order_id = int(request.match_info["order_id"])
    bot = request.app["bot"]

    reader = await request.multipart()
    part = await reader.next()
    content = await part.read(decode=False)
    if not content:
        return _error("فایلی ارسال نشد.")

    async with AsyncSessionLocal() as session:
        order = (
            await session.execute(select(Order).where(Order.id == order_id))
        ).scalar_one_or_none()
        if not order or order.user_id != tg_user["id"]:
            return _error("سفارش پیدا نشد.", status=404)
        if order.status not in ("pending", "paid"):
            return _error("برای این سفارش نمی‌توان رسید فرستاد.")

        existing = (
            await session.execute(
                select(Payment).where(
                    Payment.order_id == order.id,
                    Payment.status.in_(("pending", "approved")),
                )
            )
        ).scalar_one_or_none()
        if existing:
            return _error("رسید قبلی شما هنوز در حال بررسی است.")

        from app.services.receipts import upload_receipt

        try:
            file_id = await upload_receipt(
                bot, tg_user["id"], content, part.filename or "receipt.jpg"
            )
        except Exception as exc:
            logger.error("Receipt upload failed: %s", exc)
            return _error("ارسال رسید ناموفق بود.", status=500)

        order.status = "paid"
        payment = Payment(
            order_id=order.id,
            user_id=tg_user["id"],
            receipt_file_id=file_id,
            status="pending",
            method="card",
        )
        session.add(payment)
        await session.commit()
        await session.refresh(payment)

    for admin_id in settings.ADMIN_IDS:
        try:
            await bot.send_photo(
                chat_id=admin_id,
                photo=file_id,
                caption=(
                    f"رسید جدید\nکاربر: {tg_user['id']}\nسفارش: #{order_id}"
                ),
            )
        except Exception as exc:
            logger.warning("Could not notify admin %s: %s", admin_id, exc)

    return _json({"ok": True, "order_id": order_id})


async def _fulfill(bot, order_id: int, payment_id: int, user_id: int) -> None:
    """Deliver a paid order (auto provision, inventory, or ask the admin)."""
    from app.bot.handlers.admin import notify_admins_wallet_manual
    from app.services.users import get_or_create_user

    async with AsyncSessionLocal() as session:
        order = (
            await session.execute(select(Order).where(Order.id == order_id))
        ).scalar_one_or_none()
        payment = (
            await session.execute(select(Payment).where(Payment.id == payment_id))
        ).scalar_one_or_none()
        if not order or not payment:
            return
        plan = await get_plan(session, order.plan_id) if order.plan_id else None
        fulfill = await fulfill_paid_order(
            session, payment, order, None, plan["name"] if plan else None
        )
        if fulfill.kind in ("new_auto", "new_inventory") and fulfill.config_text:
            await bot.send_message(
                chat_id=user_id,
                text=get_text("payment_approved", subscription_url=fulfill.config_text),
            )
            return
        if fulfill.kind in ("renewal_auto", "renewal_db") and fulfill.vpn_account:
            await bot.send_message(
                chat_id=user_id,
                text=get_text(
                    "renewal_approved",
                    days=order.days,
                    traffic=order.traffic_gb,
                    expires_at=fulfill.vpn_account.expires_at.strftime("%Y-%m-%d"),
                    subscription_url=fulfill.vpn_account.subscription_path,
                ),
            )
            return
        await notify_admins_wallet_manual(bot, payment, order)


@routes.get("/api/wallet")
async def api_wallet(request: web.Request) -> web.Response:
    tg_user = _require_user(request)
    async with AsyncSessionLocal() as session:
        balance = await get_balance(session, tg_user["id"])
        card_number, card_holder = await get_card_details(session)
        result = await session.execute(
            select(WalletTopUp)
            .where(WalletTopUp.user_id == tg_user["id"])
            .order_by(WalletTopUp.created_at.desc())
            .limit(20)
        )
        topups = [
            {
                "id": t.id,
                "amount": t.requested_amount,
                "credited": t.credited_amount,
                "status": t.status,
                "created_at": t.created_at.strftime("%Y-%m-%d %H:%M"),
            }
            for t in result.scalars().all()
        ]
    return _json(
        {
            "ok": True,
            "balance": balance,
            "card_number": card_number,
            "card_holder": card_holder,
            "topups": topups,
        }
    )


@routes.post("/api/wallet/topup")
async def api_wallet_topup(request: web.Request) -> web.Response:
    """Ask the admin to credit the wallet."""
    tg_user = _require_user(request)
    body = await request.json()
    try:
        amount = int(body.get("amount"))
    except (TypeError, ValueError):
        return _error("مبلغ نامعتبر است.")
    if amount < 1:
        return _error("مبلغ باید بزرگ‌تر از صفر باشد.")

    async with AsyncSessionLocal() as session:
        await _ensure_user(session, tg_user)
        topup = WalletTopUp(
            user_id=tg_user["id"], requested_amount=amount, status="pending"
        )
        session.add(topup)
        await session.commit()
        await session.refresh(topup)

    for admin_id in settings.ADMIN_IDS:
        try:
            await request.app["bot"].send_message(
                chat_id=admin_id,
                text=(
                    f"درخواست شارژ جدید\nکاربر: {tg_user['id']}\n"
                    f"مبلغ: {amount:,} تومان\nشماره درخواست: #{topup.id}"
                ),
            )
        except Exception as exc:
            logger.warning("Could not notify admin %s: %s", admin_id, exc)

    return _json({"ok": True, "topup_id": topup.id})


@routes.get("/api/referral")
async def api_referral(request: web.Request) -> web.Response:
    tg_user = _require_user(request)
    async with AsyncSessionLocal() as session:
        user, _ = await _ensure_user(session, tg_user)
        code = await ensure_referral_code(session, user)
        await session.commit()
        discount_ok = await is_discount_eligible(session, user)
        result = await session.execute(
            select(Order.id).where(
                Order.user_id == user.id,
                Order.referral_discount_applied.is_(True),
                Order.status.in_(("pending", "paid", "completed")),
            )
        )
        used = result.scalar_one_or_none() is not None
        from sqlalchemy import func

        referrals = (
            await session.execute(
                select(func.count(User.id)).where(User.referred_by_user_id == user.id)
            )
        ).scalar_one()
        earned = (
            await session.execute(
                select(func.coalesce(func.sum(User.balance), 0)).where(
                    User.referred_by_user_id == user.id
                )
            )
        ).scalar_one()

    me = await request.app["bot"].get_me()
    return _json(
        {
            "ok": True,
            "code": code,
            "link": referral_link(me.username, code),
            "discount_available": discount_ok and not used,
            "discount_used": used,
            "referrals": int(referrals or 0),
            "referral_balance": int(earned or 0),
        }
    )


@routes.get("/api/usage")
async def api_usage(request: web.Request) -> web.Response:
    tg_user = _require_user(request)
    async with AsyncSessionLocal() as session:
        account = await _primary_account(session, tg_user["id"])
    return _json({"ok": True, "account": account})


@routes.get("/api/qr.png")
async def api_qr(request: web.Request) -> web.Response:
    """The subscription link as a QR code."""
    tg_user = _require_user(request)
    url = request.query.get("url", "")
    if not url:
        async with AsyncSessionLocal() as session:
            account = await _primary_account(session, tg_user["id"])
        url = (account or {}).get("subscription_url") or ""
    if not url:
        return _error("لینک اشتراکی برای شما پیدا نشد.", status=404)
    png = subscription_qr_png(url)
    return web.Response(body=png, content_type="image/png")


@routes.post("/api/trial")
async def api_trial(request: web.Request) -> web.Response:
    """Claim the free trial."""
    tg_user = _require_user(request)
    bot = request.app["bot"]
    async with AsyncSessionLocal() as session:
        can_take, reason = await trial_available(session, tg_user["id"])
        if not can_take:
            return _error(reason)
        user, _ = await _ensure_user(session, tg_user)
        trial_settings = await get_trial_settings(session)
        order = await create_trial_order(session, user, trial_settings)
        await mark_trial_used(session, user)
        await session.commit()
        await session.refresh(order)
        payment = Payment(
            order_id=order.id,
            user_id=user.id,
            receipt_file_id="trial",
            status="pending",
            method="trial",
        )
        session.add(payment)
        await session.commit()
        await session.refresh(payment)

    await _fulfill(bot, order.id, payment.id, tg_user["id"])
    return _json({"ok": True, "order": _order_dict(order)})
