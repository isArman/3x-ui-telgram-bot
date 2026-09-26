TEXTS = {
    "start": """
سلام. خوش آمدید.

از منوی پایین گزینه مورد نظرتان را انتخاب کنید.
""",

    "plans_list": "پلن‌های آماده:\n\n",

    "plan_details": """
{name}

مدت: {days} روز
حجم: {traffic} گیگابایت
قیمت: {price:,} تومان

{description}
""",

    "custom_plan_start": "چند روز می‌خواهید؟ عددی بین ۱ تا ۳۶۵ بنویسید.",

    "custom_plan_traffic": "چند گیگابایت می‌خواهید؟ عددی بین ۱ تا ۵۰۰ بنویسید.",

    "custom_plan_confirm": """
خلاصه سفارش

مدت: {days} روز
حجم: {traffic} گیگابایت
{price_block}

تایید می‌کنید؟
""",

    "plan_confirm": """
{name}

مدت: {days} روز
حجم: {traffic} گیگابایت
{price_block}
{description}
تایید می‌کنید؟
""",

    "order_created": """
سفارش شما ثبت شد.

شماره سفارش: #{order_id}
{price_lines}

مبلغ را به کارت زیر واریز کنید:

{card_number}
{card_holder}

بعد عکس رسید را همین‌جا بفرستید.
""",

    "order_created_partial": """
بخشی از مبلغ از کیف پول کم می‌شود.

شماره سفارش: #{order_id}
{price_lines}
از کیف پول: {wallet_amount:,} تومان
باقی‌مانده: {difference:,} تومان

فقط مبلغ باقی‌مانده را به کارت زیر واریز کنید:

{card_number}
{card_holder}

بعد عکس رسید را همین‌جا بفرستید.
""",

    "wallet_pay_prompt": """
پرداخت سفارش #{order_id}

{price_lines}
موجودی کیف پول: {balance:,} تومان

می‌خواهید از کیف پول پرداخت کنید؟
""",

    "referral_bound_notice": """

تخفیف معرفی برای شما فعال شد.
روی اولین خرید، ۱۵٪ از مبلغ کم می‌شود.
""",

    "referral_eligible_notice": """

تخفیف معرفی شما فعال است: ۱۵٪ روی اولین خرید.
""",

    "referral_plans_hint": """
تخفیف معرفی فعال است. هنگام تایید سفارش، قیمت با ۱۵٪ کاهش نشان داده می‌شود.

""",

    "referral_invite": """
دعوت دوستان

کد شما: `{code}`

لینک دعوت:
{link}

{status_line}
هر کسی با این لینک وارد شود، روی اولین خریدش ۱۵٪ تخفیف می‌گیرد.
وقتی آن خرید انجام شد، ۲۰٪ مبلغ پلن به کیف پول شما اضافه می‌شود.
تمدید شامل تخفیف و پاداش نیست.
""",

    "referral_status_active": "تخفیف شما: فعال (۱۵٪ روی اولین خرید)",
    "referral_status_used": "تخفیف شما: قبلاً استفاده شده",
    "referral_status_none": "از طریق دعوت وارد نشده‌اید. می‌توانید دیگران را دعوت کنید.",

    "referral_cashback_notify": """
پاداش معرفی به کیف پول شما اضافه شد.

از اولین خرید دعوت‌شده شما، ۲۰٪ واریز شد.
مبلغ: {amount:,} تومان
""",

    "wallet_pay_full": """
پرداخت از کیف پول انجام شد.

کسر شده: {price:,} تومان
موجودی باقی‌مانده: {balance:,} تومان
""",

    "wallet_empty": """
موجودی کیف پول شما صفر است.

می‌توانید کل مبلغ را کارت به کارت پرداخت کنید، یا اول کیف پول را شارژ کنید.
""",

    "wallet_home": """
کیف پول شما

موجودی: {balance:,} تومان

برای افزایش موجودی، «شارژ کیف پول» را بزنید.
""",

    "wallet_topup_ask_amount": """
شارژ کیف پول

مبلغ مورد نظرتان را به تومان بنویسید:
""",

    "wallet_topup_confirm": """
مبلغ شارژ: {amount:,} تومان

تایید می‌کنید؟
""",

    "wallet_topup_created": """
درخواست شارژ ثبت شد.

شماره درخواست: #{topup_id}
مبلغ: {amount:,} تومان

مبلغ را به کارت زیر واریز کنید:

{card_number}
{card_holder}

بعد عکس رسید را همین‌جا بفرستید.
""",

    "wallet_topup_receipt_received": """
رسید شما دریافت شد.

درخواست شارژ در انتظار بررسی ادمین است.
بعد از تایید، مبلغ به کیف پول شما اضافه می‌شود.
""",

    "wallet_topup_approved": """
شارژ کیف پول تایید شد.

مبلغ اضافه‌شده: {amount:,} تومان
موجودی فعلی: {balance:,} تومان
""",

    "wallet_topup_approved_manual": """
شارژ کیف پول تایید شد.

مبلغ درخواستی شما: {requested_amount:,} تومان
مبلغ تاییدشده: {credited_amount:,} تومان
موجودی فعلی: {balance:,} تومان
""",

    "wallet_topup_rejected": """
درخواست شارژ شما رد شد.

دلیل: {reason}

موجودی کیف پول تغییری نکرده است.
""",

    "admin_new_topup": """
درخواست شارژ کیف پول

کاربر: {user_id} (@{username})
درخواست: #{topup_id}
مبلغ درخواستی: {amount:,} تومان
""",

    "admin_topup_ask_manual": """
مبلغ واقعی واریزی را به تومان بنویسید
(درخواست کاربر: {requested_amount:,} تومان):
""",

    "admin_topup_credited": """
مبلغ {credited_amount:,} تومان به کیف پول کاربر اضافه شد.
(درخواستی: {requested_amount:,} تومان)
""",

    "admin_topup_credited_requested": """
مبلغ درخواستی {amount:,} تومان به کیف پول کاربر اضافه شد.
""",

    "wallet_refund_on_reject": """
مبلغ کسرشده به کیف پول شما برگشت.
موجودی فعلی: {balance:,} تومان
""",

    "receipt_received": """
رسید شما دریافت شد.

سفارش در انتظار بررسی ادمین است.
بعد از تایید، اکانت برای شما ارسال می‌شود.
""",

    "payment_approved": """
پرداخت شما تایید شد.

اکانت شما آماده است. لینک اشتراک:

{subscription_url}

این لینک را در اپلیکیشن V2Ray یا مشابه آن وارد کنید.
""",

    "renewal_approved": """
اکانت شما تمدید شد.

{days} روز به مدت اشتراک اضافه شد
{traffic} گیگابایت به حجم اضافه شد
تاریخ انقضای جدید: {expires_at}

لینک اشتراک (همان لینک قبلی):
{subscription_url}
""",

    "renewal_confirm": """
تمدید اکانت سفارش #{order_id}

{name}

+{days} روز به اشتراک
+{traffic} گیگابایت حجم
قیمت: {price:,} تومان

تایید می‌کنید؟
""",

    "payment_rejected": """
پرداخت شما رد شد.

دلیل: {reason}

اگر سوالی دارید با پشتیبانی تماس بگیرید.
""",

    "admin_new_payment": """
پرداخت جدید

کاربر: {user_id} (@{username})
سفارش: #{order_id}
پلن: {days} روز | {traffic} گیگابایت
مبلغ کل: {price:,} تومان{wallet_note}{renewal_note}
""",

    "error_invalid_number": "یک عدد معتبر وارد کنید.",

    "error_out_of_range": "مقدار وارد شده خارج از محدوده مجاز است.",

    "error_general": "خطایی رخ داد. دوباره تلاش کنید.",

    "operation_cancelled": "عملیات لغو شد.",

    "my_orders": "سفارش‌های من",

    "no_orders": "هنوز سفارشی ندارید.",

    "order_status": """
سفارش #{order_id}
{days} روز | {traffic} GB
{price:,} تومان{discount_tag}
وضعیت: {status}
{created_at}
"""
}


def get_text(key: str, **kwargs) -> str:
    """Get text with optional formatting"""
    text = TEXTS.get(key, "")
    if kwargs:
        return text.format(**kwargs)
    return text
