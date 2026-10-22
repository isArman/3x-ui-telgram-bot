"""Reply-keyboard labels shared across handlers."""

BACK_BUTTON = "بازگشت"
CANCEL_BUTTON = "لغو"

BTN_BUY_PLAN = "خرید پلن"
BTN_CUSTOM_PLAN = "پلن سفارشی"
BTN_MY_ORDERS = "سفارش‌های من"
BTN_MY_ACCOUNTS = "اکانت‌های من"
BTN_WALLET = "کیف پول من"
BTN_REFERRAL = "دعوت دوستان"
BTN_TOP_UP = "شارژ کیف پول"
BTN_ADMIN_PANEL = "پنل ادمین"
BTN_TRIAL = "تست رایگان"
BTN_PROMO_CODE = "کد پرومو"

# Telegram caches reply keyboards on the client. After we dropped emojis from
# button labels, users keep sending the old texts until they get a fresh
# keyboard (/start). Accept both so the bot does not look "dead".
_LEGACY_LABELS: dict[str, tuple[str, ...]] = {
    BTN_BUY_PLAN: ("📦 خرید پلن",),
    BTN_CUSTOM_PLAN: ("🎨 پلن سفارشی",),
    BTN_MY_ORDERS: ("📋 سفارش‌های من", "📋 سفارشهای من"),
    BTN_MY_ACCOUNTS: ("💳 اکانت‌های من", "💳 اکانتهای من"),
    BTN_WALLET: ("💰 کیف پول من",),
    BTN_REFERRAL: ("🎁 دعوت دوستان",),
    BTN_TOP_UP: ("💳 شارژ کیف پول",),
    BTN_ADMIN_PANEL: ("⚙️ پنل ادمین",),
}


def button_texts(canonical: str) -> frozenset[str]:
    """Canonical label plus any legacy variants still on cached keyboards."""
    return frozenset((canonical, *_LEGACY_LABELS.get(canonical, ())))


_MAIN_CANONICAL = (
    BTN_BUY_PLAN,
    BTN_CUSTOM_PLAN,
    BTN_MY_ORDERS,
    BTN_MY_ACCOUNTS,
    BTN_WALLET,
    BTN_REFERRAL,
    BTN_ADMIN_PANEL,
)

# Main-menu actions only. Do NOT include CANCEL/BACK here: FSM "menu interrupt"
# handlers match this set first, and would swallow لغو before real cancel
# handlers (e.g. admin waiting_for_subscription showed "use cancel" instead of
# cancelling).
MAIN_MENU_BUTTONS = frozenset().union(*(button_texts(c) for c in _MAIN_CANONICAL))

FLOW_NAV_BUTTONS = frozenset({BACK_BUTTON, CANCEL_BUTTON})

PANEL_SETUP_CANCEL_TEXTS = frozenset({CANCEL_BUTTON, BACK_BUTTON, "/cancel", "لغو"})


def canonicalize_menu_text(text: str | None) -> str | None:
    """Map a (possibly legacy) menu button text to its canonical label."""
    if not text:
        return None
    for canonical in _MAIN_CANONICAL:
        if text in button_texts(canonical):
            return canonical
    return None


ORDER_STATUS_LABELS = {
    "pending": "در انتظار پرداخت",
    "paid": "در انتظار تایید",
    "rejected": "رد شده",
    "completed": "تکمیل شد",
}

# Telegram rejects any callback_data longer than 64 bytes, and it rejects the
# whole keyboard — not just the offending button. Plan ids are admin supplied,
# so every builder has to check the final payload before rendering it.
CALLBACK_DATA_MAX_BYTES = 64


def callback_data_fits(data: str) -> bool:
    """True when a callback payload stays within Telegram's 64-byte limit."""
    return len((data or "").encode("utf-8")) <= CALLBACK_DATA_MAX_BYTES

CONFIGS_MENU_TEXT = (
    "مدیریت کانفیگ‌های پلن\n\n"
    "کانفیگ‌ها (لینک subscription یا vless://) را به هر پلن اضافه کنید.\n"
    "بعد از تایید پرداخت، یک کانفیگ آزاد به کاربر ارسال می‌شود."
)

ADMIN_MENU_TEXT = "پنل ادمین\n\nیک گزینه را انتخاب کنید:"
