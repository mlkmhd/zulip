# تحویل مهاجرت Zulip Gold از Python به Rust

- **تاریخ ثبت** — ۱۷ نوامبر ۲۰۲۶، تهران
- **شاخهٔ کار** — `codex/rust-backend`
- **مبنای پایتون** — `e28e86e48e`، نوک شاخهٔ `main` فورک Zulip Gold در شروع مهاجرت
- **آخرین کامیت کد تأییدشده** — `c13cf5c2de`

هدف این است که کل کد پایتون این فورک، شامل سرور، workerها، ابزارهای نصب و مدیریت، مهاجرت‌های پایگاه داده و تست‌ها، با Rust جایگزین شود. API و رفتار قابل مشاهدهٔ برنامه باید در طول مهاجرت سازگار بمانند. در توسعه، مسیر سازگاری موبایل و خواندن واژه‌های هشدار در سرویس HTTP مستقل Rust اجرا می‌شوند. چهار بخش دیگر منطق Rust را از داخل Django فراخوانی می‌کنند. Django مبنای مقایسه و مسیر پیش‌فرض استقرار است.

## آخرین برش

اگر پس از شروع سرویس اتصال PostgreSQL بسته شود، درخواست بعدیِ نیازمند پایگاه داده یا بررسی آمادگی می‌تواند اتصال را دوباره باز کند. Rust روی اتصال تازه همان محدودیت زمان اجرای کوئری و، در حالت غیرفعال بودن ثبت فعالیت، تنظیم فقط‌خواندنی را اعمال می‌کند. هر درخواست اتصال خودش را تا پایان نگه می‌دارد تا قطع اتصال باعث اجرای دوبارهٔ ثبت فعالیت یا کسر سهمیه نشود. [مدیریت اتصال](src/http/database.rs)، [تست بازیابی](../zerver/tests/test_alert_words.py)

تست HTTP در Multipass اتصال مخصوص فرایند Rust را در clone پایگاه دادهٔ تست قطع کرد. نسخهٔ قبلی در هر دو درخواست بعدی `503` داد. نسخهٔ جدید همان پاسخ موفق قبل از قطع اتصال را در همان فرایند برگرداند. در آزمون جداگانه، یک نمونهٔ موقت PostgreSQL با دادهٔ نمونه کاملاً متوقف و دوباره راه‌اندازی شد. بررسی آمادگی و خواندن از `200` به `503` رفتند. پس از بازگشت PostgreSQL، هشت درخواست هم‌زمان `200` گرفتند و PostgreSQL فقط یک اتصال از همان فرایند Rust ثبت کرد. این آزمون مستقیم روی loopback انجام شد.

## برش‌های قبلی HTTP

nginx آزمایشی با include اختیاری فقط `GET` و `HEAD` روی دو نشانی دقیق واژه‌های هشدار و میزبان مجاز را به Rust روی loopback می‌فرستد. بقیهٔ روش‌ها و میزبان‌ها از مسیر uWSGI جنگو عبور می‌کنند. برداشتن `alert-words-routes.conf` و reload، خواندن را به Django برمی‌گرداند. خطای upstream با پاسخ JSON استاندارد nginx برمی‌گردد و درخواست در Django دوباره اجرا نمی‌شود. [پیکربندی آزمایشی](staging/nginx/alert-words-routes.conf)، [راهنمای اجرا و بازگشت](README.md#stage-alert-word-reads-in-nginx)

بررسی آمادگی Rust روی `/health/rust-alert-words` از loopback در دسترس است. پیکربندی نشست، امکان خواندن جدول‌ها در PostgreSQL، پاسخ Redis و وضعیت اتصال و کانال RabbitMQ را می‌سنجد. در Multipass، قطع Redis اختصاصی تست پاسخ را از `200` به `503` رساند و با وصل شدن Redis، پاسخ دوباره `200` شد. `/health` جنگو در این فاصله `200` باقی ماند. قطع PostgreSQL و RabbitMQ در این برش جداگانه آزمایش نشد.

با Rust خاموش و هدایت فعال، بررسی Rust و هر دو خواندن واژه‌ها `502` با بدنهٔ JSON گرفتند. هر دو `OPTIONS` همچنان `204` بودند. پس از برداشتن include و reload همان فرایند nginx، هر ۱۷ بررسی با Rust خاموش پاس شدند. همین مجموعه قبل از فعال‌سازی و با هدایت فعال هم پاس شد. [تست مسیریابی](tests/check_alert_words_proxy.py)

پروکسی توسعه با `--rust-http-port 9997 --rust-alert-words` فقط `GET` و `HEAD` دو مسیر `/api/v1/users/me/alert_words` و `/json/users/me/alert_words` را به Rust می‌فرستد. شرط میزبان تطابق سازمان در مسیریابی Django و Rust است. برای بازگشت، پروکسی را بدون `--rust-alert-words` دوباره راه‌اندازی کنید. `--rust-http-port` همچنان می‌تواند برای سازگاری موبایل فعال باشد. [پروکسی توسعه](../tools/run-dev)، [دستور اجرا](README.md#route-alert-word-reads-through-the-development-proxy)

در Multipass، ۱۷ بررسی HTTP در هر یک از سه حالت پاس شدند. حالت اول گزینهٔ فعال بود. در دو حالت بازگشت، گزینه حذف شد و آزمون یک بار با Rust روشن و یک بار پس از خاموش کردن Rust اجرا شد. هدر سهمیه مقصد خواندن را مشخص کرد. تست‌ها علاوه بر پاسخ‌های خواندن، `Allow` در `OPTIONS`، اثر `POST` و `DELETE` روی پایگاه داده، رد نوشتن بدون CSRF، حفظ `POST` دارای `method=GET` در Django و مسیر یا میزبان خارج از دامنهٔ هدایت را بررسی کردند. [تست پروکسی](tests/check_alert_words_proxy.py)

`GET /json/users/me/alert_words` و `HEAD` متناظر در اجرای مستقیم Rust فعال شده‌اند. Rust کوکی نشست Django را با رکورد منقضی‌نشدهٔ PostgreSQL، امضای رکورد، backend مجاز و هش وابسته به رمز عبور فعلی بررسی می‌کند. `GET` و `HEAD` مانند Django به توکن CSRF نیاز ندارند. کوکی CSRF خراب بازسازی می‌شود و پاسخ به `Cookie` وابسته می‌ماند. در مسیر نشست، بررسی سهمیه پیش از بررسی وضعیت حساب و ثبت فعالیت انجام می‌شود. [اعتبارسنجی نشست](src/http/session.rs)، [دستور اجرا](README.md#read-alert-words-with-a-django-session)

در Multipass، ۱۶ سناریوی نشست و خواندن از نظر status، بدنه و هدرهای انتخاب‌شده برابر بودند. نتیجهٔ بازسازی کوکی CSRF از نظر قالب توکن، ویژگی‌های کوکی و بدنه یکسان بود. برای `POST` و `DELETE` بدون توکن، Django پاسخ `403` و listener خواندنی Rust پاسخ `405` دادند و واژه‌ها تغییر نکردند. تست مشترک Redis و RabbitMQ نیز چهار درخواست نشست را به‌صورت متناوب اجرا کرد و فقط دو درخواست پذیرفته‌شده به ثبت فعالیت `website` انجامیدند. [تست HTTP](../zerver/tests/test_alert_words.py)

`GET /api/v1/users/me/alert_words` و `HEAD` متناظر در `zulip-http` از PostgreSQL برای Basic Auth و خواندن واژه‌های همان کاربر استفاده می‌کنند. با تنظیم Redis، Rust همان اسکریپت Lua و کلیدهای `api_by_user` جنگو را به‌کار می‌برد و قواعد پیش‌فرض، سهمیهٔ اختصاصی کاربر و مسدودی دستی را اعمال می‌کند. با تنظیم RabbitMQ، فعالیت پس از احراز هویت و پیش از بررسی سهمیه در صف یا shard فعلی Django ثبت می‌شود. درخواست احرازشدهٔ ردشده با `429` نیز پیام فعالیت دارد. [handler](src/http/user_reads.rs)، [سهمیه](src/http/rate_limit.rs)، [فعالیت](src/http/activity.rs) و [دستور اجرا](README.md#run-the-alert-word-read-api-over-http)

در برش سهمیهٔ مشترک، Django و Rust روی دو درگاه loopback از یک clone PostgreSQL، فضای کلید Redis و vhost اختصاصی RabbitMQ استفاده کردند. هدرهای سهمیهٔ هر دو سرویس، کاهش ظرفیت با جابه‌جایی درخواست بین سرویس‌ها، بدنهٔ `429` و `Retry-After` برای سقف و مسدودی دستی بررسی شدند. هشت پیام فعالیت به shard صحیح رسیدند و worker فعلی Django افزایش دقیق شمارنده‌های هر کاربر و کلاینت را تأیید کرد. دو درخواست بدون اعتبارنامه پیام فعالیت یا هدر سهمیه تولید نکردند. مقایسهٔ ۲۰ سناریوی قبلی احراز هویت و بدنه نیز دوباره پاس شد. [تست مقایسه](../zerver/tests/test_alert_words.py)

اجرای Rust در پروکسی توسعه و nginx آزمایشی اختیاری است. میزبان‌هایی که در Rust و Django به سازمان‌های متفاوت نگاشت می‌شوند، `POST`، `DELETE` و `OPTIONS` در Django می‌مانند. مسیر نشست Rust از کلید جاری و تنظیمات متعارف کوکی و ورود استفاده می‌کند. چرخش کلید و تمدید نشست را Django انجام می‌دهد. Rust برای هش نامعتبر کوکی مرورگر را پاک می‌کند و رکورد نشست را در Django بازنویسی یا حذف نمی‌کند. معافیت کلاینت داخلی Basic Auth، بازیابی زیرساخت و استقرار تولید همچنان برای یکپارچه‌سازی باقی‌اند. در خطای Redis یا RabbitMQ، Rust درخواست را با `503` رد می‌کند.

[موجودی مسیرهای HTTP](API_INVENTORY.md) ۱۷۱ مدخل OpenAPI را ثبت می‌کند. سه مدخل نمونهٔ مستندات‌اند و ۱۶۸ عملیات به handler ثبت‌شده نگاشت شده‌اند. موجودی همچنین ۳۹ عملیات خارج از OpenAPI زیر `/api/v1/`، ۱۳۴ مسیر `/json/` و ۹۶ مسیر webhook ورودی را ثبت می‌کند.

## درصد و معیار پیشرفت

| معیار                                                       | وضعیت در `c13cf5c2de`                                                         |
| ----------------------------------------------------------- | ----------------------------------------------------------------------------- |
| فایل‌های پایتون مبنا که پس از جایگزینی بازنشسته شده‌اند     | **۰ از ۲٬۰۶۲، معادل ۰٪**                                                      |
| عملیات OpenAPI با HTTP مستقل و تست مقایسه در توسعه          | **۱ از ۱۷۱ عملیات**، `GET /api/v1/users/me/alert_words`                       |
| عملیات OpenAPI منتقل‌شده به مسیر پیش‌فرض استقرار            | **۰ از ۱۷۱ عملیات**                                                           |
| مسیر کاربردی مستقل HTTP خارج از OpenAPI                     | **۲ مسیر**، `/compatibility` و `/json/users/me/alert_words` با `GET` و `HEAD` |
| مسیر بررسی آمادگی Rust                                      | `GET /health/rust-alert-words` و `HEAD` متناظر در محیط آزمایشی                |
| بخش‌هایی که Django بخشی از پردازش آن‌ها را به Rust می‌سپارد | **۴ بخش**                                                                     |

این درصد پیشرفتِ حذف وابستگی به فایل‌های پایتون را اندازه می‌گیرد و تخمینی از کار یا زمان باقی‌مانده نیست. شاخهٔ فعلی ۲٬۰۶۴ فایل `.py` دارد، چون دو تست Python برای مقایسه با Rust و بررسی پروکسی اضافه شده‌اند. از فایل‌های فعلی، ۹۶۹ مورد در مسیرهای `migrations/` هستند. دست‌کم ۷۰ فایل دیگر در `tools/` و `scripts/` با shebang پایتون اجرا می‌شوند و پسوند `.py` ندارند. شمارش OpenAPI نیز مسیر سازگاری موبایل، مسیرهای مستندنشده و همهٔ مسیرهای داخلی را پوشش نمی‌دهد.

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
| `df020668a6` | `GET /api/v1/users/me/alert_words` و `HEAD` متناظر در [handler واژه‌های هشدار](src/http/user_reads.rs)                            | Axum، Basic Auth و خواندن مستقیم PostgreSQL برای توسعه. پروکسی توسعه و استقرار عادی این مسیر را از Django سرو می‌کنند.                                                                                                                                                                                                    |
| `a7a23a0758` | محدودیت نرخ مشترک و ثبت فعالیت در مسیر خواندن واژه‌های هشدار                                                                       | اجرای مستقیم Rust به Redis و RabbitMQ وصل می‌شود. worker فعلی Django همان پیام‌های فعالیت را پردازش می‌کند.                                                                                                                                                                                                               |
| `de6911bc6e` | `GET /json/users/me/alert_words` و `HEAD` متناظر با نشست Django و رفتار CSRF روش‌های امن                                           | خواندن مستقیم و اختیاری در Rust. مدیریت نشست و روش‌های تغییردهنده در Django هستند.                                                                                                                                                                                                                                        |
| `e5ad17b2bf` | هدایت اختیاری روش‌های خواندنی واژه‌های هشدار در [پروکسی توسعه](../tools/run-dev)                                                   | هر دو مسیر با `--rust-alert-words` روی میزبان سازگار به Rust می‌روند. با حذف گزینه و راه‌اندازی دوباره، Django همهٔ روش‌ها را سرو می‌کند.                                                                                                                                                                                 |
| `fa4262e32a` | هدایت خواندن واژه‌ها در nginx آزمایشی و بررسی آمادگی Rust                                                                          | سه include اختیاری و reload برای بازگشت. پیکربندی معمول استقرار Django است.                                                                                                                                                                                                                                               |
| `c13cf5c2de` | بازیابی اتصال PostgreSQL برای [خواندن واژه‌ها](src/http/user_reads.rs) و بررسی آمادگی                                             | بعد از بسته شدن اتصال در زمان اجرا، درخواست بعدی می‌تواند بدون راه‌اندازی دوبارهٔ Rust اتصال را باز کند.                                                                                                                                                                                                                  |

پل Python در [python.rs](src/python.rs) و بسته‌بندی آن در [pyproject.toml](pyproject.toml) است. گزینهٔ Cargo به نام `http`، سرور `zulip-http` را بدون PyO3 فعال می‌کند. گزینهٔ `python` افزونهٔ `_zulip_backend` را می‌سازد. [تنظیمات CI فعلی](../.github/workflows/rust-backend.yml) تست‌های Rust، lint و مقایسهٔ رنگ کانال را اجرا می‌کند. تست‌های HTTP و Django در Multipass اجرا شده‌اند و در این workflow پوشش داده نمی‌شوند.

## شواهد تست‌ها

اجرای هر ردیف به محدوده و کامیت همان مرحله مربوط است. ردیف‌های تجمیعی شامل ردیف‌های جزئی هستند.

| تست                                                              | نسخهٔ Python                                         | نسخهٔ Rust                                                | شواهد و محدوده                                                                                                                                                                                                                                                                                        |
| ---------------------------------------------------------------- | ---------------------------------------------------- | --------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `zerver.tests.test_subs`                                         | ۱۳۳ پاس                                              | ۱۳۳ پاس                                                   | تست‌های موجود اشتراک و رنگ کانال در Multipass. لاگ‌های `zulip-test-python.log` و `zulip-test-rust.log`.                                                                                                                                                                                               |
| `test_alert_words` و `NormalActionsTest.test_alert_words_events` | ۱۴ پاس                                               | ۱۴ پاس                                                    | اضافه و حذف، پاسخ و ذخیره‌سازی، کش و رویداد؛ شامل کیس جدید Unicode و واژهٔ تکراری. لاگ‌های `zulip-alert-words-{0,1}.log`.                                                                                                                                                                             |
| وضعیت کاربر همراه با مجموعهٔ واژه‌های هشدار و رویداد آن          | ۱۷ پاس                                               | ۱۷ پاس                                                    | شامل کیس جدید API برای پاک‌کردن ایموجی و حفظ متن وضعیت. لاگ‌های `zulip-user-status-{0,1}.log`.                                                                                                                                                                                                        |
| `zerver.tests.test_embedded_bot_system`                          | ۱۸ پاس                                               | ۱۸ پاس                                                    | شامل سقف Unicode و اطمینان از تغییر نکردن داده پس از رد درخواست. لاگ‌های `zulip-bot-storage-{0,1}.log`.                                                                                                                                                                                               |
| چهار بخش داخلی به‌صورت تجمیعی                                    | ۱۶۸ پاس                                              | ۱۶۸ پاس                                                   | در `135c51f5c6` با هر چهار پرچم خاموش یا روشن. لاگ‌های `zulip-rust-compatibility-{0,1}.log`.                                                                                                                                                                                                          |
| `zerver.tests.test_compatibility`                                | ۸ پاس با Django                                      | ۸ پاس مستقیم و ۸ پاس از پشت پروکسی                        | از ۸ تست، ۴ تست به درخواست HTTP مربوط‌اند. مجموعه شامل ۶ تست قبلی و ۲ تست جدید برای `HEAD` و ترجمه است. لاگ‌های `zulip-compatibility-{django,rust,proxy}.log`.                                                                                                                                        |
| مقایسهٔ مستقیم پاسخ HTTP                                         | مبنای Django                                         | ۱۲۳ درخواست، ۰ اختلاف                                     | مقایسهٔ status، بدنه و هدرهای `Content-Type`، `Content-Language` و `Vary`. آزمون کمکی فعلاً در VM با نام `/home/ubuntu/zulip-rust-http-compare.py` است و در Git ثبت نشده است.                                                                                                                         |
| مقایسهٔ انتخاب رنگ                                               | مبنای Python                                         | ۵۰۲ ورودی برابر                                           | [تست مقایسه](tests/test_stream_color_parity.py) ترتیب نتیجه و تغییر نکردن ورودی‌ها را هم بررسی می‌کند.                                                                                                                                                                                                |
| `cargo test --locked --features http`                            | —                                                    | ۹ پاس                                                     | در بازبینی این سند روی `dd0b414ed1` دوباره اجرا شد. `cargo fmt --check` و Clippy با همهٔ featureها هم پاس شدند.                                                                                                                                                                                       |
| بررسی موجودی و مجموعهٔ واژه‌های هشدار                            | ۱۵ پاس                                               | ۱۴ پاس با پل PyO3                                         | روی `dd0b414ed1` در Multipass. اجرای Django شامل `OpenAPIArgumentsTest.test_openapi_arguments` و هر دو اجرا شامل تست‌های واژه‌ها و `test_alert_words_events` بودند. هر دو فرمان با کد خروج `0` تمام شدند. لاگ‌های `zulip-api-inventory-django.log` و `zulip-api-inventory-rust.log`.                  |
| مقایسهٔ HTTP واژه‌های هشدار در `df020668a6`                      | مبنای Django                                         | ۲۰ سناریو، ۰ اختلاف                                       | در `AlertWordsRustHTTPTest` به‌صورت HTTP واقعی. بدنهٔ بایتی، status، هدرهای محتوا، زبان، کش، چالش Basic و هدایت مقایسه شدند. اختلاف زمانی مجاز `Expires` پنج ثانیه است. مجموعهٔ هدفمند شامل این تست، واژه‌های هشدار، رویداد آن و سازگاری موبایل ۲۳ پاس داشت. لاگ `zulip-alert-words-http-parity.log`. |
| بازآزمایی Rust در `df020668a6`                                   | —                                                    | ۹ تست Cargo و ۸ تست سازگاری موبایل                        | `cargo test --locked --features http`، قالب‌بندی و Clippy با همهٔ featureها پاس شدند. سازگاری موبایل مستقیم روی Rust با تنظیمات قبلی و بدون PostgreSQL دوباره پاس شد. لاگ `zulip-alert-words-compatibility-regression.log`.                                                                           |
| سهمیهٔ مشترک و ثبت فعالیت در `a7a23a0758`                        | مبنای Django                                         | ۱۰ درخواست HTTP و ۸ پیام فعالیت                           | ظرفیت با درخواست‌های متناوب کم شد. تست، سقف، مسدودی دستی، سهمیهٔ اختصاصی، هدرهای `X-RateLimit-*` و `Retry-After` و افزایش دقیق شمارندهٔ worker را بررسی کرد. مقایسهٔ زمان هدرهای پویا حداکثر یک ثانیه اختلاف را می‌پذیرد. لاگ `zulip-alert-words-shared-integration.log`.                             |
| بازآزمایی `a7a23a0758`                                           | ۳۵ تست هدفمند                                        | ۹ تست Rust و ۹ تست سازگاری و worker                       | مجموعهٔ هدفمند شامل آزمون‌های HTTP، واژه‌های هشدار، رویداد، سازگاری و محدودیت نرخ بود. lint، قالب‌بندی و Clippy پاس شدند. لاگ‌های `zulip-alert-words-shared-regression.log` و `zulip-alert-words-shared-compatibility.log`.                                                                           |
| نشست و CSRF در `de6911bc6e`                                      | مبنای Django                                         | ۱۶ سناریوی برابر، یک بررسی کوکی CSRF و دو بررسی منع نوشتن | کل مجموعهٔ ۳۷ تست هدفمند پاس شد. تست سهمیه و فعالیت هر دو مسیر Basic Auth و نشست را پوشش داد. ۹ تست Cargo، lint، قالب‌بندی و Clippy نیز پاس شدند. لاگ‌های `zulip-alert-words-session-parity.log` و `zulip-alert-words-session-regression.log`.                                                        |
| مسیریابی و بازگشت پروکسی در `e5ad17b2bf`                         | ۱۷ بررسی با Rust روشن و ۱۷ بررسی پس از خاموش کردن آن | ۱۷ بررسی با گزینهٔ فعال                                   | ابزار بدون پارامتر پورت با کد خروج `2` خطای پیکربندی داد. lint و قالب‌بندی پاس شدند. لاگ‌ها `zulip-rust-proxy-check-on.log`، `zulip-rust-proxy-check-off.log` و `zulip-rust-proxy-check-offline.log`.                                                                                                 |
| nginx آزمایشی در `fa4262e32a`                                    | ۱۷ بررسی پایه و ۱۷ بررسی بعد از بازگشت               | ۱۷ بررسی با هدایت فعال                                    | nginx 1.24.0 و uWSGI 2.0.24 با clone مجزا در Multipass. `nginx -t` در هر حالت پاس شد. آمادگی `200 → 503 → 200` در خرابی Redis و `502` با Rust خاموش بررسی شد. قالب‌بندی Rust، ۹ تست Cargo، Clippy و lint پاس شدند. لاگ‌ها در `/home/ubuntu/zulip-rust-nginx-staging-logs/`.                           |
| بازیابی PostgreSQL در `c13cf5c2de`                               | —                                                    | ۳۷ تست هدفمند و ۹ تست Cargo پاس                           | PostgreSQL موقت در Multipass. آمادگی و خواندن `200 → 503 → 200` و یک اتصال پس از هشت درخواست هم‌زمان تأیید شد. هشت تست سازگاری روی listener مستقیم Rust، lint هدفمند Python، قالب‌بندی Rust و Clippy هم پاس شدند.                                                                                     |

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

برای تکرار مقایسهٔ واژه‌های هشدار، [فرمان ساخت و تست](README.md#run-the-alert-word-read-api-over-http) را اجرا کنید. تست خودش هر دو سرویس را روی درگاه آزاد راه می‌اندازد و نام و اعتبارنامهٔ clone موقت را از Django می‌گیرد. در Multipass ساخت `df020668a6` با `--offline` و vendor جدید در `/home/ubuntu/zulip-rust-alert-deps/vendor` پاس شد.

برای تست Redis و RabbitMQ، طبق [راهنمای اجرا](README.md#run-the-alert-word-read-api-over-http) یک vhost اختصاصی تنظیم کنید. در Multipass از `zulip-rust-test-alert-words` و vendor در `/home/ubuntu/zulip-rust-shared-deps/vendor` استفاده شد. اتصال Redis و RabbitMQ با متغیرهای محیط جداگانه فعال می‌شود.

برای اجرای تست نشست، [راهنمای نشست](README.md#read-alert-words-with-a-django-session) را دنبال کنید. تست خودش کلید آزمایشی را در فایل موقت می‌نویسد و نشست‌های commit‌شده را در clone تست می‌سازد. ساخت در Multipass با vendor در `/home/ubuntu/zulip-rust-session-deps/vendor` تأیید شد.

برای تکرار تست پروکسی از [راهنمای اجرا](README.md#route-alert-word-reads-through-the-development-proxy) استفاده کنید. Rust روی `9997` و پروکسی تست روی `9981` بود. برای شروع Django در VM، دایرکتوری محلی `var/uploads` که وجود نداشت ساخته شد. clone موقت بعد از آزمون‌ها حذف شد.

برای تست nginx از [راهنمای staging](README.md#stage-alert-word-reads-in-nginx) و `ZULIP_RUST_PROXY_PORT=8880` استفاده کنید. در Multipass، nginx و uWSGI جداگانه روی loopback اجرا شدند. Redis روی `6388`، vhost اختصاصی RabbitMQ و clone موقت PostgreSQL برای همین آزمایش بودند. پس از آزمون، سرویس‌های آزمایشی متوقف و clone و vhost حذف شدند.

برای تکرار تست بازیابی اتصال، باینری Rust را بسازید و طبق [راهنمای تست](README.md#run-the-alert-word-read-api-over-http) متغیر `ZULIP_RUST_HTTP_BIN` را تنظیم کنید. سپس `./tools/test-backend zerver.tests.test_alert_words.AlertWordsRustHTTPTest.test_postgresql_connection_recovery` را اجرا کنید. تست فقط اتصال دارای نام اختصاصی در clone همان تست را قطع می‌کند. اسکریپت آزمون توقف کامل PostgreSQL در VM در `/home/ubuntu/zulip-rust-postgresql-recovery-drill.py` است و از یک نمونهٔ موقت با socket جدا استفاده می‌کند.

لاگ‌های این مرحله `zulip-rust-postgresql-recovery-baseline.log`، `zulip-rust-postgresql-recovery-regression.log` و `zulip-rust-postgresql-recovery-drill.log` در `/home/ubuntu/` هستند.

اگر دانلود Cargo دوباره در VM مسدود شد، از همان `Cargo.lock` و فرمان `cargo vendor --manifest-path rust-backend/Cargo.toml --locked <vendor-dir>` روی میزبان استفاده کنید. محل کش موجود در VM در فرمان زیر آمده است. دایرکتوری vendor و تنظیمات مخصوص ماشین را محلی نگه دارید.

```bash
cargo build --manifest-path rust-backend/Cargo.toml --locked --offline \
    --features http --bin zulip-http \
    --config 'source.crates-io.replace-with="local-vendor"' \
    --config 'source.local-vendor.directory="/home/ubuntu/zulip-rust-http-deps/vendor"'
```

## کار باقی‌مانده و ترتیب پیشنهادی

1. **موجودی API و روش تحویل هر عملیات.** [فهرست پایه و ممیزی واژه‌های هشدار](API_INVENTORY.md) ثبت شده‌اند. هنگام انتخاب هر خانوادهٔ بعدی، مجوز، منبع داده، تغییرات پایگاه داده، کش، رویداد، تست مرجع و وضعیت استقرار را تکمیل کنید. موجودی مسیرهای SCIM، SSO و افزونه‌های اختصاصی استقرار نیز باقی است.
2. **تکمیل قرارداد مسیر خواندنی.** خواندن واژه‌های هشدار با Basic Auth یا نشست، سهمیه و فعالیت با Django مقایسه شده‌اند. هدایت و بازگشت در پروکسی توسعه و nginx آزمایشی تأیید شده‌اند. بسته‌بندی سرویس برای استقرار و تنظیمات میزبان، کوکی و ورود روی پیکربندی هدف باقی است. [قرارداد واژه‌های هشدار](API_INVENTORY.md#قرارداد-واژههای-هشدار)
3. **زیرساخت و استقرار مسیرها.** سیاست مشترک احراز هویت، مجوز، locale، خطای JSON، CORS/CSRF، محدودیت نرخ و ردیابی را برای مسیرهای بعدی از کد موجود استخراج کنید. بازیابی PostgreSQL پس از قطع اتصال در اجرای مستقیم تأیید شده است. بسته‌بندی سرویس Rust و Supervisor، تنظیمات staging مقصد، بازیابی RabbitMQ، تکرار آزمون PostgreSQL در staging مقصد و آزمون زیر بار باقی‌اند. [پیکربندی nginx](../puppet/zulip/files/nginx/zulip-include-frontend/app)
4. **سایر APIها و جریان‌های دارای اثر جانبی.** بعد از مسیرهای خواندنی، مسیرهای تغییردهندهٔ داده را منتقل کنید. ارسال و ویرایش پیام، کانال، عضویت، پیوست، جست‌وجو، ثبت رویداد و long polling، notification، webhook، worker و وظایف زمان‌دار هنوز باقی‌اند. در هر مرحله نتیجهٔ HTTP، تغییرات داده، ترتیب و محتوای رویداد، کش و رفتار retry را بررسی کنید. [معماری](../docs/overview/architecture-overview.md) و [سیستم رویداد](../docs/subsystems/events-system.md) نقطهٔ شروع‌اند.
5. **پایگاه داده، ابزارها و حذف Python.** نصب تازه و آپگرید از نسخه‌های پشتیبانی‌شده باید با مسیر جدید همان schema و دادهٔ قابل‌استفاده را بسازند. معادل مهاجرت‌های تاریخی Django، PGroonga و جست‌وجو را قبل از حذف فایل‌های مهاجرت تأیید کنید. دستورهای مدیریت، نصب، انتشار، پشتیبان‌گیری، واردات/صادرات، analytics، billing، rendering و تست‌رانر را هم در موجودی پروژه ثبت کنید. تست‌های مرجع را پس از انتقال به fixture، تست Rust یا تست پروتکل مستقل منتقل کنید تا در پایان امکان حذف Python وجود داشته باشد. [راهنمای مهاجرت پایگاه داده](../docs/subsystems/schema-migrations.md)

برای هر برش، ابتدا baseline همان تست‌ها را روی Python ثبت کنید. سپس مسیر Rust را برای همان درخواست و داده فعال کنید، مطمئن شوید درخواست واقعاً به Rust رسیده، خروجی و آثار جانبی را مقایسه کنید و وضعیت خروج تست‌رانر را ثبت کنید. اگر اختلافی پیدا شد، مشخص کنید که مربوط به انتقال، محیط تست یا رفتار موجود است. یک کامیت قابل‌بازبینی برای هر برش نگه دارید و این سند را با شناسهٔ کامیت، حالت اجرا، تعداد تست و نتیجه به‌روز کنید. دستورهای [AGENTS.md](../AGENTS.md) و [راهنمای طراحی API](../docs/processes/api-design.md) برای هر برش اعمال می‌شوند.
