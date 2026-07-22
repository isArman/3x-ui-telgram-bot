from aiogram import F, Router
from aiogram.filters import StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from app.bot.auth import deny_non_admin_callback, is_admin
from app.bot.constants import MAIN_MENU_BUTTONS, PANEL_SETUP_CANCEL_TEXTS
from app.bot.keyboards.admin import inbound_select_keyboard, panel_menu_keyboard
from app.bot.menu_dispatch import dispatch_main_menu
from app.bot.states import AdminStates
from app.database.session import AsyncSessionLocal
from app.services.panel_settings import (
    PROVISIONING_AUTO,
    PROVISIONING_MANUAL,
    get_panel_password,
    get_panel_settings,
    get_selected_inbound_ids,
    set_panel_password,
    toggle_inbound_id,
    xui_client_for_panel,
)
from app.utils.logger import logger
from app.utils.validation import is_valid_subscription_base_url
from app.xui.client import XUIClient, XUIError, normalize_panel_url

router = Router()


async def _try_delete_message(message: Message) -> None:
    try:
        await message.delete()
    except Exception:
        pass


async def _panel_status_text(session) -> str:
    ps = await get_panel_settings(session)
    mode = "خودکار (3x-ui)" if ps.provisioning_mode == PROVISIONING_AUTO else "انبار دستی"
    verified = "✅ متصل" if ps.is_verified else "❌ تنظیم نشده"
    inbounds = get_selected_inbound_ids(ps)
    lines = [
        "🔗 تنظیمات پنل 3x-ui\n",
        f"وضعیت اتصال: {verified}",
        f"حالت ارسال کانفیگ: {mode}",
    ]
    if ps.panel_url:
        lines.append(f"URL: {ps.panel_url}")
    if ps.subscription_base_url:
        lines.append(f"Subscription: {ps.subscription_base_url}")
    if inbounds:
        lines.append(f"Inboundهای انتخاب‌شده: {', '.join(map(str, inbounds))}")
    else:
        lines.append("Inboundهای انتخاب‌شده: (هیچ)")
    return "\n".join(lines)


@router.callback_query(F.data == "admin:panel")
async def panel_menu(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        await deny_non_admin_callback(callback)
        return
    await state.clear()
    async with AsyncSessionLocal() as session:
        ps = await get_panel_settings(session)
        text = await _panel_status_text(session)
        await callback.message.edit_text(
            text,
            reply_markup=panel_menu_keyboard(ps.provisioning_mode, ps.is_verified),
        )
    await callback.answer()


@router.callback_query(F.data == "admin:panel:mode")
async def panel_toggle_mode(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await deny_non_admin_callback(callback)
        return

    async with AsyncSessionLocal() as session:
        ps = await get_panel_settings(session)
        if ps.provisioning_mode == PROVISIONING_AUTO:
            ps.provisioning_mode = PROVISIONING_MANUAL
        else:
            if not ps.is_verified:
                await callback.answer(
                    "ابتدا اتصال پنل را تنظیم و تایید کنید.",
                    show_alert=True,
                )
                return
            if not get_selected_inbound_ids(ps):
                await callback.answer(
                    "حداقل یک Inbound انتخاب کنید.",
                    show_alert=True,
                )
                return
            if not ps.subscription_base_url:
                await callback.answer(
                    "آدرس Subscription تنظیم نشده است.",
                    show_alert=True,
                )
                return
            ps.provisioning_mode = PROVISIONING_AUTO
        await session.commit()
        text = await _panel_status_text(session)
        await callback.message.edit_text(
            text,
            reply_markup=panel_menu_keyboard(ps.provisioning_mode, ps.is_verified),
        )
    await callback.answer("حالت تغییر کرد")


@router.callback_query(F.data == "admin:panel:setup")
async def panel_setup_start(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        await deny_non_admin_callback(callback)
        return
    await state.set_state(AdminStates.waiting_for_panel_url)
    await callback.message.answer(
        "آدرس کامل پنل 3x-ui را بفرستید.\n\n"
        "مثال:\n"
        "`https://example.com:2053/YJBJbvcdMmIAnCYoAN`\n\n"
        "برای لغو: `❌ لغو`",
        parse_mode="Markdown",
    )
    await callback.answer()


_PANEL_SETUP_STATES = (
    AdminStates.waiting_for_panel_url,
    AdminStates.waiting_for_panel_username,
    AdminStates.waiting_for_panel_password,
    AdminStates.waiting_for_subscription_base_url,
)


@router.message(
    StateFilter(*_PANEL_SETUP_STATES),
    F.text.in_(MAIN_MENU_BUTTONS),
)
async def panel_setup_menu_interrupt(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return
    await dispatch_main_menu(message, state)


@router.message(AdminStates.waiting_for_panel_url)
async def panel_receive_url(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return
    if not message.text:
        await message.answer("لطفاً URL را به صورت متن ارسال کنید.")
        return
    if message.text.strip() in PANEL_SETUP_CANCEL_TEXTS:
        await state.clear()
        await message.answer("تنظیم پنل لغو شد.")
        return
    try:
        url = normalize_panel_url(message.text.strip())
    except ValueError as exc:
        await message.answer(f"❌ {exc}")
        return
    await state.update_data(panel_url=url)
    await state.set_state(AdminStates.waiting_for_panel_username)
    await message.answer("نام کاربری پنل را بفرستید:")


@router.message(AdminStates.waiting_for_panel_username)
async def panel_receive_username(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return
    if not message.text or not message.text.strip():
        await message.answer("نام کاربری نمی‌تواند خالی باشد.")
        return
    await state.update_data(panel_username=message.text.strip())
    await state.set_state(AdminStates.waiting_for_panel_password)
    await message.answer("رمز عبور پنل را بفرستید:")


@router.message(AdminStates.waiting_for_panel_password)
async def panel_receive_password(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return
    if not message.text:
        await message.answer("رمز عبور نمی‌تواند خالی باشد.")
        return

    data = await state.get_data()
    panel_url = data["panel_url"]
    panel_username = data["panel_username"]
    panel_password = message.text.strip()

    await _try_delete_message(message)
    await message.answer("در حال تست اتصال و خواندن تنظیمات Subscription...")

    try:
        async with XUIClient(panel_url, panel_username, panel_password) as client:
            inbounds = await client.list_inbounds()
            enabled_count = sum(1 for ib in inbounds if ib.get("enable"))
            try:
                sub_base = await client.get_subscription_base_url()
            except XUIError as sub_exc:
                async with AsyncSessionLocal() as session:
                    ps = await get_panel_settings(session)
                    ps.panel_url = panel_url
                    ps.panel_username = panel_username
                    set_panel_password(ps, panel_password)
                    ps.is_verified = False
                    await session.commit()
                await state.set_state(AdminStates.waiting_for_subscription_base_url)
                await message.answer(
                    f"✅ اتصال پنل OK ({enabled_count} inbound فعال)\n\n"
                    f"⚠️ {sub_exc}\n\n"
                    "آدرس پایه Subscription را دستی بفرستید:\n"
                    "مثال: `https://example.com:2096/sub/`",
                    parse_mode="Markdown",
                )
                return
            summary = {
                "enabled_inbounds": enabled_count,
                "total_inbounds": len(inbounds),
                "subscription_base_url": sub_base,
            }
    except XUIError as exc:
        await message.answer(f"❌ اتصال ناموفق: {exc}")
        return
    except Exception as exc:
        logger.error("Panel connection test failed: %s", exc)
        await message.answer("❌ خطا در اتصال به پنل.")
        return

    sub_base = summary["subscription_base_url"]
    async with AsyncSessionLocal() as session:
        ps = await get_panel_settings(session)
        ps.panel_url = panel_url
        ps.panel_username = panel_username
        set_panel_password(ps, panel_password)
        ps.subscription_base_url = sub_base
        ps.is_verified = True
        await session.commit()

    await state.clear()
    await message.answer(
        "✅ اتصال موفق و تنظیمات ذخیره شد!\n\n"
        f"Inbound فعال: {summary['enabled_inbounds']} / {summary['total_inbounds']}\n"
        f"Subscription (خوانده‌شده از پنل):\n`{sub_base}`\n\n"
        "از منوی «🔗 پنل 3x-ui» → «📡 بروزرسانی Inboundها» inboundها را انتخاب کنید.\n"
        "سپس حالت ارسال را روی «خودکار» بگذارید.",
        parse_mode="Markdown",
    )


@router.message(AdminStates.waiting_for_subscription_base_url)
async def panel_receive_subscription_base(message: Message, state: FSMContext):
    """Fallback: manual subscription URL if auto-detection failed."""
    if not is_admin(message.from_user.id):
        return
    if not message.text or not message.text.strip():
        await message.answer("آدرس Subscription نمی‌تواند خالی باشد.")
        return

    sub_base = message.text.strip().rstrip("/") + "/"
    if not is_valid_subscription_base_url(sub_base):
        await message.answer("❌ آدرس Subscription نامعتبر است. باید با http:// یا https:// شروع شود.")
        return

    async with AsyncSessionLocal() as session:
        ps = await get_panel_settings(session)
        if not get_panel_password(ps):
            await message.answer("❌ ابتدا اتصال پنل را از نو تنظیم کنید.")
            await state.clear()
            return
        ps.subscription_base_url = sub_base
        ps.is_verified = True
        await session.commit()

    await state.clear()
    await message.answer(
        "✅ آدرس Subscription دستی ذخیره شد.\n\n"
        "از منوی «🔗 پنل 3x-ui» → «📡 بروزرسانی Inboundها» inboundها را انتخاب کنید."
    )


@router.callback_query(F.data == "admin:panel:test")
async def panel_test_connection(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await deny_non_admin_callback(callback)
        return

    async with AsyncSessionLocal() as session:
        ps = await get_panel_settings(session)
        if not ps.panel_url or not ps.panel_username or not get_panel_password(ps):
            await callback.answer("پنل تنظیم نشده.", show_alert=True)
            return
        try:
            async with xui_client_for_panel(ps) as client:
                summary = await client.test_connection()
                sub_base = summary["subscription_base_url"]
                ps.subscription_base_url = sub_base
                await session.commit()
        except XUIError as exc:
            await callback.answer(f"خطا: {exc}", show_alert=True)
            return
        except Exception as exc:
            logger.error("Panel test failed: %s", exc)
            await callback.answer("خطای اتصال", show_alert=True)
            return

    await callback.message.answer(
        f"✅ اتصال OK — {summary['enabled_inbounds']} inbound فعال\n"
        f"Subscription: `{sub_base}`",
        parse_mode="Markdown",
    )
    await callback.answer()


@router.callback_query(F.data == "admin:panel:inbounds")
async def panel_list_inbounds(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await deny_non_admin_callback(callback)
        return

    async with AsyncSessionLocal() as session:
        ps = await get_panel_settings(session)
        if not ps.is_verified:
            await callback.answer("ابتدا اتصال پنل را تنظیم کنید.", show_alert=True)
            return
        try:
            async with xui_client_for_panel(ps) as client:
                inbounds = await client.list_inbounds()
        except XUIError as exc:
            await callback.answer(f"خطا: {exc}", show_alert=True)
            return
        except Exception as exc:
            logger.error("List inbounds failed: %s", exc)
            await callback.answer("خطا در دریافت inboundها", show_alert=True)
            return

        enabled = [ib for ib in inbounds if ib.get("enable")]
        selected = set(get_selected_inbound_ids(ps))
        await callback.message.edit_text(
            "Inboundهایی که برای کاربران استفاده می‌شوند را انتخاب کنید:\n"
            "(روی هر مورد بزنید تا ✅/⬜ شود)",
            reply_markup=inbound_select_keyboard(enabled, selected),
        )
    await callback.answer()


@router.callback_query(F.data.startswith("admin:panel:inbound_toggle:"))
async def panel_toggle_inbound(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await deny_non_admin_callback(callback)
        return

    inbound_id = int(callback.data.split(":")[3])

    async with AsyncSessionLocal() as session:
        ps = await get_panel_settings(session)
        toggle_inbound_id(ps, inbound_id)
        await session.commit()
        try:
            async with xui_client_for_panel(ps) as client:
                inbounds = await client.list_inbounds()
        except Exception as exc:
            logger.error("Refresh inbounds failed: %s", exc)
            await callback.answer("ذخیره شد ولی لیست بروز نشد", show_alert=True)
            return

        enabled = [ib for ib in inbounds if ib.get("enable")]
        selected = set(get_selected_inbound_ids(ps))
        await callback.message.edit_reply_markup(
            reply_markup=inbound_select_keyboard(enabled, selected),
        )
    await callback.answer()


@router.callback_query(F.data == "admin:panel:inbounds_save")
async def panel_inbounds_save(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await deny_non_admin_callback(callback)
        return

    async with AsyncSessionLocal() as session:
        ps = await get_panel_settings(session)
        selected = get_selected_inbound_ids(ps)
        if not selected:
            await callback.answer("حداقل یک inbound انتخاب کنید.", show_alert=True)
            return
        text = await _panel_status_text(session)
        await callback.message.edit_text(
            text + f"\n\n✅ {len(selected)} inbound ذخیره شد.",
            reply_markup=panel_menu_keyboard(ps.provisioning_mode, ps.is_verified),
        )
    await callback.answer("ذخیره شد")
