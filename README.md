# Mini App فروش VPN در تلگرام

[![Tests](https://github.com/isArman/3x-ui-telgram-bot/actions/workflows/test.yml/badge.svg)](https://github.com/isArman/3x-ui-telgram-bot/actions/workflows/test.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)

> **English:** A Telegram **Mini App** that sells VPN subscriptions. The Mini App is the
> only customer interface; the bot is a launcher and the Telegram Stars payment
> gateway. Subscriptions are provisioned automatically through a
> [3x-ui](https://github.com/MHSanaei/3x-ui) panel, with a manual config
> inventory as a fallback.

---

## معماری

```text
مشتری ──▶ Mini App (وب)  ──▶ JSON API  ──▶ SQLite
                │                             │
                │                             └──▶ پنل 3x-ui (ساخت/تمدید کلاینت)
                │
                └──▶ ربات فقط یک «در را باز می‌کند» + تسویه Stars
```

- **Mini App** تنها رابط کاربری است: فروش، کیف پول، سفارش‌ها، دعوت، و پنل ادمین.
- **ربات** فقط `/start` را می‌گیرد، Mini App را باز می‌کند، کد معرف را وصل می‌کند
  و پرداخت‌های Stars را تسویه می‌کند.
- هر دو در **یک کانتینر** اجرا می‌شوند: `aiohttp` روی یک پورت + `aiogram` برای polling.

---

## قابلیت‌ها

### برای مشتری

- پلن‌های آماده + پلن سفارشی (روز × حجم)
- سه روش پرداخت: **کیف پول**، **تلگرام Stars**، **کارت به کارت** (با رسید)
- حجم مصرف‌شده، حجم باقی‌مانده و تاریخ انقضا در صفحه «خانه»
- کپی لینک اشتراک و **کد QR**
- پلن تست رایگان (یک‌بار برای هر کاربر)
- سیستم معرفی: ۱۵٪ تخفیف برای خریدار جدید، ۲۰٪ پاداش کیف پول برای معرف
- مدیریت کیف پول و سوابق شارژ
- پیام‌های خودکار انقضا و کم‌شدن ترافیک (با اشاره به Mini App)

### برای ادمین (داخل Mini App)

| صفحه | کار |
|------|-----|
| داشبورد | آمار فروش و موجودی کانفیگ |
| بررسی | تأیید/رد پرداخت کارت و شارژ کیف پول |
| پرداخت‌ها | تاریخچه کامل پرداخت‌ها |
| پلن‌ها | افزودن/غیرفعال/حذف پلن و قیمت پلن سفارشی |
| پرومو | کد تخفیف درصدی یا روز/گیگابایت اضافه |
| کاربران | جستجو، مسدودسازی، افزایش موجودی |
| پنل | اتصال به 3x-ui، inboundها، ساخت توکن API، تست اتصال |
| انبار | کانفیگ‌های دستی برای حالت manual |
| تنظیمات | کارت بانکی، پلن تست، نرخ Stars، قیمت‌گذاری، حالت نگهداری |
| پیام | ارسال پیام همگانی |

---

## نصب سریع (Docker)

```bash
git clone https://github.com/isArman/3x-ui-telgram-bot.git
cd 3x-ui-telgram-bot

cp .env.example .env
# اختیاری — فقط برای seed اولیه پلن‌ها در اولین اجرا:
cp app/config/plans.example.yaml app/config/plans.yaml
mkdir -p data

# ویرایش .env
nano .env

# تولید SECRET_KEY (رمزنگاری پسورد پنل در دیتابیس)
python3 -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"

docker compose up -d --build
docker compose logs -f bot
```

بعد از استارت:

1. Mini App را باز کنید و با اکانت ادمین وارد **مدیریت** شوید.
2. **تنظیمات** → شماره کارت و نام صاحب کارت را وارد کنید.
3. **پنل** → آدرس پنل 3x-ui، نام کاربری و رمز را بدهید → **تست اتصال** →
   **ساخت توکن API** → inboundها را انتخاب کنید → حالت را روی «خودکار» بگذارید.
4. **پلن‌ها** → پلن‌های فروش را بسازید.

### ساخت Mini App در BotFather

1. `/newapp` → یک Mini App بسازید و آدرس `https://your-domain/vpn/` را بدهید.
2. `/setmenubutton` → دکمه منو را روی همان آدرس بگذارید (اپ خودش این کار را می‌کند).

---

## به‌روزرسانی روی سرور

```bash
cd ~/3x-ui-telgram-bot

cp -a .env /tmp/bot.env.bak
cp -a data /tmp/bot-data.bak

git fetch origin
git reset --hard origin/main

# build با لایه‌های cache — بدون --no-cache
nice -n 19 ionice -c3 docker compose build bot
docker compose up -d --force-recreate bot
docker compose logs --tail=50 bot
```

> **مهم:** هرگز `--no-cache` استفاده نکنید. لایه‌ی `pip install` حجیم‌ترین بخش
> build است؛ با ثابت نگه‌داشتن `requirements.txt` در cache می‌ماند و build
> سریع و کم‌مصرف است. پکیج‌های اختیاری در `requirements-extra.txt` هستند تا
> لایه‌ی اصلی pip باطل نشود.

`.env` و پوشه `data/` را commit نکنید؛ بعد از update سر جایشان می‌مانند.

---

## تنظیم `.env`

```env
BOT_TOKEN=7123456789:AAHxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
ADMIN_IDS=123456789,987654321
MINIAPP_URL=https://your-domain/vpn/
SECRET_KEY=your-fernet-key-here
DATABASE_URL=sqlite+aiosqlite:///data/db.sqlite3
WEB_PORT=8080
XUI_VERIFY_SSL=true
LOG_LEVEL=INFO
```

| متغیر | توضیح |
|--------|--------|
| `BOT_TOKEN` | توکن BotFather — **الزامی** |
| `ADMIN_IDS` | آیدی عددی ادمین(ها)، با ویرگول — **الزامی** |
| `MINIAPP_URL` | آدرس عمومی HTTPS اپ (باید قابل دسترس برای تلگرام باشد) — **الزامی** |
| `SECRET_KEY` | رمزنگاری پسورد/توکن پنل در SQLite — **الزامی** |
| `DATABASE_URL` | مسیر SQLite |
| `WEB_PORT` | پورت وب‌سرور (۸۰۸۰ برای prod) |
| `XUI_VERIFY_SSL` | `false` فقط برای پنل self-signed در dev |
| `LOG_LEVEL` | سطح لاگ |

> `SECRET_KEY` دیگر از `BOT_TOKEN` مشتق نمی‌شود؛ با چرخاندن توکن ربات، پسورد
> ذخیره‌شده پنل غیرقابل‌خواندن می‌شد.

---

## API پنل 3x-ui که استفاده می‌شود

| Endpoint | کاربرد |
|---|---|
| `GET /panel/api/clients/get/:email` | یک کلاینت + inboundها + `usedTraffic` |
| `GET /panel/api/clients/traffic/:email` | شمارنده‌های `up`/`down` |
| `GET /panel/api/clients/list` | همه کلاینت‌ها با ترافیک (یک درخواست برای کل پنل) |
| `POST /panel/api/clients/add` | ساخت کلاینت |
| `POST /panel/api/clients/update/:email` | جایگزینی کامل رکورد کلاینت |
| `POST /panel/api/clients/:email/attach` · `detach` | اتصال/قطع از inbound |
| `POST /panel/api/clients/bulkAdjust` | افزودن روز/حجم (تمدید، اتمی سمت پنل) |
| `GET /panel/api/inbounds/list` | inboundهای فعال |
| `POST /panel/api/setting/all` | تنظیمات پنل (آدرس subscription) |
| `GET`·`POST /panel/api/setting/apiTokens` | مدیریت توکن API |

### احراز هویت با پنل

اپ از **API Token** استفاده می‌کند (`Authorization: Bearer <token>`):

- درخواست‌های Bearer در پنل هم CSRF را رد می‌کنند و هم نیازی به session/login ندارند.
- پنل بعد از **۵ تلاش ناموفق لاگین در ۵ دقیقه**، آن `ip/username` را **۱۵ دقیقه**
  قفل می‌کند — با توکن این اتفاق اصلاً نمی‌افتد.
- اگر توکن حذف/منقضی شود، اپ خودکار به لاگین با نام کاربری و رمز برمی‌گردد.

### رفتار auto-provision

- Email کلاینت = Telegram user ID · Comment = پروفایل تلگرام
- خرید مجدد = **تمدید** همان کلاینت با `bulkAdjust` (اتمی، سمت پنل)
- محدودیت دستگاه (`limitIp`) در صورت نیاز جداگانه به‌روزرسانی می‌شود؛ رکورد
  **بعد** از تمدید دوباره از پنل خوانده می‌شود تا سهمیه و انقضا عقب‌نرود.
- خطای پنل → سفارش به ادمین می‌رسد، نه اینکه به مشتری «تمدید شد» گفته شود.

### آدرس subscription

از تنظیمات خود پنل خوانده می‌شود (`subURI`، `subPort`، `subPath`، `subDomain`).
اگر سِرور اشتراک گواهی TLS نداشته باشد، آدرس با `http` ساخته می‌شود — همان
طور که خود پنل این کار را می‌کند.

---

## ساختار پروژه

```text
app/
  bot/          # launcher.py (باز کردن اپ), payments.py (تسویه Stars)
  config/       # settings, texts, YAML seed
  database/     # SQLAlchemy models + SQLite migrations
  services/     # wallet, referral, plans, fulfillment, 3x-ui provisioning, …
  web/          # api.py (مشتری), admin_api.py (ادمین), static/ (Mini App)
  xui/          # کلاینت API پنل 3x-ui
  utils/        # encryption, validation, scheduler, notifications
tests/
```

---

## تست

```bash
python3 -m pytest tests/ -q
```

تست‌ها روی SQLite in-memory اجرا می‌شوند و به اینترنت نیاز ندارند.
یکی از تست‌ها بررسی می‌کند که Mini App فقط endpointهایی را صدا می‌زند که
واقعاً روی سرور ثبت شده‌اند.

---

## عیب‌یابی

| مشکل | راه‌حل |
|------|--------|
| اپ باز نمی‌شود | `docker compose logs -f bot` + بررسی `MINIAPP_URL` |
| «دسترسی نامعتبر است» | توکن ربات با `BOT_TOKEN` هم‌خوانی ندارد |
| زبانه «مدیریت» دیده نمی‌شود | `ADMIN_IDS` را چک کنید + rebuild |
| «کارت تنظیم نشده» | مدیریت → تنظیمات → کارت بانکی |
| لیست پلن خالی است | مدیریت → پلن‌ها، یا seed از `plans.yaml` در اولین boot |
| auto-provision fail | مدیریت → پنل → تست اتصال؛ inbound انتخاب شده؟ subscription فعال؟ |
| TLS error به پنل | `XUI_VERIFY_SSL=false` (فقط dev) |
| پنل زیر مسیر است | مدیریت → پنل → «پیشوند مسیر» (مثلاً `/panel`) |
| Stars اشتباه حساب می‌شود | نرخ هر استارز در تنظیمات (پیش‌فرض: ۵۰ تومان) |

---

## امنیت

- `.env`، `data/` و `app/config/plans.yaml` هرگز commit نمی‌شوند.
- پسورد و توکن پنل با Fernet و `SECRET_KEY` در SQLite ذخیره می‌شوند.
- فیلد comment در 3x-ui فقط شامل id، username و نام است.
- رسیدها روی سرور تلگرام می‌مانند؛ فایل روی دیسک نگه‌داری نمی‌شود.

→ [SECURITY.md](SECURITY.md)

---

## مشارکت

[CONTRIBUTING.md](CONTRIBUTING.md)

---

## لایسنس

[MIT](LICENSE)
