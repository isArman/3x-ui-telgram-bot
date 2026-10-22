"""Customer API for the Mini App.

Every request is authenticated with Telegram's signed launch data, so the Mini
App acts on behalf of the signed-in customer without a password.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from aiohttp import web
from sqlalchemy import select, update

from app.bot.auth import is_admin
from app.config.settings import settings
from app.database.models import Order, Payment, User, VPNAccount, WalletTopUp
from app.database.session import AsyncSessionLocal
from app.services.bot_settings import get_bot_settings, get_card_details
from app.services.fulfillment import deliver_paid_order
from app.services.panel_settings import get_panel_settings, xui_client_for_panel
from app.services.plans_catalog import get_plan, get_pricing, list_active_plans
from app.services.promocodes import (
    get_promo,
    normalize_code,
    reserve_promo,
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
from app.services.traffic_usage import used_bytes
from app.services.trial import (
    claim_trial,
    create_trial_order,
    get_trial_settings,
    trial_available,
)
from app.services.users import get_or_create_user, get_user_summary
from app.utils.logger import logger
from app.utils.rate_limiter import rate_limiter
from app.web.auth import WebAuthError, parse_user, validate_init_data
from app.web.responses import ApiError, error_response, fail, forbidden, not_found, ok, unauthorized

routes = web.RouteTableDef()

# A receipt is a phone photo; anything bigger is not one.
MAX_RECEIPT_BYTES = 8 * 1024 * 1024

# Order status while a Telegram Stars invoice is outstanding.
STATUS_AWAITING_STARS = "awaiting_stars"


# --- helpers ---------------------------------------------------------------


def _init_data(request: web.Request) -> str:
    header = request.headers.get("X-Telegram-Init-Data", "")
    if header:
        return header
    auth = request.headers.get("Authorization", "")
    if auth.startswith("tma "):
        return auth[4:].strip()
    return ""


def _telegram_user(request: web.Request) -> dict[str, Any]:
    user = request.get("telegram_user")
    if not user:
        raise ApiError("دسترسی نامعتبر است.", status=401, code="unauthorized")
    return user


async def _bot_username(request: web.Request) -> str:
    """The bot's username, cached so we do not call Telegram on every call."""
    cached = request.app.get("bot_username")
    if cached:
        return cached
    me = await request.app["bot"].get_me()
    username = me.username or ""
    request.app["bot_username"] = username
    return username


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
    used = 0
    try:
        panel = await get_panel_settings(session)
        if panel.panel_url:
            async with xui_client_for_panel(panel) as client:
                detail = await client.get_client(str(user_id))
            if detail:
                used = used_bytes(detail)
    except Exception as exc:
        logger.warning("Could not read panel traffic for user %s: %s", user_id, exc)

    return {
        "order_id": account.order_id,
        "expires_at": account.expires_at.strftime("%Y-%m-%d"),
        "days_left": max((account.expires_at - datetime.utcnow()).days, 0),
        "traffic_gb": account.traffic_limit_gb,
        "used_gb": round(used / 1024**3, 2),
        "subscription_url": account.subscription_path,
    }


async def _quote(session, user, body: dict[str, Any]) -> dict[str, Any]:
    """
    Price a plan (optionally with a promo code).

    The result carries the *base* product (days/traffic before a promo bonus)
    and the *final* product separately. The order endpoint re-runs this exact
    function, so the customer is always charged for what they were shown.
    """
    plan_id = (body.get("plan_id") or "").strip()
    promo_code = normalize_code(body.get("promo_code") or "")

    if plan_id:
        plan = await get_plan(session, plan_id, active_only=True)
        if not plan:
            return {"error": "این پلن در دسترس نیست."}
        days = int(plan["days"])
        traffic = int(plan["traffic"])
        devices = int(plan.get("devices") or 1)
        name = plan["name"]
        list_price = int(plan["price"])
        is_custom = False
    else:
        try:
            days = int(body.get("days"))
            traffic = int(body.get("traffic"))
        except (TypeError, ValueError):
            return {"error": "مدت و حجم را وارد کنید."}
        if not 1 <= days <= 365 or not 1 <= traffic <= 500:
            return {
                "error": "مدت باید بین ۱ تا ۳۶۵ روز و حجم بین ۱ تا ۵۰۰ گیگابایت باشد."
            }
        pricing = await get_pricing(session)
        devices = 1
        name = "پلن سفارشی"
        list_price = days * pricing["per_day"] + traffic * pricing["per_gb"]
        is_custom = True

    promo = None
    promo_error = ""
    if promo_code:
        promo, promo_error = await validate_promo(
            session, promo_code, user_id=user.id, price=list_price
        )

    bonus_days = promo.bonus_days if promo else 0
    bonus_gb = promo.bonus_gb if promo else 0
    total_days = days + bonus_days
    total_traffic = traffic + bonus_gb

    if is_custom:
        # A custom plan is priced per day and per gigabyte, so the bonus is
        # paid for as well — otherwise a one-day order with a "30 extra days"
        # code would cost a single day.
        pricing = await get_pricing(session)
        base_price = (
            total_days * pricing["per_day"] + total_traffic * pricing["per_gb"]
        )
    else:
        base_price = list_price

    # The percentage discount always applies to the final price.
    discount = 0
    if promo and promo.kind == "percent":
        discount = base_price * int(promo.value) // 100

    referral_amount = 0
    referral_applied = False
    if await is_discount_eligible(session, user):
        referral_payable, _original, referral_applied = apply_purchase_discount(
            base_price
        )
        referral_amount = base_price - referral_payable

    # A promo code and the referral discount do not stack: the customer gets
    # whichever of the two is bigger.
    if discount >= referral_amount:
        referral_amount = 0
        referral_applied = False
    else:
        discount = 0

    payable = max(base_price - discount - referral_amount, 0)

    return {
        "name": name,
        "plan_id": plan_id or None,
        "days": days,
        "traffic": traffic,
        "total_days": total_days,
        "total_traffic": total_traffic,
        "devices": devices,
        "base_price": base_price,
        "promo_code": promo.code if promo else None,
        "promo_discount": discount,
        "promo_bonus_days": bonus_days,
        "promo_bonus_gb": bonus_gb,
        "promo_error": promo_error,
        "referral_discount": referral_applied,
        "referral_discount_amount": referral_amount,
        "total": payable,
    }


# --- middleware ------------------------------------------------------------


@web.middleware
async def auth_middleware(request: web.Request, handler):
    """Validate the launch data once per request."""
    if not request.path.startswith("/api/"):
        return await handler(request)

    try:
        request["telegram_user"] = parse_user(
            validate_init_data(_init_data(request), settings.BOT_TOKEN)
        )
    except WebAuthError as exc:
        logger.warning("Rejected Mini App request: %s", exc)
        return unauthorized()

    if not request["telegram_user"]:
        return unauthorized()

    # A blocked customer may read the shop, but not buy or change anything.
    blocked = await _is_blocked(request["telegram_user"]["id"])
    request["is_blocked"] = blocked
    if blocked and request.method not in ("GET", "HEAD", "OPTIONS"):
        if not request.path.startswith("/api/admin/"):
            return forbidden("دسترسی شما مسدود شده است.")

    try:
        return await handler(request)
    except ApiError as exc:
        return exc.response()
    except web.HTTPException as exc:
        return error_response(exc)


async def _is_blocked(user_id: int) -> bool:
    async with AsyncSessionLocal() as session:
        summary = await get_user_summary(session, user_id)
        return bool(summary.user.is_blocked) if summary else False


def _rate_limit(request: web.Request, action: str, seconds: int) -> Optional[web.Response]:
    """
    Slow down a customer that repeats the same action.

    Returns an error response when the customer has to wait, else None.
    """
    user = request.get("telegram_user") or {}
    user_id = int(user.get("id") or 0)
    allowed, remaining = rate_limiter.check_limit(user_id, action, seconds)
    if allowed:
        return None
    return fail(
        f"لطفاً {remaining} ثانیه دیگر تلاش کنید.",
        status=429,
        code="rate_limited",
    )


# --- profile ---------------------------------------------------------------


@routes.get("/api/me")
async def api_me(request: web.Request) -> web.Response:
    tg_user = _telegram_user(request)
    async with AsyncSessionLocal() as session:
        user, _ = await get_or_create_user(
            session,
            _as_telegram_user(tg_user),
        )
        await session.commit()
        balance = await _balance(session, user.id)
        code = await ensure_referral_code(session, user)
        await session.commit()
        discount_ok = await is_discount_eligible(session, user)
        shop = await get_bot_settings(session)
        can_trial, trial_reason = await trial_available(session, user.id)
        trial = await get_trial_settings(session)
        account = await _primary_account(session, user.id)

    username = await _bot_username(request)
    return ok(
        {
            "user": {
                "id": tg_user["id"],
                "username": tg_user["username"],
                "first_name": tg_user["first_name"],
                "last_name": tg_user["last_name"],
            },
            "is_admin": is_admin(tg_user["id"]),
            "is_blocked": bool(request.get("is_blocked")),
            "bot_username": username,
            "balance": balance,
            "referral_code": code,
            "referral_link": referral_link(username, code),
            "referral_discount_available": discount_ok,
            "maintenance": bool(shop.maintenance_mode),
            "trial": {
                "available": can_trial,
                "reason": trial_reason,
                "days": trial.days,
                "traffic": trial.gb,
            },
            "account": account,
        }
    )


def _as_telegram_user(tg_user: dict[str, Any]):
    """A Telegram user object the services can read."""

    class _Telegram:
        id = tg_user["id"]
        username = tg_user["username"]
        first_name = tg_user["first_name"]
        last_name = tg_user["last_name"]

    return _Telegram()


async def _balance(session, user_id: int) -> int:
    from app.services.wallet import get_balance

    return await get_balance(session, user_id)


# --- catalogue -------------------------------------------------------------


@routes.get("/api/plans")
async def api_plans(request: web.Request) -> web.Response:
    _telegram_user(request)
    async with AsyncSessionLocal() as session:
        plans = await list_active_plans(session)
        pricing = await get_pricing(session)
    return ok({"plans": plans, "custom_pricing": pricing})


@routes.post("/api/quote")
async def api_quote(request: web.Request) -> web.Response:
    """Price a plan before ordering."""
    tg_user = _telegram_user(request)
    limited = _rate_limit(request, "quote", 3)
    if limited is not None:
        return limited
    body = await _json_body(request)
    async with AsyncSessionLocal() as session:
        user, _ = await get_or_create_user(session, _as_telegram_user(tg_user))
        quote = await _quote(session, user, body)
    if quote.get("error"):
        return fail(quote["error"], code="invalid_quote")
    return ok(quote)


# --- orders ----------------------------------------------------------------


@routes.post("/api/orders")
async def api_create_order(request: web.Request) -> web.Response:
    """Create an order and return how much has to be paid."""
    tg_user = _telegram_user(request)
    limited = _rate_limit(request, "order", 10)
    if limited is not None:
        return limited
    body = await _json_body(request)

    async with AsyncSessionLocal() as session:
        shop = await get_bot_settings(session)
        if shop.maintenance_mode:
            return fail("فروش موقتاً متوقف است.", status=503, code="maintenance")
        if request.get("is_blocked"):
            return forbidden("دسترسی شما مسدود شده است.")

        user, _ = await get_or_create_user(session, _as_telegram_user(tg_user))
        quote = await _quote(session, user, body)
        if quote.get("error"):
            return fail(quote["error"], code="invalid_quote")

        order = Order(
            user_id=user.id,
            days=quote["total_days"],
            traffic_gb=quote["total_traffic"],
            price=quote["total"],
            original_price=quote["base_price"],
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
            if promo and not await reserve_promo(session, promo):
                # The code filled up between the quote and the order.
                await session.rollback()
                return fail(
                    "ظرفیت این کد پرومو تکمیل شده است.",
                    status=409,
                    code="promo_full",
                )
        await session.commit()
        await session.refresh(order)
        card_number, card_holder = await get_card_details(session)

    return ok(
        {
            "order": _order_dict(order),
            "card_number": card_number,
            "card_holder": card_holder,
        }
    )


@routes.get("/api/orders")
async def api_orders(request: web.Request) -> web.Response:
    tg_user = _telegram_user(request)
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(Order)
            .where(Order.user_id == tg_user["id"])
            .order_by(Order.created_at.desc())
            .limit(30)
        )
        orders = [_order_dict(o) for o in result.scalars().all()]
    return ok({"orders": orders})


@routes.post("/api/orders/{order_id}/pay/wallet")
async def api_pay_wallet(request: web.Request) -> web.Response:
    """Pay for an order from the wallet balance."""
    tg_user = _telegram_user(request)
    limited = _rate_limit(request, "pay", 5)
    if limited is not None:
        return limited
    order_id = int(request.match_info["order_id"])
    bot = request.app["bot"]

    from app.services.wallet import debit_balance, get_balance

    async with AsyncSessionLocal() as session:
        order = (
            await session.execute(select(Order).where(Order.id == order_id))
        ).scalar_one_or_none()
        if not order or order.user_id != tg_user["id"]:
            return not_found("سفارش پیدا نشد.")
        if order.status != "pending":
            return fail("این سفارش قابل پرداخت نیست.", code="not_payable")

        # Claim the order first: only one request can move it out of "pending",
        # so a double click can never debit the wallet twice.
        claimed = await session.execute(
            update(Order)
            .where(Order.id == order_id, Order.status == "pending")
            .values(status="paid")
            .execution_options(synchronize_session=False)
        )
        if claimed.rowcount != 1:
            await session.rollback()
            return fail(
                "این سفارش قبلاً پرداخت شده است.",
                status=409,
                code="already_paid",
            )

        balance = await get_balance(session, tg_user["id"])
        if balance < order.price:
            return fail("موجودی کیف پول کافی نیست.", code="insufficient_balance")

        if order.price > 0:
            new_balance = await debit_balance(session, tg_user["id"], order.price)
        else:
            new_balance = balance

        order.wallet_debit = order.price
        order.status = "paid"
        payment = Payment(
            order_id=order.id,
            user_id=tg_user["id"],
            receipt_file_id="",
            status="pending",
            method="wallet",
        )
        session.add(payment)
        await session.commit()
        await session.refresh(payment)

    delivery = await deliver_paid_order(bot, order.id, payment.id)
    return ok({"balance": new_balance, "order": _order_dict(order), "delivery": delivery.kind})


@routes.post("/api/orders/{order_id}/pay/stars")
async def api_pay_stars(request: web.Request) -> web.Response:
    """Send a Telegram Stars invoice for an order."""
    tg_user = _telegram_user(request)
    limited = _rate_limit(request, "pay", 5)
    if limited is not None:
        return limited
    order_id = int(request.match_info["order_id"])
    bot = request.app["bot"]

    async with AsyncSessionLocal() as session:
        order = (
            await session.execute(select(Order).where(Order.id == order_id))
        ).scalar_one_or_none()
        if not order or order.user_id != tg_user["id"]:
            return not_found("سفارش پیدا نشد.")
        if order.status != "pending":
            return fail("این سفارش قابل پرداخت نیست.", code="not_payable")
        stars, rate = await build_stars_invoice(session, order)
        plan = await get_plan(session, order.plan_id) if order.plan_id else None

        if stars <= 0:
            return fail("این سفارش مبلغی ندارد.", code="no_amount")

        # Reserve the order: only one Stars invoice may be outstanding, so a
        # second click cannot charge the customer twice.
        claimed = await session.execute(
            update(Order)
            .where(Order.id == order_id, Order.status == "pending")
            .values(status=STATUS_AWAITING_STARS)
            .execution_options(synchronize_session=False)
        )
        if claimed.rowcount != 1:
            await session.rollback()
            return fail(
                "برای این سفارش قبلاً فاکتور ارسال شده است.",
                status=409,
                code="invoice_sent",
            )
        order.status = STATUS_AWAITING_STARS
        await session.commit()

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
        # Free the order again so the customer can pick another payment method.
        async with AsyncSessionLocal() as session:
            await session.execute(
                update(Order)
                .where(Order.id == order_id, Order.status == STATUS_AWAITING_STARS)
                .values(status="pending")
                .execution_options(synchronize_session=False)
            )
            await session.commit()
        return fail("ارسال فاکتور ناموفق بود.", status=500, code="invoice_failed")

    return ok({"stars": stars, "toman": stars * rate})


@routes.post("/api/orders/{order_id}/pay/card")
async def api_pay_card(request: web.Request) -> web.Response:
    """Return the bank card details for a card transfer."""
    tg_user = _telegram_user(request)
    order_id = int(request.match_info["order_id"])

    async with AsyncSessionLocal() as session:
        order = (
            await session.execute(select(Order).where(Order.id == order_id))
        ).scalar_one_or_none()
        if not order or order.user_id != tg_user["id"]:
            return not_found("سفارش پیدا نشد.")
        if order.status != "pending":
            return fail("این سفارش قابل پرداخت نیست.", code="not_payable")
        card_number, card_holder = await get_card_details(session)
        if not card_number or not card_holder:
            return fail(
                "اطلاعات کارت بانکی هنوز تنظیم نشده است.",
                status=503,
                code="no_card",
            )

    return ok(
        {
            "card_number": card_number,
            "card_holder": card_holder,
            "amount": order.price,
        }
    )


@routes.post("/api/orders/{order_id}/receipt")
async def api_upload_receipt(request: web.Request) -> web.Response:
    """Upload the payment receipt photo for a card order."""
    tg_user = _telegram_user(request)
    limited = _rate_limit(request, "receipt", 30)
    if limited is not None:
        return limited
    order_id = int(request.match_info["order_id"])
    bot = request.app["bot"]

    reader = await request.multipart()
    part = await reader.next()
    if part is None:
        return fail("فایلی ارسال نشد.", code="no_file")

    # The part is read in chunks so an oversized upload never lands in memory.
    content = bytearray()
    while True:
        chunk = await part.read_chunk(64 * 1024)
        if not chunk:
            break
        content.extend(chunk)
        if len(content) > MAX_RECEIPT_BYTES:
            return fail("حجم رسید بیش از حد مجاز است.", code="file_too_large")
    if not content:
        return fail("فایلی ارسال نشد.", code="no_file")

    async with AsyncSessionLocal() as session:
        order = (
            await session.execute(select(Order).where(Order.id == order_id))
        ).scalar_one_or_none()
        if not order or order.user_id != tg_user["id"]:
            return not_found("سفارش پیدا نشد.")
        if order.status not in ("pending", "paid"):
            return fail("برای این سفارش نمی‌توان رسید فرستاد.", code="not_payable")

        existing = (
            await session.execute(
                select(Payment).where(
                    Payment.order_id == order.id,
                    Payment.status.in_(("pending", "approved")),
                )
            )
        ).scalar_one_or_none()
        if existing:
            return fail("رسید قبلی شما هنوز در حال بررسی است.", code="receipt_exists")

        from app.services.receipts import upload_receipt

        try:
            file_id = await upload_receipt(
                bot, tg_user["id"], bytes(content), part.filename or "receipt.jpg"
            )
        except Exception as exc:
            logger.error("Receipt upload failed: %s", exc)
            return fail("ارسال رسید ناموفق بود.", status=500, code="upload_failed")

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

    return ok({"order_id": order_id})


# --- wallet ----------------------------------------------------------------


@routes.get("/api/wallet")
async def api_wallet(request: web.Request) -> web.Response:
    tg_user = _telegram_user(request)
    async with AsyncSessionLocal() as session:
        balance = await _balance(session, tg_user["id"])
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
    return ok(
        {
            "balance": balance,
            "card_number": card_number,
            "card_holder": card_holder,
            "topups": topups,
        }
    )


@routes.post("/api/wallet/topup")
async def api_wallet_topup(request: web.Request) -> web.Response:
    """Ask the admins to top the wallet up."""
    tg_user = _telegram_user(request)
    limited = _rate_limit(request, "topup", 60)
    if limited is not None:
        return limited
    body = await _json_body(request)
    try:
        amount = int(body.get("amount"))
    except (TypeError, ValueError):
        return fail("مبلغ نامعتبر است.", code="invalid_amount")
    if amount < 1:
        return fail("مبلغ باید بزرگ‌تر از صفر باشد.", code="invalid_amount")

    async with AsyncSessionLocal() as session:
        await get_or_create_user(session, _as_telegram_user(tg_user))
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

    return ok({"topup_id": topup.id})


# --- referral, usage, trial ------------------------------------------------


@routes.get("/api/referral")
async def api_referral(request: web.Request) -> web.Response:
    from sqlalchemy import func

    tg_user = _telegram_user(request)
    async with AsyncSessionLocal() as session:
        user, _ = await get_or_create_user(session, _as_telegram_user(tg_user))
        code = await ensure_referral_code(session, user)
        await session.commit()
        discount_ok = await is_discount_eligible(session, user)
        used = (
            await session.execute(
                select(Order.id).where(
                    Order.user_id == user.id,
                    Order.referral_discount_applied.is_(True),
                    Order.status.in_(("pending", "paid", "completed")),
                )
            )
        ).scalar_one_or_none() is not None
        referrals = (
            await session.execute(
                select(func.count(User.id)).where(User.referred_by_user_id == user.id)
            )
        ).scalar_one()
        earned = int(user.referral_cashback_earned or 0)

    username = await _bot_username(request)
    return ok(
        {
            "code": code,
            "link": referral_link(username, code),
            "discount_available": discount_ok and not used,
            "discount_used": used,
            "referrals": int(referrals or 0),
            "referral_balance": earned,
        }
    )


@routes.get("/api/usage")
async def api_usage(request: web.Request) -> web.Response:
    tg_user = _telegram_user(request)
    async with AsyncSessionLocal() as session:
        account = await _primary_account(session, tg_user["id"])
    return ok({"account": account})


@routes.get("/api/qr.png")
async def api_qr(request: web.Request) -> web.Response:
    """
    The customer's own subscription link as a QR code.

    The link always comes from the database — it is never taken from the
    request, so a customer cannot render a QR for an arbitrary address.
    """
    tg_user = _telegram_user(request)
    async with AsyncSessionLocal() as session:
        account = await _primary_account(session, tg_user["id"])
    url = (account or {}).get("subscription_url") or ""
    if not url:
        return not_found("لینک اشتراکی برای شما پیدا نشد.")
    return web.Response(body=subscription_qr_png(url), content_type="image/png")


@routes.post("/api/trial")
async def api_trial(request: web.Request) -> web.Response:
    """Claim the free trial."""
    tg_user = _telegram_user(request)
    limited = _rate_limit(request, "trial", 60)
    if limited is not None:
        return limited
    bot = request.app["bot"]

    async with AsyncSessionLocal() as session:
        can_take, reason = await trial_available(session, tg_user["id"])
        if not can_take:
            return fail(reason, code="trial_unavailable")

        user, _ = await get_or_create_user(session, _as_telegram_user(tg_user))
        trial_settings = await get_trial_settings(session)

        # Claim the trial atomically: two parallel clicks must not create two
        # free subscriptions.
        if not await claim_trial(session, user):
            await session.rollback()
            return fail(
                "شما قبلاً از پلن تست رایگان استفاده کرده‌اید.",
                code="trial_unavailable",
            )

        order = await create_trial_order(session, user, trial_settings)
        payment = Payment(
            order_id=order.id,
            user_id=user.id,
            receipt_file_id="",
            status="pending",
            method="trial",
        )
        session.add(payment)
        await session.commit()
        await session.refresh(payment)
        await session.refresh(order)

    delivery = await deliver_paid_order(bot, order.id, payment.id)
    return ok({"order": _order_dict(order), "delivery": delivery.kind})


# --- body helper -----------------------------------------------------------


async def _json_body(request: web.Request) -> dict[str, Any]:
    """The JSON body, or an empty dict when there is none."""
    if not request.can_read_body:
        return {}
    try:
        body = await request.json()
    except ValueError:
        return {}
    return body if isinstance(body, dict) else {}
