"""Reply-keyboard labels shared across handlers."""

BACK_BUTTON = "🔙 بازگشت"
CANCEL_BUTTON = "❌ لغو"

BTN_BUY_PLAN = "📦 خرید پلن"
BTN_CUSTOM_PLAN = "🎨 پلن سفارشی"
BTN_MY_ORDERS = "📋 سفارش‌های من"
BTN_MY_ACCOUNTS = "💳 اکانت‌های من"
BTN_WALLET = "💰 کیف پول من"
BTN_TOP_UP = "💳 شارژ کیف پول"
BTN_ADMIN_PANEL = "⚙️ پنل ادمین"

MAIN_MENU_BUTTONS = frozenset(
    {
        BTN_BUY_PLAN,
        BTN_CUSTOM_PLAN,
        BTN_MY_ORDERS,
        BTN_MY_ACCOUNTS,
        BTN_WALLET,
        BTN_ADMIN_PANEL,
        CANCEL_BUTTON,
    }
)

FLOW_NAV_BUTTONS = frozenset({BACK_BUTTON, CANCEL_BUTTON})

PANEL_SETUP_CANCEL_TEXTS = frozenset({CANCEL_BUTTON, "/cancel", "لغو"})

ORDER_STATUS_LABELS = {
    "pending": "در انتظار پرداخت",
    "paid": "در انتظار تایید",
    "rejected": "رد شده",
    "completed": "تکمیل شده",
}

CONFIGS_MENU_TEXT = (
    "🗂 مدیریت کانفیگ‌های پلن\n\n"
    "کانفیگ‌ها (لینک subscription یا vless://) را به هر پلن اضافه کنید.\n"
    "پس از تایید پرداخت، یک کانفیگ آزاد به کاربر ارسال می‌شود."
)

ADMIN_MENU_TEXT = "⚙️ پنل ادمین\n\nیک گزینه را انتخاب کنید:"
