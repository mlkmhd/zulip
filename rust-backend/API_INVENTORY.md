# موجودی مسیرهای HTTP برای مهاجرت به Rust

مبنای این فهرست `dd0b414ed1` است. روش و مسیر از [OpenAPI](../zerver/openapi/zulip.yaml)
و handler و alias از تنظیمات توسعهٔ
[Django](../zproject/urls.py)، [Tornado](../zproject/tornado_urls.py)
و [سرویس اعلان push](../zilencer/urls.py) استخراج شده‌اند. فعال بودن مسیرهای push
در استقرار به تنظیمات همان سرویس وابسته است.

- OpenAPI شامل ۱۷۱ عملیات در ۱۲۲ مدخل است. سه مدخل
  `/real-time`، `/rest-error-handling` و `/zulip-outgoing-webhook`
  نمونه‌های مستندات هستند. ۱۶۸ عملیات دیگر به handler ثبت‌شده نگاشت شدند.
  [آزمون تطبیق OpenAPI و مسیریابی](../zerver/tests/test_openapi.py)
- در جدول‌های مسیریابی، روش‌های صریح و روش مصرف‌شدهٔ مسیرهای مستقیم آمده‌اند.
  در `rest_dispatch`، `GET` به‌طور ضمنی `HEAD` دارد، مگر اینکه جداگانه
  تعریف شده باشد. `OPTIONS` پیش از احراز هویت با `204` و هدر `Allow`
  پاسخ داده می‌شود. روش نامعتبر پاسخ `405` می‌گیرد.
  [REST dispatcher](../zerver/lib/rest.py)
- ستون «مسیریابی» اجرای پیش‌فرض هر عملیات OpenAPI را نشان می‌دهد. ادغام‌های
  جزئی PyO3 و اجرای توسعهٔ `/compatibility` در بخش وضعیت مهاجرت آمده‌اند.
  ممیزی وابستگی برای واژه‌های هشدار و خواندن متن‌های ذخیره‌شده
  در بخش‌های قرارداد آمده است.
- صفحات HTML، مسیرهای locale، افزونه‌های اختصاصی هر استقرار، SCIM و callbackهای
  SSO همچنان برای موجودی بعدی باقی‌اند.

## وضعیت مهاجرت

| عملیات یا جریان                                                                         | منطق اختیاری Rust                                                      | مسیر HTTP در توسعه و staging                                    | مسیر پیش‌فرض استقرار |
| --------------------------------------------------------------------------------------- | ---------------------------------------------------------------------- | --------------------------------------------------------------- | -------------------- |
| `GET /api/v1/users/me/alert_words` و `HEAD` متناظر                                      | Basic Auth، خواندن PostgreSQL، سهمیهٔ مشترک Redis و صف فعالیت RabbitMQ | Rust مستقیم، پروکسی توسعه یا nginx آزمایشی                      | Django               |
| `GET /json/users/me/alert_words` و `HEAD` متناظر                                        | نشست امضاشدهٔ Django، CSRF روش‌های امن، سهمیه و فعالیت                 | Rust مستقیم، پروکسی توسعه یا nginx آزمایشی با تنظیم نشست        | Django               |
| `GET /api/v1/saved_snippets` و `HEAD` متناظر                                            | Basic Auth، خواندن PostgreSQL، سهمیهٔ Redis و صف فعالیت RabbitMQ       | Rust مستقیم، پروکسی توسعه یا nginx آزمایشی                      | Django               |
| `GET /json/saved_snippets` و `HEAD` متناظر                                              | نشست Django، رفتار CSRF روش‌های امن، سهمیه و فعالیت                    | Rust مستقیم، پروکسی توسعه یا nginx آزمایشی با تنظیم نشست        | Django               |
| `POST /json/saved_snippets` | نشست Django، اعتبارسنجی CSRF، ثبت متن و audit، اعلان پس از commit | Rust مستقیم، پروکسی توسعه یا nginx آزمایشی با تنظیم نشست و پروتکل | Django |
| `PATCH /json/saved_snippets/{saved_snippet_id}` | نشست Django، CSRF از هدر، ویرایش با شرط مالکیت و اعلان پس از commit | Rust مستقیم، پروکسی توسعه یا nginx آزمایشی با تنظیم نشست و پروتکل | Django |
| `DELETE /json/saved_snippets/{saved_snippet_id}` | نشست Django، CSRF از هدر، حذف با شرط مالکیت و اعلان پس از commit | Rust مستقیم، پروکسی توسعه یا nginx آزمایشی با تنظیم نشست و پروتکل | Django |
| `POST /api/v1/saved_snippets`                                                           | ثبت متن و audit، اعلان پس از commit                                    | Rust مستقیم، پروکسی توسعه یا nginx آزمایشی                      | Django               |
| `PATCH /api/v1/saved_snippets/{saved_snippet_id}`                                       | ویرایش با شرط مالکیت، اعلان پس از commit                               | Rust مستقیم، پروکسی توسعه یا nginx آزمایشی                      | Django               |
| `DELETE /api/v1/saved_snippets/{saved_snippet_id}`                                      | حذف با شرط مالکیت، اعلان پس از commit                                  | Rust مستقیم، پروکسی توسعه یا nginx آزمایشی                      | Django               |
| `POST /api/v1/users/me/alert_words` و alias وب                                          | اعتبارسنجی و پاک‌سازی، درج گروهی، پاک‌سازی کش و رویداد پس از commit    | Rust مستقیم یا هدایت اختیاری در پروکسی توسعه و nginx آزمایشی    | Django               |
| `DELETE /api/v1/users/me/alert_words` و alias وب                                        | تراکنش PostgreSQL، پاک‌سازی Memcached و رویداد پس از commit             | Rust مستقیم یا هدایت اختیاری در پروکسی توسعه و nginx آزمایشی    | Django               |
| `GET /api/v1/drafts` و alias وب با `HEAD` متناظر                                        | شرط همگام‌سازی حساب، خواندن PostgreSQL و ارائهٔ گیرنده                | Rust مستقیم یا هدایت اختیاری در پروکسی توسعه و nginx آزمایشی    | Django               |
| `DELETE /api/v1/drafts/{draft_id}` و alias وب                                          | حذف محدود به مالک و رویداد بعد از commit                              | Rust مستقیم یا هدایت اختیاری در پروکسی توسعه و nginx آزمایشی    | Django               |
| `POST /api/v1/drafts` و alias وب | درج گروهی با `ZULIP_RUST_DRAFT_CREATION` و عادی‌سازی متن و عنوان با `ZULIP_RUST_DRAFT_TEXT` | Django با افزونهٔ اختیاری Rust | Django |
| `PATCH /api/v1/drafts/{draft_id}` و alias وب | عادی‌سازی متن و عنوان با `ZULIP_RUST_DRAFT_TEXT` و ثبت ویرایش با `ZULIP_RUST_DRAFT_EDIT` | Django با افزونهٔ اختیاری Rust | Django |
| `GET /api/v1/realm/linkifiers` و alias وب با `HEAD` متناظر                              | خواندن لینک‌سازهای سازمان با ترتیب اولویت از PostgreSQL              | Rust مستقیم یا هدایت اختیاری در پروکسی توسعه و nginx آزمایشی    | Django               |
| `GET /api/v1/realm/domains` و alias وب با `HEAD` متناظر                                 | خواندن دامنه‌های مجاز ایمیل سازمان از PostgreSQL                     | Rust مستقیم یا هدایت اختیاری در پروکسی توسعه و nginx آزمایشی    | Django               |
| `GET /api/v1/realm/profile_fields` و alias وب با `HEAD` متناظر | تعاریف مرتب‌شدهٔ فیلدهای پروفایل سازمان از PostgreSQL | Rust مستقیم یا هدایت اختیاری در پروکسی توسعه و nginx آزمایشی | Django |
| `GET /api/v1/navigation_views` و alias وب با `HEAD` متناظر | تنظیمات شخصی نماهای ناوبری از PostgreSQL | Rust مستقیم یا هدایت اختیاری در پروکسی توسعه و nginx آزمایشی | Django |
| `POST /api/v1/navigation_views` و alias وب | ثبت نما و ممیزی در PostgreSQL و رویداد مالک | Rust مستقیم یا هدایت اختیاری در پروکسی توسعه و nginx آزمایشی | Django |
| `PATCH` و `DELETE /api/v1/navigation_views/{fragment}` و alias وب | ویرایش یا حذف نما، ممیزی و رویداد مالک | Rust مستقیم یا هدایت اختیاری در پروکسی توسعه و nginx آزمایشی | Django |
| `GET /api/v1/attachments` و alias وب با `HEAD` متناظر | فایل‌های مالک، پیام‌های مرتبط و مصرف فضای سازمان | Rust مستقیم یا هدایت اختیاری در پروکسی توسعه و nginx آزمایشی | Django |
| `GET /api/v1/realm/emoji` و alias وب با `HEAD` متناظر | ایموجی سازمان و نشانی عمومی فایل و تصویر ثابت | Rust مستقیم با تنظیم ریشهٔ نشانی یا هدایت اختیاری در پروکسی توسعه و nginx آزمایشی | Django |
| `GET /api/v1/channel_folders` و alias وب با `HEAD` متناظر | پوشه‌های سازمان با فیلتر بایگانی و ترتیب نمایش از PostgreSQL | Rust مستقیم یا هدایت اختیاری در پروکسی توسعه و nginx آزمایشی | Django |
| `GET /api/v1/scheduled_messages` و alias وب با `HEAD` متناظر | پیام‌های زمان‌بندی‌شدهٔ ارسال‌نشدهٔ کاربر از PostgreSQL | Rust مستقیم یا هدایت اختیاری در پروکسی توسعه و nginx آزمایشی | Django |
| `GET /api/v1/reminders` و alias وب با `HEAD` متناظر | یادآوری‌های تحویل‌نشدهٔ کاربر از PostgreSQL | Rust مستقیم یا هدایت اختیاری در پروکسی توسعه و nginx آزمایشی | Django |
| `DELETE /api/v1/scheduled_messages/{scheduled_message_id}` و alias وب | حذف پیام زمان‌بندی‌شدهٔ مالک و پیوندهای پیوست در یک تراکنش | Rust مستقیم یا هدایت اختیاری در پروکسی توسعه و nginx آزمایشی | Django |
| `DELETE /api/v1/reminders/{reminder_id}` و alias وب | حذف یادآوری مالک و پیوندهای پیوست در یک تراکنش | Rust مستقیم یا هدایت اختیاری در پروکسی توسعه و nginx آزمایشی | Django |
| `POST /api/v1/users/me/status`، `GET/POST /api/v1/users/{user_id}/status` و aliasهای وب | ساخت بخشی از پاسخ و قواعد به‌روزرسانی با `ZULIP_RUST_USER_STATUS`      | Django                                                          | Django               |
| `POST /api/v1/register` و alias وب                                                      | قالب‌بندی وضعیت کاربران در snapshot با `ZULIP_RUST_USER_STATUS`        | Django                                                          | Django               |
| `GET /api/v1/bot_storage` و alias وب با `HEAD` متناظر | خواندن همهٔ کلیدها یا کلیدهای درخواست‌شده برای ربات احرازشده | Rust مستقیم یا هدایت اختیاری در پروکسی توسعه و nginx آزمایشی | Django |
| `DELETE /api/v1/bot_storage` و alias وب | حذف اتمیک کلیدهای ربات احرازشده با `ZULIP_RUST_BOT_STORAGE_DELETES` | Rust مستقیم یا هدایت اختیاری در پروکسی توسعه و nginx آزمایشی | Django |
| `PUT /api/v1/bot_storage` و alias وب | درج یا به‌روزرسانی گروهی با سهمیهٔ اتمیک و پرچم `ZULIP_RUST_BOT_STORAGE_UPDATES` | Rust مستقیم یا هدایت اختیاری در پروکسی توسعه و nginx آزمایشی | Django |
| `POST /api/v1/users/me/subscriptions` و `POST /api/v1/channels/create` و aliasهای وب    | انتخاب رنگ هنگام ایجاد اشتراک با `ZULIP_RUST_STREAM_COLOR`             | Django                                                          | Django               |
| `GET /compatibility` و `HEAD /compatibility`                                            | handler مستقل HTTP                                                     | Rust مستقیم یا از پروکسی با `--rust-http-port` برای میزبان اصلی | Django               |

پرچم‌ها با مقدار `1` فعال‌اند. این جدول مسیرهای مرتبط با HTTP را ثبت می‌کند.
برای محل پیاده‌سازی و تست‌های هر مؤلفه به [مستند تحویل](MIGRATION_HANDOFF.md) رجوع کنید.

## قرارداد ایجاد پیش‌نویس

`POST /api/v1/drafts` از Basic Auth و `POST /json/drafts` از نشست و CSRF
استفاده می‌کند. Django تنظیم همگام‌سازی، شکل ورودی گروهی، timestamp و
دسترسی به گیرندهٔ کانال یا پیام مستقیم را بررسی می‌کند. در حالت پرچم
`ZULIP_RUST_DRAFT_CREATION=1`، Rust پیش‌نویس‌های بررسی‌شده را با cursor
همان تراکنش در `zerver_draft` درج می‌کند و شناسه‌ها را به ترتیب درخواست
برمی‌گرداند. Django رویداد `drafts/add` را پس از commit فقط برای مالک
ارسال می‌کند. فهرست خالی باعث درج سطر نمی‌شود و رویداد خالی مبنا را حفظ
می‌کند. تست‌های ثبت، بازگشت تراکنش و رویداد در
[test_drafts.py](../zerver/tests/test_drafts.py) و
[DraftActionTest](../zerver/tests/test_events.py) هستند.

در ایجاد و ویرایش، `ZULIP_RUST_DRAFT_GROUPS=1` ثبت `Recipient` و عضویت‌های
گروه تازهٔ پیام مستقیم را به Rust می‌سپارد. Django دسترسی را پیش از
جست‌وجو یا ایجاد سطر گروه بررسی می‌کند. Rust روی همان cursor تراکنش،
گیرنده را به گروه وصل می‌کند و عضویت‌های متمایز، شامل فرستنده، را می‌نویسد.
تراکنش ساخت گروه پیش از تراکنش درج پیش‌نویس است. اگر پس از ساخت گروه،
اعتبارسنجی یکی از پیش‌نویس‌های بعدی شکست بخورد، گروه باقی می‌ماند و سطر
یا رویداد پیش‌نویس ایجاد نمی‌شود.

## قرارداد ویرایش پیش‌نویس

`PATCH` ابتدا همگام‌سازی، مالکیت پیش‌نویس و دسترسی به گیرندهٔ جدید را
در Django بررسی می‌کند. با `ZULIP_RUST_DRAFT_EDIT=1`، به‌روزرسانی متن،
عنوان، گیرنده و زمان در Rust با شرط شناسه و مالک انجام می‌شود. Django
از cursor همان تراکنش استفاده می‌کند و `drafts/update` را فقط پس از
commit به مالک می‌فرستد. اگر ساخت رویداد خطا بدهد، ویرایش لغو می‌شود.
[تست ویرایش](../zerver/tests/test_drafts.py) و
[فعال‌سازی](README.md#run-the-compatibility-checks)

## قرارداد فضای ذخیره‌سازی ربات

`GET`، `DELETE` و `PUT` برای `/api/v1/bot_storage` و `/json/bot_storage` فقط برای
ربات احرازشده اجرا می‌شوند. احراز هویت API با Basic Auth و احراز هویت وب
با نشست Django است. حذف و نوشتن وب CSRF را بررسی می‌کنند. هر سه عملیات
سهمیهٔ `api_by_user` و ثبت فعالیت `get_storage`، `remove_storage` یا
`update_storage` را دارند.

- خواندن رکوردهای `zerver_botstoragedata` را به مالک محدود می‌کند.
  حذف پارامتر `keys` یا مقدار JSON `null` به معنی خواندن همهٔ کلیدهاست.
  تکرار کلید در خواندن یک مقدار برمی‌گرداند. کلید ناموجود پاسخ `400` دارد.
- حذف بدون `keys` یا با `null` همهٔ رکوردهای مالک را پاک می‌کند. برای
  فهرست صریح، Rust سطرهای منطبق را قفل و حذف می‌کند، به شرطی که همهٔ
  کلیدها موجود و متمایز باشند. فهرست خالی اثری روی داده ندارد. حذف
  کش یا رویداد همگام‌سازی ندارد.
- `PUT` دیکشنری `storage` با کلید و مقدار رشته‌ای را می‌گیرد. اندازهٔ
  نهایی فضای ربات برحسب نویسه‌های Unicode بعد از جایگزینی محاسبه می‌شود.
  بررسی سهمیه و upsert گروهی در یک تراکنش‌اند. Django و Rust برای نوشتن
  و حذف از قفل تراکنشی PostgreSQL برای همان ربات استفاده می‌کنند. این
  عملیات کش یا رویداد همگام‌سازی ندارد. تغییر مسیر پروکسی فقط روی `PUT`
  مستقیم اعمال می‌شود.

[تست مقایسه](../zerver/tests/test_embedded_bot_system.py)،
[فعال‌سازی و بازگشت](README.md#read-bot-storage-through-rust)

## قرارداد واژه‌های هشدار

`GET`، `POST` و `DELETE` برای هر دو مسیر `/api/v1/users/me/alert_words`
و `/json/users/me/alert_words` به view مشترک می‌رسند. API از Basic Auth
با ایمیل و کلید API و alias وب از نشست Django استفاده می‌کند. درخواست وب
مشمول CSRF است. هر سه عملیات به کاربر احرازشده محدودند. اعتبار حساب، سازمان
و میزبان بررسی می‌شود و ربات webhook ورودی مجوز این عملیات را ندارد.
هر دو مسیر از محدودیت نرخ `api_by_user` استفاده می‌کنند. احراز هویت،
ثبت فعالیت کلاینت و محدودیت نرخ هم جزو رفتار مسیر خواندنی‌اند.
[مسیریابی REST](../zerver/lib/rest.py)، [احراز هویت و کلاینت](../zerver/decorator.py)،
[محدودیت نرخ](../zerver/lib/rate_limiter.py)

| عملیات         | داده و تغییرات پایگاه داده                                                                                                                             | کش و رویداد                                                                                                          | تست مرجع                                                                                                                |
| -------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------ | -------------------------------------------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------- |
| `GET` و `HEAD` | خواندن `word` از `zerver_alertword` با شناسهٔ کاربر احرازشده. پاسخ `GET` آرایهٔ `alert_words` را با املای ذخیره‌شده و بدون مرتب‌سازی صریح برمی‌گرداند. | خود view کش واژه‌های سازمان را استفاده نمی‌کند و رویداد واژهٔ هشدار منتشر نمی‌کند.                                   | `test_json_list_default` و `test_json_list_nonempty`                                                                    |
| `POST`         | اعتبارسنجی طول حداکثر ۱۰۰ نویسه پیش از پاک‌سازی، حذف فاصله‌های دو سر و رشته‌های خالی، حذف تکرار بدون حساسیت به بزرگی حروف و درج گروهی واژه‌های جدید.   | پاک‌کردن کش فهرست واژه‌ها و automaton تطبیق سازمان. رویداد `alert_words` بعد از commit برای همان کاربر ارسال می‌شود. | `test_json_list_add`، `test_api_add_normalizes_and_deduplicates_alert_words`، `test_basics` و `test_alert_words_events` |
| `DELETE`       | مقایسهٔ ورودی lowercase پایتون با `Lower("word")` در PostgreSQL و حذف گروهی رکوردهای همان کاربر.                                                       | پاک‌کردن همان دو کش و ارسال رویداد `alert_words` برای همان کاربر بعد از commit.                                      | `test_json_list_remove`، `test_remove_words_bulk_case_insensitive`، `test_basics` و `test_alert_words_events`           |

منابع قرارداد و تست‌ها
[view](../zerver/views/alert_words.py)، [دسترسی داده](../zerver/lib/alert_words.py)،
[مدل و کش](../zerver/models/alert_words.py)، [تراکنش و رویداد](../zerver/actions/alert_words.py)،
[تست واژه‌ها](../zerver/tests/test_alert_words.py) و [تست رویدادها](../zerver/tests/test_events.py).

با `ZULIP_RUST_ALERT_WORDS=1`، Rust کوئری پارامتری حذف را روی cursor
تراکنش Django اجرا می‌کند. ورودی با `str.lower()` پایتون و مقدار ذخیره‌شده با
`lower` PostgreSQL مقایسه می‌شود. Django دو کش سازمان را با همان سازوکار
فضای نام استقرار پاک می‌کند و انتشار رویداد را به commit موکول می‌کند.
درخواست با فهرست خالی همچنان پاسخ و رویداد فهرست فعلی را می‌دهد.
[راهنمای پرچم](README.md)، [تست حذف](../zerver/tests/test_alert_words.py)

با `ZULIP_RUST_ALERT_WORD_CACHE=1`، Django پیشوندهای فعال و کلید نهایی
هر دو کش را محاسبه می‌کند و Rust آن‌ها را از Memcached محلی حذف می‌کند.
این پرچم مستقل از حذف گروهی است و برای یک Memcached محلی بدون احراز هویت
فعال می‌شود. خطای اتصال یا پاسخ نامعتبر به تراکنش برمی‌گردد.
[راهنمای پرچم کش](README.md#invalidate-alert-word-caches-through-rust)

فرمان `export_rust_alert_word_cache` از تنظیمات Django پیشوندهای فیزیکی
و آدرس Memcached را در یک فایل منتشر می‌کند. در صورت تنظیم
`ZULIP_RUST_ALERT_WORD_CACHE_MANIFEST`، بررسی آمادگی Rust فایل جاری و
اتصال Memcached را اعتبارسنجی می‌کند.
[راهنمای انتشار فایل](README.md#share-djangos-alert-word-cache-namespace-with-zulip-http)

با `ZULIP_RUST_ALERT_WORD_LOWERCASE_FILE`، سرویس مستقیم Rust آرایهٔ JSON رشته‌ها
را از فرم یا query می‌گیرد و با قواعد صادرشده از Python همان استقرار کوچک
می‌کند. حذف و پاک‌سازی کش در تراکنش اختصاصی انجام می‌شوند و پس از commit
رویداد `alert_words` فقط برای همان کاربر به صف `notify_tornado` می‌رود.
نسخهٔ وب از نشست و هدر `X-CSRFToken` استفاده می‌کند. در استقرار چندفرایندی،
فایل مسیر صف را مطابق تقسیم‌بندی Django مشخص می‌کند. هدایت اختیاری و بازگشت
مستقل حذف در پروکسی توسعه و nginx آزمایشی تأیید شدند. Tornado در آزمون
ایزوله رویداد را فقط به صاحب واژه تحویل داد.
[تنظیم و تست مسیر مستقیم](README.md#delete-alert-words-through-rust)

با `ZULIP_RUST_ALERT_WORD_POSTS=1`، Rust مسیر افزودن API و وب را هم سرو می‌کند.
این مسیر پیش از trim طول ورودی را می‌سنجد، تکرارها را با قواعد Python حذف
می‌کند و پس از درج، همان کش و رویداد `alert_words` را به‌روزرسانی می‌کند.
در وب، توکن فرم POST بر هدر مقدم است. بازگشت مستقل هدایت POST در پروکسی‌ها
و تحویل رویداد در Tornado آزمایشی تأیید شده‌اند.
[تنظیم و تست POST](README.md#add-alert-words-through-rust)

تست HTTP با دادهٔ commit‌شده در Django و Rust، رفتار فهرست خالی، واژهٔ Unicode،
کلید نامعتبر، سازمان نادرست، حساب غیرفعال و ربات webhook را برای مسیر API
بررسی کرد. در حالت بدون اعتبارنامه،
`Accept` بر انتخاب پاسخ JSON یا هدایت به صفحهٔ ورود اثر دارد و هدر
`WWW-Authenticate` پاسخ JSON با پیشوند مسیر عوض می‌شود. تستِ سهمیه در Redis مشترک و ثبت فعالیت در RabbitMQ برای مسیر API پاس شد.
تست نشست وب، اعتبار امضا و هش نشست، هدرهای کوکی و رفتار CSRF برای `GET` و `HEAD` پاس شد. `OPTIONS` در Django است.
[نتیجهٔ مقایسهٔ HTTP](MIGRATION_HANDOFF.md#برشهای-قبلی-http)
[خطاهای API](../api_docs/rest-error-handling.md)، [middleware](../zerver/middleware.py)

## قرارداد پیش‌نویس‌ها

`GET /api/v1/drafts` و `GET /json/drafts` فقط پیش‌نویس‌های کاربر احرازشده را
از `zerver_draft` برمی‌گردانند. خاموش بودن همگام‌سازی پاسخ `400` دارد.
`count` از تعداد پیش‌نویس‌های برگشتی تشکیل می‌شود. ترتیب SQL Django صعودی
برحسب زمان آخرین ویرایش است. کانال با شناسهٔ کانال و پیام مستقیم با شناسهٔ
عضوها برمی‌گردد. گیرندهٔ ناموجود به `type: ""` و `to: []` تبدیل می‌شود.
`HEAD` هدرها را بدون بدنه برمی‌گرداند. خواندن، رویداد پیش‌نویس منتشر نمی‌کند.
خواندن و حذف با هدایت اختیاری و بازگشت در پروکسی توسعه و nginx آزمایشی
تأیید شده‌اند. `DELETE` پس از بررسی همگام‌سازی فقط ردیف مالک را حذف می‌کند
و `drafts/remove` را پس از commit می‌فرستد. `POST` و `PATCH` در Django هستند.
[view](../zerver/views/drafts.py)، [پیاده‌سازی Rust](src/http/drafts.rs)،
[تست مقایسه](../zerver/tests/test_drafts.py)

## قرارداد لینک‌سازهای سازمان

`GET /api/v1/realm/linkifiers` و `GET /json/realm/linkifiers` لینک‌سازهای
سازمان حساب احرازشده را به ترتیب فیلد `order` برمی‌گردانند. Rust
`zerver_realmfilter` را مستقیم می‌خواند و قالب‌های جایگزین و فیلدهای
اختیاری را حفظ می‌کند. سهمیه و فعالیت از پیکربندی مشترک استفاده می‌کنند.
ایجاد، ویرایش، تغییر ترتیب و حذف لینک‌ساز در Django انجام می‌شوند.
[پیاده‌سازی](src/http/linkifiers.rs)، [تست مقایسه](../zerver/tests/test_realm_linkifiers.py)

## قرارداد دامنه‌های مجاز سازمان

`GET /api/v1/realm/domains` و `GET /json/realm/domains` مقدارهای `domain`
و `allow_subdomains` را برای سازمان حساب احرازشده برمی‌گردانند. Rust
`zerver_realmdomain` را از PostgreSQL می‌خواند. مانند کوئری Django، ترتیب
صریحی تعریف نشده است. سهمیه و فعالیت از پیکربندی مشترک استفاده می‌کنند.
عملیات نوشتن در Django انجام می‌شوند.
[پیاده‌سازی](src/http/realm_domains.rs)، [تست مقایسه](../zerver/tests/test_realm_domains.py)

## قرارداد فیلدهای سفارشی پروفایل سازمان

`GET /api/v1/realm/profile_fields` و `GET /json/realm/profile_fields`
تعاریف سازمان حساب احرازشده را بر اساس `order` از
`zerver_customprofilefield` برمی‌گردانند. `field_data` رشتهٔ ذخیره‌شده
است و دو کلید `display_in_profile_summary` و `use_for_user_matching`
فقط در حالت true می‌آیند. مقادیر شخصی در جدول جداگانه نگه‌داری می‌شوند.
[پیاده‌سازی](src/http/custom_profile_fields.rs)،
[تست مقایسه](../zerver/tests/test_custom_profile_data.py)

## قرارداد نماهای ناوبری

`GET /api/v1/navigation_views` و `GET /json/navigation_views` بر اساس
شناسهٔ کاربر احرازشده از `zerver_navigationview` می‌خوانند. هر نما
`fragment`، `is_pinned` و `name` دارد. نام نمای داخلی می‌تواند
`null` باشد. ترتیب صریحی در قرارداد Django تعریف نشده است.
[پیاده‌سازی](src/http/navigation_views.rs)،
[تست مقایسه](../zerver/tests/test_navigation_views.py)

ایجاد نما در `POST /api/v1/navigation_views` و مسیر وب به‌صورت اختیاری
در Rust اجرا می‌شود. `fragment` و نام کوتاه‌شده از فاصله‌های ابتدا و انتها پس از
اعتبارسنجی ذخیره می‌شوند. نمای داخلی نام سفارشی ندارد. ثبت نما و audit
با نوع `NAVIGATION_VIEW_CREATED` و دادهٔ `fragment` در یک تراکنش انجام
می‌شوند. پس از commit رویداد `navigation_view/add` فقط به مالک می‌رسد.
قید پایگاه داده تکرار `(user, fragment)` را محدود می‌کند. Rust نوشتن هم‌زمان برای یک کاربر را پیش از بررسی نام تکراری قفل می‌کند.
Django نام تکراری را در view بررسی می‌کند و رقابت بین دو پیاده‌سازی
با محدودیت یکتایی نام در پایگاه داده پوشش داده نمی‌شود.
[view](../zerver/views/navigation_views.py)،
[تراکنش و رویداد](../zerver/actions/navigation_views.py)

`PATCH` فیلدهای اختیاری `name` و `is_pinned` را می‌پذیرد و برای هر
فیلد ارسال‌شده یک audit با مقدار قبلی و جدید ثبت می‌کند. درخواست
خالی رویداد `update` با `data` خالی می‌فرستد. `DELETE` نما و audit
را در یک تراکنش تغییر می‌دهد و سپس رویداد `remove` می‌فرستد. هر دو
روش فقط به fragment مالک دسترسی دارند. رقابت دو حذف از دو فرایند Rust
فقط یک audit و یک رویداد ایجاد کرد. هدایت مسیر جزئیات مستقل از مسیر
فهرست است.
[راهنمای مسیرهای جزئیات](README.md#edit-and-delete-navigation-views-through-rust)

## قرارداد فایل‌های آپلودشده

`GET /api/v1/attachments` و `GET /json/attachments` فایل‌های مالک
و `message_ids` مرتبط را برمی‌گردانند. مصرف فضای سازمان از آخرین
`upload_quota_used_bytes::day` بدون زیرگروه و مجموع اندازهٔ فایل‌های
پس از مرز آن، با احتساب خود مرز، محاسبه می‌شود. بدون snapshot،
مجموع اندازهٔ همهٔ فایل‌های سازمان استفاده می‌شود. Rust خواندن را
از یک snapshot در PostgreSQL انجام می‌دهد.
[پیاده‌سازی](src/http/attachments.rs)،
[تست مقایسه](../zerver/tests/test_attachments.py)

## قرارداد ایموجی‌های سفارشی

`GET /api/v1/realm/emoji` و `GET /json/realm/emoji` فقط ایموجی‌های
سازمان حساب احرازشده با `file_name` ثبت‌شده را نمایش می‌دهند. پاسخ هم
رکورد فعال و هم غیرفعال را شامل می‌شود. نشانی دانلود و تصویر ثابت
از ریشهٔ عمومی ذخیره‌سازی Django ساخته می‌شود. هر دو حالت ذخیره‌سازی
محلی و S3 با CDN در آزمون مقایسه پوشش داده شدند.
[پیاده‌سازی](src/http/realm_emoji.rs)،
[تست مقایسه](../zerver/tests/test_realm_emoji.py)

## قرارداد پوشه‌های کانال

`GET /api/v1/channel_folders` و `GET /json/channel_folders` پوشه‌های
سازمان حساب احرازشده را از `zerver_channelfolder` بر اساس `order`
برمی‌گردانند. `include_archived` به‌طور پیش‌فرض false است و فقط یک
مقدار بولی JSON می‌پذیرد. آخرین مقدار پارامتر تکراری اعمال می‌شود.
`ignored_parameters_unsupported` و خطاهای نوع و نحو JSON با Django
مقایسه شده‌اند. پاسخ HTML ذخیره‌شدهٔ توضیح، شناسهٔ اختیاری سازنده و
ثانیهٔ زمان ایجاد را حفظ می‌کند.
[پیاده‌سازی](src/http/channel_folders.rs)،
[تست مقایسه](../zerver/tests/test_channel_folders.py)

## قرارداد پیام‌های زمان‌بندی‌شده و یادآوری‌ها

`GET /api/v1/scheduled_messages` و `GET /api/v1/reminders` و مسیرهای
متناظر وب فقط رکوردهای تحویل‌نشدهٔ نوع درخواست‌شده را برای کاربر و
سازمان احرازشده از `zerver_scheduledmessage` بر اساس زمان ارسال
برمی‌گردانند. موارد ناموفق در فهرست می‌مانند. پیام کانال شناسهٔ کانال
و عنوان دارد. پیام مستقیم فهرست گیرنده‌ها را از جدول عضویت می‌سازد و
در پیام به خود، شناسهٔ مالک را نگه می‌دارد. یادآوری شناسهٔ پیام مرجع
را برمی‌گرداند.

`DELETE` در هر دو خانواده فقط ردیف هم‌نوع متعلق به فرستنده را حذف
می‌کند و برای شناسهٔ ناموجود، متعلق به کاربر دیگر یا نوع دیگر `404`
می‌دهد. پیوندهای همان ردیف در `zerver_attachment_scheduled_messages`
در همان تراکنش پاک می‌شوند. رکورد پیوست و پیوندهای دیگر آن حفظ می‌شوند.
مسیر وب نشست و CSRF را بررسی می‌کند. پس از commit یک رویداد
`scheduled_messages/remove` یا `reminders/remove` به مالک ارسال می‌شود.
فعال‌سازی و بازگشت هر مسیر مستقل است. `POST`، `PATCH` و worker تحویل
در Django هستند.
[پیاده‌سازی](src/http/scheduled_messages.rs)،
[تست مقایسه](../zerver/tests/test_scheduled_messages.py)،
[فعال‌سازی و بازگشت](README.md#delete-scheduled-messages-and-reminders-through-rust)

## قرارداد متن‌های ذخیره‌شده

`GET /api/v1/saved_snippets` با Basic Auth فقط متن‌های کاربر احرازشده
را از `zerver_savedsnippet` برمی‌گرداند. نتیجه شامل `id`، `title`،
`content` و `date_created` برحسب ثانیهٔ صحیح UTC است. ترتیب صریحی
در کوئری Django و Rust وجود ندارد. view خواندنی دادهٔ متن‌ها را تغییر
نمی‌دهد، کش متن‌ها را استفاده نمی‌کند و رویداد `saved_snippets` منتشر نمی‌کند.
در صورت پیکربندی Redis و RabbitMQ، ثبت فعالیت با نام `get_saved_snippets`
پیش از بررسی سهمیهٔ `api_by_user` انجام می‌شود. کلاینت `internal` با
IP واقعی `127.0.0.1` یا `::1` معاف است. تنظیم debug در Django و معادل
آن در Rust همین معافیت را برای IP غیرمحلی هم فعال می‌کند.
[view](../zerver/views/saved_snippets.py)، [مدل](../zerver/models/saved_snippets.py)،
[تست HTTP](../zerver/tests/test_saved_snippets.py)

`HEAD` همان هدرها و بدنهٔ خالی دارد. Rust این دو روش را مستقیم یا از
پروکسی توسعه و nginx آزمایشی سرو می‌کند.

`POST` در هر دو پیاده‌سازی عنوان و محتوای فرم را اعتبارسنجی می‌کند و
ردیف `zerver_savedsnippet` را همراه با `zerver_realmauditlog` و نوع
`SAVED_SNIPPET_CREATED` در یک تراکنش ثبت می‌کند. پاسخ شناسهٔ تازهٔ
`saved_snippet_id` را می‌دهد. رویداد `saved_snippets/add` پس از commit
فقط برای صاحب متن به صف `notify_tornado` فرستاده می‌شود. برش Rust برای
یک فرایند Tornado فعال شده است و تنظیمات نوشتن آن مستقل از خواندن است.
[تراکنش و رویداد Django](../zerver/actions/saved_snippets.py)، [راهنمای staging برای POST](README.md#enable-saved-snippet-creation)

`PATCH /api/v1/saved_snippets/{saved_snippet_id}` عنوان یا محتوا یا هر دو را
برای صاحب متن به‌روز می‌کند. تاریخ ایجاد و فیلدهای حذف‌شده از درخواست
حفظ می‌شوند. درخواست بدون عنوان و محتوا موفق است و تغییری در متن یا
رویداد ویرایش ایجاد نمی‌کند. با ورودی معتبر برای تغییر، متن ناموجود و
متن متعلق به کاربر دیگر هر دو `404` می‌گیرند. پس از commit در PostgreSQL،
رویداد `saved_snippets/update` با متن کامل فقط برای صاحب متن از Tornado
تحویل می‌شود. هدایت PATCH مستقل از GET و POST است.
[تست مقایسه](../zerver/tests/test_saved_snippets.py)، [راهنمای staging برای PATCH](README.md#enable-saved-snippet-editing)

`DELETE /api/v1/saved_snippets/{saved_snippet_id}` فقط ردیف صاحب متن را
حذف می‌کند و audit قبلی را نگه می‌دارد. پس از commit، رویداد
`saved_snippets/remove` شامل شناسهٔ متن فقط برای صاحب آن فرستاده می‌شود.
متن ناموجود یا متعلق به دیگری `404` می‌گیرد. هدایت DELETE در staging
با عوض کردن map مستقل از PATCH قابل برگشت است.
[راهنمای staging برای DELETE](README.md#enable-saved-snippet-deletion)

`GET /json/saved_snippets` و `HEAD` همان فهرست شخصی را با نشست Django
برمی‌گردانند. Rust از اعتبارسنج مشترک امضا، انقضا و هش نشست، رفتار CSRF
روش‌های امن، و سهمیهٔ وب قبل از ثبت فعالیت استفاده می‌کند. مسیریابی
خواندن وب مستقل از API است. Django مسیر `OPTIONS` را سرو می‌کند.
[راهنمای staging برای خواندن وب](README.md#enable-saved-snippet-web-reads)

`POST /json/saved_snippets` با نشست Django و اعتبارسنجی CSRF اجرا می‌شود.
بررسی CSRF پیش از سهمیه، ثبت فعالیت و نوشتن است و برای POST با
`method=GET` نیز انجام می‌شود. Rust از همان تراکنش ایجاد API برای ثبت متن
و audit استفاده می‌کند و بعد از commit رویداد `saved_snippets/add` را فقط
برای صاحب متن منتشر می‌کند. هدایت POST وب از خواندن وب و مسیر API مستقل
قابل برگشت است.
[راهنمای staging برای ایجاد متن در وب](README.md#enable-saved-snippet-web-creation)

`PATCH /json/saved_snippets/{saved_snippet_id}` با نشست Django و مقایسهٔ
کوکی CSRF با هدر `X-CSRFToken` اجرا می‌شود. بررسی نشست و CSRF پیش از سهمیه
و ثبت فعالیت است. ویرایش از همان عملیات PostgreSQL و اعلان API استفاده
می‌کند. کش متن‌ها در این عملیات تغییر نمی‌کند. هدایت PATCH وب مستقل
است و Django درخواست POST با override روش را سرو می‌کند.
[تست مقایسه](../zerver/tests/test_saved_snippets.py)،
[راهنمای staging برای ویرایش متن در وب](README.md#enable-saved-snippet-web-editing)

`DELETE /json/saved_snippets/{saved_snippet_id}` با همان نشست و بررسی
هدر CSRF اجرا می‌شود. منطق حذف API فقط ردیف صاحب متن را حذف می‌کند.
audit ایجاد حفظ می‌شود و رویداد `saved_snippets/remove` پس از commit فقط
برای صاحب متن منتشر می‌شود. کش متن‌ها در این عملیات تغییر نمی‌کند.
تغییر map در nginx می‌تواند فقط DELETE را به Django برگرداند.
[راهنمای staging برای حذف متن در وب](README.md#enable-saved-snippet-web-deletion)

## فهرست OpenAPI

«مستندات» در ستون مسیریابی به مدخل‌های نمونه اشاره می‌کند. «توسعه» یعنی مسیر
فقط در تنظیمات توسعه ثبت می‌شود. «push» یعنی مسیر با URLconf سرویس اعلان
ثبت می‌شود. «Rust اختیاری» یعنی آن عملیات با تنظیم پروکسی توسعه یا nginx آزمایشی به Rust می‌رود.

| روش      | مسیر                                                    | operationId                            | handler                                                                        | مسیریابی       |
| -------- | ------------------------------------------------------- | -------------------------------------- | ------------------------------------------------------------------------------ | -------------- |
| `GET`    | `/api/v1/attachments`                                   | `get-attachments`                      | `zerver.views.attachments.list_by_user`                                        | Rust اختیاری |
| `DELETE` | `/api/v1/attachments/{attachment_id}`                   | `remove-attachment`                    | `zerver.views.attachments.remove`                                              | Django         |
| `DELETE` | `/api/v1/bot_storage`                                   | `remove-bot-storage`                   | `zerver.views.storage.remove_storage`                                          | Rust اختیاری |
| `GET`    | `/api/v1/bot_storage`                                   | `get-bot-storage`                      | `zerver.views.storage.get_storage`                                             | Rust اختیاری |
| `PUT`    | `/api/v1/bot_storage`                                   | `update-bot-storage`                   | `zerver.views.storage.update_storage`                                          | Rust اختیاری |
| `GET`    | `/api/v1/bots/{bot_id}/api_key`                         | `get-bot-api-key`                      | `zerver.views.users.get_bot_api_key`                                           | Django         |
| `POST`   | `/api/v1/bots/{bot_id}/api_key/regenerate`              | `regenerate-bot-api-key`               | `zerver.views.users.regenerate_bot_api_key`                                    | Django         |
| `GET`    | `/api/v1/calls/bigbluebutton/create`                    | `create-big-blue-button-video-call`    | `zerver.views.video_calls.get_bigbluebutton_url`                               | Django         |
| `POST`   | `/api/v1/calls/constructorgroups/create`                | `create-constructor-groups-video-call` | `zerver.views.video_calls.make_constructor_groups_video_call`                  | Django         |
| `POST`   | `/api/v1/calls/google_meet/create`                      | `create-google-meet-video-call`        | `zerver.views.video_calls.make_google_meet_video_call`                         | Django         |
| `POST`   | `/api/v1/calls/nextcloud_talk/create`                   | `create-nextcloud-talk-video-call`     | `zerver.views.video_calls.create_nextcloud_talk_url`                           | Django         |
| `POST`   | `/api/v1/calls/webex/create`                            | `create-webex-video-call`              | `zerver.views.video_calls.make_webex_video_call`                               | Django         |
| `GET`    | `/api/v1/channel_folders`                               | `get-channel-folders`                  | `zerver.views.channel_folders.get_channel_folders`                             | Rust اختیاری |
| `PATCH`  | `/api/v1/channel_folders`                               | `patch-channel-folders`                | `zerver.views.channel_folders.reorder_realm_channel_folders`                   | Django         |
| `POST`   | `/api/v1/channel_folders/create`                        | `create-channel-folder`                | `zerver.views.channel_folders.create_channel_folder`                           | Django         |
| `PATCH`  | `/api/v1/channel_folders/{channel_folder_id}`           | `update-channel-folder`                | `zerver.views.channel_folders.update_channel_folder`                           | Django         |
| `POST`   | `/api/v1/channels/create`                               | `create-channel`                       | `zerver.views.streams.create_channel`                                          | Django         |
| `DELETE` | `/api/v1/default_streams`                               | `remove-default-stream`                | `zerver.views.streams.remove_default_stream`                                   | Django         |
| `POST`   | `/api/v1/default_streams`                               | `add-default-stream`                   | `zerver.views.streams.add_default_stream`                                      | Django         |
| `POST`   | `/api/v1/dev_fetch_api_key`                             | `dev-fetch-api-key`                    | `zerver.views.development.dev_login.api_dev_fetch_api_key`                     | Django (توسعه) |
| `GET`    | `/api/v1/dev_list_users`                                | `dev-list-users`                       | `zerver.views.development.dev_login.api_dev_list_users`                        | Django (توسعه) |
| `GET`    | `/api/v1/drafts`                                        | `get-drafts`                           | `zerver.views.drafts.fetch_drafts`                                             | Rust اختیاری |
| `POST`   | `/api/v1/drafts`                                        | `create-drafts`                        | `zerver.views.drafts.create_drafts`                                            | Django با درج اختیاری Rust |
| `DELETE` | `/api/v1/drafts/{draft_id}`                             | `delete-draft`                         | `zerver.views.drafts.delete_draft`                                             | Rust اختیاری |
| `PATCH`  | `/api/v1/drafts/{draft_id}`                             | `edit-draft`                           | `zerver.views.drafts.edit_draft`                                               | Django با افزونهٔ اختیاری Rust |
| `DELETE` | `/api/v1/events`                                        | `delete-queue`                         | `zerver.tornado.views.cleanup_event_queue`                                     | Tornado        |
| `GET`    | `/api/v1/events`                                        | `get-events`                           | `zerver.tornado.views.get_events`                                              | Tornado        |
| `GET`    | `/api/v1/export/realm`                                  | `get-realm-exports`                    | `zerver.views.realm_export.get_realm_exports`                                  | Django         |
| `POST`   | `/api/v1/export/realm`                                  | `export-realm`                         | `zerver.views.realm_export.export_realm`                                       | Django         |
| `GET`    | `/api/v1/export/realm/consents`                         | `get-realm-export-consents`            | `zerver.views.realm_export.get_users_export_consents`                          | Django         |
| `DELETE` | `/api/v1/export/realm/{export_id}`                      | `delete-realm-export`                  | `zerver.views.realm_export.delete_realm_export`                                | Django         |
| `POST`   | `/api/v1/fetch_api_key`                                 | `fetch-api-key`                        | `zerver.views.auth.api_fetch_api_key`                                          | Django         |
| `GET`    | `/api/v1/get_stream_id`                                 | `get-stream-id`                        | `zerver.views.streams.json_get_stream_id`                                      | Django         |
| `GET`    | `/api/v1/invites`                                       | `get-invites`                          | `zerver.views.invite.get_user_invites`                                         | Django         |
| `POST`   | `/api/v1/invites`                                       | `send-invites`                         | `zerver.views.invite.invite_users_backend`                                     | Django         |
| `POST`   | `/api/v1/invites/multiuse`                              | `create-invite-link`                   | `zerver.views.invite.generate_multiuse_invite_backend`                         | Django         |
| `DELETE` | `/api/v1/invites/multiuse/{invite_id}`                  | `revoke-invite-link`                   | `zerver.views.invite.revoke_multiuse_invite`                                   | Django         |
| `DELETE` | `/api/v1/invites/{invite_id}`                           | `revoke-email-invite`                  | `zerver.views.invite.revoke_user_invite`                                       | Django         |
| `POST`   | `/api/v1/invites/{invite_id}/resend`                    | `resend-email-invite`                  | `zerver.views.invite.resend_user_invite_email`                                 | Django         |
| `POST`   | `/api/v1/jwt/fetch_api_key`                             | `jwt-fetch-api-key`                    | `zerver.views.auth.jwt_fetch_api_key`                                          | Django         |
| `POST`   | `/api/v1/mark_all_as_read`                              | `mark-all-as-read`                     | `zerver.views.message_flags.mark_all_as_read`                                  | Django         |
| `POST`   | `/api/v1/mark_stream_as_read`                           | `mark-stream-as-read`                  | `zerver.views.message_flags.mark_stream_as_read`                               | Django         |
| `POST`   | `/api/v1/mark_topic_as_read`                            | `mark-topic-as-read`                   | `zerver.views.message_flags.mark_topic_as_read`                                | Django         |
| `GET`    | `/api/v1/messages`                                      | `get-messages`                         | `zerver.views.message_fetch.get_messages_backend`                              | Django         |
| `POST`   | `/api/v1/messages`                                      | `send-message`                         | `zerver.views.message_send.send_message_backend`                               | Django         |
| `POST`   | `/api/v1/messages/flags`                                | `update-message-flags`                 | `zerver.views.message_flags.update_message_flags`                              | Django         |
| `POST`   | `/api/v1/messages/flags/narrow`                         | `update-message-flags-for-narrow`      | `zerver.views.message_flags.update_message_flags_for_narrow`                   | Django         |
| `GET`    | `/api/v1/messages/matches_narrow`                       | `check-messages-match-narrow`          | `zerver.views.message_fetch.messages_in_narrow_backend`                        | Django         |
| `POST`   | `/api/v1/messages/render`                               | `render-message`                       | `zerver.views.message_send.render_message_backend`                             | Django         |
| `DELETE` | `/api/v1/messages/{message_id}`                         | `delete-message`                       | `zerver.views.message_edit.delete_message_backend`                             | Django         |
| `GET`    | `/api/v1/messages/{message_id}`                         | `get-message`                          | `zerver.views.message_edit.json_fetch_raw_message`                             | Django         |
| `PATCH`  | `/api/v1/messages/{message_id}`                         | `update-message`                       | `zerver.views.message_edit.update_message_backend`                             | Django         |
| `GET`    | `/api/v1/messages/{message_id}/history`                 | `get-message-history`                  | `zerver.views.message_edit.get_message_edit_history`                           | Django         |
| `DELETE` | `/api/v1/messages/{message_id}/reactions`               | `remove-reaction`                      | `zerver.views.reactions.remove_reaction`                                       | Django         |
| `POST`   | `/api/v1/messages/{message_id}/reactions`               | `add-reaction`                         | `zerver.views.reactions.add_reaction`                                          | Django         |
| `GET`    | `/api/v1/messages/{message_id}/read_receipts`           | `get-read-receipts`                    | `zerver.views.read_receipts.read_receipts`                                     | Django         |
| `POST`   | `/api/v1/messages/{message_id}/report`                  | `report-message`                       | `zerver.views.message_report.report_message_backend`                           | Django         |
| `POST`   | `/api/v1/messages/{message_id}/typing`                  | `set-typing-status-for-message-edit`   | `zerver.views.typing.send_message_edit_notification_backend`                   | Django         |
| `POST`   | `/api/v1/mobile_push/e2ee/test_notification`            | `e2ee-test-notify`                     | `zerver.views.push_notifications.send_e2ee_test_push_notification_api`         | Django         |
| `POST`   | `/api/v1/mobile_push/register`                          | `register-push-device`                 | `zerver.views.push_notifications.register_push_device`                         | Django         |
| `POST`   | `/api/v1/mobile_push/test_notification`                 | `test-notify`                          | `zerver.views.push_notifications.send_test_push_notification_api`              | Django         |
| `GET`    | `/api/v1/navigation_views`                              | `get-navigation-views`                 | `zerver.views.navigation_views.get_navigation_views`                           | Rust اختیاری |
| `POST`   | `/api/v1/navigation_views`                              | `add-navigation-view`                  | `zerver.views.navigation_views.add_navigation_view`                            | Rust اختیاری |
| `DELETE` | `/api/v1/navigation_views/{fragment}`                   | `remove-navigation-view`               | `zerver.views.navigation_views.remove_navigation_view`                         | Rust اختیاری |
| `PATCH`  | `/api/v1/navigation_views/{fragment}`                   | `edit-navigation-view`                 | `zerver.views.navigation_views.update_navigation_view`                         | Rust اختیاری |
| `POST`   | `/real-time`                                            | —                                      | —                                                                              | مستندات        |
| `POST`   | `/api/v1/realm/deactivate`                              | `deactivate-realm`                     | `zerver.views.realm.deactivate_realm`                                          | Django         |
| `GET`    | `/api/v1/realm/domains`                                 | `get-realm-domains`                    | `zerver.views.realm_domains.list_realm_domains`                                | Rust اختیاری |
| `POST`   | `/api/v1/realm/domains`                                 | `add-realm-domain`                     | `zerver.views.realm_domains.create_realm_domain`                               | Django         |
| `DELETE` | `/api/v1/realm/domains/{domain}`                        | `delete-realm-domain`                  | `zerver.views.realm_domains.delete_realm_domain`                               | Django         |
| `PATCH`  | `/api/v1/realm/domains/{domain}`                        | `patch-realm-domain`                   | `zerver.views.realm_domains.patch_realm_domain`                                | Django         |
| `GET`    | `/api/v1/realm/emoji`                                   | `get-custom-emoji`                     | `zerver.views.realm_emoji.list_emoji`                                          | Rust اختیاری |
| `DELETE` | `/api/v1/realm/emoji/{emoji_name}`                      | `deactivate-custom-emoji`              | `zerver.views.realm_emoji.delete_emoji`                                        | Django         |
| `POST`   | `/api/v1/realm/emoji/{emoji_name}`                      | `upload-custom-emoji`                  | `zerver.views.realm_emoji.upload_emoji`                                        | Django         |
| `POST`   | `/api/v1/realm/filters`                                 | `add-linkifier`                        | `zerver.views.realm_linkifiers.create_linkifier`                               | Django         |
| `DELETE` | `/api/v1/realm/filters/{filter_id}`                     | `remove-linkifier`                     | `zerver.views.realm_linkifiers.delete_linkifier`                               | Django         |
| `PATCH`  | `/api/v1/realm/filters/{filter_id}`                     | `update-linkifier`                     | `zerver.views.realm_linkifiers.update_linkifier`                               | Django         |
| `GET`    | `/api/v1/realm/linkifiers`                              | `get-linkifiers`                       | `zerver.views.realm_linkifiers.list_linkifiers`                                | Rust اختیاری |
| `PATCH`  | `/api/v1/realm/linkifiers`                              | `reorder-linkifiers`                   | `zerver.views.realm_linkifiers.reorder_linkifiers`                             | Django         |
| `POST`   | `/api/v1/realm/playgrounds`                             | `add-code-playground`                  | `zerver.views.realm_playgrounds.add_realm_playground`                          | Django         |
| `DELETE` | `/api/v1/realm/playgrounds/{playground_id}`             | `remove-code-playground`               | `zerver.views.realm_playgrounds.delete_realm_playground`                       | Django         |
| `GET`    | `/api/v1/realm/presence`                                | `get-presence`                         | `zerver.views.presence.get_statuses_for_realm`                                 | Django         |
| `GET`    | `/api/v1/realm/profile_fields`                          | `get-custom-profile-fields`            | `zerver.views.custom_profile_fields.list_realm_custom_profile_fields`          | Rust اختیاری |
| `PATCH`  | `/api/v1/realm/profile_fields`                          | `reorder-custom-profile-fields`        | `zerver.views.custom_profile_fields.reorder_realm_custom_profile_fields`       | Django         |
| `POST`   | `/api/v1/realm/profile_fields`                          | `create-custom-profile-field`          | `zerver.views.custom_profile_fields.create_realm_custom_profile_field`         | Django         |
| `DELETE` | `/api/v1/realm/profile_fields/{field_id}`               | `delete-custom-profile-field`          | `zerver.views.custom_profile_fields.delete_realm_custom_profile_field`         | Django         |
| `PATCH`  | `/api/v1/realm/profile_fields/{field_id}`               | `update-custom-profile-field`          | `zerver.views.custom_profile_fields.update_realm_custom_profile_field`         | Django         |
| `POST`   | `/api/v1/realm/test_welcome_bot_custom_message`         | `test-welcome-bot-custom-message`      | `zerver.views.welcome_bot_custom_message.send_test_welcome_bot_custom_message` | Django         |
| `PATCH`  | `/api/v1/realm/user_settings_defaults`                  | `update-realm-user-settings-defaults`  | `zerver.views.realm.update_realm_user_settings_defaults`                       | Django         |
| `POST`   | `/api/v1/register`                                      | `register-queue`                       | `zerver.views.events_register.events_register_backend`                         | Django         |
| `POST`   | `/api/v1/register_client_device`                        | `register-client-device`               | `zerver.views.devices.register_device`                                         | Django         |
| `GET`    | `/api/v1/reminders`                                     | `get-reminders`                        | `zerver.views.scheduled_messages.fetch_reminders`                              | Rust اختیاری |
| `POST`   | `/api/v1/reminders`                                     | `create-message-reminder`              | `zerver.views.reminders.create_reminders_message_backend`                      | Django         |
| `DELETE` | `/api/v1/reminders/{reminder_id}`                       | `delete-reminder`                      | `zerver.views.reminders.delete_reminder`                                       | Rust اختیاری |
| `POST`   | `/api/v1/remotes/push/e2ee/register`                    | `register-remote-push-device`          | `zilencer.views.register_remote_push_device_for_e2ee_push_notification`        | Django (push)  |
| `POST`   | `/api/v1/remove_client_device`                          | `remove-client-device`                 | `zerver.views.devices.remove_device`                                           | Django         |
| `POST`   | `/rest-error-handling`                                  | `rest-error-handling`                  | —                                                                              | مستندات        |
| `GET`    | `/api/v1/saved_snippets`                                | `get-saved-snippets`                   | `zerver.views.saved_snippets.get_saved_snippets`                               | Rust اختیاری |
| `POST`   | `/api/v1/saved_snippets`                                | `create-saved-snippet`                 | `zerver.views.saved_snippets.create_saved_snippet`                             | Rust اختیاری |
| `DELETE` | `/api/v1/saved_snippets/{saved_snippet_id}`             | `delete-saved-snippet`                 | `zerver.views.saved_snippets.delete_saved_snippet`                             | Rust اختیاری |
| `PATCH`  | `/api/v1/saved_snippets/{saved_snippet_id}`             | `edit-saved-snippet`                   | `zerver.views.saved_snippets.edit_saved_snippet`                               | Rust اختیاری |
| `GET`    | `/api/v1/scheduled_messages`                            | `get-scheduled-messages`               | `zerver.views.scheduled_messages.fetch_scheduled_messages`                     | Rust اختیاری |
| `POST`   | `/api/v1/scheduled_messages`                            | `create-scheduled-message`             | `zerver.views.scheduled_messages.create_scheduled_message_backend`             | Django         |
| `DELETE` | `/api/v1/scheduled_messages/{scheduled_message_id}`     | `delete-scheduled-message`             | `zerver.views.scheduled_messages.delete_scheduled_messages`                    | Rust اختیاری |
| `PATCH`  | `/api/v1/scheduled_messages/{scheduled_message_id}`     | `update-scheduled-message`             | `zerver.views.scheduled_messages.update_scheduled_message_backend`             | Django         |
| `GET`    | `/api/v1/server_settings`                               | `get-server-settings`                  | `zerver.views.auth.api_get_server_settings`                                    | Django         |
| `PATCH`  | `/api/v1/settings`                                      | `update-settings`                      | `zerver.views.user_settings.json_change_settings`                              | Django         |
| `GET`    | `/api/v1/streams`                                       | `get-streams`                          | `zerver.views.streams.get_streams_backend`                                     | Django         |
| `DELETE` | `/api/v1/streams/{stream_id}`                           | `archive-stream`                       | `zerver.views.streams.deactivate_stream_backend`                               | Django         |
| `GET`    | `/api/v1/streams/{stream_id}`                           | `get-stream-by-id`                     | `zerver.views.streams.get_stream_backend`                                      | Django         |
| `PATCH`  | `/api/v1/streams/{stream_id}`                           | `update-stream`                        | `zerver.views.streams.update_stream_backend`                                   | Django         |
| `POST`   | `/api/v1/streams/{stream_id}/delete_topic`              | `delete-topic`                         | `zerver.views.streams.delete_in_topic`                                         | Django         |
| `GET`    | `/api/v1/streams/{stream_id}/email_address`             | `get-stream-email-address`             | `zerver.views.streams.get_stream_email_address`                                | Django         |
| `DELETE` | `/api/v1/streams/{stream_id}/icon`                      | `delete-channel-picture`               | `zerver.views.channel_icon.delete_channel_icon`                                | Django         |
| `GET`    | `/api/v1/streams/{stream_id}/icon`                      | `get-channel-picture`                  | `zerver.views.channel_icon.serve_channel_icon`                                 | Django         |
| `POST`   | `/api/v1/streams/{stream_id}/icon`                      | `upload-channel-picture`               | `zerver.views.channel_icon.upload_channel_icon`                                | Django         |
| `GET`    | `/api/v1/streams/{stream_id}/members`                   | `get-subscribers`                      | `zerver.views.streams.get_subscribers_backend`                                 | Django         |
| `GET`    | `/api/v1/thumbnail/status/{realm_id_str}/{filename}`    | `check-thumbnail-status`               | `zerver.views.thumbnail.check_thumbnail_status`                                | Django         |
| `POST`   | `/api/v1/typing`                                        | `set-typing-status`                    | `zerver.views.typing.send_notification_backend`                                | Django         |
| `GET`    | `/api/v1/user_groups`                                   | `get-user-groups`                      | `zerver.views.user_groups.get_user_groups`                                     | Django         |
| `POST`   | `/api/v1/user_groups/create`                            | `create-user-group`                    | `zerver.views.user_groups.add_user_group`                                      | Django         |
| `PATCH`  | `/api/v1/user_groups/{user_group_id}`                   | `update-user-group`                    | `zerver.views.user_groups.edit_user_group`                                     | Django         |
| `POST`   | `/api/v1/user_groups/{user_group_id}/deactivate`        | `deactivate-user-group`                | `zerver.views.user_groups.deactivate_user_group`                               | Django         |
| `GET`    | `/api/v1/user_groups/{user_group_id}/members`           | `get-user-group-members`               | `zerver.views.user_groups.get_user_group_members`                              | Django         |
| `POST`   | `/api/v1/user_groups/{user_group_id}/members`           | `update-user-group-members`            | `zerver.views.user_groups.update_user_group_backend`                           | Django         |
| `GET`    | `/api/v1/user_groups/{user_group_id}/members/{user_id}` | `get-is-user-group-member`             | `zerver.views.user_groups.get_is_user_group_member`                            | Django         |
| `GET`    | `/api/v1/user_groups/{user_group_id}/subgroups`         | `get-user-group-subgroups`             | `zerver.views.user_groups.get_subgroups_of_user_group`                         | Django         |
| `POST`   | `/api/v1/user_groups/{user_group_id}/subgroups`         | `update-user-group-subgroups`          | `zerver.views.user_groups.update_subgroups_of_user_group`                      | Django         |
| `POST`   | `/api/v1/user_topics`                                   | `update-user-topic`                    | `zerver.views.user_topics.update_user_topic`                                   | Django         |
| `POST`   | `/api/v1/user_uploads`                                  | `upload-file`                          | `zerver.views.upload.upload_file_backend`                                      | Django         |
| `GET`    | `/api/v1/user_uploads/{realm_id_str}/{filename}`        | `get-file-temporary-url`               | `zerver.views.upload.serve_file_url_backend`                                   | Django         |
| `GET`    | `/api/v1/users`                                         | `get-users`                            | `zerver.views.users.get_members_backend`                                       | Django         |
| `POST`   | `/api/v1/users`                                         | `create-user`                          | `zerver.views.users.create_user_backend`                                       | Django         |
| `DELETE` | `/api/v1/users/me`                                      | `deactivate-own-user`                  | `zerver.views.users.deactivate_user_own_backend`                               | Django         |
| `GET`    | `/api/v1/users/me`                                      | `get-own-user`                         | `zerver.views.users.get_profile_backend`                                       | Django         |
| `DELETE` | `/api/v1/users/me/alert_words`                          | `remove-alert-words`                   | `zerver.views.alert_words.remove_alert_words`                                  | Rust اختیاری |
| `GET`    | `/api/v1/users/me/alert_words`                          | `get-alert-words`                      | `zerver.views.alert_words.list_alert_words`                                    | Rust اختیاری |
| `POST`   | `/api/v1/users/me/alert_words`                          | `add-alert-words`                      | `zerver.views.alert_words.add_alert_words`                                     | Rust اختیاری |
| `DELETE` | `/api/v1/users/me/android_gcm_reg_id`                   | `remove-fcm-token`                     | `zerver.views.push_notifications.remove_android_reg_id`                        | Django         |
| `POST`   | `/api/v1/users/me/android_gcm_reg_id`                   | `add-fcm-token`                        | `zerver.views.push_notifications.add_android_reg_id`                           | Django         |
| `POST`   | `/api/v1/users/me/api_key/regenerate`                   | `regenerate-api-key`                   | `zerver.views.user_settings.regenerate_api_key`                                | Django         |
| `DELETE` | `/api/v1/users/me/apns_device_token`                    | `remove-apns-token`                    | `zerver.views.push_notifications.remove_apns_device_token`                     | Django         |
| `POST`   | `/api/v1/users/me/apns_device_token`                    | `add-apns-token`                       | `zerver.views.push_notifications.add_apns_device_token`                        | Django         |
| `DELETE` | `/api/v1/users/me/avatar`                               | `delete-avatar`                        | `zerver.views.user_settings.delete_avatar_backend`                             | Django         |
| `POST`   | `/api/v1/users/me/avatar`                               | `upload-avatar`                        | `zerver.views.user_settings.set_avatar_backend`                                | Django         |
| `POST`   | `/api/v1/users/me/dm_conversations/pin`                 | `update-dm-conversation-pin`           | `zerver.views.direct_message_groups.update_dm_conversation_pin`                | Django         |
| `DELETE` | `/api/v1/users/me/muted_users/{muted_user_id}`          | `unmute-user`                          | `zerver.views.muted_users.unmute_user`                                         | Django         |
| `POST`   | `/api/v1/users/me/muted_users/{muted_user_id}`          | `mute-user`                            | `zerver.views.muted_users.mute_user`                                           | Django         |
| `POST`   | `/api/v1/users/me/presence`                             | `update-presence`                      | `zerver.views.presence.update_active_status_backend`                           | Django         |
| `DELETE` | `/api/v1/users/me/profile_data`                         | `remove-profile-data`                  | `zerver.views.custom_profile_fields.remove_user_custom_profile_data`           | Django         |
| `PATCH`  | `/api/v1/users/me/profile_data`                         | `update-profile-data`                  | `zerver.views.custom_profile_fields.update_user_custom_profile_data`           | Django         |
| `POST`   | `/api/v1/users/me/status`                               | `update-status`                        | `zerver.views.presence.update_user_status_backend`                             | Django         |
| `DELETE` | `/api/v1/users/me/subscriptions`                        | `unsubscribe`                          | `zerver.views.streams.remove_subscriptions_backend`                            | Django         |
| `GET`    | `/api/v1/users/me/subscriptions`                        | `get-subscriptions`                    | `zerver.views.streams.list_subscriptions_backend`                              | Django         |
| `POST`   | `/api/v1/users/me/subscriptions`                        | `subscribe`                            | `zerver.views.streams.add_subscriptions_backend`                               | Django         |
| `PATCH`  | `/api/v1/users/me/subscriptions/muted_topics`           | `mute-topic`                           | `zerver.views.user_topics.update_muted_topic`                                  | Django         |
| `POST`   | `/api/v1/users/me/subscriptions/properties`             | `update-subscription-settings`         | `zerver.views.streams.update_subscription_properties_backend`                  | Django         |
| `PATCH`  | `/api/v1/users/me/subscriptions/{stream_id}`            | `update-subscription-property`         | `zerver.views.streams.update_subscriptions_property`                           | Django         |
| `GET`    | `/api/v1/users/me/{stream_id}/topics`                   | `get-stream-topics`                    | `zerver.views.streams.get_topics_backend`                                      | Django         |
| `GET`    | `/api/v1/users/{email}`                                 | `get-user-by-email`                    | `zerver.views.users.get_user_by_email`                                         | Django         |
| `PATCH`  | `/api/v1/users/{email}`                                 | `update-user-by-email`                 | `zerver.views.users.update_user_by_email_api`                                  | Django         |
| `GET`    | `/api/v1/users/{user_id_or_email}/presence`             | `get-user-presence`                    | `zerver.views.presence.get_presence_backend`                                   | Django         |
| `DELETE` | `/api/v1/users/{user_id}`                               | `deactivate-user`                      | `zerver.views.users.deactivate_user_backend`                                   | Django         |
| `GET`    | `/api/v1/users/{user_id}`                               | `get-user`                             | `zerver.views.users.get_member_backend`                                        | Django         |
| `PATCH`  | `/api/v1/users/{user_id}`                               | `update-user`                          | `zerver.views.users.update_user_by_id_api`                                     | Django         |
| `GET`    | `/api/v1/users/{user_id}/channels`                      | `get-user-channels`                    | `zerver.views.users.get_subscribed_channels_backend`                           | Django         |
| `POST`   | `/api/v1/users/{user_id}/reactivate`                    | `reactivate-user`                      | `zerver.views.users.reactivate_user_backend`                                   | Django         |
| `GET`    | `/api/v1/users/{user_id}/status`                        | `get-user-status`                      | `zerver.views.presence.get_status_backend`                                     | Django         |
| `POST`   | `/api/v1/users/{user_id}/status`                        | `update-status-for-user`               | `zerver.views.presence.update_user_status_admin`                               | Django         |
| `GET`    | `/api/v1/users/{user_id}/subscriptions/{stream_id}`     | `get-subscription-status`              | `zerver.views.users.get_subscription_backend`                                  | Django         |
| `POST`   | `/zulip-outgoing-webhook`                               | `zulip-outgoing-webhooks`              | —                                                                              | مستندات        |

## عملیات خارج از OpenAPI زیر `/api/v1/`

مسیرهای مستقیم و عملیات ثبت‌شده در URLconfهای Django و سرویس push. شرح view flagها در [REST dispatcher](../zerver/lib/rest.py) آمده است.

| روش      | مسیر                                               | handler                                                                               | مسیریابی      | view flags                   |
| -------- | -------------------------------------------------- | ------------------------------------------------------------------------------------- | ------------- | ---------------------------- |
| `GET`    | `/api/v1/bots`                                     | `zerver.views.users.get_bots_backend`                                                 | Django        | —                            |
| `POST`   | `/api/v1/bots`                                     | `zerver.views.users.add_bot_backend`                                                  | Django        | —                            |
| `DELETE` | `/api/v1/bots/{bot_id}`                            | `zerver.views.users.deactivate_bot_backend`                                           | Django        | —                            |
| `PATCH`  | `/api/v1/bots/{bot_id}`                            | `zerver.views.users.patch_bot_backend`                                                | Django        | —                            |
| `POST`   | `/api/v1/calls/zoom/create`                        | `zerver.views.video_calls.make_zoom_video_call`                                       | Django        | —                            |
| `POST`   | `/api/v1/default_stream_groups/create`             | `zerver.views.streams.create_default_stream_group`                                    | Django        | —                            |
| `DELETE` | `/api/v1/default_stream_groups/{group_id}`         | `zerver.views.streams.remove_default_stream_group`                                    | Django        | —                            |
| `PATCH`  | `/api/v1/default_stream_groups/{group_id}`         | `zerver.views.streams.update_default_stream_group_info`                               | Django        | —                            |
| `PATCH`  | `/api/v1/default_stream_groups/{group_id}/streams` | `zerver.views.streams.update_default_stream_group_streams`                            | Django        | —                            |
| `GET`    | `/api/v1/messages/summary`                         | `zerver.views.message_summary.get_messages_summary`                                   | Django        | `intentionally_undocumented` |
| `PATCH`  | `/api/v1/realm`                                    | `zerver.views.realm.update_realm`                                                     | Django        | —                            |
| `DELETE` | `/api/v1/realm/icon`                               | `zerver.views.realm_icon.delete_icon_backend`                                         | Django        | —                            |
| `GET`    | `/api/v1/realm/icon`                               | `zerver.views.realm_icon.get_icon_backend`                                            | Django        | —                            |
| `POST`   | `/api/v1/realm/icon`                               | `zerver.views.realm_icon.upload_icon`                                                 | Django        | —                            |
| `DELETE` | `/api/v1/realm/logo`                               | `zerver.views.realm_logo.delete_logo_backend`                                         | Django        | —                            |
| `GET`    | `/api/v1/realm/logo`                               | `zerver.views.realm_logo.get_logo_backend`                                            | Django        | —                            |
| `POST`   | `/api/v1/realm/logo`                               | `zerver.views.realm_logo.upload_logo`                                                 | Django        | —                            |
| `GET`    | `/api/v1/realm/subdomain/{subdomain}`              | `zerver.views.realm.check_subdomain_available`                                        | Django        | —                            |
| `POST`   | `/api/v1/remotes/push/e2ee/notify`                 | `zilencer.views.remote_server_send_e2ee_push_notification`                            | Django (push) | —                            |
| `POST`   | `/api/v1/remotes/push/notify`                      | `zilencer.views.remote_server_notify_push`                                            | Django (push) | `intentionally_undocumented` |
| `POST`   | `/api/v1/remotes/push/register`                    | `zilencer.views.register_remote_push_device`                                          | Django (push) | `intentionally_undocumented` |
| `POST`   | `/api/v1/remotes/push/test_notification`           | `zilencer.views.remote_server_send_test_notification`                                 | Django (push) | `intentionally_undocumented` |
| `POST`   | `/api/v1/remotes/push/unregister`                  | `zilencer.views.unregister_remote_push_device`                                        | Django (push) | `intentionally_undocumented` |
| `POST`   | `/api/v1/remotes/push/unregister/all`              | `zilencer.views.unregister_all_remote_push_devices`                                   | Django (push) | `intentionally_undocumented` |
| `POST`   | `/api/v1/remotes/server/analytics`                 | `zilencer.views.remote_server_post_analytics`                                         | Django (push) | —                            |
| `GET`    | `/api/v1/remotes/server/analytics/status`          | `zilencer.views.remote_server_check_analytics`                                        | Django (push) | —                            |
| `POST`   | `/api/v1/remotes/server/billing`                   | `corporate.views.remote_billing_page.remote_realm_billing_entry`                      | Django (push) | —                            |
| `POST`   | `/api/v1/remotes/server/deactivate`                | `zilencer.views.deactivate_remote_server`                                             | Django (push) | —                            |
| `POST`   | `/api/v1/remotes/server/register`                  | `zilencer.views.register_remote_server`                                               | Django (push) | —                            |
| `POST`   | `/api/v1/remotes/server/register/transfer`         | `zilencer.views.transfer_remote_server_registration`                                  | Django (push) | —                            |
| `POST`   | `/api/v1/remotes/server/register/verify_challenge` | `zilencer.views.verify_registration_transfer_challenge_ack_endpoint`                  | Django (push) | —                            |
| `PATCH`  | `/api/v1/settings/display`                         | `zerver.views.user_settings.json_change_settings`                                     | Django        | `intentionally_undocumented` |
| `PATCH`  | `/api/v1/settings/notifications`                   | `zerver.views.user_settings.json_change_settings`                                     | Django        | `intentionally_undocumented` |
| `PATCH`  | `/api/v1/settings/web_display_preferences`         | `zerver.views.user_settings.update_web_display_preferences`                           | Django        | `intentionally_undocumented` |
| `PATCH`  | `/api/v1/settings/web_ui_version`                  | `zerver.views.user_settings.update_web_ui_version`                                    | Django        | `intentionally_undocumented` |
| `POST`   | `/api/v1/submessage`                               | `zerver.views.submessage.process_submessage`                                          | Django        | —                            |
| `POST`   | `/api/v1/users/me/onboarding_steps`                | `zerver.views.onboarding_steps.mark_onboarding_step_as_read`                          | Django        | `intentionally_undocumented` |
| `POST`   | `/api/v1/zcommand`                                 | `zerver.views.message_send.zcommand_backend`                                          | Django        | —                            |
| `POST`   | `/api/v1/zulip-services/verify/{access_token}/`    | `zerver.views.push_notifications.self_hosting_registration_transfer_challenge_verify` | Django        | —                            |

## مسیرهای وب زیر `/json/`

مسیرهای مشترک handler جدول‌های API را دارند. ستون مسیریابی، مسیرهای مخصوص وب را هم مشخص می‌کند.

| مسیر                                                  | روش‌ها                   | مسیریابی          |
| ----------------------------------------------------- | ------------------------ | ----------------- |
| `/json/antispam_challenge`                            | `GET`                    | Django (مخصوص وب) |
| `/json/attachments`                                   | `GET`                    | Rust اختیاری |
| `/json/attachments/{attachment_id}`                   | `DELETE`                 | Django            |
| `/json/bot_storage`                                   | `DELETE`, `GET`, `PUT`   | هر سه روش در Rust اختیاری |
| `/json/bots`                                          | `GET`, `POST`            | Django            |
| `/json/bots/{bot_id}`                                 | `DELETE`, `PATCH`        | Django            |
| `/json/bots/{bot_id}/api_key`                         | `GET`                    | Django            |
| `/json/bots/{bot_id}/api_key/regenerate`              | `POST`                   | Django            |
| `/json/calls/bigbluebutton/create`                    | `GET`                    | Django            |
| `/json/calls/constructorgroups/create`                | `POST`                   | Django            |
| `/json/calls/google_meet/create`                      | `POST`                   | Django            |
| `/json/calls/nextcloud_talk/create`                   | `POST`                   | Django            |
| `/json/calls/webex/create`                            | `POST`                   | Django            |
| `/json/calls/zoom/create`                             | `POST`                   | Django            |
| `/json/channel_folders`                               | `GET`, `PATCH`           | `GET` در Rust اختیاری، نوشتن در Django |
| `/json/channel_folders/create`                        | `POST`                   | Django            |
| `/json/channel_folders/{channel_folder_id}`           | `PATCH`                  | Django            |
| `/json/channels/create`                               | `POST`                   | Django            |
| `/json/default_stream_groups/create`                  | `POST`                   | Django            |
| `/json/default_stream_groups/{group_id}`              | `DELETE`, `PATCH`        | Django            |
| `/json/default_stream_groups/{group_id}/streams`      | `PATCH`                  | Django            |
| `/json/default_streams`                               | `DELETE`, `POST`         | Django            |
| `/json/drafts`                                        | `GET`, `POST`            | `GET` در Rust اختیاری، `POST` در Django با درج اختیاری Rust |
| `/json/drafts/{draft_id}`                             | `DELETE`, `PATCH`        | `DELETE` در Rust اختیاری، `PATCH` در Django با افزونهٔ اختیاری Rust |
| `/json/events`                                        | `DELETE`, `GET`          | Tornado           |
| `/json/export/realm`                                  | `GET`, `POST`            | Django            |
| `/json/export/realm/consents`                         | `GET`                    | Django            |
| `/json/export/realm/{export_id}`                      | `DELETE`                 | Django            |
| `/json/fetch_api_key`                                 | `POST`                   | Django (مخصوص وب) |
| `/json/get_stream_id`                                 | `GET`                    | Django            |
| `/json/invites`                                       | `GET`, `POST`            | Django            |
| `/json/invites/multiuse`                              | `POST`                   | Django            |
| `/json/invites/multiuse/{invite_id}`                  | `DELETE`                 | Django            |
| `/json/invites/{invite_id}`                           | `DELETE`                 | Django            |
| `/json/invites/{invite_id}/resend`                    | `POST`                   | Django            |
| `/json/mark_all_as_read`                              | `POST`                   | Django            |
| `/json/mark_stream_as_read`                           | `POST`                   | Django            |
| `/json/mark_topic_as_read`                            | `POST`                   | Django            |
| `/json/messages`                                      | `GET`, `POST`            | Django            |
| `/json/messages/flags`                                | `POST`                   | Django            |
| `/json/messages/flags/narrow`                         | `POST`                   | Django            |
| `/json/messages/matches_narrow`                       | `GET`                    | Django            |
| `/json/messages/render`                               | `POST`                   | Django            |
| `/json/messages/summary`                              | `GET`                    | Django            |
| `/json/messages/{message_id}`                         | `DELETE`, `GET`, `PATCH` | Django            |
| `/json/messages/{message_id}/history`                 | `GET`                    | Django            |
| `/json/messages/{message_id}/reactions`               | `DELETE`, `POST`         | Django            |
| `/json/messages/{message_id}/read_receipts`           | `GET`                    | Django            |
| `/json/messages/{message_id}/report`                  | `POST`                   | Django            |
| `/json/messages/{message_id}/typing`                  | `POST`                   | Django            |
| `/json/mobile_push/e2ee/test_notification`            | `POST`                   | Django            |
| `/json/mobile_push/register`                          | `POST`                   | Django            |
| `/json/mobile_push/test_notification`                 | `POST`                   | Django            |
| `/json/navigation_views`                              | `GET`, `POST`            | Rust اختیاری |
| `/json/navigation_views/{fragment}`                   | `DELETE`, `PATCH`        | Rust اختیاری |
| `/json/realm`                                         | `PATCH`                  | Django            |
| `/json/realm/deactivate`                              | `POST`                   | Django            |
| `/json/realm/domains`                                 | `GET`, `POST`            | `GET` در Rust اختیاری، `POST` در Django |
| `/json/realm/domains/{domain}`                        | `DELETE`, `PATCH`        | Django            |
| `/json/realm/emoji`                                   | `GET`                    | Rust اختیاری |
| `/json/realm/emoji/{emoji_name}`                      | `DELETE`, `POST`         | Django            |
| `/json/realm/filters`                                 | `POST`                   | Django            |
| `/json/realm/filters/{filter_id}`                     | `DELETE`, `PATCH`        | Django            |
| `/json/realm/icon`                                    | `DELETE`, `GET`, `POST`  | Django            |
| `/json/realm/import/status/{confirmation_key}`        | `GET`                    | Django (مخصوص وب) |
| `/json/realm/linkifiers`                              | `GET`, `PATCH`           | `GET` در Rust اختیاری، `PATCH` در Django |
| `/json/realm/logo`                                    | `DELETE`, `GET`, `POST`  | Django            |
| `/json/realm/playgrounds`                             | `POST`                   | Django            |
| `/json/realm/playgrounds/{playground_id}`             | `DELETE`                 | Django            |
| `/json/realm/presence`                                | `GET`                    | Django            |
| `/json/realm/profile_fields`                          | `GET`, `PATCH`, `POST`   | `GET` در Rust اختیاری، نوشتن در Django |
| `/json/realm/profile_fields/{field_id}`               | `DELETE`, `PATCH`        | Django            |
| `/json/realm/subdomain/{subdomain}`                   | `GET`                    | Django            |
| `/json/realm/test_welcome_bot_custom_message`         | `POST`                   | Django            |
| `/json/realm/user_settings_defaults`                  | `PATCH`                  | Django            |
| `/json/register`                                      | `POST`                   | Django            |
| `/json/register_client_device`                        | `POST`                   | Django            |
| `/json/reminders`                                     | `GET`, `POST`            | `GET` در Rust اختیاری، نوشتن در Django |
| `/json/reminders/{reminder_id}`                       | `DELETE`                 | Rust اختیاری |
| `/json/remove_client_device`                          | `POST`                   | Django            |
| `/json/saved_snippets`                                | `GET`, `POST`            | Rust اختیاری |
| `/json/saved_snippets/{saved_snippet_id}`             | `DELETE`, `PATCH`        | Rust اختیاری |
| `/json/scheduled_messages`                            | `GET`, `POST`            | `GET` در Rust اختیاری، نوشتن در Django |
| `/json/scheduled_messages/{scheduled_message_id}`     | `DELETE`, `PATCH`        | `DELETE` در Rust اختیاری، `PATCH` در Django |
| `/json/self-hosted-billing`                           | `GET`                    | Django (مخصوص وب) |
| `/json/settings`                                      | `PATCH`                  | Django            |
| `/json/settings/display`                              | `PATCH`                  | Django            |
| `/json/settings/notifications`                        | `PATCH`                  | Django            |
| `/json/settings/web_display_preferences`              | `PATCH`                  | Django            |
| `/json/settings/web_ui_version`                       | `PATCH`                  | Django            |
| `/json/streams`                                       | `GET`                    | Django            |
| `/json/streams/{stream_id}`                           | `DELETE`, `GET`, `PATCH` | Django            |
| `/json/streams/{stream_id}/delete_topic`              | `POST`                   | Django            |
| `/json/streams/{stream_id}/email_address`             | `GET`                    | Django            |
| `/json/streams/{stream_id}/icon`                      | `DELETE`, `GET`, `POST`  | Django            |
| `/json/streams/{stream_id}/members`                   | `GET`                    | Django            |
| `/json/submessage`                                    | `POST`                   | Django            |
| `/json/thumbnail/status/{realm_id_str}/{filename}`    | `GET`                    | Django            |
| `/json/typing`                                        | `POST`                   | Django            |
| `/json/user_groups`                                   | `GET`                    | Django            |
| `/json/user_groups/create`                            | `POST`                   | Django            |
| `/json/user_groups/{user_group_id}`                   | `PATCH`                  | Django            |
| `/json/user_groups/{user_group_id}/deactivate`        | `POST`                   | Django            |
| `/json/user_groups/{user_group_id}/members`           | `GET`, `POST`            | Django            |
| `/json/user_groups/{user_group_id}/members/{user_id}` | `GET`                    | Django            |
| `/json/user_groups/{user_group_id}/subgroups`         | `GET`, `POST`            | Django            |
| `/json/user_topics`                                   | `POST`                   | Django            |
| `/json/user_uploads`                                  | `POST`                   | Django            |
| `/json/user_uploads/{realm_id_str}/{filename}`        | `GET`                    | Django            |
| `/json/users`                                         | `GET`, `POST`            | Django            |
| `/json/users/me`                                      | `DELETE`, `GET`          | Django            |
| `/json/users/me/alert_words`                          | `DELETE`, `GET`, `POST`  | Rust اختیاری |
| `/json/users/me/android_gcm_reg_id`                   | `DELETE`, `POST`         | Django            |
| `/json/users/me/apns_device_token`                    | `DELETE`, `POST`         | Django            |
| `/json/users/me/avatar`                               | `DELETE`, `POST`         | Django            |
| `/json/users/me/dm_conversations/pin`                 | `POST`                   | Django            |
| `/json/users/me/muted_users/{muted_user_id}`          | `DELETE`, `POST`         | Django            |
| `/json/users/me/onboarding_steps`                     | `POST`                   | Django            |
| `/json/users/me/presence`                             | `POST`                   | Django            |
| `/json/users/me/profile_data`                         | `DELETE`, `PATCH`        | Django            |
| `/json/users/me/status`                               | `POST`                   | Django            |
| `/json/users/me/subscriptions`                        | `DELETE`, `GET`, `POST`  | Django            |
| `/json/users/me/subscriptions/muted_topics`           | `PATCH`                  | Django            |
| `/json/users/me/subscriptions/properties`             | `POST`                   | Django            |
| `/json/users/me/subscriptions/{stream_id}`            | `PATCH`                  | Django            |
| `/json/users/me/{stream_id}/topics`                   | `GET`                    | Django            |
| `/json/users/{email}`                                 | `GET`, `PATCH`           | Django            |
| `/json/users/{user_id_or_email}/presence`             | `GET`                    | Django            |
| `/json/users/{user_id}`                               | `DELETE`, `GET`, `PATCH` | Django            |
| `/json/users/{user_id}/channels`                      | `GET`                    | Django            |
| `/json/users/{user_id}/reactivate`                    | `POST`                   | Django            |
| `/json/users/{user_id}/status`                        | `GET`, `POST`            | Django            |
| `/json/users/{user_id}/subscriptions/{stream_id}`     | `GET`                    | Django            |
| `/json/zcommand`                                      | `POST`                   | Django            |

## مسیرهای داخلی و مسیرهای ویژهٔ بدون پیشوند API

چهار مسیر ارتباط سرویس‌ها و مسیرهای سلامت، گزارش CSP و تحویل فایل از URLconfها استخراج شده‌اند. مسیرهای تحویل فایل با پرچم `override_api_url_scheme` از نشست و احراز هویت API پشتیبانی می‌کنند. [مسیریابی Django](../zproject/urls.py)، [مسیریابی Tornado](../zproject/tornado_urls.py)

| روش         | مسیر                                                                   | handler پایتون                                          | مسیریابی              |
| ----------- | ---------------------------------------------------------------------- | ------------------------------------------------------- | --------------------- |
| `POST`      | `/api/internal/notify_tornado`                                         | `zerver.tornado.views.notify`                           | Tornado               |
| `POST`      | `/api/internal/tusd`                                                   | `zerver.views.tusd.handle_tusd_hook`                    | Django                |
| `POST`      | `/api/internal/web_reload_clients`                                     | `zerver.tornado.views.web_reload_clients`               | Tornado               |
| `POST`      | `/api/v1/events/internal`                                              | `zerver.tornado.views.get_events_internal`              | Tornado               |
| `GET`       | `/avatar/{email}`                                                      | `zerver.views.users.avatar_by_email`                    | Django                |
| `GET`       | `/avatar/{email}/medium`                                               | `zerver.views.users.avatar_medium`                      | Django                |
| `GET`       | `/avatar/{user_id}`                                                    | `zerver.views.users.avatar_by_id`                       | Django                |
| `GET`       | `/avatar/{user_id}/medium`                                             | `zerver.views.users.avatar_medium`                      | Django                |
| `GET, HEAD` | `/compatibility`                                                       | `zerver.views.compatibility.check_global_compatibility` | Django / Rust (توسعه) |
| `GET`       | `/health`                                                              | `zerver.views.health.health`                            | Django                |
| `POST`      | `/report/csp_violations`                                               | `zerver.views.report.report_csp_violations`             | Django                |
| `GET`       | `/thumbnail`                                                           | `zerver.views.thumbnail.backend_serve_thumbnail`        | Django                |
| `GET`       | `/user_avatars/{path}`                                                 | `zerver.views.upload.serve_local_avatar_unauthed`       | Django                |
| `GET`       | `/user_uploads/download/{realm_id_str}/{filename}`                     | `zerver.views.upload.serve_file_download_backend`       | Django                |
| `GET`       | `/user_uploads/temporary/{token}/{filename}`                           | `zerver.views.upload.serve_file_unauthed_from_token`    | Django                |
| `GET`       | `/user_uploads/thumbnail/{realm_id_str}/{filename}/{thumbnail_format}` | `zerver.views.upload.serve_file_backend`                | Django                |
| `GET`       | `/user_uploads/{realm_id_str}/{filename}`                              | `zerver.views.upload.serve_file_backend`                | Django                |

## Webhookهای ورودی

۹۶ مسیر ثبت‌شده برای ۹۲ اتصال، شامل چهار alias قدیمی. مسیرها در Django ثبت می‌شوند. `POST` روش ارسال رویداد است. Dropbox برای اعتبارسنجی آدرس، درخواست challenge غیر `POST` را نیز پردازش می‌کند. جدول بر اساس ثبت مسیر تهیه شده و همهٔ روش‌های پذیرفته‌شده به تفکیک handler ممیزی نشده‌اند. [ثبت اتصال‌ها](../zerver/lib/integrations.py)، [handler در Dropbox](../zerver/webhooks/dropbox/view.py)

| اتصال                 | مسیرها                                                                | handler                                                                    |
| --------------------- | --------------------------------------------------------------------- | -------------------------------------------------------------------------- |
| `airbrake`            | `/api/v1/external/airbrake`                                           | `zerver.webhooks.airbrake.view.api_airbrake_webhook`                       |
| `airbyte`             | `/api/v1/external/airbyte`                                            | `zerver.webhooks.airbyte.view.api_airbyte_webhook`                         |
| `alertmanager`        | `/api/v1/external/alertmanager`                                       | `zerver.webhooks.alertmanager.view.api_alertmanager_webhook`               |
| `ansibletower`        | `/api/v1/external/ansibletower`                                       | `zerver.webhooks.ansibletower.view.api_ansibletower_webhook`               |
| `appfollow`           | `/api/v1/external/appfollow`                                          | `zerver.webhooks.appfollow.view.api_appfollow_webhook`                     |
| `appveyor`            | `/api/v1/external/appveyor`                                           | `zerver.webhooks.appveyor.view.api_appveyor_webhook`                       |
| `azuredevops`         | `/api/v1/external/azuredevops`                                        | `zerver.webhooks.azuredevops.view.api_azuredevops_webhook`                 |
| `basecamp`            | `/api/v1/external/basecamp`                                           | `zerver.webhooks.basecamp.view.api_basecamp_webhook`                       |
| `beanstalk`           | `/api/v1/external/beanstalk`                                          | `zerver.webhooks.beanstalk.view.api_beanstalk_webhook`                     |
| `beeminder`           | `/api/v1/external/beeminder`                                          | `zerver.webhooks.beeminder.view.api_beeminder_webhook`                     |
| `bitbucket`           | `/api/v1/external/bitbucket`, `/api/v1/external/bitbucket2`           | `zerver.webhooks.bitbucket.view.api_bitbucket_webhook`                     |
| `bitbucketdatacenter` | `/api/v1/external/bitbucketdatacenter`, `/api/v1/external/bitbucket3` | `zerver.webhooks.bitbucketdatacenter.view.api_bitbucketdatacenter_webhook` |
| `buildbot`            | `/api/v1/external/buildbot`                                           | `zerver.webhooks.buildbot.view.api_buildbot_webhook`                       |
| `canarytoken`         | `/api/v1/external/canarytoken`                                        | `zerver.webhooks.canarytoken.view.api_canarytoken_webhook`                 |
| `circleci`            | `/api/v1/external/circleci`                                           | `zerver.webhooks.circleci.view.api_circleci_webhook`                       |
| `codeship`            | `/api/v1/external/codeship`                                           | `zerver.webhooks.codeship.view.api_codeship_webhook`                       |
| `crashlytics`         | `/api/v1/external/crashlytics`                                        | `zerver.webhooks.crashlytics.view.api_crashlytics_webhook`                 |
| `dbt`                 | `/api/v1/external/dbt`                                                | `zerver.webhooks.dbt.view.api_dbt_webhook`                                 |
| `delighted`           | `/api/v1/external/delighted`                                          | `zerver.webhooks.delighted.view.api_delighted_webhook`                     |
| `dialogflow`          | `/api/v1/external/dialogflow`                                         | `zerver.webhooks.dialogflow.view.api_dialogflow_webhook`                   |
| `dropbox`             | `/api/v1/external/dropbox`                                            | `zerver.webhooks.dropbox.view.api_dropbox_webhook`                         |
| `dropboxsign`         | `/api/v1/external/dropboxsign`, `/api/v1/external/hellosign`          | `zerver.webhooks.dropboxsign.view.api_dropboxsign_webhook`                 |
| `errbit`              | `/api/v1/external/errbit`                                             | `zerver.webhooks.errbit.view.api_errbit_webhook`                           |
| `flock`               | `/api/v1/external/flock`                                              | `zerver.webhooks.flock.view.api_flock_webhook`                             |
| `freshdesk`           | `/api/v1/external/freshdesk`                                          | `zerver.webhooks.freshdesk.view.api_freshdesk_webhook`                     |
| `freshping`           | `/api/v1/external/freshping`                                          | `zerver.webhooks.freshping.view.api_freshping_webhook`                     |
| `freshstatus`         | `/api/v1/external/freshstatus`                                        | `zerver.webhooks.freshstatus.view.api_freshstatus_webhook`                 |
| `front`               | `/api/v1/external/front`                                              | `zerver.webhooks.front.view.api_front_webhook`                             |
| `gitea`               | `/api/v1/external/gitea`                                              | `zerver.webhooks.gitea.view.api_gitea_webhook`                             |
| `github`              | `/api/v1/external/github`                                             | `zerver.webhooks.github.view.api_github_webhook`                           |
| `githubsponsors`      | `/api/v1/external/githubsponsors`                                     | `zerver.webhooks.github.view.api_github_webhook`                           |
| `gitlab`              | `/api/v1/external/gitlab`                                             | `zerver.webhooks.gitlab.view.api_gitlab_webhook`                           |
| `gocd`                | `/api/v1/external/gocd`                                               | `zerver.webhooks.gocd.view.api_gocd_webhook`                               |
| `gogs`                | `/api/v1/external/gogs`                                               | `zerver.webhooks.gogs.view.api_gogs_webhook`                               |
| `gong`                | `/api/v1/external/gong`                                               | `zerver.webhooks.gong.view.api_gong_webhook`                               |
| `gosquared`           | `/api/v1/external/gosquared`                                          | `zerver.webhooks.gosquared.view.api_gosquared_webhook`                     |
| `grafana`             | `/api/v1/external/grafana`                                            | `zerver.webhooks.grafana.view.api_grafana_webhook`                         |
| `greenhouse`          | `/api/v1/external/greenhouse`                                         | `zerver.webhooks.greenhouse.view.api_greenhouse_webhook`                   |
| `groove`              | `/api/v1/external/groove`                                             | `zerver.webhooks.groove.view.api_groove_webhook`                           |
| `harbor`              | `/api/v1/external/harbor`                                             | `zerver.webhooks.harbor.view.api_harbor_webhook`                           |
| `helloworld`          | `/api/v1/external/helloworld`                                         | `zerver.webhooks.helloworld.view.api_helloworld_webhook`                   |
| `heroku`              | `/api/v1/external/heroku`                                             | `zerver.webhooks.heroku.view.api_heroku_webhook`                           |
| `homeassistant`       | `/api/v1/external/homeassistant`                                      | `zerver.webhooks.homeassistant.view.api_homeassistant_webhook`             |
| `ifttt`               | `/api/v1/external/ifttt`                                              | `zerver.webhooks.ifttt.view.api_ifttt_webhook`                             |
| `intercom`            | `/api/v1/external/intercom`                                           | `zerver.webhooks.intercom.view.api_intercom_webhook`                       |
| `jira`                | `/api/v1/external/jira`                                               | `zerver.webhooks.jira.view.api_jira_webhook`                               |
| `jotform`             | `/api/v1/external/jotform`                                            | `zerver.webhooks.jotform.view.api_jotform_webhook`                         |
| `json`                | `/api/v1/external/json`                                               | `zerver.webhooks.json.view.api_json_webhook`                               |
| `librato`             | `/api/v1/external/librato`                                            | `zerver.webhooks.librato.view.api_librato_webhook`                         |
| `lidarr`              | `/api/v1/external/lidarr`                                             | `zerver.webhooks.lidarr.view.api_lidarr_webhook`                           |
| `linear`              | `/api/v1/external/linear`                                             | `zerver.webhooks.linear.view.api_linear_webhook`                           |
| `mention`             | `/api/v1/external/mention`                                            | `zerver.webhooks.mention.view.api_mention_webhook`                         |
| `netlify`             | `/api/v1/external/netlify`                                            | `zerver.webhooks.netlify.view.api_netlify_webhook`                         |
| `newrelic`            | `/api/v1/external/newrelic`                                           | `zerver.webhooks.newrelic.view.api_newrelic_webhook`                       |
| `notion`              | `/api/v1/external/notion`                                             | `zerver.webhooks.notion.view.api_notion_webhook`                           |
| `opencollective`      | `/api/v1/external/opencollective`                                     | `zerver.webhooks.opencollective.view.api_opencollective_webhook`           |
| `openproject`         | `/api/v1/external/openproject`                                        | `zerver.webhooks.openproject.view.api_openproject_webhook`                 |
| `opensearch`          | `/api/v1/external/opensearch`                                         | `zerver.webhooks.opensearch.view.api_opensearch_webhook`                   |
| `opsgenie`            | `/api/v1/external/opsgenie`                                           | `zerver.webhooks.opsgenie.view.api_opsgenie_webhook`                       |
| `pagerduty`           | `/api/v1/external/pagerduty`                                          | `zerver.webhooks.pagerduty.view.api_pagerduty_webhook`                     |
| `papertrail`          | `/api/v1/external/papertrail`                                         | `zerver.webhooks.papertrail.view.api_papertrail_webhook`                   |
| `patreon`             | `/api/v1/external/patreon`                                            | `zerver.webhooks.patreon.view.api_patreon_webhook`                         |
| `pingdom`             | `/api/v1/external/pingdom`                                            | `zerver.webhooks.pingdom.view.api_pingdom_webhook`                         |
| `radarr`              | `/api/v1/external/radarr`                                             | `zerver.webhooks.radarr.view.api_radarr_webhook`                           |
| `raygun`              | `/api/v1/external/raygun`                                             | `zerver.webhooks.raygun.view.api_raygun_webhook`                           |
| `redmine`             | `/api/v1/external/redmine`                                            | `zerver.webhooks.redmine.view.api_redmine_webhook`                         |
| `reviewboard`         | `/api/v1/external/reviewboard`                                        | `zerver.webhooks.reviewboard.view.api_reviewboard_webhook`                 |
| `rhodecode`           | `/api/v1/external/rhodecode`                                          | `zerver.webhooks.rhodecode.view.api_rhodecode_webhook`                     |
| `rundeck`             | `/api/v1/external/rundeck`                                            | `zerver.webhooks.rundeck.view.api_rundeck_webhook`                         |
| `semaphore`           | `/api/v1/external/semaphore`                                          | `zerver.webhooks.semaphore.view.api_semaphore_webhook`                     |
| `sentry`              | `/api/v1/external/sentry`                                             | `zerver.webhooks.sentry.view.api_sentry_webhook`                           |
| `shortcut`            | `/api/v1/external/shortcut`, `/api/v1/external/clubhouse`             | `zerver.webhooks.shortcut.view.api_shortcut_webhook`                       |
| `slack`               | `/api/v1/external/slack`                                              | `zerver.webhooks.slack.view.api_slack_webhook`                             |
| `slack_incoming`      | `/api/v1/external/slack_incoming`                                     | `zerver.webhooks.slack_incoming.view.api_slack_incoming_webhook`           |
| `sonarqube`           | `/api/v1/external/sonarqube`                                          | `zerver.webhooks.sonarqube.view.api_sonarqube_webhook`                     |
| `sonarr`              | `/api/v1/external/sonarr`                                             | `zerver.webhooks.sonarr.view.api_sonarr_webhook`                           |
| `splunk`              | `/api/v1/external/splunk`                                             | `zerver.webhooks.splunk.view.api_splunk_webhook`                           |
| `statuspage`          | `/api/v1/external/statuspage`                                         | `zerver.webhooks.statuspage.view.api_statuspage_webhook`                   |
| `stripe`              | `/api/v1/external/stripe`                                             | `zerver.webhooks.stripe.view.api_stripe_webhook`                           |
| `taiga`               | `/api/v1/external/taiga`                                              | `zerver.webhooks.taiga.view.api_taiga_webhook`                             |
| `teamcity`            | `/api/v1/external/teamcity`                                           | `zerver.webhooks.teamcity.view.api_teamcity_webhook`                       |
| `thinkst`             | `/api/v1/external/thinkst`                                            | `zerver.webhooks.thinkst.view.api_thinkst_webhook`                         |
| `transifex`           | `/api/v1/external/transifex`                                          | `zerver.webhooks.transifex.view.api_transifex_webhook`                     |
| `travis`              | `/api/v1/external/travis`                                             | `zerver.webhooks.travis.view.api_travis_webhook`                           |
| `trello`              | `/api/v1/external/trello`                                             | `zerver.webhooks.trello.view.api_trello_webhook`                           |
| `updown`              | `/api/v1/external/updown`                                             | `zerver.webhooks.updown.view.api_updown_webhook`                           |
| `uptimerobot`         | `/api/v1/external/uptimerobot`                                        | `zerver.webhooks.uptimerobot.view.api_uptimerobot_webhook`                 |
| `wekan`               | `/api/v1/external/wekan`                                              | `zerver.webhooks.wekan.view.api_wekan_webhook`                             |
| `wordpress`           | `/api/v1/external/wordpress`                                          | `zerver.webhooks.wordpress.view.api_wordpress_webhook`                     |
| `zabbix`              | `/api/v1/external/zabbix`                                             | `zerver.webhooks.zabbix.view.api_zabbix_webhook`                           |
| `zapier`              | `/api/v1/external/zapier`                                             | `zerver.webhooks.zapier.view.api_zapier_webhook`                           |
| `zendesk`             | `/api/v1/external/zendesk`                                            | `zerver.webhooks.zendesk.view.api_zendesk_webhook`                         |
