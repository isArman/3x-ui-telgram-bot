"""Texts sent through Telegram.

The Mini App carries its own interface strings; this file only holds the
messages the bot itself delivers (payment results, admin notices).
"""

TEXTS = {
    "start": (
        "سلام! 👋\n\n"
        "برای خرید اشتراک، دکمه «فروشگاه» را بزنید."
    ),
    "payment_approved": (
        "پرداخت شما تایید شد. اکانت شما آماده است. لینک اشتراک:\n\n"
        "{subscription_url}\n\n"
        "این لینک را در اپلیکیشن V2Ray یا مشابه آن وارد کنید."
    ),
    "renewal_approved": (
        "تمدید شما تایید شد.\n\n"
        "{days} روز به مدت اشتراک اضافه شد\n"
        "{traffic} گیگابایت به حجم اضافه شد\n"
        "تاریخ انقضای جدید: {expires_at}\n\n"
        "لینک اشتراک:\n{subscription_url}"
    ),
    "order_awaiting_admin": (
        "پرداخت شما ثبت شد. اکانت شما به‌زودی توسط ادمین ارسال می‌شود."
    ),
}


def get_text(key: str, **kwargs) -> str:
    """Get a text with optional formatting."""
    text = TEXTS.get(key, "")
    if kwargs:
        return text.format(**kwargs)
    return text
