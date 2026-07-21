# ربات فروش VPN در تلگرام

با این پروژه می‌توانید روی تلگرام اشتراک VPN بفروشید: کاربر پلن می‌خرد، به کارت شما واریز می‌کند، رسید می‌فرستد؛ شما تأیید می‌کنید و ربات یک کانفیگ از موجودی همان پلن برایش می‌فرستد.

نیازی به اتصال خودکار به پنل 3x-ui نیست — کانفیگ‌ها را خودتان می‌سازید و در ربات انبار می‌کنید.

---

## پیش‌نیازها

روی سیستم یا سرور باید این‌ها را داشته باشید:

1. **Docker** و **Docker Compose** (روش پیشنهادی)  
   یا به‌جای آن Python 3.11+
2. یک اکانت تلگرام
3. شماره کارت بانکی برای دریافت وجه (برای نمایش به مشتری)

بررسی نصب Docker:

```bash
docker --version
docker compose version
```

اگر نبود، از مستندات رسمی Docker نصب کنید: https://docs.docker.com/get-docker/

---

## مرحله ۱ — ساخت ربات در تلگرام

1. در تلگرام به [@BotFather](https://t.me/BotFather) بروید.
2. دستور `/newbot` را بزنید و نام و یوزرنیم ربات را انتخاب کنید.
3. توکنی شبیه این می‌گیرید — نگه دارید:

```text
7123456789:AAHxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
```

4. (اختیاری) با `/setuserpic` و `/setdescription` ظاهر ربات را تنظیم کنید.

---

## مرحله ۲ — پیدا کردن آیدی عددی ادمین

آیدی عددی تلگرام خودتان را لازم دارید (نه یوزرنیم).

1. به [@userinfobot](https://t.me/userinfobot) یا [@getidsbot](https://t.me/getidsbot) پیام دهید.
2. عدد `Id` را کپی کنید (مثلاً `123456789`).

اگر چند ادمین دارید، همه آیدی‌ها را با ویرگول جدا کنید.

---

## مرحله ۳ — دانلود پروژه

```bash
git clone https://github.com/isArman/3x-ui-telgram-bot.git
cd 3x-ui-telgram-bot
```

فایل‌های تنظیمات را از روی نمونه بسازید:

```bash
cp .env.example .env
cp app/config/plans.example.yaml app/config/plans.yaml
mkdir -p data
```

> بدون `plans.yaml` و `.env` ربات درست بالا نمی‌آید. هر دو فایل را باید خودتان از روی نمونه بسازید.

---

## مرحله ۴ — تنظیم `.env`

فایل `.env` را با یک ادیتور باز کنید:

```bash
nano .env
```

مقادیر را این‌طور پر کنید:

```env
BOT_TOKEN=7123456789:AAHxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
ADMIN_IDS=123456789
CARD_NUMBER=6037-1234-5678-9012
CARD_HOLDER=نام صاحب کارت
DATABASE_URL=sqlite+aiosqlite:///data/db.sqlite3
```

| متغیر | توضیح |
|--------|--------|
| `BOT_TOKEN` | توکن BotFather |
| `ADMIN_IDS` | آیدی عددی ادمین(ها). چندتایی: `111,222,333` |
| `CARD_NUMBER` | شماره کارتی که به مشتری نشان داده می‌شود |
| `CARD_HOLDER` | نام صاحب کارت |
| `DATABASE_URL` | معمولاً همین مقدار پیش‌فرض کافی است |

### نکات مهم `.env`

- **قبل از نام متغیر فاصله نگذارید.**  
  درست: `ADMIN_IDS=123`  
  غلط: ` ADMIN_IDS=123` (Docker این خط را نادیده می‌گیرد)
- فایل `.env` را commit / در گیت‌هاب آپلود نکنید.
- بعد از عوض کردن `.env` باید کانتینر را دوباره بسازید یا حداقل recreate کنید.

---

## مرحله ۵ — تنظیم پلن‌ها و قیمت‌ها

فایل پلن‌ها:

```bash
nano app/config/plans.yaml
```

نمونه ساختار:

```yaml
plans:
  - id: basic
    name: "پلن پایه"
    days: 15
    traffic: 10
    price: 100000
    description: "15 روزه — 10 گیگابایت"

  - id: standard
    name: "پلن استاندارد"
    days: 30
    traffic: 15
    price: 150000
    description: "یک ماهه — 15 گیگابایت"

pricing:
  per_day: 4000
  per_gb: 9000
```

| فیلد | معنی |
|------|------|
| `id` | شناسه یکتا (انگلیسی، بدون فاصله) — برای انبار کانفیگ مهم است |
| `name` | نام نمایشی در ربات |
| `days` | مدت اشتراک (روز) |
| `traffic` | حجم به گیگابایت |
| `price` | قیمت پلن آماده (عدد صحیح، مثلاً تومان) |
| `description` | توضیح کوتاه |
| `per_day` / `per_gb` | فرمول پلن سفارشی: `(روز × per_day) + (گیگ × per_gb)` |

می‌توانید پلن اضافه/حذف کنید؛ فقط `id`ها یکتا باشند.

---

## مرحله ۶ — روشن کردن ربات (Docker — پیشنهادی)

از ریشه پروژه:

```bash
docker compose up -d --build
```

وضعیت:

```bash
docker compose ps
docker compose logs -f bot
```

در لاگ باید چیزی شبیه این ببینید:

```text
Admin IDs loaded: [123456789]
Starting bot polling...
```

اگر این خطوط آمد، ربات آنلاین است. با `Ctrl+C` از دنبال‌کردن لاگ خارج شوید (ربات خاموش نمی‌شود).

### دستورات روزمره

| کار | دستور |
|-----|--------|
| مشاهده لاگ | `docker compose logs -f bot` |
| خاموش کردن | `docker compose down` |
| روشن مجدد | `docker compose up -d` |
| بعد از تغییر قیمت (`plans.yaml`) | `docker compose restart` |
| بعد از تغییر کد یا `.env` | `docker compose up -d --build` |

> `plans.yaml` به کانتینر mount شده؛ برای تغییر قیمت معمولاً فقط `restart` کافی است و نیازی به rebuild نیست.

روی سرور می‌توانید از اسکریپت هم استفاده کنید (`.env` و `plans.yaml` از قبل باید آماده باشند):

```bash
chmod +x scripts/deploy.sh
./scripts/deploy.sh
```

---

## مرحله ۷ — اولین ورود به ربات به‌عنوان ادمین

1. در تلگرام ربات خودتان را باز کنید و `/start` بزنید.
2. اگر آیدی‌تان در `ADMIN_IDS` باشد، در منو دکمه **⚙️ پنل ادمین** را می‌بینید.
3. اگر ندیدید:
   - آیدی را دوباره چک کنید
   - فاصله اضافه در `.env` نباشد
   - با `docker compose up -d --build` دوباره بالا بیاورید
   - لاگ را برای `Admin IDs loaded` ببینید

---

## مرحله ۸ — پر کردن انبار کانفیگ (الزامی قبل از فروش)

ربات خودش روی سرور VPN کانفیگ نمی‌سازد. باید لینک‌ها را دستی بسازید و در انبار بگذارید.

1. روی سرور/پنل VPN خودتان به تعداد لازم کانفیگ یا لینک سابسکریپشن بسازید  
   (مثلاً `vless://...` یا لینک `https://.../sub/...`).
2. در ربات: **⚙️ پنل ادمین** → مدیریت کانفیگ‌ها → افزودن.
3. پلن مورد نظر را انتخاب کنید و لینک را بفرستید.
4. برای هر فروش موفق، یک کانفیگ آزاد لازم است — موجودی را از قبل پر نگه دارید.

دستورهای ادمین:

| دستور | کار |
|--------|-----|
| `/admin` | پنل ادمین |
| `/configs` | مدیریت موجودی کانفیگ |
| `/dashboard` | آمار و موجودی |
| `/pending` | پرداخت‌های در انتظار |
| `/payments` | تاریخچه پرداخت‌ها |

---

## مرحله ۹ — تست یک خرید کامل

با یک اکانت تلگرام دیگر (غیر ادمین بهتر است):

1. `/start` → **📦 خرید پلن** → یک پلن را انتخاب و تأیید کنید.
2. شماره کارت و مبلغ را ببینید؛ یک عکس رسید (حتی تستی) بفرستید.
3. با اکانت ادمین، رسید را تأیید (`approve`) کنید.
4. باید کانفیگ برای کاربر ارسال شود.

اگر موجودی آن پلن خالی باشد، ربات از ادمین می‌خواهد لینک/کانفیگ را همان لحظه دستی بفرستد. برای پلن‌های سفارشی هم معمولاً لینک را دستی وارد می‌کنید.

---

## اجرای بدون Docker (اختیاری)

```bash
cd 3x-ui-telgram-bot
python3 -m venv .venv
source .venv/bin/activate          # ویندوز: .venv\Scripts\activate
pip install -r requirements.txt

cp .env.example .env
cp app/config/plans.example.yaml app/config/plans.yaml
mkdir -p data

# .env و plans.yaml را مثل مراحل ۴ و ۵ ویرایش کنید

python -m app.main
```

برای توقف: `Ctrl+C`.

---

## عیب‌یابی

| مشکل | کارهایی که چک کنید |
|------|---------------------|
| ربات به پیام جواب نمی‌دهد | `docker compose logs -f bot` — خطا یا قطع شدن polling |
| دکمه پنل ادمین نیست | `ADMIN_IDS`، نبودن فاصله اول خط، rebuild بعد از تغییر `.env` |
| قیمت‌های قدیمی نشان می‌دهد | فایل `app/config/plans.yaml` روی هاست را چک کنید، بعد `docker compose restart` |
| بعد از recreate هنوز کد/قیمت قدیم است | حتماً `--build` بزنید: `docker compose up -d --build` |
| خطای Missing required settings | یکی از `BOT_TOKEN` / `ADMIN_IDS` / `CARD_NUMBER` / `CARD_HOLDER` خالی است |
| کانتینر بالا نمی‌آید چون `plans.yaml` نیست | `cp app/config/plans.example.yaml app/config/plans.yaml` |
| دیتابیس از بین رفت | پوشه `data/` را پاک نکنید؛ آنجا `db.sqlite3` است |

لاگ زنده:

```bash
docker compose logs -f bot
```

ورود به کانتینر (در صورت نیاز):

```bash
docker compose exec bot sh
```

---

## امنیت

- `.env`، پوشه `data/` و `app/config/plans.yaml` در `.gitignore` هستند — در ریپوی عمومی commit نکنید.
- توکن ربات را مثل رمز عبور بدانید؛ اگر لو رفت از BotFather با `/revoke` عوضش کنید.
- فقط آیدی ادمین‌های واقعی را در `ADMIN_IDS` بگذارید.

---

## ساختار خلاصه پروژه

```text
.env                         # تنظیمات محرمانه (خودتان می‌سازید)
app/config/plans.yaml        # پلن و قیمت (خودتان می‌سازید)
app/config/plans.example.yaml
app/bot/handlers/            # منطق کاربر و ادمین
data/                        # دیتابیس SQLite (خودکار ساخته می‌شود)
docker-compose.yml
scripts/deploy.sh
```

---

## لایسنس

MIT
