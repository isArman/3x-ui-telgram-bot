# ربات فروش VPN در تلگرام

[![Tests](https://github.com/isArman/3x-ui-telgram-bot/actions/workflows/test.yml/badge.svg)](https://github.com/isArman/3x-ui-telgram-bot/actions/workflows/test.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)

> **English:** Telegram bot to sell VPN subscriptions. Card-to-card payments with receipt review, wallet balance, referrals, and admin-managed plans/card details in-bot. Supports manual config inventory **or** automatic provisioning via [3x-ui](https://github.com/MHSanaei/3x-ui).

---

## قابلیت‌ها

- فروش پلن‌های آماده و پلن سفارشی (روز + حجم)
- پرداخت کارت‌به‌کارت + آپلود رسید + تأیید/رد ادمین
- **کیف پول:** شارژ با مبلغ دلخواه، پرداخت کامل یا جزئی از موجودی
- **معرفی دوستان:** لینک دعوت، ۱۵٪ تخفیف اولین خرید معرف‌شده، ۲۰٪ پاداش کیف پول برای معرف
- مدیریت **پلن‌ها** و **کارت بانکی** از داخل پنل ادمین ربات (ذخیره در دیتابیس)
- **دو حالت ارسال کانفیگ:**
  1. **خودکار (3x-ui)** — ساخت/آپدیت کلاینت در پنل + لینک subscription
  2. **انبار دستی** — کانفیگ‌های از پیش ذخیره‌شده per-plan
- fallback: auto/انبار ناموفق → ادمین لینک را دستی می‌فرستد
- تمدید اکانت از داخل ربات
- نوتیفیکیشن انقضا و کم‌شدن ترافیک
- داشبورد ادمین + موجودی کانفیگ

---

## پیش‌نیازها

| مورد | الزامی |
|------|--------|
| Docker + Docker Compose | پیشنهادی |
| Python 3.11+ | بدون Docker |
| اکانت تلگرام + BotFather token | بله |
| پنل 3x-ui | فقط برای حالت خودکار |
| شماره کارت بانکی | بله (از داخل ربات تنظیم می‌شود) |

---

## نصب سریع (Docker)

```bash
git clone https://github.com/isArman/3x-ui-telgram-bot.git
cd 3x-ui-telgram-bot

cp .env.example .env
# اختیاری — فقط برای seed اولیه پلن‌ها در اولین اجرا:
cp app/config/plans.example.yaml app/config/plans.yaml
mkdir -p data

# ویرایش .env — حداقل BOT_TOKEN و ADMIN_IDS
nano .env

# تولید SECRET_KEY (برای رمزنگاری پسورد پنل در دیتابیس)
python3 -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"

docker compose up -d --build
docker compose logs -f bot
```

در لاگ باید ببینید: `Bot started with N admin(s) configured`

بعد از استارت، در ربات:

1. **⚙️ پنل ادمین** → **💳 کارت بانکی** — شماره و نام صاحب کارت
2. **⚙️ پنل ادمین** → **📦 مدیریت پلن‌ها** — پلن‌ها و قیمت پلن سفارشی  
   (اگر `plans.yaml` وجود داشته باشد، در اولین اجرا به‌صورت خودکار وارد دیتابیس می‌شود)

---

## به‌روزرسانی ربات روی سرور

```bash
cd /root/3x-ui-telgram-bot   # مسیر پروژه روی سرور

# بکاپ سریع (اختیاری)
cp -a .env /tmp/bot.env.bak
cp -a data /tmp/bot-data.bak

git fetch origin
git reset --hard origin/main
git clean -fd

docker compose up -d --build
docker compose logs --tail=50 bot
```

`.env` و پوشه `data/` را commit نکنید؛ بعد از update سر جایشان می‌مانند.

---

## تنظیم `.env`

```env
BOT_TOKEN=7123456789:AAHxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
ADMIN_IDS=123456789,987654321
DATABASE_URL=sqlite+aiosqlite:///data/db.sqlite3
SECRET_KEY=your-fernet-key-here
XUI_VERIFY_SSL=true
LOG_LEVEL=INFO

# اختیاری — فقط برای seed اولیه کارت در دیتابیس
# CARD_NUMBER=6037-1234-5678-9012
# CARD_HOLDER=نام صاحب کارت
```

| متغیر | توضیح |
|--------|--------|
| `BOT_TOKEN` | توکن BotFather — **الزامی** |
| `ADMIN_IDS` | آیدی عددی ادمین(ها)، با ویرگول — **الزامی** |
| `DATABASE_URL` | مسیر SQLite (پیش‌فرض برای Docker کافی است) |
| `SECRET_KEY` | رمزنگاری پسورد پنل در SQLite — **در production الزامی** |
| `XUI_VERIFY_SSL` | `false` فقط برای پنل self-signed در dev |
| `LOG_LEVEL` | سطح لاگ |
| `CARD_NUMBER` / `CARD_HOLDER` | اختیاری؛ ترجیحاً از داخل ربات تنظیم کنید |

> **نکته:** قبل از نام متغیر فاصله نگذارید. ` ADMIN_IDS=123` توسط Docker نادیده گرفته می‌شود.

---

## مدیریت پلن‌ها و کارت (داخل ربات)

پلن‌ها و کارت دیگر نیازی به ویرایش فایل روی سرور ندارند.

| منو | کار |
|-----|-----|
| **📦 مدیریت پلن‌ها** | افزودن / ویرایش / فعال‌غیرفعال پلن آماده |
| **💰 قیمت پلن سفارشی** | تنظیم `per_day` و `per_gb` |
| **💳 کارت بانکی** | شماره کارت و نام صاحب کارت |

فرمول پلن سفارشی: `(روز × per_day) + (گیگ × per_gb)`

`plans.yaml` / `plans.example.yaml` فقط برای **seed اولیه** استفاده می‌شوند؛ بعد از آن منبع حقیقت دیتابیس است.

---

## کیف پول

- منو: **💰 کیف پول من** → شارژ با مبلغ دلخواه → کارت → رسید → تأیید ادمین
- هنگام خرید/تمدید ربات می‌پرسد از کیف پول استفاده شود یا نه:
  - موجودی کافی → کسر و فعال‌سازی (بدون رسید)
  - موجودی کمتر → کسر موجودی + پرداخت مابه‌تفاوت با کارت/رسید
  - رد پرداخت جزئی → مبلغ کیف پول برمی‌گردد

ادمین برای شارژ می‌تواند مبلغ درخواستی را تأیید کند یا مبلغ دستی وارد کند.

---

## سیستم معرفی (Referral)

- هر کاربر کد و لینک دعوت دارد: **🎁 دعوت دوستان**  
  `https://t.me/<BOT>?start=<code>`
- فقط کاربر **جدید** که با لینک وارد شود به معرف وصل می‌شود (یک‌بار، غیرقابل تغییر)
- روی **اولین خرید اشتراک جدید** (نه تمدید):
  - معرف‌شده: **۱۵٪ تخفیف**
  - معرف: **۲۰٪ مبلغ اصلی پلن** به‌صورت اعتبار کیف پول (غیرقابل برداشت جداگانه — همان موجودی کیف پول)

---

## راه‌اندازی پنل 3x-ui (حالت خودکار)

1. **⚙️ پنل ادمین** → **🔗 پنل 3x-ui**
2. **⚙️ تنظیم اتصال پنل** — URL، username، password
3. ربات اتصال را تست می‌کند و آدرس subscription را از پنل می‌خواند
4. **📡 بروزرسانی Inboundها** — inboundهای مورد نظر را انتخاب کنید
5. **حالت ارسال** را روی **🤖 خودکار** بگذارید

### رفتار auto-provision

- Email کلاینت = Telegram user ID
- Comment = پروفایل تلگرام (id, username, name)
- خرید مجدد = **آپدیت** همان کلاینت
- خطا → fallback به انبار دستی → ارسال دستی

---

## انبار دستی (حالت manual)

1. کانفیگ‌ها را در پنل VPN بسازید
2. **⚙️ پنل ادمین** → **🗂 مدیریت کانفیگ‌ها** → **➕ افزودن**
3. پلن را انتخاب کنید و لینک `vless://` یا subscription بفرستید
4. هنگام تأیید پرداخت، یک کانفیگ آزاد assign می‌شود

---

## فلوی خرید کاربر

```text
/start [کد معرف]
  → خرید پلن / پلن سفارشی
  → تایید (با تخفیف معرف در صورت واجد شرایط بودن)
  → استفاده از کیف پول؟ (بله کامل / بله جزئی / خیر)
  → در صورت نیاز: واریز کارت + ارسال رسید
  → ادمین تأیید/رد
  → auto 3x-ui / انبار / دستی
  → لینک subscription (+ پاداش کیف پول به معرف در صورت اولین خرید)
```

---

## دستورات و منوی ادمین

| دستور / منو | کار |
|-------------|-----|
| `/admin` یا **⚙️ پنل ادمین** | منوی اصلی ادمین |
| **📦 مدیریت پلن‌ها** | پلن‌های آماده + قیمت سفارشی |
| **💳 کارت بانکی** | شماره و نام صاحب کارت |
| **🔗 پنل 3x-ui** | اتصال، inbound، auto/manual |
| **🗂 مدیریت کانفیگ‌ها** | انبار کانفیگ |
| **📊 داشبورد** / `/dashboard` | آمار |
| **📋 پرداخت‌های در انتظار** / `/pending` | سفارش و شارژ کیف پول |
| `/payments` | تاریخچه پرداخت‌ها |
| `/configs` | میانبر انبار کانفیگ |

---

## امنیت و حریم خصوصی

### فایل‌های حساس — هرگز commit نکنید

| فایل / پوشه | محتوا |
|-------------|--------|
| `.env` | توکن ربات، SECRET_KEY، ADMIN_IDS |
| `data/` | دیتابیس SQLite، لاگ‌ها |
| `app/config/plans.yaml` | اختیاری؛ فقط seed اولیه |

همه در `.gitignore` هستند.

### متغیرهای امنیتی

```env
SECRET_KEY=...          # رمزنگاری پسورد پنل 3x-ui در دیتابیس
XUI_VERIFY_SSL=true     # false فقط برای پنل self-signed در dev
```

```bash
python3 -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

### داده‌های ذخیره‌شده

| داده | محل | توضیح |
|------|-----|--------|
| توکن ربات | `.env` | با `/revoke` در BotFather قابل rotate |
| پسورد پنل 3x-ui | SQLite | رمزنگاری‌شده با Fernet |
| کارت و پلن‌ها | SQLite | قابل ویرایش از پنل ادمین |
| موجودی کیف پول / معرف | SQLite | روی جدول کاربران و سفارش‌ها |
| رسید پرداخت | Telegram + DB | `receipt_file_id` |
| لینک subscription | SQLite | تحویل سرویس |

### حریم خصوصی کاربران

- فیلد **comment** در 3x-ui فقط شامل id، username و نام است
- لاگ‌ها توکن و پسورد را ذخیره نمی‌کنند
- پیام پسورد پنل بعد از setup از چت ادمین حذف می‌شود

### توصیه‌های عملیاتی

1. فقط آیدی‌های مورد اعتماد در `ADMIN_IDS`
2. توکن لو رفته → `/revoke` در BotFather
3. پسورد پنل لو رفته → در 3x-ui عوض کنید و دوباره setup کنید
4. از HTTPS برای پنل استفاده کنید
5. از `data/` بکاپ بگیرید — بکاپ را public نکنید

→ [SECURITY.md](SECURITY.md)

---

## تست

```bash
docker run --rm \
  -e BOT_TOKEN=test:123 -e ADMIN_IDS=1 \
  -e SECRET_KEY=test-key \
  -v "$(pwd):/app" \
  3x-ui-telgram-bot-bot \
  sh -c "pip install -q pytest pytest-asyncio && python -m pytest tests/ -v"
```

---

## ساختار پروژه

```text
app/
  bot/handlers/     # user, wallet, admin, shop_admin, panel_admin
  bot/keyboards/    # Reply & inline keyboards
  bot/auth.py       # Admin authorization
  config/           # settings, texts, optional YAML seed
  database/         # SQLAlchemy models + SQLite migrations
  services/         # wallet, referral, plans, provisioning, …
  xui/              # 3x-ui API client
  utils/            # encryption, validation, scheduler
data/               # SQLite + logs (gitignored)
tests/
```

---

## عیب‌یابی

| مشکل | راه‌حل |
|------|--------|
| ربات جواب نمی‌دهد | `docker compose logs -f bot` |
| دکمه ادمین نیست | `ADMIN_IDS` را چک کنید + rebuild |
| «کارت تنظیم نشده» | پنل ادمین → **کارت بانکی** |
| لیست پلن خالی است | پنل ادمین → **مدیریت پلن‌ها** یا seed از `plans.yaml` در اولین boot |
| auto-provision fail | تست اتصال پنل؛ inbound انتخاب شده؟ subscription فعال؟ |
| TLS error به پنل | `XUI_VERIFY_SSL=false` (فقط dev) |
| قیمت عوض نمی‌شود | قیمت را از **مدیریت پلن‌ها** در ربات تغییر دهید (نه فایل YAML) |
| Missing settings | `.env` حداقل `BOT_TOKEN` و `ADMIN_IDS` |

---

## مشارکت

[CONTRIBUTING.md](CONTRIBUTING.md)

---

## لایسنس

[MIT](LICENSE)
