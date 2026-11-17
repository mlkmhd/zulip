# تحویل مهاجرت Zulip Gold از Python به Rust

- **تاریخ ثبت** — ۱۷ نوامبر ۲۰۲۶، تهران
- **شاخهٔ کار** — `codex/rust-backend`
- **مبنای پایتون** — `e28e86e48e`، نوک شاخهٔ `main` فورک Zulip Gold در شروع مهاجرت
- **آخرین کامیت کد تأییدشده** — `dd0b414ed1`

هدف این است که کل کد پایتون این فورک، شامل سرور، workerها، ابزارهای نصب و مدیریت، مهاجرت‌های پایگاه داده و تست‌ها، با Rust جایگزین شود. API و رفتار قابل مشاهدهٔ برنامه باید در طول مهاجرت سازگار بمانند. تا این تاریخ یک مسیر HTTP برای API موبایل در Rust مستقل اجرا می‌شود و چهار بخش دیگر منطق Rust را از داخل Django فراخوانی می‌کنند. پیاده‌سازی Django مبنای مقایسه است و در استقرار فعلی مسیر پیش‌فرض محسوب می‌شود.

## آخرین برش

[موجودی مسیرهای HTTP](API_INVENTORY.md) هر ۱۷۱ مدخل روش و مسیر OpenAPI را در مبنای `dd0b414ed1` ثبت می‌کند. سه مدخل نمونهٔ مستندات‌اند و ۱۶۸ عملیات به handler ثبت‌شده نگاشت شده‌اند. موجودی همچنین ۳۹ عملیات خارج از OpenAPI زیر `/api/v1/`، ۱۳۴ مسیر `/json/`، ۹۶ مسیر webhook ورودی و مسیرهای داخلی Django و Tornado را ثبت می‌کند. برای خانوادهٔ واژه‌های هشدار، احراز هویت، دسترسی داده، کش، رویداد، تست مرجع و وضعیت استقرار به تفکیک عملیات مستند شده است. شمارنده‌های انتقال کد همچنان مقدار ردیف مبنا را دارند.

## درصد و معیار پیشرفت

| معیار                                                        | وضعیت در `dd0b414ed1`                          |
| ------------------------------------------------------------ | ---------------------------------------------- |
| فایل‌های پایتون مبنا که پس از جایگزینی بازنشسته شده‌اند      | **۰ از ۲٬۰۶۲، معادل ۰٪**                       |
| عملیات OpenAPI که مسیر کامل HTTP آن‌ها در Rust تأیید شده است | **۰ از ۱۷۱ عملیات** در ۱۲۲ مسیر مستند          |
| مسیر مستقل HTTP خارج از OpenAPI                              | **۱ مسیر**، `/compatibility` با `GET` و `HEAD` |
| بخش‌هایی که Django بخشی از پردازش آن‌ها را به Rust می‌سپارد  | **۴ بخش**                                      |

این درصد پیشرفتِ حذف وابستگی به فایل‌های پایتون را اندازه می‌گیرد و تخمینی از کار یا زمان باقی‌مانده نیست. شاخهٔ فعلی ۲٬۰۶۳ فایل `.py` دارد، چون یک تست Python برای مقایسه با Rust اضافه شده است. از فایل‌های فعلی، ۹۶۹ مورد در مسیرهای `migrations/` هستند. دست‌کم ۷۰ فایل دیگر در `tools/` و `scripts/` با shebang پایتون اجرا می‌شوند و پسوند `.py` ندارند. شمارش OpenAPI نیز مسیر سازگاری موبایل، مسیرهای مستندنشده و همهٔ مسیرهای داخلی را پوشش نمی‌دهد.

با محیط مجازی Zulip فعال، فرمان‌های بازشماری را از ریشهٔ مخزن اجرا کنید.

```bash
git ls-tree -r --name-only e28e86e48e | awk '/\.py$/ {n++} END {print n+0}'
git ls-files '*.py' | wc -l
git diff --diff-filter=D --name-only e28e86e48e..HEAD -- '*.py'
git grep -Il '^#!.*python' HEAD -- tools scripts | awk '!/\.py$/ {n++} END {print n+0}'
python - <<'PY'
from pathlib import Path
import yaml

paths = yaml.safe_load(Path("zerver/openapi/zulip.yaml").read_text())["paths"]
methods = {"get", "head", "post", "put", "patch", "delete", "options", "trace"}
print(len(paths), sum(len(methods & spec.keys()) for spec in paths.values()))
PY
```

در گزارش‌های بعدی، فایل حذف‌شده را فقط وقتی به‌عنوان «بازنشسته» بشمارید که جایگزین Rust و تست‌های معادل آن تأیید شده باشند. وضعیت عملیات API را بر اساس روش و مسیر، مانند `GET /api/v1/users/me/alert_words`، جداگانه ثبت کنید. برای هر عملیات مشخص کنید که منطق داخلی، مسیر HTTP در توسعه یا مسیر پیش‌فرض استقرار منتقل شده است.

## بخش‌های انجام‌شده

| کامیت        | بخش منتقل‌شده                                                                                                                      | مرز پیاده‌سازی فعلی                                                                                                                                                                                                                                                                                                       |
| ------------ | ---------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `d8044013cd` | انتخاب رنگ پیش‌فرض اشتراک کانال در [stream_color.rs](src/stream_color.rs)                                                          | `ZULIP_RUST_STREAM_COLOR=1` تابع انتخاب رنگ را عوض می‌کند. Django اشتراک و رویدادها را مدیریت می‌کند.                                                                                                                                                                                                                     |
| `9a76e8e9d7` | پاک‌سازی و حذف تکرار هنگام افزودن واژهٔ هشدار در [alert_words.rs](src/alert_words.rs)                                              | `ZULIP_RUST_ALERT_WORDS=1` منطق داخلی `POST /api/v1/users/me/alert_words` و معادل وب را فعال می‌کند. Django اعتبارسنجی درخواست، تراکنش، کش و رویداد را انجام می‌دهد. پل PyO3 برای تبدیل حروف به کوچک از قواعد Unicode نسخهٔ جاری Python استفاده می‌کند.                                                                   |
| `de0e7dcda4` | ساخت فیلدهای پاسخ و قواعد به‌روزرسانی وضعیت کاربر در [user_status.rs](src/user_status.rs)                                          | `ZULIP_RUST_USER_STATUS=1` قواعد فیلدهای حذف‌شده و خالی و پاک‌کردن ایموجی را فعال می‌کند. مجوزها، جست‌وجوی ایموجی، دسترسی داده و انتشار رویداد در Django هستند.                                                                                                                                                           |
| `135c51f5c6` | محاسبهٔ تغییر مصرف سهمیهٔ ذخیره‌سازی ربات بر اساس تعداد نویسه‌ها در [bot_storage.rs](src/bot_storage.rs)                           | `ZULIP_RUST_BOT_STORAGE=1` در `PUT /api/v1/bot_storage` و هنگام نوشتن ربات داخلی استفاده می‌شود. عملیات خواندن و نوشتن همچنان در Django است.                                                                                                                                                                              |
| `dd0b414ed1` | `GET /compatibility` و `HEAD /compatibility` در [zulip-http](src/bin/zulip-http.rs) و [handler سازگاری](src/http/compatibility.rs) | Axum درخواست را دریافت می‌کند، کلاینت را بررسی می‌کند و پاسخ HTTP و JSON را می‌سازد. پیام ترجمه‌شدنی از فایل‌های کامپایل‌شدهٔ `locale/` خوانده می‌شود. پروکسی توسعه با `--rust-http-port` فقط درخواست‌های `GET` و `HEAD` این مسیر را روی میزبان اصلی توسعه به Rust می‌فرستد. استقرار تولید هنوز از Django استفاده می‌کند. |

پل Python در [python.rs](src/python.rs) و بسته‌بندی آن در [pyproject.toml](pyproject.toml) است. گزینهٔ Cargo به نام `http`، سرور `zulip-http` را بدون PyO3 فعال می‌کند. گزینهٔ `python` افزونهٔ `_zulip_backend` را می‌سازد. [تنظیمات CI فعلی](../.github/workflows/rust-backend.yml) تست‌های Rust، lint و مقایسهٔ رنگ کانال را اجرا می‌کند. تست‌های HTTP و Django در Multipass اجرا شده‌اند و در این workflow پوشش داده نمی‌شوند.

## شواهد تست‌ها

اجرای هر ردیف به محدوده و کامیت همان مرحله مربوط است. ردیف‌های تجمیعی شامل ردیف‌های جزئی هستند.

| تست                                                              | نسخهٔ Python    | نسخهٔ Rust                         | شواهد و محدوده                                                                                                                                                                                                                                                                       |
| ---------------------------------------------------------------- | --------------- | ---------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| `zerver.tests.test_subs`                                         | ۱۳۳ پاس         | ۱۳۳ پاس                            | تست‌های موجود اشتراک و رنگ کانال در Multipass. لاگ‌های `zulip-test-python.log` و `zulip-test-rust.log`.                                                                                                                                                                              |
| `test_alert_words` و `NormalActionsTest.test_alert_words_events` | ۱۴ پاس          | ۱۴ پاس                             | اضافه و حذف، پاسخ و ذخیره‌سازی، کش و رویداد؛ شامل کیس جدید Unicode و واژهٔ تکراری. لاگ‌های `zulip-alert-words-{0,1}.log`.                                                                                                                                                            |
| وضعیت کاربر همراه با مجموعهٔ واژه‌های هشدار و رویداد آن          | ۱۷ پاس          | ۱۷ پاس                             | شامل کیس جدید API برای پاک‌کردن ایموجی و حفظ متن وضعیت. لاگ‌های `zulip-user-status-{0,1}.log`.                                                                                                                                                                                       |
| `zerver.tests.test_embedded_bot_system`                          | ۱۸ پاس          | ۱۸ پاس                             | شامل سقف Unicode و اطمینان از تغییر نکردن داده پس از رد درخواست. لاگ‌های `zulip-bot-storage-{0,1}.log`.                                                                                                                                                                              |
| چهار بخش داخلی به‌صورت تجمیعی                                    | ۱۶۸ پاس         | ۱۶۸ پاس                            | در `135c51f5c6` با هر چهار پرچم خاموش یا روشن. لاگ‌های `zulip-rust-compatibility-{0,1}.log`.                                                                                                                                                                                         |
| `zerver.tests.test_compatibility`                                | ۸ پاس با Django | ۸ پاس مستقیم و ۸ پاس از پشت پروکسی | از ۸ تست، ۴ تست به درخواست HTTP مربوط‌اند. مجموعه شامل ۶ تست قبلی و ۲ تست جدید برای `HEAD` و ترجمه است. لاگ‌های `zulip-compatibility-{django,rust,proxy}.log`.                                                                                                                       |
| مقایسهٔ مستقیم پاسخ HTTP                                         | مبنای Django    | ۱۲۳ درخواست، ۰ اختلاف              | مقایسهٔ status، بدنه و هدرهای `Content-Type`، `Content-Language` و `Vary`. آزمون کمکی فعلاً در VM با نام `/home/ubuntu/zulip-rust-http-compare.py` است و در Git ثبت نشده است.                                                                                                        |
| مقایسهٔ انتخاب رنگ                                               | مبنای Python    | ۵۰۲ ورودی برابر                    | [تست مقایسه](tests/test_stream_color_parity.py) ترتیب نتیجه و تغییر نکردن ورودی‌ها را هم بررسی می‌کند.                                                                                                                                                                               |
| `cargo test --locked --features http`                            | —               | ۹ پاس                              | در بازبینی این سند روی `dd0b414ed1` دوباره اجرا شد. `cargo fmt --check` و Clippy با همهٔ featureها هم پاس شدند.                                                                                                                                                                      |
| بررسی موجودی و مجموعهٔ واژه‌های هشدار                            | ۱۵ پاس          | ۱۴ پاس با پل PyO3                  | روی `dd0b414ed1` در Multipass. اجرای Django شامل `OpenAPIArgumentsTest.test_openapi_arguments` و هر دو اجرا شامل تست‌های واژه‌ها و `test_alert_words_events` بودند. هر دو فرمان با کد خروج `0` تمام شدند. لاگ‌های `zulip-api-inventory-django.log` و `zulip-api-inventory-rust.log`. |

**خطاها و موارد تأییدنشده**

- در اجرای نهایی تست‌های هدفمند بالا شکست باز گزارش نشده است. دو اجرای اولیه که چند تست را در یک فرمان اجرا می‌کردند، کد خروج خطا داشتند در حالی که خود تست‌رانرها `OK` ثبت کرده بودند. اجرای دوبارهٔ مستقیم مجموعه‌های اشتراک و ذخیره‌سازی ربات با کد خروج `0` تأیید شد.
- آماده‌سازی Kiri در دانلود بسته‌های PostgreSQL به timeout و قطع اتصال خورد. مخزن PGDG در Multipass هم هنگام دریافت فهرست بسته‌ها متوقف شد. نصب PostgreSQL از مخزن Ubuntu و نصب PGroonga در همان VM مشکل عملی را برطرف کرد. علت قطعی مشکل شبکه مشخص نشده است.
- ساخت Rust در VM یک بار به علت اتصال به `index.crates.io:443` شکست خورد. همان `Cargo.lock` با `cargo vendor` روی میزبان بسته‌بندی و ساخت در VM با `--offline` تأیید شد.
- lint هدفمند Python پس از اصلاح خطاهای mypy پاس شد. بررسی `gitlint` در VM به علت checkout کم‌عمق و نبود `upstream/main` اجرا نشد. این استثنا فقط مربوط به بازبینی پیام کامیت در VM است.
- مقایسهٔ HTTP پاسخ‌های عادی و خطاهای مورد انتظار را پوشش می‌دهد. در مواردی مثل User-Agent موبایل Android بدون نسخه، Rust پاسخ `500` بدون قالب خطای Django می‌دهد. قبل از فعال‌سازی در تولید، قرارداد خطاهای غیرعادی، تنظیمات زبان و رفتار میزبان‌های فرعی را جداگانه بررسی کنید.
- کل مجموعهٔ backend، مرورگر و استقرار تولید روی شاخهٔ Rust اجرا نشده‌اند. جابه‌جایی ترافیک تولید، کارایی زیر بار و هم‌ارزی آپگریدهای پایگاه داده هنوز اعتبارسنجی نشده‌اند.

## ادامهٔ کار در Multipass

ماشین موجود `zulip-rust-tests` از Ubuntu 24.04 روی ARM64 استفاده می‌کند. checkout آزمایشی در `/home/ubuntu/zulip` روی `dd0b414ed1` است. نسخه‌های ثبت‌شده Python 3.12.3، Rust 1.89.0، PostgreSQL 16.15 و PGroonga 4.0.9 هستند. لاگ‌های جدول در `/home/ubuntu/` قرار دارند.

تغییر محلی `scripts/setup/apt-repos/zulip/noble.list` در این checkout فقط برای حذف PGDG از نصب VM است. آن را در کامیت مهاجرت وارد نکنید. این VM با `./tools/provision --skip-dev-db-build` آماده شده است و پایگاه دادهٔ تست آن آماده است. برای اجرای معمولی Django با دیتابیس توسعه، ابتدا وضعیت همان دیتابیس را جداگانه بررسی کنید.

```bash
multipass shell zulip-rust-tests
cd /home/ubuntu/zulip
source .venv/bin/activate
export PATH="$HOME/.cargo/bin:$HOME/.local/bin:$PATH"
git status --short --branch
git rev-parse --short HEAD
```

برای تحویل به agent روی ماشین دیگری، همراه سند دسترسی به checkout شاخه یا یک Git bundle از `codex/rust-backend` را بدهید. ماشین Multipass و لاگ‌های آن همراه Git منتقل نمی‌شوند. در محیط تازه یک VM جدا با Ubuntu 24.04 بسازید، همان کامیت را checkout کنید و از `./tools/provision --skip-dev-db-build` برای آماده‌کردن تست‌ها استفاده کنید. پیش از همگام‌کردن VM موجود با `git bundle` و `multipass transfer`، تغییرات محلی آن را بررسی کنید.

اگر نصب یا قفل وابستگی‌ها تغییر کرده، محیط را دوباره provision کنید. پس از تغییر کد PyO3، افزونه را دوباره در همان `.venv` نصب کنید. نسخهٔ بسته هنوز `0.1.0` است، پس صریحاً نصب مجدد بخواهید تا wheel قدیمی استفاده نشود. [دستورهای مخزن](../AGENTS.md)

```bash
uv pip install --reinstall-package zulip-backend --python .venv/bin/python ./rust-backend
cargo test --manifest-path rust-backend/Cargo.toml --locked --features http
cargo fmt --manifest-path rust-backend/Cargo.toml --check
cargo clippy --manifest-path rust-backend/Cargo.toml --locked --all-features --all-targets -- -D warnings
ZULIP_RUST_STREAM_COLOR=1 python -m unittest discover -s rust-backend/tests
```

برای چهار بخش داخلی، یک مجموعهٔ ثابت را با همهٔ پرچم‌ها یک بار خاموش و یک بار روشن اجرا کنید.

```bash
for mode in 0 1; do
    ZULIP_RUST_STREAM_COLOR="$mode" \
    ZULIP_RUST_ALERT_WORDS="$mode" \
    ZULIP_RUST_USER_STATUS="$mode" \
    ZULIP_RUST_BOT_STORAGE="$mode" \
    ./tools/test-backend zerver.tests.test_subs \
        zerver.tests.test_alert_words \
        zerver.tests.test_events.NormalActionsTest.test_alert_words_events \
        zerver.tests.test_user_status \
        zerver.tests.test_embedded_bot_system || exit $?
done
```

برای مسیر HTTP، `./tools/test-backend zerver.tests.test_compatibility` را ابتدا بدون `ZULIP_RUST_COMPATIBILITY_TEST_PORT` اجرا کنید. سپس `zulip-http` را روی `127.0.0.1:9997` بالا بیاورید و همان فرمان را با `ZULIP_RUST_COMPATIBILITY_TEST_PORT=9997` اجرا کنید. برای تکرار از پشت پروکسی، `EXTERNAL_HOST=localhost:9981 ./tools/run-dev --test --streamlined --rust-http-port 9997` را در VM اجرا کنید و پورت تست را روی `9981` بگذارید. فرمان ساخت سرویس و تنظیمات محیط در [راهنمای Rust](README.md#run-the-mobile-compatibility-api-over-http) آمده است.

اگر دانلود Cargo دوباره در VM مسدود شد، از همان `Cargo.lock` و فرمان `cargo vendor --manifest-path rust-backend/Cargo.toml --locked <vendor-dir>` روی میزبان استفاده کنید. محل کش موجود در VM در فرمان زیر آمده است. دایرکتوری vendor و تنظیمات مخصوص ماشین را محلی نگه دارید.

```bash
cargo build --manifest-path rust-backend/Cargo.toml --locked --offline \
    --features http --bin zulip-http \
    --config 'source.crates-io.replace-with="local-vendor"' \
    --config 'source.local-vendor.directory="/home/ubuntu/zulip-rust-http-deps/vendor"'
```

## کار باقی‌مانده و ترتیب پیشنهادی

1. **موجودی API و روش تحویل هر عملیات.** [فهرست پایه و ممیزی واژه‌های هشدار](API_INVENTORY.md) ثبت شده‌اند. هنگام انتخاب هر خانوادهٔ بعدی، مجوز، منبع داده، تغییرات پایگاه داده، کش، رویداد، تست مرجع و وضعیت استقرار را تکمیل کنید. موجودی مسیرهای SCIM، SSO و افزونه‌های اختصاصی استقرار نیز باقی است.
2. **یک مسیر کامل با احراز هویت و PostgreSQL.** برش بعدی `GET /api/v1/users/me/alert_words` است. از [قرارداد ثبت‌شده](API_INVENTORY.md#قرارداد-واژههای-هشدار) برای ساخت مقایسهٔ HTTP استفاده کنید. ابتدا رفتار Django را برای Basic Auth، نشست در alias وب، جداسازی سازمان‌ها، خطاهای ورود، محدودیت نرخ و روش‌های `HEAD` و `OPTIONS` ثبت کنید. سپس با دادهٔ برابر روی Python و Rust، بدنه، status و هدرها را مقایسه کنید. اتصال مستقل Rust تغییرات commit نشدهٔ `Django TestCase` را نمی‌بیند، بنابراین تست HTTP را با fixtureهای commit شده یا snapshot جدا اجرا کنید.
3. **زیرساخت و استقرار مسیرها.** سیاست مشترک احراز هویت، مجوز، locale، خطای JSON، CORS/CSRF، محدودیت نرخ و ردیابی را از کد موجود استخراج کنید. سپس بسته‌بندی سرویس Rust، تنظیمات reverse proxy و Supervisor، readiness و روش برگشت به Django را در محیط آزمایشی تأیید کنید. الگوی توسعه در [tools/run-dev](../tools/run-dev) و الگوی تولید در [پیکربندی nginx](../puppet/zulip/files/nginx/zulip-include-frontend/app) قرار دارند.
4. **سایر APIها و جریان‌های دارای اثر جانبی.** بعد از مسیرهای خواندنی، مسیرهای تغییردهندهٔ داده را منتقل کنید. ارسال و ویرایش پیام، کانال، عضویت، پیوست، جست‌وجو، ثبت رویداد و long polling، notification، webhook، worker و وظایف زمان‌دار هنوز باقی‌اند. در هر مرحله نتیجهٔ HTTP، تغییرات داده، ترتیب و محتوای رویداد، کش و رفتار retry را بررسی کنید. [معماری](../docs/overview/architecture-overview.md) و [سیستم رویداد](../docs/subsystems/events-system.md) نقطهٔ شروع‌اند.
5. **پایگاه داده، ابزارها و حذف Python.** نصب تازه و آپگرید از نسخه‌های پشتیبانی‌شده باید با مسیر جدید همان schema و دادهٔ قابل‌استفاده را بسازند. معادل مهاجرت‌های تاریخی Django، PGroonga و جست‌وجو را قبل از حذف فایل‌های مهاجرت تأیید کنید. دستورهای مدیریت، نصب، انتشار، پشتیبان‌گیری، واردات/صادرات، analytics، billing، rendering و تست‌رانر را هم در موجودی پروژه ثبت کنید. تست‌های مرجع را پس از انتقال به fixture، تست Rust یا تست پروتکل مستقل منتقل کنید تا در پایان امکان حذف Python وجود داشته باشد. [راهنمای مهاجرت پایگاه داده](../docs/subsystems/schema-migrations.md)

برای هر برش، ابتدا baseline همان تست‌ها را روی Python ثبت کنید. سپس مسیر Rust را برای همان درخواست و داده فعال کنید، مطمئن شوید درخواست واقعاً به Rust رسیده، خروجی و آثار جانبی را مقایسه کنید و وضعیت خروج تست‌رانر را ثبت کنید. اگر اختلافی پیدا شد، مشخص کنید که مربوط به انتقال، محیط تست یا رفتار موجود است. یک کامیت قابل‌بازبینی برای هر برش نگه دارید و این سند را با شناسهٔ کامیت، حالت اجرا، تعداد تست و نتیجه به‌روز کنید. دستورهای [AGENTS.md](../AGENTS.md) و [راهنمای طراحی API](../docs/processes/api-design.md) برای هر برش اعمال می‌شوند.
