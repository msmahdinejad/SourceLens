# شارژ هنگام خرید — دموی دوزبانهٔ محصول

[English](README.md) · [گزارش PDF فارسی](outputs/SourceLens-demo-evaluation-fa.pdf) ·
[پاورپوینت گزارش فارسی](outputs/SourceLens-demo-evaluation-fa.pptx) ·
[گزارش PDF انگلیسی](outputs/SourceLens-demo-evaluation-en.pdf)

این صفحه نتیجهٔ یک اجرای واقعی و سرتاسری SourceLens است. برنامه **۷ منبع** را به **۲۳۶ قطعه**
تبدیل کرد و هر **۱۳ قالب استودیو** را به‌طور جداگانه در دو زبان انگلیسی و فارسی ساخت؛ یعنی در مجموع
**۲۶ خروجی**. محتوای خروجی‌ها پس از دریافت بازنویسی نشده است. گزارش‌های ارزیابی نیز با استفاده از
تصاویر واقعی رابط و فایل‌های خروجی همین اجرا آماده شده‌اند.

![هفت منبع آماده در فضای کاری دمو](assets/02-sources-ready-en.png)

## پرسش دمو

آیا پارکینگ فروشگاه یا مرکز خرید می‌تواند به ایستگاه شارژی راحت، قابل‌اعتماد، سازگار و هماهنگ با
شبکهٔ برق تبدیل شود؟ روایت دمو یک تجربهٔ آشنا را دنبال می‌کند: رسیدن، اتصال خودرو، خرید، بررسی وضعیت
شارژ و ادامهٔ مسیر. این روایت در کنار تجربهٔ راننده، استانداردهای عمومی، راهبرد خرده‌فروشی و محدودیت‌های
شبکهٔ برق را نیز بررسی می‌کند.

فضای کاری شامل دو مطالعه با مجوز CC BY 4.0 و پنج منبع وب منتخب است که از مسیر Search وارد شده‌اند.
گفته‌های شرکت‌ها به‌عنوان ادعای همان شرکت برچسب خورده‌اند، نه نتیجهٔ مستقل. جزئیات منشأ و مجوزها در
[یادداشت منابع](source-research.md) آمده است.

## خروجی‌های فارسی

[فهرست ماشین‌خوان](outputs/fa/index.json)، عنوان، نوع، اندازه و هش SHA-256 همهٔ خروجی‌های فارسی را
ثبت می‌کند.

| ردیف | قالب استودیو | دریافت |
| ---: | --- | --- |
| ۱ | مرور صوتی | [MP3](outputs/fa/charge-while-you-shop-audio-overview.mp3) · [رونوشت](outputs/fa/charge-while-you-shop-audio-overview-transcript.txt) |
| ۲ | گزارش | [Markdown](outputs/fa/charge-while-you-shop-report.md) |
| ۳ | نقشهٔ مفهومی | [JSON](outputs/fa/charge-while-you-shop-concept-map.json) |
| ۴ | فلش‌کارت | [JSON](outputs/fa/charge-while-you-shop-flashcards.json) |
| ۵ | آزمون | [JSON](outputs/fa/charge-while-you-shop-quiz.json) |
| ۶ | اطلاع‌نگاشت | [JSON](outputs/fa/charge-while-you-shop-infographic.json) |
| ۷ | جدول داده | [JSON](outputs/fa/charge-while-you-shop-data-table.json) |
| ۸ | نمودار | [JSON](outputs/fa/charge-while-you-shop-chart.json) |
| ۹ | خط زمانی | [JSON](outputs/fa/charge-while-you-shop-timeline.json) |
| ۱۰ | مقایسهٔ منابع | [Markdown](outputs/fa/charge-while-you-shop-comparison.md) |
| ۱۱ | تحلیل شکاف پژوهشی | [Markdown](outputs/fa/charge-while-you-shop-research-gap.md) |
| ۱۲ | مجموعه‌اسلاید ویرایش‌پذیر | **[دریافت PowerPoint](https://github.com/msmahdinejad/SourceLens/raw/main/docs/demo/outputs/fa/charge-while-you-shop-slide-deck.pptx)** · [مانیفست](outputs/fa/charge-while-you-shop-slide-deck-manifest.json) |
| ۱۳ | مرور ویدیویی ۶۰ ثانیه‌ای | **[دریافت MP4](https://github.com/msmahdinejad/SourceLens/raw/main/docs/demo/outputs/fa/charge-while-you-shop-video-overview.mp4)** · [زیرنویس](outputs/fa/charge-while-you-shop-video-overview-captions.srt) · [خط زمانی](outputs/fa/charge-while-you-shop-video-overview-timeline.json) · [مانیفست](outputs/fa/charge-while-you-shop-video-overview-manifest.json) · [اعتبار موسیقی](outputs/fa/audio-credits.txt) |

مجموعهٔ کامل انگلیسی در [صفحهٔ انگلیسی دمو](README.md) و
[فهرست خروجی‌های انگلیسی](outputs/en/index.json) در دسترس است.

## بخش‌های آزموده‌شده

### ورود منبع و جست‌وجوی وب

![پردازش PDF در SourceLens](assets/01-source-ingestion-en.png)

این اجرا استخراج PDF، قطعه‌بندی، نمایه‌سازی، بررسی نتیجه پیش از ورود از Search، انتخاب منبع، استناد و
پنل شواهد را پوشش داد.

### پژوهش ساختاریافته و ابزارهای یادگیری

![نقشهٔ مفهومی مستند](assets/06-concept-map-en.png)

![فلش‌کارت‌های مستند](assets/08-flashcards-en.png)

گزارش، نقشهٔ مفهومی، فلش‌کارت، آزمون، جدول، نمودار، خط زمانی، مقایسهٔ منابع و تحلیل شکاف پژوهشی، همگی
از همان مرز هفت‌منبعی ساخته شدند.

### پاورپوینت، صوت و ویدیو

![مجموعه‌اسلاید تکمیل‌شده در استودیو](assets/13-slide-deck-studio-en.png)

![مرور ویدیویی تکمیل‌شده در استودیو](assets/15-video-studio-en.png)

مجموعه‌اسلایدهای هر دو زبان ویرایش‌پذیرند. هر دو ویدیو دقیقاً ۶۰ ثانیه، با وضوح Full HD، کدگذاری
H.264/AAC، رسانه‌های یکتای تأییدشده، گفتار با سرعت طبیعی، حاشیهٔ امن متن، زیرنویس، خط زمانی و مانیفست
منشأ ساخته شده‌اند. مرورهای صوتی نیز همراه رونوشت تولیدشده ارائه شده‌اند.
موسیقی‌های Jamendo مجوز CC BY-NC 3.0 دارند و ویدیوهای دمو فقط برای استفادهٔ غیرتجاری‌اند؛ نام قطعه،
هنرمند، منبع و پیوند مجوز در فایل‌های اعتباردهی ثبت شده است.

### گفت‌وگوی فارسی

پاسخ فارسی از همان منابع منتخب ساخته شد و رابط راست‌به‌چپ، انتخاب منبع، استناد، پنل شواهد و رویدادهای
زندهٔ تولید را در تمام مسیر حفظ کرد.

## گزارش‌های ارزیابی

گزارش کوتاه و مینیمال انتشار در هر دو زبان به‌صورت PDF و PowerPoint ویرایش‌پذیر موجود است:

- [PDF فارسی](outputs/SourceLens-demo-evaluation-fa.pdf) ·
  [PowerPoint فارسی](outputs/SourceLens-demo-evaluation-fa.pptx)
- [PDF انگلیسی](outputs/SourceLens-demo-evaluation-en.pdf) ·
  [PowerPoint انگلیسی](outputs/SourceLens-demo-evaluation-en.pptx)

در گزارش‌ها تنها چند نمای منتخب نمایش داده شده و نسخهٔ کامل اسلاید و ویدیو از طریق لینک مستقیم قابل
دریافت است. خروجی‌های بصری فارسی و گزارش فارسی با تایپوگرافی کلمه ساخته شده‌اند.

## نتیجه

هر ۲۶ خروجی استودیو از مسیرهای خود برنامه به وضعیت آماده و قابل دانلود رسیدند. اعتبارسنجی انتشار،
فهرست هر دو زبان، هش فایل‌ها، قالب خروجی، ساختار اسلاید، زمان و کدک ویدیو، انتخاب فونت و حریم خصوصی
مانیفست عمومی را بررسی می‌کند.
