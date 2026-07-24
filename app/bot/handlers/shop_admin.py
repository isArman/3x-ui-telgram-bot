"""Admin UI for payment card and shop plans (DB-backed)."""

from aiogram import F, Router
from aiogram.filters import StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message, ReplyKeyboardRemove

from app.bot.auth import is_admin
from app.bot.constants import MAIN_MENU_BUTTONS
from app.bot.keyboards.admin import (
    admin_cancel_keyboard,
    admin_menu_keyboard,
    card_settings_keyboard,
    plan_admin_detail_keyboard,
    plan_admin_list_keyboard,
    plans_admin_keyboard,
    pricing_admin_keyboard,
)
from app.bot.menu_dispatch import dispatch_main_menu
from app.bot.states import AdminStates
from app.config.texts import get_text
from app.database.session import AsyncSessionLocal
from app.services.bot_settings import (
    get_bot_settings,
    set_card_holder,
    set_card_number,
)
from app.services.plans_catalog import (
    create_plan,
    get_plan_row,
    get_pricing_settings,
    list_all_plans,
    set_plan_active,
    set_pricing,
    update_plan_fields,
)

router = Router()

_SHOP_FSM = StateFilter(
    AdminStates.waiting_for_card_number,
    AdminStates.waiting_for_card_holder,
    AdminStates.waiting_for_plan_id,
    AdminStates.waiting_for_plan_name,
    AdminStates.waiting_for_plan_days,
    AdminStates.waiting_for_plan_traffic,
    AdminStates.waiting_for_plan_price,
    AdminStates.waiting_for_plan_description,
    AdminStates.waiting_for_plan_edit_value,
    AdminStates.waiting_for_pricing_per_day,
    AdminStates.waiting_for_pricing_per_gb,
)


def _card_text(card_number: str | None, card_holder: str | None) -> str:
    return (
        "💳 تنظیمات کارت بانکی\n\n"
        f"شماره کارت: {card_number or '—'}\n"
        f"نام صاحب کارت: {card_holder or '—'}\n\n"
        "این مقادیر برای خرید و شارژ کیف پول به کاربر نشان داده می‌شوند."
    )


@router.message(_SHOP_FSM, F.text.in_(MAIN_MENU_BUTTONS))
async def shop_fsm_menu_interrupt(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return
    await dispatch_main_menu(message, state)


@router.callback_query(F.data == "admin:card")
async def admin_card_menu(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        await callback.answer("دسترسی ندارید!", show_alert=True)
        return
    await state.clear()
    async with AsyncSessionLocal() as session:
        row = await get_bot_settings(session)
    await callback.message.edit_text(
        _card_text(row.card_number, row.card_holder),
        reply_markup=card_settings_keyboard(),
    )
    await callback.answer()


@router.callback_query(F.data == "admin:card:number")
async def admin_card_number_start(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        await callback.answer("دسترسی ندارید!", show_alert=True)
        return
    await state.set_state(AdminStates.waiting_for_card_number)
    await callback.message.answer(
        "شماره کارت جدید را ارسال کنید:",
        reply_markup=admin_cancel_keyboard(),
    )
    await callback.answer()


@router.callback_query(F.data == "admin:card:holder")
async def admin_card_holder_start(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        await callback.answer("دسترسی ندارید!", show_alert=True)
        return
    await state.set_state(AdminStates.waiting_for_card_holder)
    await callback.message.answer(
        "نام صاحب کارت را ارسال کنید:",
        reply_markup=admin_cancel_keyboard(),
    )
    await callback.answer()


@router.message(AdminStates.waiting_for_card_number)
async def admin_card_number_save(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return
    if message.text == "❌ لغو":
        await state.clear()
        await message.answer("لغو شد.", reply_markup=ReplyKeyboardRemove())
        return
    value = (message.text or "").strip()
    if len(value) < 4:
        await message.answer("❌ شماره کارت نامعتبر است.")
        return
    async with AsyncSessionLocal() as session:
        row = await set_card_number(session, value)
    await state.clear()
    await message.answer(
        "✅ شماره کارت ذخیره شد.\n\n" + _card_text(row.card_number, row.card_holder),
        reply_markup=ReplyKeyboardRemove(),
    )
    await message.answer("منوی کارت:", reply_markup=card_settings_keyboard())


@router.message(AdminStates.waiting_for_card_holder)
async def admin_card_holder_save(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return
    if message.text == "❌ لغو":
        await state.clear()
        await message.answer("لغو شد.", reply_markup=ReplyKeyboardRemove())
        return
    value = (message.text or "").strip()
    if len(value) < 2:
        await message.answer("❌ نام نامعتبر است.")
        return
    async with AsyncSessionLocal() as session:
        row = await set_card_holder(session, value)
    await state.clear()
    await message.answer(
        "✅ نام صاحب کارت ذخیره شد.\n\n" + _card_text(row.card_number, row.card_holder),
        reply_markup=ReplyKeyboardRemove(),
    )
    await message.answer("منوی کارت:", reply_markup=card_settings_keyboard())


@router.callback_query(F.data == "admin:plans")
async def admin_plans_menu(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        await callback.answer("دسترسی ندارید!", show_alert=True)
        return
    await state.clear()
    await callback.message.edit_text(
        "📦 مدیریت پلن‌ها\n\nپلن‌های آماده و قیمت پلن سفارشی را از اینجا ویرایش کنید.",
        reply_markup=plans_admin_keyboard(),
    )
    await callback.answer()


@router.callback_query(F.data == "admin:plans:list")
async def admin_plans_list(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("دسترسی ندارید!", show_alert=True)
        return
    async with AsyncSessionLocal() as session:
        plans = await list_all_plans(session)
    if not plans:
        await callback.message.edit_text(
            "هنوز پلنی ثبت نشده. از «افزودن پلن» استفاده کنید.",
            reply_markup=plans_admin_keyboard(),
        )
    else:
        await callback.message.edit_text(
            "📋 پلن‌ها (برای ویرایش یکی را انتخاب کنید):",
            reply_markup=plan_admin_list_keyboard(plans),
        )
    await callback.answer()


@router.callback_query(F.data.startswith("admin:plans:view:"))
async def admin_plan_view(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("دسترسی ندارید!", show_alert=True)
        return
    plan_id = callback.data.split(":")[-1]
    async with AsyncSessionLocal() as session:
        plan = await get_plan_row(session, plan_id)
    if not plan:
        await callback.answer("پلن یافت نشد!", show_alert=True)
        return
    status = "فعال" if plan.is_active else "غیرفعال"
    text = (
        f"📦 {plan.name}\n\n"
        f"🆔 `{plan.id}`\n"
        f"⏱ {plan.days} روز\n"
        f"📊 {plan.traffic_gb} گیگابایت\n"
        f"💰 {plan.price:,} تومان\n"
        f"📝 {plan.description or '—'}\n"
        f"وضعیت: {status}"
    )
    await callback.message.edit_text(
        text,
        reply_markup=plan_admin_detail_keyboard(plan.id, plan.is_active),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("admin:plans:toggle:"))
async def admin_plan_toggle(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("دسترسی ندارید!", show_alert=True)
        return
    plan_id = callback.data.split(":")[-1]
    async with AsyncSessionLocal() as session:
        plan = await get_plan_row(session, plan_id)
        if not plan:
            await callback.answer("پلن یافت نشد!", show_alert=True)
            return
        plan = await set_plan_active(session, plan, not plan.is_active)
    await callback.answer("وضعیت پلن تغییر کرد.")
    # Refresh view
    callback.data = f"admin:plans:view:{plan_id}"
    await admin_plan_view(callback)


@router.callback_query(F.data.startswith("admin:plans:edit:"))
async def admin_plan_edit_start(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        await callback.answer("دسترسی ندارید!", show_alert=True)
        return
    parts = callback.data.split(":")
    # admin:plans:edit:{id}:{field}
    plan_id = parts[3]
    field = parts[4]
    await state.update_data(edit_plan_id=plan_id, edit_plan_field=field)
    await state.set_state(AdminStates.waiting_for_plan_edit_value)
    labels = {
        "name": "نام جدید پلن را بفرستید:",
        "days": "تعداد روز (عدد) را بفرستید:",
        "traffic": "حجم به گیگابایت (عدد) را بفرستید:",
        "price": "قیمت به تومان (عدد) را بفرستید:",
        "description": "توضیحات جدید را بفرستید (یا - برای خالی):",
    }
    await callback.message.answer(
        labels.get(field, "مقدار جدید را بفرستید:"),
        reply_markup=admin_cancel_keyboard(),
    )
    await callback.answer()


@router.message(AdminStates.waiting_for_plan_edit_value)
async def admin_plan_edit_save(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return
    if message.text == "❌ لغو":
        await state.clear()
        await message.answer("لغو شد.", reply_markup=ReplyKeyboardRemove())
        return

    data = await state.get_data()
    plan_id = data.get("edit_plan_id")
    field = data.get("edit_plan_field")
    raw = (message.text or "").strip()

    async with AsyncSessionLocal() as session:
        plan = await get_plan_row(session, plan_id)
        if not plan:
            await message.answer("پلن یافت نشد.", reply_markup=ReplyKeyboardRemove())
            await state.clear()
            return
        try:
            if field == "name":
                if len(raw) < 1:
                    raise ValueError("نام خالی است")
                await update_plan_fields(session, plan, name=raw)
            elif field == "days":
                days = int(raw)
                if not 1 <= days <= 3650:
                    raise ValueError("روز نامعتبر")
                await update_plan_fields(session, plan, days=days)
            elif field == "traffic":
                traffic = int(raw)
                if not 1 <= traffic <= 10000:
                    raise ValueError("حجم نامعتبر")
                await update_plan_fields(session, plan, traffic_gb=traffic)
            elif field == "price":
                price = int(raw.replace(",", "").replace("،", ""))
                if price < 0:
                    raise ValueError("قیمت نامعتبر")
                await update_plan_fields(session, plan, price=price)
            elif field == "description":
                desc = "" if raw in ("-", "—") else raw
                await update_plan_fields(session, plan, description=desc)
            else:
                raise ValueError("فیلد نامعتبر")
        except (TypeError, ValueError):
            await message.answer(get_text("error_invalid_number"))
            return

        plan = await get_plan_row(session, plan_id)

    await state.clear()
    await message.answer("✅ ذخیره شد.", reply_markup=ReplyKeyboardRemove())
    status = "فعال" if plan.is_active else "غیرفعال"
    await message.answer(
        f"📦 {plan.name}\n\n"
        f"🆔 `{plan.id}`\n"
        f"⏱ {plan.days} روز | 📊 {plan.traffic_gb} GB\n"
        f"💰 {plan.price:,} تومان\n"
        f"وضعیت: {status}",
        reply_markup=plan_admin_detail_keyboard(plan.id, plan.is_active),
    )


@router.callback_query(F.data == "admin:plans:add")
async def admin_plan_add_start(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        await callback.answer("دسترسی ندارید!", show_alert=True)
        return
    await state.clear()
    await state.set_state(AdminStates.waiting_for_plan_id)
    await callback.message.answer(
        "شناسه یکتای پلن را وارد کنید (لاتین، مثلاً basic):",
        reply_markup=admin_cancel_keyboard(),
    )
    await callback.answer()


@router.message(AdminStates.waiting_for_plan_id)
async def admin_plan_add_id(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return
    if message.text == "❌ لغو":
        await state.clear()
        await message.answer("لغو شد.", reply_markup=ReplyKeyboardRemove())
        return
    plan_id = (message.text or "").strip().lower().replace(" ", "_")
    if not plan_id or not plan_id.replace("_", "").replace("-", "").isalnum():
        await message.answer("❌ شناسه فقط حروف/عدد/-/_ باشد.")
        return
    async with AsyncSessionLocal() as session:
        if await get_plan_row(session, plan_id):
            await message.answer("❌ این شناسه قبلاً وجود دارد.")
            return
    await state.update_data(new_plan_id=plan_id)
    await state.set_state(AdminStates.waiting_for_plan_name)
    await message.answer("نام نمایشی پلن را بفرستید:")


@router.message(AdminStates.waiting_for_plan_name)
async def admin_plan_add_name(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return
    if message.text == "❌ لغو":
        await state.clear()
        await message.answer("لغو شد.", reply_markup=ReplyKeyboardRemove())
        return
    name = (message.text or "").strip()
    if len(name) < 1:
        await message.answer("❌ نام نامعتبر است.")
        return
    await state.update_data(new_plan_name=name)
    await state.set_state(AdminStates.waiting_for_plan_days)
    await message.answer("تعداد روز را بفرستید (مثلاً 30):")


@router.message(AdminStates.waiting_for_plan_days)
async def admin_plan_add_days(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return
    if message.text == "❌ لغو":
        await state.clear()
        await message.answer("لغو شد.", reply_markup=ReplyKeyboardRemove())
        return
    try:
        days = int(message.text.strip())
        if not 1 <= days <= 3650:
            raise ValueError
    except (TypeError, ValueError):
        await message.answer(get_text("error_invalid_number"))
        return
    await state.update_data(new_plan_days=days)
    await state.set_state(AdminStates.waiting_for_plan_traffic)
    await message.answer("حجم به گیگابایت را بفرستید (مثلاً 40):")


@router.message(AdminStates.waiting_for_plan_traffic)
async def admin_plan_add_traffic(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return
    if message.text == "❌ لغو":
        await state.clear()
        await message.answer("لغو شد.", reply_markup=ReplyKeyboardRemove())
        return
    try:
        traffic = int(message.text.strip())
        if not 1 <= traffic <= 10000:
            raise ValueError
    except (TypeError, ValueError):
        await message.answer(get_text("error_invalid_number"))
        return
    await state.update_data(new_plan_traffic=traffic)
    await state.set_state(AdminStates.waiting_for_plan_price)
    await message.answer("قیمت به تومان را بفرستید:")


@router.message(AdminStates.waiting_for_plan_price)
async def admin_plan_add_price(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return
    if message.text == "❌ لغو":
        await state.clear()
        await message.answer("لغو شد.", reply_markup=ReplyKeyboardRemove())
        return
    try:
        price = int(message.text.replace(",", "").replace("،", "").strip())
        if price < 0:
            raise ValueError
    except (TypeError, ValueError):
        await message.answer(get_text("error_invalid_number"))
        return
    await state.update_data(new_plan_price=price)
    await state.set_state(AdminStates.waiting_for_plan_description)
    await message.answer("توضیحات پلن را بفرستید (یا - برای خالی):")


@router.message(AdminStates.waiting_for_plan_description)
async def admin_plan_add_description(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return
    if message.text == "❌ لغو":
        await state.clear()
        await message.answer("لغو شد.", reply_markup=ReplyKeyboardRemove())
        return
    raw = (message.text or "").strip()
    description = "" if raw in ("-", "—") else raw
    data = await state.get_data()
    async with AsyncSessionLocal() as session:
        plan = await create_plan(
            session,
            plan_id=data["new_plan_id"],
            name=data["new_plan_name"],
            days=data["new_plan_days"],
            traffic_gb=data["new_plan_traffic"],
            price=data["new_plan_price"],
            description=description,
        )
    await state.clear()
    await message.answer(
        f"✅ پلن «{plan.name}» اضافه شد.\n💰 {plan.price:,} تومان",
        reply_markup=ReplyKeyboardRemove(),
    )
    await message.answer(
        "مدیریت پلن‌ها:",
        reply_markup=plans_admin_keyboard(),
    )


@router.callback_query(F.data == "admin:plans:pricing")
async def admin_pricing_menu(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        await callback.answer("دسترسی ندارید!", show_alert=True)
        return
    await state.clear()
    async with AsyncSessionLocal() as session:
        pricing = await get_pricing_settings(session)
    await callback.message.edit_text(
        "💰 قیمت پلن سفارشی\n\n"
        f"هر روز: {pricing.per_day:,} تومان\n"
        f"هر گیگابایت: {pricing.per_gb:,} تومان\n\n"
        "فرمول: (روز × قیمت روز) + (گیگ × قیمت گیگ)",
        reply_markup=pricing_admin_keyboard(),
    )
    await callback.answer()


@router.callback_query(F.data == "admin:plans:pricing:day")
async def admin_pricing_day_start(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        await callback.answer("دسترسی ندارید!", show_alert=True)
        return
    await state.set_state(AdminStates.waiting_for_pricing_per_day)
    await callback.message.answer(
        "قیمت هر روز (تومان) را بفرستید:",
        reply_markup=admin_cancel_keyboard(),
    )
    await callback.answer()


@router.callback_query(F.data == "admin:plans:pricing:gb")
async def admin_pricing_gb_start(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        await callback.answer("دسترسی ندارید!", show_alert=True)
        return
    await state.set_state(AdminStates.waiting_for_pricing_per_gb)
    await callback.message.answer(
        "قیمت هر گیگابایت (تومان) را بفرستید:",
        reply_markup=admin_cancel_keyboard(),
    )
    await callback.answer()


@router.message(AdminStates.waiting_for_pricing_per_day)
async def admin_pricing_day_save(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return
    if message.text == "❌ لغو":
        await state.clear()
        await message.answer("لغو شد.", reply_markup=ReplyKeyboardRemove())
        return
    try:
        value = int(message.text.replace(",", "").replace("،", "").strip())
        if value < 0:
            raise ValueError
    except (TypeError, ValueError):
        await message.answer(get_text("error_invalid_number"))
        return
    async with AsyncSessionLocal() as session:
        pricing = await set_pricing(session, per_day=value)
    await state.clear()
    await message.answer(
        f"✅ قیمت هر روز: {pricing.per_day:,} تومان",
        reply_markup=ReplyKeyboardRemove(),
    )
    await message.answer(
        f"هر روز: {pricing.per_day:,}\nهر گیگ: {pricing.per_gb:,}",
        reply_markup=pricing_admin_keyboard(),
    )


@router.message(AdminStates.waiting_for_pricing_per_gb)
async def admin_pricing_gb_save(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return
    if message.text == "❌ لغو":
        await state.clear()
        await message.answer("لغو شد.", reply_markup=ReplyKeyboardRemove())
        return
    try:
        value = int(message.text.replace(",", "").replace("،", "").strip())
        if value < 0:
            raise ValueError
    except (TypeError, ValueError):
        await message.answer(get_text("error_invalid_number"))
        return
    async with AsyncSessionLocal() as session:
        pricing = await set_pricing(session, per_gb=value)
    await state.clear()
    await message.answer(
        f"✅ قیمت هر گیگ: {pricing.per_gb:,} تومان",
        reply_markup=ReplyKeyboardRemove(),
    )
    await message.answer(
        f"هر روز: {pricing.per_day:,}\nهر گیگ: {pricing.per_gb:,}",
        reply_markup=pricing_admin_keyboard(),
    )
