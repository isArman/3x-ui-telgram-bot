# ربات فروش VPN در تلگرام

[![Tests](https://github.com/isArman/3x-ui-telgram-bot/actions/workflows/test.yml/badge.svg)](https://github.com/isArman/3x-ui-telgram-bot/actions/workflows/test.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)

> **English:** Telegram bot to sell VPN subscriptions. Supports manual config inventory **or** automatic provisioning via [3x-ui](https://github.com/MHSanaei/3x-ui) panel. Users pay by bank transfer, upload receipt; admin approves; bot delivers subscription link.

---

## قابلیت‌ها

- فروش پلن‌های آماده و پلن سفارشی (روز + حجم)
- پرداخت کارت‌به‌کارت + آپلود رسید
- تأیید/رد پرداخت توسط ادمین
- **دو حالت ارسال کانفیگ:**
  1. **خودکار (3x-ui)** — ساخت/آپدیت کلاینت در پنل + ارسال لینک subscription
  2. **انبار دستی** — کانفیگ‌های از پیش ذخیره‌شده per-plan
- fallback: اگر auto یا انبار ناموفق بود → ادمین لینک را دستی می‌فرستد
- نوتیفیکیشن انقضای اکانت (هر ۶ ساعت)
- داشبورد ادمین + موجودی کانفیگ
- دکمه بازگشت در تمام مراحل خرید

---

## پیش‌نیازها

| مورد | الزامی |
|------|--------|
| Docker + Docker Compose | پیشنهادی |
| Python 3.11+ | بدون Docker |
| اکانت تلگرام + BotFather token | بله |
| پنل 3x-ui | فقط برای حالت خودکار |
| شماره کارت بانکی | بله |

---

## نصب سریع (Docker)

```bash
git clone https://github.com/isArman/3x-ui-telgram-bot.git
cd 3x-ui-telgram-bot

cp .env.example .env
cp app/config/plans.example.yaml app/config/plans.yaml
mkdir -p data

# ویرایش .env — حداقل BOT_TOKEN, ADMIN_IDS, CARD_NUMBER, CARD_HOLDER
nano .env

# تولید SECRET_KEY (برای رمزنگاری پسورد پنل در دیتابیس)
python3 -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"

docker compose up -d --build
docker compose logs -f bot
```

در لاگ باید ببینید: `Bot started with N admin(s) configured`

---

## به‌روزرسانی ربات روی سرور

روی سروری که ربات با Docker اجرا می‌شود (مثلاً مسیر `/root/3x-ui-telgram-bot`):

```bash
cd /root/3x-ui-telgram-bot

# اگر git به خاطر ownership خطا داد:
# git -c safe.directory=/root/3x-ui-telgram-bot status

# بکاپ سریع فایل‌های محلی (اختیاری ولی توصیه‌شده)
cp -a .env /tmp/bot.env.bak
cp -a app/config/plans.yaml /tmp/plans.yaml.bak
cp -a data /tmp/bot-data.bak

# گرفتن آخرین کد
git fetch origin
git reset --hard origin/main
# فقط فایل‌های untracked مزاحم را پاک می‌کند؛ .env و data و plans.yaml (gitignore) حفظ می‌شوند
git clean -fd

# بیلد و ری‌استارت
docker compose up -d --build
docker compose logs --tail=50 bot
```

اگر فقط `git pull` به‌خاطر تغییرات محلی روی سرور fail شد، همان `reset --hard origin/main` لازم است.  
`.env`، `data/` و `app/config/plans.yaml` را commit نکنید؛ بعد از update باید سر جایشان بمانند.

اسکریپت آماده: `scripts/deploy.sh` (در صورت کثیف بودن working tree ممکن است `git pull` داخل آن fail شود — در آن حالت از دستورات بالا استفاده کنید).

---

## تنظیم `.env`

```env
BOT_TOKEN=7123456789:AAHxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
ADMIN_IDS=123456789
CARD_NUMBER=6037-1234-5678-9012
CARD_HOLDER=نام صاحب کارت
DATABASE_URL=sqlite+aiosqlite:///data/db.sqlite3
SECRET_KEY=your-fernet-key-here
XUI_VERIFY_SSL=true
LOG_LEVEL=INFO
```

| متغیر | توضیح |
|--------|--------|
| `BOT_TOKEN` | توکن BotFather |
| `ADMIN_IDS` | آیدی عددی ادمین(ها)، با ویرگول |
| `CARD_NUMBER` / `CARD_HOLDER` | اطلاعات پرداخت |
| `SECRET_KEY` | رمزنگاری پسورد پنل در SQLite — **در production الزامی** |
| `XUI_VERIFY_SSL` | `false` فقط برای پنل با گواهی self-signed در dev |
| `LOG_LEVEL` | سطح لاگ |

> **نکته:** قبل از نام متغیر فاصله نگذارید. ` ADMIN_IDS=123` توسط Docker نادیده گرفته می‌شود.

---

## تنظیم پلن‌ها (`plans.yaml`)

```bash
cp app/config/plans.example.yaml app/config/plans.yaml
nano app/config/plans.yaml
```

```yaml
plans:
  - id: basic
    name: "پلن پایه"
    days: 15
    traffic: 10
    price: 100000
    description: "15 روزه — 10 گیگابایت"

pricing:
  per_day: 4000
  per_gb: 9000
```

فرمول پلن سفارشی: `(روز × per_day) + (گیگ × per_gb)`

بعد از تغییر قیمت: `docker compose restart`

---

## راه‌اندازی پنل 3x-ui (حالت خودکار)

1. در ربات: **⚙️ پنل ادمین** → **🔗 پنل 3x-ui**
2. **⚙️ تنظیم اتصال پنل** — URL، username، password
3. ربات اتصال را تست می‌کند و آدرس subscription را از تنظیمات پنل می‌خواند
4. **📡 بروزرسانی Inboundها** — inboundهای مورد نظر را انتخاب کنید
5. **حالت ارسال** را روی **🤖 خودکار** بگذارید

### رفتار auto-provision

- Email کلاینت = Telegram user ID
- Comment = فقط اطلاعات پروفایل تلگرام (id, username, name)
- خرید مجدد = **آپدیت** همان کلاینت (نه ساخت جدید)
- خطا → fallback به انبار دستی → fallback به ارسال دستی

---

## انبار دستی (حالت manual)

1. کانفیگ‌ها را در پنل VPN بسازید
2. **⚙️ پنل ادمین** → **🗂 مدیریت کانفیگ‌ها** → **➕ افزودن**
3. پلن را انتخاب کنید و لینک `vless://` یا subscription بفرستید
4. هنگام تأیید پرداخت، یک کانفیگ آزاد assign می‌شود

---

## فلوی خرید کاربر

```
/start → خرید پلن → انتخاب → تایید → واریز → ارسال رسید
                                              ↓
                                    ادمین تأیید/رد
                                              ↓
                              auto 3x-ui / انبار / دستی
                                              ↓
                                   لینک subscription
```

---

## دستورات ادمین

| دستور / منو | کار |
|-------------|-----|
| `/admin` | پنل ادمین |
| `/configs` | مدیریت انبار کانفیگ |
| `/dashboard` | آمار |
| `/pending` | پرداخت‌های در انتظار |
| `/payments` | تاریخچه |
| **🔗 پنل 3x-ui** | اتصال پنل، inbound، auto/manual |

---

## امنیت و حریم خصوصی

### فایل‌های حساس — هرگز commit نکنید

| فایل / پوشه | محتوا |
|-------------|--------|
| `.env` | توکن ربات، SECRET_KEY، ADMIN_IDS |
| `data/` | دیتابیس SQLite، لاگ‌ها |
| `app/config/plans.yaml` | قیمت‌ها و پلن‌های شما |

همه در `.gitignore` هستند. قبل از public کردن repo، تاریخچه git را برای leak بررسی کنید.

### متغیرهای امنیتی

```env
SECRET_KEY=...          # رمزنگاری پسورد پنل 3x-ui در دیتابیس (الزامی در production)
XUI_VERIFY_SSL=true     # false فقط برای پنل self-signed در محیط dev
```

تولید `SECRET_KEY`:

```bash
python3 -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

### داده‌های ذخیره‌شده

| داده | محل | توضیح |
|------|-----|--------|
| توکن ربات | `.env` | با `/revoke` در BotFather قابل rotate |
| پسورد پنل 3x-ui | SQLite | رمزنگاری‌شده با Fernet |
| رسید پرداخت | Telegram + DB | `receipt_file_id` — مرجع فایل تلگرام |
| لینک subscription | SQLite | برای تحویل سرویس به کاربر |
| پروفایل تلگرام | SQLite | id، username، نام |

### حریم خصوصی کاربران

- فیلد **comment** در پنل 3x-ui فقط شامل id، username و نام تلگرام است (بدون جزئیات سفارش)
- لاگ‌ها توکن و پسورد را ذخیره نمی‌کنند
- پیام پسورد پنل بعد از setup از چت admin حذف می‌شود

### توصیه‌های عملیاتی

1. فقط آیدی‌های مورد اعتماد در `ADMIN_IDS`
2. توکن لو رفته → فوراً `/revoke` در BotFather
3. پسورد پنل لو رفته → در 3x-ui عوض کنید و اتصال را دوباره setup کنید
4. از HTTPS برای پنل 3x-ui استفاده کنید (`XUI_VERIFY_SSL=true`)
5. پوشه `data/` را backup بگیرید — ولی backup را public نکنید

### گزارش آسیب‌پذیری

لطفاً issue عمومی باز نکنید. جزئیات را به maintainer به‌صورت خصوصی بفرستید.

→ راهنمای کامل: [SECURITY.md](SECURITY.md)

---

## تست

```bash
docker run --rm \
  -e BOT_TOKEN=test:123 -e ADMIN_IDS=1 \
  -e CARD_NUMBER=1234 -e CARD_HOLDER=Test \
  -e SECRET_KEY=test-key \
  -v "$(pwd):/app" \
  3x-ui-telgram-bot-bot \
  sh -c "pip install -q pytest pytest-asyncio && python -m pytest tests/ -v"
```

---

## ساختار پروژه

```text
app/
  bot/handlers/     # user, admin, panel_admin
  bot/keyboards/    # Reply & inline keyboards
  bot/auth.py       # Admin authorization
  config/           # settings, plans, texts
  database/         # SQLAlchemy models
  services/         # provisioning, inventory, users
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
| auto-provision fail | پنل را تست کنید؛ inbound انتخاب شده؟ subscription فعال؟ |
| TLS error به پنل | `XUI_VERIFY_SSL=false` (فقط dev) |
| قیمت قدیمی | `docker compose restart` |
| Missing settings | `.env` را کامل کنید |

---

## مشارکت

[CONTRIBUTING.md](CONTRIBUTING.md)

---

## لایسنس

[MIT](LICENSE)
