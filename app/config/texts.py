TEXTS = {
    "start": """
👋 سلام! به ربات فروش VPN خوش آمدید.

از منوی زیر یکی از گزینه‌ها را انتخاب کنید:
""",
    
    "plans_list": "📋 لیست پلن‌های آماده:\n\n",
    
    "plan_details": """
📦 {name}

⏱ مدت: {days} روز
📊 حجم: {traffic} گیگابایت
💰 قیمت: {price:,} تومان

{description}
""",
    
    "custom_plan_start": "لطفاً تعداد روز مورد نظر خود را وارد کنید (1-365):",
    
    "custom_plan_traffic": "لطفاً حجم مورد نظر خود را به گیگابایت وارد کنید (1-500):",
    
    "custom_plan_confirm": """
📦 پلن سفارشی شما:

⏱ مدت: {days} روز
📊 حجم: {traffic} گیگابایت
💰 قیمت: {price:,} تومان

آیا تایید می‌کنید؟
""",
    
    "order_created": """
✅ سفارش شما ثبت شد.

🔢 شماره سفارش: #{order_id}
💰 مبلغ قابل پرداخت: {price:,} تومان

لطفاً مبلغ را به شماره کارت زیر واریز کنید:

💳 {card_number}
👤 {card_holder}

سپس تصویر رسید پرداخت را ارسال کنید.
""",
    
    "receipt_received": """
📸 رسید پرداخت شما دریافت شد.

سفارش شما در انتظار بررسی ادمین است.
پس از تایید، اکانت VPN برای شما ارسال خواهد شد.
""",
    
    "payment_approved": """
✅ پرداخت شما تایید شد!

🎉 اکانت VPN شما آماده است:

🔗 لینک اشتراک:
{subscription_url}

برای استفاده، لینک بالا را در اپلیکیشن V2Ray یا مشابه وارد کنید.
""",

    "renewal_approved": """
✅ تمدید اکانت شما انجام شد!

⏱ {days} روز به مدت اشتراک اضافه شد
📊 {traffic} گیگابایت به حجم اضافه شد
📅 تاریخ انقضای جدید: {expires_at}

🔗 لینک اشتراک (همان لینک قبلی):
{subscription_url}
""",

    "renewal_confirm": """
🔄 تمدید اکانت سفارش #{order_id}

📦 {name}

⏱ +{days} روز به اشتراک
📊 +{traffic} گیگابایت حجم
💰 قیمت: {price:,} تومان

آیا تایید می‌کنید؟
""",
    
    "payment_rejected": """
❌ متاسفانه پرداخت شما رد شد.

دلیل: {reason}

لطفاً با پشتیبانی تماس بگیرید.
""",
    
    "admin_new_payment": """
💳 پرداخت جدید دریافت شد

👤 کاربر: {user_id} (@{username})
🔢 سفارش: #{order_id}
📦 پلن: {days} روز | {traffic} گیگابایت
💰 مبلغ: {price:,} تومان{renewal_note}
""",
    
    "error_invalid_number": "❌ لطفاً یک عدد معتبر وارد کنید.",
    
    "error_out_of_range": "❌ مقدار وارد شده خارج از محدوده مجاز است.",
    
    "error_general": "❌ خطایی رخ داد. لطفاً دوباره تلاش کنید.",
    
    "my_orders": "📋 سفارش‌های من",
    
    "no_orders": "شما هنوز سفارشی ندارید.",
    
    "order_status": """
🔢 سفارش #{order_id}
📦 {days} روز | {traffic} GB
💰 {price:,} تومان
📊 وضعیت: {status}
📅 {created_at}
"""
}


def get_text(key: str, **kwargs) -> str:
    """Get text with optional formatting"""
    text = TEXTS.get(key, "")
    if kwargs:
        return text.format(**kwargs)
    return text
