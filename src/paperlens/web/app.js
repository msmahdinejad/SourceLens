import {
  applyStudioEvent,
  applyStudioMutation,
  applyStudioSnapshot,
  isCurrentStudioRequest,
  isCurrentWorkspaceRequest,
  markStudioReconnecting,
  mergeStudioArtifactDetail,
  patchKeyedStudioRows,
  studioStatusCategory,
} from "./studio_live.mjs?v=20260808-live-progress-3";
import {createApiClient} from "./api.mjs";
import {escapeHtml} from "./rendering.mjs";
import {
  createAppState,
  LOCALE_KEY,
  SESSION_KEY,
  VIEW_NAMES,
  VIEW_TITLES as titles,
  WORKSPACE_KEY,
} from "./state.mjs";

const $ = (selector, root = document) => root.querySelector(selector);
const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];
const requestedLocale = new URLSearchParams(location.search).get("lang");

const translations = {
  fa: {
    "nav.workspace": "فضای کاری", "nav.newChat": "گفت‌وگوی جدید", "nav.research": "محیط پژوهش", "nav.chat": "پرسش و پاسخ", "nav.documents": "منابع و پردازش", "nav.vector": "پایگاه برداری", "nav.traces": "جریان درخواست", "nav.system": "سامانه", "nav.settings": "معماری و تنظیمات", "nav.demo": "دموی شارژ هنگام خرید", "nav.recent": "گفت‌وگوهای اخیر", "nav.clear": "پاک‌کردن", "nav.addSource": "افزودن منبع",
    "nav.workspaces": "فضاهای کاری", "nav.history": "تاریخچه و خروجی‌ها", "nav.providers": "ارائه‌دهندگان", "search.global": "جست‌وجو در منابع، گفتگوها و خروجی‌ها",
    "workspaces.title": "فضاهای کاری", "workspaces.subtitle": "منابع، گفتگوها، خروجی‌ها و پایگاه دانش هر پروژه را جدا و منظم نگه دارید.",
    "history.title": "تاریخچه و خروجی‌ها", "history.subtitle": "گفتگوهای قبلی و خروجی‌های Studio را برای ادامهٔ پژوهش، مطالعه یا دانلود پیدا کنید.",
    "providers.title": "ارائه‌دهندگان هوش مصنوعی و OCR", "providers.subtitle": "کلیدهای شخصی خود را محلی ثبت کنید و مسیرهای آمادهٔ متن، تصویر، صوت و OCR را ببینید.", "providers.add": "افزودن یا بروزرسانی کلید", "providers.private": "کلیدها خصوصی می‌مانند", "providers.privateHint": "کلیدها فقط در فایل محلی .env ذخیره می‌شوند و هیچ‌وقت دوباره در پاسخ API یا رابط نمایش داده نمی‌شوند.", "providers.connect": "اتصال ارائه‌دهنده", "providers.connectHint": "یک یا چند کلید را با ویرگول یا در خط‌های جدا وارد کنید. مقدار ذخیره‌شده دوباره نمایش داده نمی‌شود.", "providers.provider": "ارائه‌دهنده", "providers.keys": "کلیدهای API", "providers.keysHint": "یک یا چند کلید API را وارد کنید", "providers.replaceHint": "ذخیره، کلیدهای فعلی این ارائه‌دهنده را جایگزین می‌کند.", "providers.save": "ذخیرهٔ امن",
    "documents.title": "منابع فضای کاری", "documents.subtitle": "فایل‌ها را اضافه کنید و مراحل استخراج، OCR، تحلیل تصویری و ساخت پایگاه را زنده ببینید.", "documents.add": "منابع تازه اضافه کنید", "documents.addHint": "چند فایل را هم‌زمان انتخاب کنید؛ پردازش هرکدام مستقل ادامه پیدا می‌کند.", "documents.drop": "فایل‌ها را اینجا رها کنید", "documents.browse": "یا برای انتخاب از دستگاه کلیک کنید", "documents.upload": "بارگذاری و پردازش", "documents.paste": "افزودن متن بدون فایل", "documents.pasteHint": "یادداشت یا متن خام را مستقیماً وارد کنید", "documents.selectedEmpty": "فایل‌های انتخاب‌شده اینجا نمایش داده می‌شوند",
    "vector.title": "پایگاه دانش فضای کاری", "vector.rebuild": "بازسازی پایگاه", "vector.search": "مرور قطعه‌های قابل بازیابی", "traces.title": "جریان کامل درخواست", "traces.subtitle": "بازیابی، مدل، fallback، زمان پاسخ و نتیجه را یک‌جا دنبال کنید.", "traces.refresh": "به‌روزرسانی", "settings.title": "معماری و وضعیت سامانه", "settings.subtitle": "نمای زندهٔ providerها، ظرفیت پردازش و مسیر واقعی داده در backend.",
    "sources.title": "منابع", "sources.help": "منابع مورد استفاده در پاسخ و Studio را انتخاب کنید.", "sources.all": "همهٔ منابع آماده", "sources.manage": "مدیریت و مشاهدهٔ پردازش",
    "workspace.active": "فضای فعال", "metrics.sources": "منبع", "metrics.chunks": "قطعه", "chat.scope": "دامنهٔ پاسخ",
    "chat.placeholder": "دربارهٔ منابع خود به فارسی یا English بپرسید…", "chat.sendHint": "ارسال", "chat.newlineHint": "خط جدید",
    "studio.title": "استودیو", "studio.help": "از منابع انتخاب‌شده خروجی حرفه‌ای و قابل دانلود بسازید.", "studio.summary": "خلاصهٔ عمیق", "studio.summaryHint": "یافته‌ها، روش‌ها و نتیجه‌ها",
    "studio.briefing": "گزارش اجرایی", "studio.briefingHint": "نکات کلیدی و پیامدها", "studio.guide": "راهنمای مطالعه", "studio.guideHint": "مفاهیم و مرور ساختاریافته",
    "studio.quiz": "کوئیز", "studio.quizHint": "سؤال چندگزینه‌ای با توضیح", "studio.chart": "نمودار داده", "studio.chartHint": "مقایسه و روندهای عددی",
    "studio.timeline": "خط زمانی", "studio.timelineHint": "رویدادها و مراحل مهم", "studio.faq": "پرسش‌های متداول", "studio.faqHint": "پاسخ‌های کوتاه و مستند",
    "studio.flashcards": "فلش‌کارت", "studio.flashcardsHint": "مرور سریع مفاهیم و تعریف‌ها", "studio.comparison": "مقایسهٔ منابع", "studio.comparisonHint": "توافق‌ها، اختلاف‌ها و محدودیت‌ها", "studio.conceptMap": "نقشهٔ مفهومی", "studio.conceptMapHint": "رابطهٔ ایده‌ها و مؤلفه‌ها", "studio.survey": "مینی‌سروی پژوهشی", "studio.surveyHint": "مرور دانشگاهی کوتاه و مستند", "studio.gap": "شکاف پژوهشی", "studio.gapHint": "پرسش‌های باز و شواهد ناکافی",
    "studio.library": "خروجی‌های اخیر", "studio.empty": "خروجی‌های ساخته‌شده اینجا ظاهر می‌شوند.", "studio.download": "دانلود خروجی",
    "evidence.title": "شواهد پاسخ", "evidence.empty": "بعد از هر پاسخ، شاهدهای صفحه‌ای اینجا ظاهر می‌شوند.",
    "welcome.kicker": "پژوهش سریع‌تر، با شاهد قابل بررسی", "welcome.title": "از میان منابع خود، پاسخ دقیق پیدا کنید.",
    "welcome.body": "اسناد، تصاویر، صدا و یادداشت‌های شما به یک فضای پژوهشی قابل جست‌وجو تبدیل می‌شوند؛ پاسخ‌ها همراه با شاهد قابل بررسی ارائه می‌شوند.",
    "welcome.scan": "مرور منابع", "welcome.scanHint": "موضوع‌ها و نکات کلیدی", "welcome.compare": "مقایسه", "welcome.compareHint": "روش، نتیجه و محدودیت",
    "welcome.survey": "گزارش پژوهش", "welcome.surveyHint": "خروجی آمادهٔ ارائه", "welcome.scanQuestion": "این اسناد درباره چه موضوع‌هایی هستند؟",
    "welcome.compareQuestion": "روش‌ها، یافته‌ها و محدودیت‌های اسناد را مقایسه کن.", "welcome.surveyQuestion": "یک مرور پژوهشی مستند از منابع انتخاب‌شده بساز.",
    "chat.thinking": "در حال بررسی منابع", "chat.new": "گفت‌وگوی جدید", "chat.failed": "درخواست ناموفق بود", "chat.allReady": "همهٔ منابع آماده",
    "chat.selectedSources": "{count} منبع انتخاب‌شده", "chat.attachImage": "تصویر همراه پرسش", "chat.removeImage": "حذف تصویر", "chat.imageReady": "آماده برای OCR و تحلیل بصری", "chat.analyzeImage": "این تصویر را تحلیل کن.",
    "status.ready": "آماده", "status.processing": "در حال پردازش", "status.failed": "ناموفق", "status.uploaded": "در انتظار", "status.unknownLanguage": "زبان نامشخص",
    "studio.generating": "در حال ساخت", "studio.ready": "آماده", "studio.failed": "ناموفق", "studio.selectSource": "برای ساخت خروجی، حداقل یک منبع آماده انتخاب کنید.",
    "studio.started": "ساخت خروجی در Studio آغاز شد.", "studio.sources": "{count} منبع", "studio.questions": "{count} سؤال", "studio.answer": "نمایش پاسخ و توضیح",
    "evidence.page": "ص.{page}", "evidence.visual": "خلاصهٔ بصری", "evidence.previewFailed": "پیش‌نمایش صفحه در دسترس نیست؛ متن شاهد نمایش داده شد.",
    "system.checking": "در حال بررسی سامانه…", "upload.max": "حداکثر حجم هر فایل: ۱۰۰ مگابایت", "paste.title": "عنوان", "paste.text": "متن", "paste.note": "یادداشت جدید", "paste.placeholder": "متن فارسی یا انگلیسی را اینجا وارد کنید…", "paste.submit": "افزودن و پردازش",
    "legend.text": "متن داخلی", "legend.visual": "تحلیل بصری", "legend.failed": "ناموفق", "vector.strategy": "قطعه‌بندی بخش‌محور با حفظ صفحه؛ جدول، شکل، OCR و خلاصهٔ بصری قابل بازیابی‌اند.", "vector.mix": "روش‌های استخراج", "vector.locations": "بخش‌ها و صفحه‌های پرتکرار", "vector.query": "عبارت جست‌وجو", "vector.queryPlaceholder": "در متن قطعه‌ها جست‌وجو کنید…", "vector.submit": "جست‌وجو",
    "traces.select": "یک درخواست را انتخاب کنید", "traces.selectHint": "جزئیات کامل اجرا و زمان‌بندی اینجا نمایش داده می‌شود.", "architecture.ingest": "دریافت منابع", "architecture.ingestHint": "PDF، متن، تصویر و صوت", "architecture.extract": "استخراج هوشمند", "architecture.extractHint": "متن، OCR، تحلیل بصری یا رونویسی", "architecture.index": "آماده‌سازی جست‌وجو", "architecture.indexHint": "قطعه‌بندی و ساخت پایگاه برداری", "architecture.retrieve": "بازیابی شواهد", "architecture.retrieveHint": "جست‌وجوی معنایی و واژگانی", "architecture.gate": "بررسی کفایت", "architecture.gateHint": "تفکیک شاهد از پاسخ عمومی", "architecture.answer": "پاسخ و گزارش", "architecture.answerHint": "تحلیل، منبع و جریان درخواست",
    "demo.title": "شارژ هنگام خرید", "demo.body": "دو منبع دسترسی‌آزاد دربارهٔ نیاز رانندگان و اجرای زیرساخت عمومی شارژ خودروهای برقی.", "demo.add": "افزودن دموی شارژ هنگام خرید", "demo.emptyTitle": "دموی پژوهشی", "demo.empty": "برای نمایش منابع، فضای دمو را انتخاب کنید.", "drawer.title": "شاهد صفحه‌ای", "drawer.body": "متن استخراج‌شده و تصویر اصلی صفحه را کنار هم بررسی کنید.", "drawer.open": "بازکردن منبع اصلی", "drawer.text": "متن استخراج‌شده", "drawer.image": "تصویر صفحه",
    "workspace.new": "فضای کاری جدید", "workspace.newHint": "منابع مربوط به یک پروژه را در فضای جدا نگه دارید.", "workspace.name": "نام فضای کاری", "workspace.placeholder": "برای نمونه: پایان‌نامه", "common.cancel": "انصراف", "workspace.create": "ایجاد فضای کاری", "rebuild.title": "بازسازی پایگاه برداری؟", "rebuild.hint": "نسخهٔ تازه ساخته می‌شود و فقط پس از تکمیل جایگزین نسخهٔ فعال خواهد شد.", "rebuild.start": "شروع بازسازی",
  },
  en: {
    "nav.workspace": "Workspace", "nav.newChat": "New conversation", "nav.research": "Research", "nav.chat": "Chat", "nav.documents": "Sources & processing", "nav.vector": "Vector store", "nav.traces": "Request flow", "nav.system": "System", "nav.settings": "Architecture & settings", "nav.demo": "Charge While You Shop demo", "nav.recent": "Recent conversations", "nav.clear": "Clear", "nav.addSource": "Add source",
    "nav.workspaces": "Workspaces", "nav.history": "History & outputs", "nav.providers": "Providers", "search.global": "Search sources, conversations, and outputs",
    "workspaces.title": "Workspaces", "workspaces.subtitle": "Keep each project's sources, conversations, outputs, and knowledge base separate.",
    "history.title": "History & outputs", "history.subtitle": "Find previous conversations and Studio outputs to continue, read, or download.",
    "providers.title": "AI & OCR providers", "providers.subtitle": "Connect your own keys locally and inspect available text, vision, audio, and OCR routes.", "providers.add": "Add or update keys", "providers.private": "Credentials stay private", "providers.privateHint": "Keys are stored only in the local .env file and are never returned by the API or shown again.", "providers.connect": "Connect a provider", "providers.connectHint": "Enter one or more keys separated by commas or new lines. Stored values are never displayed again.", "providers.provider": "Provider", "providers.keys": "API keys", "providers.keysHint": "Paste one or more API keys", "providers.replaceHint": "Saving replaces the current keys for this provider.", "providers.save": "Save securely",
    "documents.title": "Workspace sources", "documents.subtitle": "Add files and follow extraction, OCR, visual analysis, and indexing live.", "documents.add": "Add new sources", "documents.addHint": "Select multiple files; each one is processed independently.", "documents.drop": "Drop files here", "documents.browse": "or click to browse your device", "documents.upload": "Upload and process", "documents.paste": "Add text without a file", "documents.pasteHint": "Paste a note or raw text directly", "documents.selectedEmpty": "Selected files appear here",
    "vector.title": "Workspace knowledge base", "vector.rebuild": "Rebuild knowledge base", "vector.search": "Browse retrievable chunks", "traces.title": "Complete request flow", "traces.subtitle": "Follow retrieval, models, fallbacks, latency, and the final result.", "traces.refresh": "Refresh", "settings.title": "System architecture and status", "settings.subtitle": "Live provider, processing-capacity, and backend data-flow information.",
    "sources.title": "Sources", "sources.help": "Choose the sources used by chat and Studio.", "sources.all": "All ready sources", "sources.manage": "Manage sources and processing",
    "workspace.active": "Active workspace", "metrics.sources": "sources", "metrics.chunks": "chunks", "chat.scope": "Answer scope",
    "chat.placeholder": "Ask anything about your selected sources…", "chat.sendHint": "Send", "chat.newlineHint": "New line",
    "studio.title": "Studio", "studio.help": "Create polished, downloadable outputs from selected sources.", "studio.summary": "Deep summary", "studio.summaryHint": "Findings, methods, and conclusions",
    "studio.briefing": "Executive brief", "studio.briefingHint": "Key points and implications", "studio.guide": "Study guide", "studio.guideHint": "Concepts and structured review",
    "studio.quiz": "Quiz", "studio.quizHint": "Multiple choice with explanations", "studio.chart": "Data chart", "studio.chartHint": "Comparisons and numeric trends",
    "studio.timeline": "Timeline", "studio.timelineHint": "Events and important stages", "studio.faq": "FAQ", "studio.faqHint": "Short, cited answers",
    "studio.flashcards": "Flashcards", "studio.flashcardsHint": "Fast review of concepts and definitions", "studio.comparison": "Source comparison", "studio.comparisonHint": "Agreements, conflicts, and limitations", "studio.conceptMap": "Concept map", "studio.conceptMapHint": "Relationships between ideas and components", "studio.survey": "Research mini-survey", "studio.surveyHint": "A compact, cited academic overview", "studio.gap": "Research gaps", "studio.gapHint": "Open questions and missing evidence",
    "studio.library": "Recent outputs", "studio.empty": "Generated outputs will appear here.", "studio.download": "Download output",
    "evidence.title": "Answer evidence", "evidence.empty": "Page-level evidence will appear here after an answer.",
    "welcome.kicker": "Research faster with inspectable evidence", "welcome.title": "Find precise answers across your sources.",
    "welcome.body": "Documents, images, audio, and notes become a searchable research workspace with evidence you can inspect.",
    "welcome.scan": "Scan", "welcome.scanHint": "Topics and key ideas", "welcome.compare": "Compare", "welcome.compareHint": "Methods, results, and limits",
    "welcome.survey": "Overview", "welcome.surveyHint": "Presentation-ready", "welcome.scanQuestion": "What are the main topics across these sources?",
    "welcome.compareQuestion": "Compare the methods, findings, and limitations of the selected sources.", "welcome.surveyQuestion": "Create a cited research overview of the selected sources.",
    "chat.thinking": "Reviewing selected sources", "chat.new": "New conversation", "chat.failed": "Request failed", "chat.allReady": "All ready sources",
    "chat.selectedSources": "{count} selected sources", "chat.attachImage": "Prompt image", "chat.removeImage": "Remove image", "chat.imageReady": "Ready for OCR and visual analysis", "chat.analyzeImage": "Analyze this image.",
    "status.ready": "Ready", "status.processing": "Processing", "status.failed": "Failed", "status.uploaded": "Queued", "status.unknownLanguage": "Unknown language",
    "studio.generating": "Generating", "studio.ready": "Ready", "studio.failed": "Failed", "studio.selectSource": "Select at least one ready source before creating an output.",
    "studio.started": "Studio generation started.", "studio.sources": "{count} sources", "studio.questions": "{count} questions", "studio.answer": "Show answer and explanation",
    "evidence.page": "p.{page}", "evidence.visual": "Visual summary", "evidence.previewFailed": "Page preview is unavailable; showing retrieved evidence instead.",
    "system.checking": "Checking system…", "upload.max": "Maximum size per file: 100 MB", "paste.title": "Title", "paste.text": "Text", "paste.note": "New note", "paste.placeholder": "Paste Persian or English text here…", "paste.submit": "Add and process",
    "legend.text": "Embedded text", "legend.visual": "Visual analysis", "legend.failed": "Failed", "vector.strategy": "Section-aware, page-preserving chunks; tables, figures, OCR, and visual summaries stay retrievable.", "vector.mix": "Extraction methods", "vector.locations": "Frequent sections and pages", "vector.query": "Search query", "vector.queryPlaceholder": "Search indexed chunks…", "vector.submit": "Search",
    "traces.select": "Select a request", "traces.selectHint": "Complete execution and scheduling details will appear here.", "architecture.ingest": "Source intake", "architecture.ingestHint": "PDF, text, image, and audio", "architecture.extract": "Smart extraction", "architecture.extractHint": "Text, OCR, visual analysis, or transcription", "architecture.index": "Search preparation", "architecture.indexHint": "Chunking and vector indexing", "architecture.retrieve": "Evidence retrieval", "architecture.retrieveHint": "Semantic and lexical search", "architecture.gate": "Sufficiency check", "architecture.gateHint": "Grounded evidence or general answer", "architecture.answer": "Answer and output", "architecture.answerHint": "Analysis, citations, and request flow",
    "demo.title": "Charge While You Shop", "demo.body": "Two open-access sources on driver needs and public EV charging infrastructure.", "demo.add": "Add Charge While You Shop demo", "demo.emptyTitle": "Research demo", "demo.empty": "Select the demo workspace to display its sources.", "drawer.title": "Page evidence", "drawer.body": "Inspect extracted text beside the original page image.", "drawer.open": "Open original source", "drawer.text": "Extracted text", "drawer.image": "Page image",
    "workspace.new": "New workspace", "workspace.newHint": "Keep each project's sources in an isolated workspace.", "workspace.name": "Workspace name", "workspace.placeholder": "For example: Thesis", "common.cancel": "Cancel", "workspace.create": "Create workspace", "rebuild.title": "Rebuild the vector store?", "rebuild.hint": "A new version is built and replaces the active collection only after completion.", "rebuild.start": "Start rebuild",
  },
};

Object.assign(translations.fa, {
  "activity.recent": "رویدادهای اخیر",
  "activity.detail": "جزئیات فعالیت",
  "providers.create": "افزودن ارائه‌دهنده",
  "providers.runtimeTitle": "رفتار pool و پردازش",
  "providers.runtimeHint": "مقادیر واقعی اجرا و بازیابی را بدون تغییر کد تنظیم کنید.",
  "providers.languageModel": "مدل زبانی",
  "providers.retrieval": "بازیابی",
  "providers.enabled": "فعال",
  "providers.runtimeSave": "ذخیره تنظیمات اجرا",
  "providers.podcastRuntime": "مرور صوتی",
  "providers.podcastRuntimeHint": "این مقادیر برای هر نوبت صوتی اعمال می‌شوند. سهمیه Gemini در سطح پروژه محاسبه می‌شود.",
  "providers.registryTitle": "اتصال ارائه‌دهنده",
  "providers.registryHint": "مشخصات اتصال، مدل‌ها و اولویت‌های واقعی pool را تنظیم کنید.",
  "providers.connectionId": "شناسه اتصال",
  "providers.displayName": "نام نمایشی",
  "providers.apiKeys": "کلیدهای API",
  "providers.keysKeep": "برای حفظ کلیدهای فعلی خالی بگذارید",
  "providers.modelsTitle": "مدل‌ها و اولویت",
  "providers.modelsHint": "ترتیب این فهرست، ترتیب تلاش مدل‌ها در pool است.",
  "providers.addModel": "افزودن مدل",
  "providers.connectionEnabled": "این اتصال فعال باشد",
  "providers.storageHint": "کلیدها فقط در .env ذخیره می‌شوند؛ اطلاعات اتصال بدون کلید در providers.json قرار می‌گیرد.",
  "providers.delete": "حذف اتصال",
  "providers.saveSettings": "ذخیره تنظیمات",
  "studio.report": "گزارش",
  "studio.reportHint": "خلاصه، راهنمای مطالعه، FAQ و قالب‌های پژوهشی",
  "studio.conceptMap": "ذهن‌نگاشت",
  "studio.conceptMapHint": "مفاهیم و رابطه‌های میان آن‌ها",
  "studio.quiz": "آزمون",
  "studio.quizHint": "سؤال‌های قابل تنظیم همراه با پاسخ تشریحی",
  "studio.infographic": "اطلاع‌نگاشت",
  "studio.infographicHint": "روایت بصری از نکات، فرایندها و اعداد",
  "studio.dataTable": "جدول داده‌ها",
  "studio.dataTableHint": "استخراج و سازمان‌دهی اطلاعات قابل مقایسه",
  "studio.audioOverview": "مرور صوتی",
  "studio.audioOverviewHint": "پادکست شخصی‌سازی‌شده با یک تا پنج گوینده",
  "studio.customizeHint": "منابع و جزئیات خروجی را پیش از ساخت تنظیم کنید.",
  "studio.sourcesTitle": "منابع این خروجی",
  "studio.sourcesHint": "انتخاب فعلی شما به‌صورت پیش‌فرض اعمال شده است.",
  "studio.selectAll": "انتخاب همه",
  "studio.customTitle": "عنوان دلخواه",
  "studio.customTitlePlaceholder": "اختیاری",
  "studio.language": "زبان خروجی",
  "studio.languageAuto": "خودکار",
  "studio.details": "جزئیات و شخصی‌سازی",
  "studio.defaultsHint": "تنظیمات پیشنهادی از قبل انتخاب شده‌اند.",
  "studio.create": "ساخت خروجی",
  "studio.loadingSetup": "در حال آماده‌سازی تنظیمات…",
  "studio.creating": "در حال ثبت خروجی…",
  "studio.slideDeck": "مجموعه اسلاید",
  "studio.slideDeckHint": "پاورپوینت قابل ویرایش با روایت، نمودار و تصویر",
  "studio.videoOverview": "مرور ویدیویی",
  "studio.videoOverviewHint": "ویدیوی مستند با زیرنویس، رسانه و روایت اختیاری",
  "webSources.title": "یافتن منبع در وب",
  "webSources.body": "موضوع را بدهید؛ Exa و Tavily منابع را پیدا می‌کنند و فقط مواردی که انتخاب می‌کنید با متن کامل وارد این فضای کاری می‌شوند.",
  "webSources.open": "جست‌وجوی منابع",
  "webSources.dialogTitle": "یافتن منابع معتبر",
  "webSources.dialogBody": "نتایج را بررسی کنید و فقط منابع مناسب را برای دریافت متن کامل انتخاب کنید.",
  "webSources.query": "موضوع یا پرسش پژوهش",
  "webSources.queryPlaceholder": "برای نمونه: تأثیر مدل‌های زبانی در آموزش برنامه‌نویسی",
  "webSources.mode": "نحوهٔ تنظیم جست‌وجو",
  "webSources.automatic": "خودکار — پیشنهادی",
  "webSources.custom": "شخصی‌سازی",
  "webSources.count": "تعداد نتیجه",
  "webSources.engines": "موتورهای جست‌وجو",
  "webSources.exaDepth": "عمق Exa",
  "webSources.tavilyDepth": "عمق Tavily",
  "webSources.contentType": "نوع محتوا",
  "webSources.category": "دستهٔ Exa",
  "webSources.includeDomains": "فقط این دامنه‌ها",
  "webSources.excludeDomains": "حذف این دامنه‌ها",
  "webSources.startDate": "از تاریخ",
  "webSources.endDate": "تا تاریخ",
  "webSources.freshness": "تازگی محتوا",
  "webSources.search": "جست‌وجو",
  "webSources.results": "منابع پیشنهادی",
  "webSources.toggleAll": "انتخاب/لغو همه",
  "webSources.back": "تغییر جست‌وجو",
  "webSources.import": "افزودن منابع انتخاب‌شده",
  "integrations.title": "اتصال‌های پژوهش و رسانه",
  "integrations.body": "کلیدهای جست‌وجو، تصویر، ویدیو و موسیقی را از رابط یا فایل .env مدیریت کنید.",
  "integrations.loading": "در حال بررسی…",
  "integrations.clearKeys": "حذف کلیدهای ذخیره‌شده",
  "integrations.clearIds": "حذف شناسه‌های ذخیره‌شده",
  "integrations.privacy": "مقادیر محرمانه پس از ذخیره هیچ‌وقت از API بازگردانده نمی‌شوند.",
  "integrations.save": "ذخیره اتصال‌ها",
});

Object.assign(translations.en, {
  "activity.recent": "Recent events",
  "activity.detail": "Activity details",
  "providers.create": "Add provider",
  "providers.runtimeTitle": "Pool and processing behavior",
  "providers.runtimeHint": "Tune live execution and retrieval settings without changing code.",
  "providers.languageModel": "Language model",
  "providers.retrieval": "Retrieval",
  "providers.enabled": "Enabled",
  "providers.runtimeSave": "Save runtime settings",
  "providers.podcastRuntime": "Audio overview",
  "providers.podcastRuntimeHint": "These values apply to each audio turn. Gemini quotas are enforced per project.",
  "providers.registryTitle": "Provider connection",
  "providers.registryHint": "Configure connection details, models, and actual pool priorities.",
  "providers.connectionId": "Connection ID",
  "providers.displayName": "Display name",
  "providers.apiKeys": "API keys",
  "providers.keysKeep": "Leave blank to keep the current keys",
  "providers.modelsTitle": "Models and priority",
  "providers.modelsHint": "This list defines the model attempt order inside the pool.",
  "providers.addModel": "Add model",
  "providers.connectionEnabled": "Enable this connection",
  "providers.storageHint": "Keys are stored only in .env; secret-free connection metadata is stored in providers.json.",
  "providers.delete": "Delete connection",
  "providers.saveSettings": "Save settings",
  "studio.report": "Report",
  "studio.reportHint": "Summaries, study guides, FAQ, and research formats",
  "studio.conceptMap": "Mind map",
  "studio.conceptMapHint": "Concepts and the relationships between them",
  "studio.quiz": "Quiz",
  "studio.quizHint": "Customizable questions with explained answers",
  "studio.infographic": "Infographic",
  "studio.infographicHint": "A visual narrative of ideas, processes, and figures",
  "studio.dataTable": "Data table",
  "studio.dataTableHint": "Extract and organize comparable information",
  "studio.audioOverview": "Audio overview",
  "studio.audioOverviewHint": "A customizable podcast with one to five voices",
  "studio.customizeHint": "Choose sources and tune the output before generation.",
  "studio.sourcesTitle": "Sources for this output",
  "studio.sourcesHint": "Your current source selection is applied by default.",
  "studio.selectAll": "Select all",
  "studio.customTitle": "Custom title",
  "studio.customTitlePlaceholder": "Optional",
  "studio.language": "Output language",
  "studio.languageAuto": "Automatic",
  "studio.details": "Details and customization",
  "studio.defaultsHint": "Recommended defaults are already selected.",
  "studio.create": "Create output",
  "studio.loadingSetup": "Loading customization options…",
  "studio.creating": "Creating output…",
  "studio.slideDeck": "Slide deck",
  "studio.slideDeckHint": "An editable PowerPoint with narrative, charts, and media",
  "studio.videoOverview": "Video overview",
  "studio.videoOverviewHint": "A source-grounded video with captions, media, and optional narration",
  "webSources.title": "Discover web sources",
  "webSources.body": "Enter a topic; Exa and Tavily find candidates, and only the sources you select are imported with full text.",
  "webSources.open": "Find sources",
  "webSources.dialogTitle": "Find credible sources",
  "webSources.dialogBody": "Review the results and select only the sources whose full text should be extracted.",
  "webSources.query": "Research topic or question",
  "webSources.queryPlaceholder": "For example: how language models affect programming education",
  "webSources.mode": "Search setup",
  "webSources.automatic": "Automatic — recommended",
  "webSources.custom": "Customize",
  "webSources.count": "Result count",
  "webSources.engines": "Search engines",
  "webSources.exaDepth": "Exa depth",
  "webSources.tavilyDepth": "Tavily depth",
  "webSources.contentType": "Content type",
  "webSources.category": "Exa category",
  "webSources.includeDomains": "Only these domains",
  "webSources.excludeDomains": "Exclude these domains",
  "webSources.startDate": "From date",
  "webSources.endDate": "To date",
  "webSources.freshness": "Content freshness",
  "webSources.search": "Search",
  "webSources.results": "Suggested sources",
  "webSources.toggleAll": "Select/deselect all",
  "webSources.back": "Change search",
  "webSources.import": "Add selected sources",
  "integrations.title": "Research and media integrations",
  "integrations.body": "Manage search, photo, video, and music credentials from the interface or .env.",
  "integrations.loading": "Checking…",
  "integrations.clearKeys": "Clear stored credentials",
  "integrations.clearIds": "Clear stored IDs",
  "integrations.privacy": "Secret values are never returned by the API after saving.",
  "integrations.save": "Save integrations",
});

function t(key, values = {}) {
  const template = translations[state?.locale || "fa"]?.[key] || translations.en[key] || key;
  return Object.entries(values).reduce((text, [name, value]) => text.replaceAll(`{${name}}`, value), template);
}

function l(persian, english) {
  return state.locale === "fa" ? persian : english;
}

const state = createAppState({requestedLocale});

function studioArtifactIsRunning(item) {
  return ["queued", "gathering", "drafting", "generating"].includes(item.status) || ["audio_queued", "resume_queued"].includes(item.stage);
}

function syncCancellationControls() {
  const button = $("#cancel-active-requests");
  if (!button) return;
  const hasStudioJob = state.studioArtifacts.some(studioArtifactIsRunning);
  button.classList.toggle("hidden", !hasStudioJob && state.activeRequests.size === 0);
  button.disabled = !hasStudioJob && state.activeRequests.size === 0;
  $("span", button).textContent = hasStudioJob
    ? l("لغو همهٔ درخواست‌های فعال", "Cancel active requests")
    : l("توقف درخواست جاری", "Stop current request");
}

const api = createApiClient({
  activeRequests: state.activeRequests,
  onActivity: syncCancellationControls,
  cancelledMessage: () => l("درخواست لغو شد.", "The request was cancelled."),
});

async function cancelActiveFrontendRequests() {
  [...state.activeRequests.values()].forEach(controller => controller.abort());
  await loadStudioArtifacts().catch(() => {});
  const running = state.studioArtifacts.filter(studioArtifactIsRunning);
  await Promise.allSettled(running.map(item => api(
    `/api/workspaces/${encodeURIComponent(item.workspace_id || state.activeWorkspaceId)}/studio/artifacts/${encodeURIComponent(item.artifact_id)}/cancel`,
    {method: "POST", body: {}, trackRequest: false},
  )));
  await loadStudioArtifacts().catch(() => {});
  syncCancellationControls();
  toast(l("درخواست‌های فعال متوقف شدند.", "Active requests were cancelled."));
}

function setLocale(locale, {persist = true} = {}) {
  state.locale = locale === "en" ? "en" : "fa";
  if (persist) localStorage.setItem(LOCALE_KEY, state.locale);
  document.documentElement.lang = state.locale;
  document.documentElement.dir = state.locale === "fa" ? "rtl" : "ltr";
  document.body.classList.toggle("locale-en", state.locale === "en");
  document.body.classList.toggle("locale-fa", state.locale === "fa");
  $$('[data-i18n]').forEach(element => { element.textContent = t(element.dataset.i18n); });
  $$('[data-i18n-placeholder]').forEach(element => { element.placeholder = t(element.dataset.i18nPlaceholder); });
  $$('[data-i18n-aria]').forEach(element => { element.setAttribute("aria-label", t(element.dataset.i18nAria)); });
  $$('[data-i18n-title]').forEach(element => { element.title = t(element.dataset.i18nTitle); });
  $$('[data-i18n-value]').forEach(element => { element.value = t(element.dataset.i18nValue); });
  $$('[data-i18n-empty]').forEach(element => { element.dataset.emptyLabel = t(element.dataset.i18nEmpty); });
  const currentView = location.hash.slice(1) || "chat";
  const pageTitles = {
    fa: {chat: "پرسش از منابع", workspaces: "فضاهای کاری", history: "تاریخچه و خروجی‌ها", documents: "منابع و پردازش", vector: "پایگاه دانش", traces: "فعالیت‌ها", providers: "ارائه‌دهندگان", settings: "تنظیمات و معماری", demo: "دموی موج‌شکن‌های زنده"},
    en: Object.fromEntries(Object.entries(titles).map(([key, value]) => [key, value[1]])),
  };
  if ($("#page-title")) $("#page-title").textContent = pageTitles[state.locale][currentView] || pageTitles[state.locale].chat;
  if ($("#page-eyebrow")) $("#page-eyebrow").textContent = titles[currentView]?.[0] || titles.chat[0];
  if ($("#language-toggle")) $("#language-toggle").setAttribute("aria-label", state.locale === "fa" ? "Switch to English" : "تغییر زبان به فارسی");
  if ($("#messages")) restoreSession();
  if ($("#source-scope-list")) renderDocumentFilters();
  if ($("#documents-list")) renderDocuments();
  if ($("#studio-artifacts")) renderStudioArtifacts();
  if ($("#workspace-cards")) renderWorkspaceCards();
  if ($("#history-library")) renderHistoryLibrary();
  if ($("#provider-settings") && state.providers.length) renderProviderSettings();
  if ($("#provider-runtime-form") && Object.keys(state.providerRuntime).length) renderProviderRuntimeSettings();
  if ($("#font-manager-list")) renderFontManager();
  if ($("#evidence-panel")) renderEvidence(state.evidence);
  if (document.readyState !== "loading") loadStatus();
  if (document.readyState !== "loading" && currentView === "vector") loadVectorStore();
  if (document.readyState !== "loading" && currentView === "traces") loadTraces(false);
  if (document.readyState !== "loading" && currentView === "providers") loadProviderSettings();
  if (document.readyState !== "loading" && currentView === "settings") loadSettings();
}

function faNumber(value) {
  return Number(value || 0).toLocaleString(state.locale === "fa" ? "fa-IR" : "en-US");
}

function relativeTime(value) {
  if (!value) return "—";
  const seconds = Math.max(0, (Date.now() - new Date(value).getTime()) / 1000);
  if (state.locale === "en") {
    if (seconds < 60) return `${faNumber(Math.round(seconds))}s ago`;
    if (seconds < 3600) return `${faNumber(Math.round(seconds / 60))}m ago`;
    return new Date(value).toLocaleString("en-US");
  }
  if (seconds < 60) return `${faNumber(Math.round(seconds))} ثانیه پیش`;
  if (seconds < 3600) return `${faNumber(Math.round(seconds / 60))} دقیقه پیش`;
  return new Date(value).toLocaleString("fa-IR");
}

let toastTimer;
function toast(message) {
  const element = $("#toast");
  element.textContent = message;
  element.classList.add("show");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => element.classList.remove("show"), 3500);
}

function setView(name, {syncHash = true} = {}) {
  if (!VIEW_NAMES.has(name)) name = "chat";
  $$(".view").forEach(view => view.classList.toggle("active", view.id === `view-${name}`));
  $$(".nav-item").forEach(item => item.classList.toggle("active", item.dataset.view === name));
  $("#page-eyebrow").textContent = titles[name][0];
  $("#page-title").textContent = state.locale === "en" ? titles[name][1] : ({chat: "پرسش از منابع", workspaces: "فضاهای کاری", history: "تاریخچه و خروجی‌ها", documents: "منابع و پردازش", vector: "پایگاه دانش", traces: "فعالیت‌ها", providers: "ارائه‌دهندگان", settings: "تنظیمات و معماری", demo: "دموی موج‌شکن‌های زنده"})[name];
  document.body.classList.remove("menu-open");
  $("#menu-button").setAttribute("aria-expanded", "false");
  if (syncHash && location.hash !== `#${name}`) history.replaceState(null, "", `#${name}`);
  if (name === "documents") loadDocuments();
  if (name === "workspaces") renderWorkspaceCards();
  if (name === "history") renderHistoryLibrary();
  if (name === "vector") loadVectorStore();
  if (name === "traces") loadTraces();
  if (name === "providers") Promise.all([loadProviderSettings(), loadFonts()]);
  if (name === "settings") loadSettings();
}

async function loadWorkspaces() {
  state.workspaces = await api("/api/workspaces");
  if (!state.workspaces.some(item => item.workspace_id === state.activeWorkspaceId)) {
    state.activeWorkspaceId = state.workspaces[0]?.workspace_id || "default";
  }
  const options = state.workspaces.map(workspace =>
    `<option value="${escapeHtml(workspace.workspace_id)}">${escapeHtml(workspace.name)}</option>`
  ).join("");
  $("#top-workspace-select").innerHTML = options;
  $("#top-workspace-select").value = state.activeWorkspaceId;
  renderWorkspaceCards();
  await selectWorkspace(state.activeWorkspaceId, {persist: false});
}

function renderWorkspaceCards() {
  const container = $("#workspace-cards");
  if (!container) return;
  container.innerHTML = state.workspaces.map(workspace => {
    const active = workspace.workspace_id === state.activeWorkspaceId;
    return `<article class="workspace-card ${active ? "active" : ""}">
      <div class="workspace-card-mark"><svg><use href="#i-layers"></use></svg></div>
      <div class="workspace-card-copy"><span>${active ? l("فضای فعال", "Active workspace") : l("فضای پژوهشی", "Research workspace")}</span><h3>${escapeHtml(workspace.name)}</h3><p>${faNumber(workspace.document_count)} ${l("منبع", "sources")} · ${faNumber(workspace.ready_document_count)} ${l("آماده", "ready")}</p></div>
      <div class="workspace-card-actions"><button class="secondary-button" type="button" data-open-workspace="${escapeHtml(workspace.workspace_id)}">${active ? l("بازکردن محیط", "Open workspace") : l("انتخاب و بازکردن", "Select and open")}</button><button class="danger-button" type="button" data-delete-workspace="${escapeHtml(workspace.workspace_id)}">${l("حذف فضای کار", "Delete workspace")}</button></div>
    </article>`;
  }).join("") || `<div class="empty-state"><p>${l("هنوز فضای کاری ساخته نشده است.", "No workspace has been created yet.")}</p></div>`;
}

async function selectWorkspace(workspaceId, {persist = true} = {}) {
  state.workspaceRevision += 1;
  state.documentsLoading = false;
  state.activeWorkspaceId = workspaceId;
  $("#top-workspace-select").value = workspaceId;
  state.selectedDocumentIds.clear();
  state.evidence = [];
  if (persist) localStorage.setItem(WORKSPACE_KEY, workspaceId);
  if (state.processTimer) clearInterval(state.processTimer);
  if (state.rebuildTimer) clearInterval(state.rebuildTimer);
  if (state.studioTimer) clearInterval(state.studioTimer);
  state.studioStreams.forEach(source => source.close());
  state.studioStreams.clear();
  state.activeStudioArtifactId = null;
  if ($("#studio-dialog")?.open) $("#studio-dialog").close();
  state.processTimer = null;
  state.rebuildTimer = null;
  state.studioTimer = null;
  renderEvidence([]);
  await Promise.all([loadDocuments(), loadWorkspaceVector(), loadTraces(false), loadStudioArtifacts()]);
  await loadSessions();
  const workspace = state.workspaces.find(item => item.workspace_id === workspaceId);
  $("#active-workspace-name").textContent = workspace?.name || workspaceId;
}

async function createWorkspace() {
  const name = $("#workspace-name").value.trim();
  if (!name) return;
  const workspace = await api("/api/workspaces", {method: "POST", body: {name}});
  $("#workspace-dialog").close();
  $("#workspace-name").value = "";
  state.workspaces.push({...workspace, document_count: 0, ready_document_count: 0});
  $("#top-workspace-select").insertAdjacentHTML("beforeend", `<option value="${escapeHtml(workspace.workspace_id)}">${escapeHtml(workspace.name)}</option>`);
  $("#top-workspace-select").value = workspace.workspace_id;
  await selectWorkspace(workspace.workspace_id);
  setView("documents");
}

async function deleteWorkspace(workspaceId) {
  const workspace = state.workspaces.find(item => item.workspace_id === workspaceId);
  if (!workspace || !confirm(l(`فضای کار «${workspace.name}» و همهٔ منابع و خروجی‌های آن حذف شود؟`, `Delete “${workspace.name}” and all its sources and outputs?`))) return;
  await api(`/api/workspaces/${encodeURIComponent(workspaceId)}`, {method: "DELETE"});
  if (state.activeWorkspaceId === workspaceId) {
    localStorage.removeItem(WORKSPACE_KEY);
    state.activeWorkspaceId = "default";
  }
  await loadWorkspaces();
  toast(l("فضای کار حذف شد.", "Workspace deleted."));
}

function loadStoredSessions() {
  try {
    const parsed = JSON.parse(localStorage.getItem(SESSION_KEY) || "[]");
    return Array.isArray(parsed) ? parsed : [];
  } catch {
    return [];
  }
}

function saveSessions() {
  const others = loadStoredSessions().filter(item => item.workspace_id !== state.activeWorkspaceId);
  const cached = [...state.sessions, ...others].sort((left, right) =>
    String(right.updated_at || right.created_at).localeCompare(String(left.updated_at || left.created_at))
  );
  localStorage.setItem(SESSION_KEY, JSON.stringify(cached.slice(0, 80)));
}

async function loadSessions() {
  const localSessions = loadStoredSessions().filter(item => item.workspace_id === state.activeWorkspaceId);
  const localById = new Map(localSessions.map(item => [item.id, item]));
  let remoteSessions = [];
  try {
    remoteSessions = await api(`/api/workspaces/${encodeURIComponent(state.activeWorkspaceId)}/conversations`);
  } catch {
    remoteSessions = [];
  }
  const remoteIds = new Set(remoteSessions.map(item => item.id));
  state.sessions = remoteSessions.map(remote => {
    const local = localById.get(remote.id);
    const locallyComplete = Boolean(local?.turns?.length >= remote.turn_count);
    return {
      ...local,
      ...remote,
      turns: locallyComplete ? local.turns : [],
      loaded: locallyComplete,
    };
  });
  state.sessions.push(...localSessions.filter(item => !remoteIds.has(item.id)));
  state.sessions.sort((left, right) =>
    String(right.updated_at || right.created_at).localeCompare(String(left.updated_at || left.created_at))
  );
  if (!state.sessions.length) newSession({activateView: false});
  else {
    state.activeSessionId = state.sessions[0].id;
    restoreSession();
  }
  saveSessions();
  renderHistory();
}

function currentSession() {
  return state.sessions.find(item => item.id === state.activeSessionId);
}

function newSession({activateView = true} = {}) {
  const session = {
    id: crypto.randomUUID(),
    workspace_id: state.activeWorkspaceId,
    title: t("chat.new"),
    turns: [],
    created_at: new Date().toISOString(),
    updated_at: new Date().toISOString(),
    turn_count: 0,
    loaded: true,
  };
  state.sessions.unshift(session);
  state.activeSessionId = session.id;
  state.evidence = [];
  saveSessions();
  renderHistory();
  restoreSession();
  if (activateView) setView("chat");
}

function renderHistory() {
  $("#chat-history").innerHTML = state.sessions.map(session =>
    `<button type="button" class="history-item ${session.id === state.activeSessionId ? "active" : ""}" data-session="${session.id}">${escapeHtml(session.title)}</button>`
  ).join("");
  renderHistoryLibrary();
}

function studioHistoryStatusLabel(status) {
  return {
    ready: l("آمادهٔ مشاهده و دانلود", "Ready to view and download"),
    failed: l("ساخت ناموفق", "Generation failed"),
    cancelled: l("لغوشده", "Cancelled"),
    interrupted: l("متوقف‌شده؛ قابل تلاش دوباره", "Interrupted; retry available"),
    review: l("آمادهٔ بازبینی", "Ready for review"),
    preparing: l("در حال آماده‌سازی", "Preparing"),
  }[studioStatusCategory(status)];
}

function renderHistoryLibrary() {
  const container = $("#history-library");
  if (!container) return;
  const conversations = state.sessions.map(session => `<button class="history-library-card" type="button" data-library-session="${escapeHtml(session.id)}"><span class="history-kind"><svg><use href="#i-chat"></use></svg></span><span><small>${l("گفتگو", "Conversation")} · ${relativeTime(session.updated_at || session.created_at)}</small><strong>${escapeHtml(session.title)}</strong><em>${faNumber(session.turn_count ?? session.turns.length)} ${l("پیام", "messages")}</em></span><i>←</i></button>`).join("");
  const artifacts = state.studioArtifacts.map(item => `<button class="history-library-card" type="button" data-studio-artifact="${escapeHtml(item.artifact_id)}"><span class="history-kind studio"><svg><use href="#${studioTypeIcon(item.artifact_type)}"></use></svg></span><span><small>Studio · ${studioTypeLabel(item.artifact_type)}</small><strong>${escapeHtml(item.title || studioTypeLabel(item.artifact_type))}</strong><em>${studioHistoryStatusLabel(item.status)}</em></span><i>←</i></button>`).join("");
  container.innerHTML = conversations || artifacts
    ? `<section><header><h3>${l("گفتگوها", "Conversations")}</h3><span>${faNumber(state.sessions.length)}</span></header>${conversations || `<p class="history-empty">${l("گفتگویی ثبت نشده است.", "No conversations yet.")}</p>`}</section><section><header><h3>${l("خروجی‌های Studio", "Studio outputs")}</h3><span>${faNumber(state.studioArtifacts.length)}</span></header>${artifacts || `<p class="history-empty">${l("خروجی‌ای ساخته نشده است.", "No outputs yet.")}</p>`}</section>`
    : `<div class="empty-state"><span><svg><use href="#i-refresh"></use></svg></span><h4>${l("کتابخانه هنوز خالی است", "The library is empty")}</h4><p>${l("گفتگوها و خروجی‌های ساخته‌شده در این صفحه نگهداری می‌شوند.", "Conversations and generated outputs will be collected here.")}</p></div>`;
}

function renderGlobalSearch(query) {
  const panel = $("#global-search-results");
  const normalized = String(query || "").trim().toLocaleLowerCase();
  if (normalized.length < 2) {
    panel.classList.add("hidden");
    panel.innerHTML = "";
    return;
  }
  const documents = state.documents.filter(item => `${item.title || ""} ${item.original_filename || ""}`.toLocaleLowerCase().includes(normalized)).slice(0, 5);
  const sessions = state.sessions.filter(item => item.title.toLocaleLowerCase().includes(normalized)).slice(0, 5);
  const artifacts = state.studioArtifacts.filter(item => `${item.title || ""} ${studioTypeLabel(item.artifact_type)}`.toLocaleLowerCase().includes(normalized)).slice(0, 5);
  const rows = [
    ...documents.map(item => `<button type="button" data-search-document="${escapeHtml(item.document_id)}"><svg><use href="#i-files"></use></svg><span><strong>${escapeHtml(item.title || item.original_filename)}</strong><small>${l("منبع", "Source")}</small></span></button>`),
    ...sessions.map(item => `<button type="button" data-library-session="${escapeHtml(item.id)}"><svg><use href="#i-chat"></use></svg><span><strong>${escapeHtml(item.title)}</strong><small>${l("گفتگو", "Conversation")}</small></span></button>`),
    ...artifacts.map(item => `<button type="button" data-studio-artifact="${escapeHtml(item.artifact_id)}"><svg><use href="#${studioTypeIcon(item.artifact_type)}"></use></svg><span><strong>${escapeHtml(item.title || studioTypeLabel(item.artifact_type))}</strong><small>Studio</small></span></button>`),
  ];
  panel.innerHTML = rows.join("") || `<p>${l("نتیجه‌ای پیدا نشد.", "No matching results.")}</p>`;
  panel.classList.remove("hidden");
}

function welcomeMarkup() {
  return `<div class="welcome-card">
    <div class="welcome-visual"><span class="welcome-mark"><svg><use href="#i-sparkle"></use></svg></span><i class="orbit-dot one"></i><i class="orbit-dot two"></i></div>
    <div class="welcome-copy">
      <span class="welcome-kicker">${t("welcome.kicker")}</span>
      <h2>${t("welcome.title")}</h2>
      <p>${t("welcome.body")}</p>
    </div>
  </div>`;
}

function restoreSession() {
  const session = currentSession();
  if (session && session.loaded === false) {
    $("#messages").innerHTML = `<article class="message assistant"><div class="message-bubble typing"><span>${l("در حال بازیابی گفتگو…", "Loading conversation…")}</span><i></i><i></i><i></i></div></article>`;
    renderEvidence([]);
    loadSessionDetails(session);
    return;
  }
  if (!session || !session.turns.length) {
    $("#messages").innerHTML = welcomeMarkup();
    renderEvidence([]);
    return;
  }
  $("#messages").innerHTML = session.turns.map(renderTurn).join("");
  const lastAssistant = [...session.turns].reverse().find(turn => turn.role === "assistant");
  renderEvidence(lastAssistant?.evidence || []);
  $("#messages").scrollTop = $("#messages").scrollHeight;
}

async function loadSessionDetails(session) {
  if (!session || session.loading || session.loaded !== false) return;
  session.loading = true;
  try {
    const payload = await api(`/api/workspaces/${encodeURIComponent(state.activeWorkspaceId)}/conversations/${encodeURIComponent(session.id)}`);
    const localTurns = Array.isArray(session.turns) ? session.turns : [];
    session.turns = (payload.turns || []).map((turn, index) => {
      const cached = localTurns[index];
      return cached?.role === turn.role && cached?.content === turn.content ? {...turn, ...cached} : turn;
    });
    session.title = payload.title;
    session.turn_count = payload.turn_count;
    session.created_at = payload.created_at;
    session.updated_at = payload.updated_at;
    session.loaded = true;
    saveSessions();
    renderHistory();
  } catch (error) {
    toast(error.message);
  } finally {
    session.loading = false;
    if (session.id === state.activeSessionId) restoreSession();
  }
}

function renderTurn(turn) {
  const content = turn.role === "assistant"
    ? citationButtons(turn.html || `<p>${escapeHtml(turn.content)}</p>`)
    : escapeHtml(turn.content);
  const artifacts = (turn.artifacts || []).map(item =>
    `<a href="${escapeHtml(item.url)}" target="_blank" rel="noopener" download>${escapeHtml(item.label || item.name)}</a>`
  ).join("");
  const attachment = turn.attachment?.preview
    ? `<div class="message-attachment"><img src="${escapeHtml(turn.attachment.preview)}" alt=""><span>${escapeHtml(turn.attachment.name || t("chat.attachImage"))}</span></div>`
    : "";
  return `<article class="message ${turn.role}"><div class="message-bubble" dir="auto">${attachment}${content}${artifacts ? `<div class="artifact-links">${artifacts}</div>` : ""}</div></article>`;
}

function citationButtons(html) {
  return html.replace(/\[E(\d+)\]/g, (_, number) =>
    `<button type="button" class="citation-link" data-citation="${number}">[E${number}]</button>`
  );
}

async function submitQuestion(value) {
  const promptImage = state.promptImage;
  const question = String(value || "").trim() || (promptImage ? t("chat.analyzeImage") : "");
  if (!question) return;
  const session = currentSession();
  if (!session || session.loading || session.loaded === false) return;
  const priorHistory = session.turns.slice(-20).map(turn => ({role: turn.role, content: turn.content}));
  if (!session.turns.length) session.title = question.slice(0, 52);
  session.turns.push({role: "user", content: question, attachment: promptImage ? {name: promptImage.file.name, preview: promptImage.thumbnail} : null});
  $("#question-input").value = "";
  setChatPending(true);
  renderHistory();
  $("#messages").innerHTML = session.turns.map(renderTurn).join("") + `<article class="message assistant" id="typing-message"><div class="message-bubble typing"><span>${t("chat.thinking")}</span><i></i><i></i><i></i></div></article>`;
  $("#messages").scrollTop = $("#messages").scrollHeight;
  const selected = [...state.selectedDocumentIds];
  try {
    let result;
    if (promptImage) {
      const form = new FormData();
      form.append("image", promptImage.file, promptImage.file.name);
      form.append("question", question);
      form.append("history", JSON.stringify(priorHistory));
      form.append("workspace_id", state.activeWorkspaceId);
      form.append("document_ids", JSON.stringify(selected));
      form.append("conversation_id", session.id);
      result = await api("/api/chat/image", {method: "POST", body: form});
    } else {
      result = await api("/api/chat", {method: "POST", body: {question, history: priorHistory, workspace_id: state.activeWorkspaceId, document_ids: selected, conversation_id: session.id}});
    }
    session.turns.push({
      role: "assistant",
      content: result.text,
      html: result.html,
      evidence: result.evidence || [],
      artifacts: result.artifacts || [],
      trace_id: result.trace_id,
      prompt_attachment: result.prompt_attachment || null,
    });
    state.evidence = result.evidence || [];
    session.turn_count = session.turns.length;
    session.updated_at = new Date().toISOString();
    session.loaded = true;
    saveSessions();
    restoreSession();
    loadTraces(false);
  } catch (error) {
    $("#typing-message")?.remove();
    session.turns.push({role: "assistant", content: `${t("chat.failed")}: ${error.message}`});
    saveSessions();
    restoreSession();
  } finally {
    if (promptImage) clearPromptImage();
    setChatPending(false);
  }
}

function setChatPending(pending) {
  $("#chat-form").setAttribute("aria-busy", String(pending));
  $("#chat-form").classList.toggle("pending", pending);
  $("#chat-form .send-button").disabled = pending;
}

async function imageThumbnail(file) {
  try {
    const bitmap = await createImageBitmap(file);
    const scale = Math.min(1, 360 / Math.max(bitmap.width, bitmap.height));
    const canvas = document.createElement("canvas");
    canvas.width = Math.max(1, Math.round(bitmap.width * scale));
    canvas.height = Math.max(1, Math.round(bitmap.height * scale));
    canvas.getContext("2d").drawImage(bitmap, 0, 0, canvas.width, canvas.height);
    bitmap.close();
    return canvas.toDataURL("image/jpeg", 0.76);
  } catch {
    return "";
  }
}

async function setPromptImage(file) {
  if (!file) return;
  if (!file.type.startsWith("image/") || file.size > 15 * 1024 * 1024) {
    toast(state.locale === "fa" ? "تصویر باید کمتر از ۱۵ مگابایت باشد." : "The image must be smaller than 15 MB.");
    return;
  }
  clearPromptImage();
  state.promptImage = {
    file,
    previewUrl: URL.createObjectURL(file),
    thumbnail: await imageThumbnail(file),
  };
  renderPromptAttachment();
}

function clearPromptImage() {
  if (state.promptImage?.previewUrl) URL.revokeObjectURL(state.promptImage.previewUrl);
  state.promptImage = null;
  if ($("#prompt-image-input")) $("#prompt-image-input").value = "";
  renderPromptAttachment();
}

function renderPromptAttachment() {
  const box = $("#prompt-attachment-preview");
  if (!box) return;
  const item = state.promptImage;
  box.classList.toggle("hidden", !item);
  if (!item) {
    box.innerHTML = "";
    return;
  }
  box.innerHTML = `<img src="${escapeHtml(item.previewUrl)}" alt=""><div><strong>${escapeHtml(item.file.name)}</strong><small>${t("chat.imageReady")}</small></div><button type="button" data-remove-prompt-image aria-label="${t("chat.removeImage")}"><svg><use href="#i-close"></use></svg></button>`;
}

function renderEvidence(evidence) {
  state.evidence = evidence;
  document.body.classList.toggle("view-chat-has-evidence", evidence.length > 0);
  if (!evidence.length) {
    $("#evidence-panel").innerHTML = `<div class="empty-state evidence-empty"><span><svg><use href="#i-files"></use></svg></span><p>${t("evidence.empty")}</p></div>`;
    return;
  }
  activateContextPanel("evidence");
  $("#evidence-panel").innerHTML = evidence.map((item, index) =>
    `<button type="button" class="evidence-card" data-evidence-index="${index}">
      <header><span>E${index + 1} · ${escapeHtml(item.filename || item.paper_title)}</span><span>${t("evidence.page", {page: faNumber(item.page_start)})}</span></header>
      <strong>${escapeHtml(item.section || item.chunk_type)}</strong>
      <p>${escapeHtml(String(item.text || "").replace(/\s+/g, " ").slice(0, 240))}</p>
      <small class="method-badge">${escapeHtml(item.extraction_method || "text")} · ${Number(item.similarity || 0).toFixed(3)}</small>
    </button>`
  ).join("");
}

function activateContextPanel(name) {
  const selected = name === "evidence" ? "evidence" : "studio";
  $$("[data-context-panel]").forEach(button => {
    const active = button.dataset.contextPanel === selected;
    button.classList.toggle("active", active);
    button.setAttribute("aria-selected", String(active));
  });
  $$(".context-panel").forEach(panel => {
    const active = panel.id === `context-panel-${selected}`;
    panel.classList.toggle("active", active);
    panel.hidden = !active;
  });
}

async function openEvidence(item) {
  state.drawerReturnFocus = document.activeElement;
  $("#evidence-drawer").classList.add("open");
  $("#evidence-drawer").setAttribute("aria-hidden", "false");
  $("#drawer-backdrop").classList.remove("hidden");
  document.body.classList.add("drawer-open");
  $("#drawer-title").textContent = `${item.filename || item.paper_title} · ${t("evidence.page", {page: faNumber(item.page_start)})}`;
  const sourceLink = $("#drawer-pdf-link");
  sourceLink.href = item.pdf_url || item.source_url || "#";
  sourceLink.classList.toggle("hidden", !item.pdf_url && !item.source_url);
  $("#evidence-text-preview").innerHTML = `<p class="preview-text" dir="auto">${escapeHtml(item.text || "")}</p>`;
  $("#evidence-warnings").innerHTML = "";
  $("#page-preview-image").removeAttribute("src");
  if (!item.page_preview_url) return;
  try {
    const page = await api(item.page_preview_url);
    $("#evidence-text-preview").innerHTML = `<p class="preview-text" dir="auto">${escapeHtml(page.normalized_text || item.text || "")}</p>${page.visual_summary ? `<h3>${t("evidence.visual")}</h3><p dir="auto">${escapeHtml(page.visual_summary)}</p>` : ""}`;
    if (page.image_url) $("#page-preview-image").src = page.image_url;
    sourceLink.href = page.pdf_url || page.source_url || item.source_url || "#";
    sourceLink.classList.toggle("hidden", !page.pdf_url && !page.source_url && !item.source_url);
    const imageTab = $("[data-evidence-tab=image]");
    imageTab.classList.toggle("hidden", !page.image_url);
    if (!page.image_url) setEvidenceTab("text");
    $("#evidence-warnings").innerHTML = (page.warnings || []).map(warning => `<p>${escapeHtml(warning)}</p>`).join("");
  } catch (error) {
    $("#evidence-warnings").innerHTML = `<p>${t("evidence.previewFailed")}</p>`;
  }
}

function closeEvidence() {
  $("#evidence-drawer").classList.remove("open");
  $("#evidence-drawer").setAttribute("aria-hidden", "true");
  $("#drawer-backdrop").classList.add("hidden");
  document.body.classList.remove("drawer-open");
  state.drawerReturnFocus?.focus?.();
}

async function loadDocuments() {
  if (state.documentsLoading) return;
  const revision = state.workspaceRevision;
  const workspaceId = state.activeWorkspaceId;
  state.documentsLoading = true;
  try {
    const documents = await api(`/api/workspaces/${encodeURIComponent(workspaceId)}/documents`);
    if (revision !== state.workspaceRevision || workspaceId !== state.activeWorkspaceId) return;
    state.documents = documents;
    renderDocuments();
    renderDocumentFilters();
    const processing = state.documents.filter(item => item.status === "processing").length;
    const ready = state.documents.filter(item => item.status === "ready").length;
    $("#processing-summary").textContent = state.locale === "fa"
      ? `${faNumber(ready)} منبع آماده · ${faNumber(processing)} در حال پردازش · ${faNumber(state.documents.length - ready - processing)} در انتظار یا ناموفق`
      : `${faNumber(ready)} ready · ${faNumber(processing)} processing · ${faNumber(state.documents.length - ready - processing)} queued or failed`;
    $("#hero-document-count").textContent = faNumber(state.documents.length);
    if (processing && !state.processTimer) {
      state.processTimer = setInterval(loadDocuments, 1200);
    } else if (!processing && state.processTimer) {
      clearInterval(state.processTimer);
      state.processTimer = null;
      loadWorkspaceVector();
    }
  } catch (error) {
    if (revision === state.workspaceRevision) {
      $("#processing-summary").textContent = state.locale === "fa" ? "ارتباط موقتاً قطع شد؛ تلاش دوباره ادامه دارد." : "Connection interrupted; retrying automatically.";
    }
  } finally {
    if (revision === state.workspaceRevision) state.documentsLoading = false;
  }
}

function renderDocumentFilters() {
  const allActive = state.selectedDocumentIds.size === 0;
  const readyDocuments = state.documents.filter(item => item.status === "ready");
  $("#chat-document-filter").innerHTML = `<button type="button" class="filter-chip ${allActive ? "active" : ""}" data-document-filter="">${state.locale === "fa" ? "همه" : "All"}</button>` +
    readyDocuments.map(item =>
      `<button type="button" class="filter-chip ${state.selectedDocumentIds.has(item.document_id) ? "active" : ""}" data-document-filter="${escapeHtml(item.document_id)}">${escapeHtml(item.title || item.original_filename)}</button>`
    ).join("");
  $("#composer-scope").textContent = allActive
    ? t("chat.allReady")
    : t("chat.selectedSources", {count: faNumber(state.selectedDocumentIds.size)});
  $("#source-ready-count").textContent = faNumber(readyDocuments.length);
  $("#select-all-sources").classList.toggle("active", allActive);
  $("#source-scope-list").innerHTML = state.documents.map(item => {
    const active = allActive ? item.status === "ready" : state.selectedDocumentIds.has(item.document_id);
    const status = t(`status.${item.status}`);
    return `<button type="button" class="source-scope-item ${active ? "active" : ""}" data-document-filter="${escapeHtml(item.document_id)}" ${item.status !== "ready" ? "disabled" : ""}>
      <span class="source-check"><svg><use href="#i-check"></use></svg></span>
      <span><strong>${escapeHtml(item.title || item.original_filename)}</strong><small>${escapeHtml(item.source_type.toUpperCase())} · ${status}</small></span>
      <i class="source-state-dot ${escapeHtml(item.status)}"></i>
    </button>`;
  }).join("") || `<div class="studio-empty"><p>${state.locale === "fa" ? "هنوز منبعی اضافه نشده است." : "No sources yet."}</p></div>`;
}

async function loadStudioArtifacts() {
  if (!state.activeWorkspaceId) return;
  const workspaceId = state.activeWorkspaceId;
  const revision = state.workspaceRevision;
  const mutationRevision = state.studioMutationRevision;
  try {
    const payload = await api(`/api/workspaces/${encodeURIComponent(workspaceId)}/studio/artifacts?limit=50`);
    if (!isCurrentStudioRequest(state, workspaceId, revision, mutationRevision)) return;
    applyStudioSnapshot(state, payload);
    renderStudioArtifacts();
    syncStudioStreams();
    if (state.activeStudioArtifactId && $("#studio-dialog")?.open) await loadStudioArtifactDetail(state.activeStudioArtifactId);
  } catch {
    if (!isCurrentStudioRequest(state, workspaceId, revision, mutationRevision)) return;
    markStudioReconnecting(state);
    renderStudioArtifacts();
    startStudioPollingFallback();
  }
}

async function loadStudioArtifactDetail(artifactId) {
  const workspaceId = state.activeWorkspaceId;
  const revision = state.workspaceRevision;
  const item = await api(`/api/workspaces/${encodeURIComponent(workspaceId)}/studio/artifacts/${encodeURIComponent(artifactId)}`);
  if (!isCurrentWorkspaceRequest(state, workspaceId, revision)) return null;
  const merged = mergeStudioArtifactDetail(state, item);
  renderStudioArtifacts();
  if (state.activeStudioArtifactId === artifactId && $("#studio-dialog")?.open) renderStudioArtifact(merged);
  return merged;
}

function startStudioPollingFallback() {
  if (!state.studioTimer) state.studioTimer = setInterval(loadStudioArtifacts, 1500);
}

function syncStudioStreams() {
  const running = new Map(state.studioArtifacts.filter(studioArtifactIsRunning).map(item => [item.artifact_id, item]));
  state.studioStreams.forEach((source, artifactId) => {
    if (!running.has(artifactId)) {
      source.close();
      state.studioStreams.delete(artifactId);
    }
  });
  running.forEach((item, artifactId) => {
    if (state.studioStreams.has(artifactId)) return;
    const workspaceId = item.workspace_id || state.activeWorkspaceId;
    const revision = state.workspaceRevision;
    const source = new EventSource(`/api/workspaces/${encodeURIComponent(workspaceId)}/studio/artifacts/${encodeURIComponent(artifactId)}/events?tail=true`);
    source.onopen = () => {
      if (!isCurrentWorkspaceRequest(state, workspaceId, revision)) {
        source.close();
        state.studioStreams.delete(artifactId);
        return;
      }
      state.studioConnection = "connected";
      if (state.studioTimer) {
        clearInterval(state.studioTimer);
        state.studioTimer = null;
      }
    };
    source.addEventListener("studio", async event => {
      if (!isCurrentWorkspaceRequest(state, workspaceId, revision)) {
        source.close();
        state.studioStreams.delete(artifactId);
        return;
      }
      const update = JSON.parse(event.data);
      const current = applyStudioEvent(state, artifactId, update);
      renderStudioArtifacts();
      if (state.activeStudioArtifactId === artifactId && $("#studio-dialog")?.open) {
        await loadStudioArtifactDetail(artifactId).catch(() => markStudioReconnecting(state));
      }
      if (current && !studioArtifactIsRunning(current)) {
        source.close();
        state.studioStreams.delete(artifactId);
        await loadStudioArtifacts();
      }
    });
    source.onerror = () => {
      if (!isCurrentWorkspaceRequest(state, workspaceId, revision)) return;
      markStudioReconnecting(state);
      renderStudioArtifacts();
      startStudioPollingFallback();
    };
    state.studioStreams.set(artifactId, source);
  });
}

async function loadStudioCatalog() {
  state.studioCatalog = await api("/api/studio/catalog");
  return state.studioCatalog;
}

async function loadFonts() {
  const payload = await api("/api/fonts");
  state.fonts = payload.fonts || [];
  renderFontManager();
  return state.fonts;
}

function renderFontManager() {
  const list = $("#font-manager-list");
  if (!list) return;
  $("#font-manager-title").textContent = l("فونت‌های خروجی", "Output fonts");
  $("#font-manager-hint").textContent = l("فونت TTF یا OTF خود را برای اسلاید و ویدیو اضافه کنید.", "Add a TTF or OTF font for slide and video exports.");
  $("#font-upload-label").textContent = l("افزودن فونت", "Add font");
  list.innerHTML = state.fonts.length
    ? state.fonts.map(font => `<article><span><strong>${escapeHtml(font.family)}</strong><small>${escapeHtml(font.filename)} · ${escapeHtml(font.source === "bundled" ? l("داخلی", "bundled") : l("سفارشی", "custom"))}</small></span>${font.managed === false ? `<span class="status-badge ready">${l("محافظت‌شده", "read-only")}</span>` : `<button class="icon-button danger" type="button" data-delete-font="${escapeHtml(font.font_id)}" aria-label="${escapeHtml(l("حذف فونت", "Delete font"))}"><svg><use href="#i-trash"></use></svg></button>`}</article>`).join("")
    : `<p>${l("فونت خروجی در دسترس نیست.", "No output font is available.")}</p>`;
}

async function uploadFont() {
  const input = $("#font-upload-input");
  if (!input.files?.[0]) return;
  const body = new FormData();
  body.append("file", input.files[0]);
  await api("/api/fonts", {method: "POST", body});
  input.value = "";
  await loadFonts();
  toast(l("فونت اضافه شد.", "Font added."));
}

async function deleteFont(fontId) {
  await api(`/api/fonts/${encodeURIComponent(fontId)}`, {method: "DELETE"});
  await loadFonts();
  toast(l("فونت حذف شد.", "Font deleted."));
}

function studioTypeLabel(type) {
  const labels = {
    audio_overview: t("studio.audioOverview"),
    report: t("studio.report"), infographic: t("studio.infographic"), data_table: t("studio.dataTable"),
    summary: t("studio.summary"), briefing: t("studio.briefing"), study_guide: t("studio.guide"),
    quiz: t("studio.quiz"), chart: t("studio.chart"), timeline: t("studio.timeline"), faq: t("studio.faq"),
    flashcards: t("studio.flashcards"), comparison: t("studio.comparison"), concept_map: t("studio.conceptMap"),
    mini_survey: t("studio.survey"), research_gap: t("studio.gap"),
    slide_deck: l("مجموعه اسلاید", "Slide deck"), video_overview: l("مرور ویدیویی", "Video overview"),
  };
  return labels[type] || type;
}

function studioTypeIcon(type) {
  return ({audio_overview: "i-mic", report: "i-files", infographic: "i-chart", data_table: "i-layers", summary: "i-sparkle", briefing: "i-files", study_guide: "i-book", quiz: "i-quiz", chart: "i-chart", timeline: "i-activity", faq: "i-chat", flashcards: "i-book", comparison: "i-layers", concept_map: "i-database", mini_survey: "i-files", research_gap: "i-search", slide_deck: "i-presentation", video_overview: "i-play"})[type] || "i-sparkle";
}

function renderStudioArtifacts() {
  const box = $("#studio-artifacts");
  if (!box) return;
  if (!state.studioArtifacts.length) {
    if (!$(".studio-empty", box)) {
      box.replaceChildren();
      const empty = document.createElement("div");
      empty.className = "studio-empty";
      empty.textContent = t("studio.empty");
      box.append(empty);
    }
  } else {
    $(".studio-empty", box)?.remove();
    patchKeyedStudioRows(box, state.studioArtifacts, () => {
      const row = document.createElement("article");
      row.className = "studio-artifact-row";
      row.innerHTML = `<button class="studio-artifact-card" type="button"><span class="studio-artifact-icon"><svg><use></use></svg><i class="status-spinner hidden"></i></span><span><strong></strong><small></small></span><i data-studio-progress></i></button><button class="icon-button danger hidden" type="button" aria-label="Cancel generation" title="Cancel generation"><svg><use href="#i-close"></use></svg></button>`;
      return row;
    }, (row, item) => {
      const running = studioArtifactIsRunning(item);
      const status = running ? t("studio.generating") : item.status === "script_ready" ? l("آمادهٔ بازبینی", "Ready to review") : item.status === "plan_ready" ? l("آمادهٔ ساخت صوت", "Ready for audio") : item.status === "ready" ? t("studio.ready") : item.status === "cancelled" ? l("لغوشده", "Cancelled") : item.status === "interrupted" ? l("متوقف‌شده", "Interrupted") : t("studio.failed");
      const connection = state.studioConnection === "reconnecting" && running ? ` · ${l("اتصال دوباره…", "Reconnecting…")}` : "";
      const open = $(".studio-artifact-card", row);
      open.dataset.studioArtifact = item.artifact_id;
      $("strong", open).textContent = item.title || studioTypeLabel(item.artifact_type);
      $("small", open).textContent = `${studioTypeLabel(item.artifact_type)} · ${status}${connection}`;
      $("[data-studio-progress]", open).textContent = running ? `${faNumber(item.progress)}%` : l("←", "→");
      $(".status-spinner", row).classList.toggle("hidden", !running);
      $(".studio-artifact-icon svg", row).classList.toggle("hidden", running);
      $(".studio-artifact-icon use", row).setAttribute("href", `#${studioTypeIcon(item.artifact_type)}`);
      const cancel = $(".icon-button.danger", row);
      cancel.dataset.studioCancel = item.artifact_id;
      cancel.classList.toggle("hidden", !running);
    });
  }
  const running = state.studioArtifacts.some(studioArtifactIsRunning);
  if (!running && state.studioTimer) {
    clearInterval(state.studioTimer);
    state.studioTimer = null;
  }
  syncCancellationControls();
  renderHistoryLibrary();
}

const studioFieldLabels = {
  design_control: ["کنترل طراحی", "Design control"], font_family: ["فونت خروجی", "Output font"],
  evidence_depth: ["عمق بررسی شواهد", "Evidence depth"], focus: ["تمرکز موضوعی", "Topic focus"], custom_instructions: ["دستورهای تکمیلی", "Additional instructions"],
  report_format: ["نوع گزارش", "Report type"], custom_report_type: ["تعریف گزارش سفارشی", "Custom report brief"], detail_level: ["میزان جزئیات", "Detail level"], audience: ["مخاطب", "Audience"], tone: ["لحن", "Tone"], structure: ["ساختار ارائه", "Presentation structure"], include_executive_summary: ["خلاصهٔ مدیریتی اضافه شود", "Include executive summary"], include_recommendations: ["پیشنهادهای عملی اضافه شود", "Include recommendations"],
  depth: ["عمق شاخه‌ها", "Branch depth"], max_nodes: ["حداکثر گره‌ها", "Maximum nodes"], layout: ["چیدمان", "Layout"], relationship_detail: ["جزئیات رابطه‌ها", "Relationship detail"], group_by_theme: ["گره‌ها موضوعی گروه‌بندی شوند", "Group nodes by theme"], include_descriptions: ["توضیح هر مفهوم نمایش داده شود", "Include concept descriptions"],
  card_count: ["تعداد کارت", "Card count"], difficulty: ["سطح دشواری", "Difficulty"], answer_length: ["طول پاسخ", "Answer length"], include_examples: ["مثال هم اضافه شود", "Include examples"], card_style: ["سبک کارت‌ها", "Card style"], include_source_hints: ["راهنمای منبع نمایش داده شود", "Include source hints"], learning_focus: ["تمرکز یادگیری", "Learning focus"],
  question_count: ["تعداد سؤال", "Question count"], question_style: ["نوع سؤال‌ها", "Question style"], include_explanations: ["پاسخ تشریحی نمایش داده شود", "Include explanations"], randomize_choices: ["ترتیب گزینه‌ها متنوع باشد", "Vary answer choices"], passing_score: ["حدنصاب قبولی", "Passing score"],
  density: ["تراکم اطلاعات", "Information density"], section_count: ["تعداد بخش‌ها", "Section count"], visual_emphasis: ["تأکید محتوایی", "Content emphasis"], include_metrics: ["اعداد و شاخص‌ها برجسته شوند", "Highlight metrics"], include_takeaway: ["جمع‌بندی نهایی اضافه شود", "Include key takeaway"],
  row_limit: ["حداکثر ردیف", "Maximum rows"], organization: ["شیوهٔ سازمان‌دهی", "Organization"], include_quotes: ["نقل‌قول‌های کوتاه اضافه شود", "Include short quotes"], include_page_columns: ["ستون منبع و صفحه اضافه شود", "Include source and page columns"], columns_focus: ["ستون‌ها و اطلاعات موردنیاز", "Required columns and information"], missing_value_policy: ["نحوهٔ نمایش دادهٔ ناموجود", "Missing data policy"], sort_by: ["مرتب‌سازی بر اساس", "Sort by"],
  chart_type: ["نوع نمودار", "Chart type"], metric_focus: ["شاخص‌های موردنظر", "Metrics to visualize"], group_by: ["گروه‌بندی بر اساس", "Group by"], max_series: ["حداکثر سری داده", "Maximum data series"], include_explanation: ["تحلیل نمودار اضافه شود", "Include chart analysis"],
  event_count: ["حداکثر رویداد", "Maximum events"], granularity: ["سطح جزئیات زمانی", "Timeline granularity"], order: ["ترتیب نمایش", "Display order"], include_uncertain_dates: ["تاریخ‌های تقریبی هم نمایش داده شوند", "Include uncertain dates"], include_significance: ["اهمیت هر رویداد توضیح داده شود", "Explain event significance"],
  aspects: ["محورهای مقایسه", "Comparison aspects"], comparison_style: ["شیوهٔ مقایسه", "Comparison style"], depth_per_source: ["عمق بررسی هر منبع", "Depth per source"], include_limitations: ["محدودیت‌ها مقایسه شوند", "Compare limitations"], include_verdict: ["جمع‌بندی نهایی ارائه شود", "Include final verdict"],
  gap_count: ["تعداد شکاف‌های پژوهشی", "Research gap count"], gap_scope: ["دامنهٔ استنتاج شکاف‌ها", "Gap inference scope"], rank_by: ["اولویت‌بندی بر اساس", "Rank gaps by"], include_research_questions: ["پرسش پژوهشی پیشنهاد شود", "Suggest research questions"], include_method_ideas: ["ایدهٔ روش اجرا پیشنهاد شود", "Suggest method ideas"],
  production_mode: ["شیوهٔ تولید", "Production mode"], model: ["مدل صوتی", "Audio model"], duration_minutes: ["مدت هدف (دقیقه)", "Target duration (minutes)"], format: ["قالب برنامه", "Episode format"], custom_format: ["تعریف قالب سفارشی", "Custom format brief"], pace: ["سرعت گفتار", "Speaking pace"], creativity: ["میزان بداهه‌پردازی", "Improvisation level"], spoken_citations: ["ارجاع در گفتار", "Spoken citations"], chapter_count: ["تعداد فصل‌های پیشنهادی", "Suggested chapters"], pause_milliseconds: ["مکث میان گویندگان (میلی‌ثانیه)", "Pause between speakers (milliseconds)"], include_intro: ["مقدمه اضافه شود", "Include an introduction"], include_outro: ["جمع‌بندی و پایان اضافه شود", "Include a closing"], topic_focus: ["تمرکز موضوعی پادکست", "Podcast focus"], participants: ["گویندگان", "Participants"],
  deck_format: ["نوع ارائه", "Presentation type"], custom_deck_brief: ["تعریف ارائهٔ سفارشی", "Custom deck brief"], slide_count_strategy: ["راهبرد تعداد اسلاید", "Slide count strategy"], slide_count: ["تعداد/سقف اسلاید", "Slide target/maximum"], content_depth: ["عمق محتوای روی اسلاید", "On-slide content depth"], media_density: ["تراکم رسانهٔ واقعی", "Verified media density"], design_style: ["زبان بصری", "Visual language"], color_theme: ["پالت رنگ", "Color palette"], custom_colors: ["رنگ‌های سفارشی", "Custom colors"], aspect_ratio: ["نسبت تصویر", "Aspect ratio"], visual_density: ["تراکم بصری", "Visual density"], image_strategy: ["راهبرد تصویر", "Image strategy"], narrative_arc: ["قوس روایی", "Narrative arc"], include_charts: ["فقط نمودارهای مستند اضافه شوند", "Include sourced charts only"], include_equations: ["فرمول‌های مرتبط اضافه شوند", "Include relevant equations"], include_case_studies: ["مطالعهٔ موردی اضافه شود", "Include case studies"], include_speaker_notes: ["یادداشت ارائه‌دهنده اضافه شود", "Include speaker notes"], show_citations: ["ارجاع‌ها روی خروجی نمایش داده شوند", "Show citations"], presenter_name: ["نام ارائه‌دهنده", "Presenter name"],
  duration_seconds: ["مدت هدف (ثانیه)", "Target duration (seconds)"], scene_count_strategy: ["راهبرد تعداد صحنه", "Scene count strategy"], scene_count: ["تعداد/سقف صحنه", "Scene target/maximum"], video_style: ["سبک ویدیو", "Video style"], resolution: ["وضوح", "Resolution"], fps: ["نرخ فریم", "Frame rate"], media_mode: ["منبع تصویر", "Visual source"], narration: ["روایت صوتی", "Narration"], narration_model: ["مدل روایت", "Narration model"], voice: ["صدای روایت", "Narration voice"], transition_style: ["نوع گذار", "Transition style"], include_captions: ["زیرنویس ساخته شود", "Create captions"], music: ["موسیقی پس‌زمینهٔ غیرتجاری", "Non-commercial background music"],
};

const studioChoiceLabels = {
  focused: ["متمرکز", "Focused"], balanced: ["متعادل", "Balanced"], comprehensive: ["جامع", "Comprehensive"],
  general_overview: ["مرور کلی", "General overview"], academic_review: ["گزارش علمی", "Academic review"], technical_analysis: ["تحلیل فنی", "Technical analysis"], executive_brief: ["گزارش مدیریتی", "Executive brief"], study_guide: ["راهنمای مطالعه", "Study guide"], faq: ["پرسش‌های متداول", "FAQ"], literature_review: ["مرور ادبیات", "Literature review"], custom: ["سفارشی", "Custom"],
  concise: ["مختصر", "Concise"], standard: ["استاندارد", "Standard"], deep: ["عمیق", "Deep"], fixed: ["تعداد دقیق", "Fixed count"], maximum: ["سقف تعداد", "Maximum count"], detailed: ["مفصل", "Detailed"], general: ["عمومی", "General"], student: ["دانشجو", "Student"], researcher: ["پژوهشگر", "Researcher"], executive: ["مدیر", "Executive"], clear: ["روشن", "Clear"], academic: ["دانشگاهی", "Academic"], technical: ["فنی", "Technical"], actionable: ["عمل‌گرا", "Actionable"], sectioned: ["بخش‌بندی‌شده", "Sectioned"], narrative: ["روایی", "Narrative"], bullet_led: ["نکته‌محور", "Bullet-led"],
  hierarchical: ["سلسله‌مراتبی", "Hierarchical"], radial: ["شعاعی", "Radial"], flow: ["جریانی", "Flow"], labels_only: ["فقط عنوان رابطه", "Labels only"], brief: ["کوتاه", "Brief"], explained: ["همراه توضیح", "Explained"],
  introductory: ["مقدماتی", "Introductory"], intermediate: ["متوسط", "Intermediate"], advanced: ["پیشرفته", "Advanced"], definition: ["تعریف‌محور", "Definition"], conceptual: ["مفهومی", "Conceptual"], application: ["کاربردی", "Application"],
  multiple_choice: ["چندگزینه‌ای", "Multiple choice"], true_false: ["درست/نادرست", "True or false"], mixed: ["ترکیبی", "Mixed"], vertical: ["عمودی", "Vertical"], dashboard: ["داشبورد", "Dashboard"], process: ["فرایندی", "Process"], comparison: ["مقایسه‌ای", "Comparison"], minimal: ["کمینه", "Minimal"], rich: ["پربار", "Rich"], concepts: ["مفاهیم", "Concepts"], metrics: ["اعداد و شاخص‌ها", "Metrics"],
  topics: ["موضوع‌ها", "Topics"], sources: ["منابع", "Sources"], chronology: ["زمان‌بندی", "Chronology"], mark_unknown: ["با برچسب نامشخص", "Mark as unknown"], leave_blank: ["خالی گذاشته شود", "Leave blank"], explain: ["دلیل نبود داده نوشته شود", "Explain absence"],
  auto: ["انتخاب هوشمند", "Automatic"], bar: ["میله‌ای", "Bar"], line: ["خطی", "Line"], pie: ["دایره‌ای", "Pie"], scatter: ["پراکندگی", "Scatter"], high_level: ["سطح بالا", "High level"], chronological: ["از قدیم به جدید", "Chronological"], reverse_chronological: ["از جدید به قدیم", "Reverse chronological"], process_order: ["ترتیب فرایند", "Process order"],
  matrix: ["ماتریسی", "Matrix"], side_by_side: ["کنارهم", "Side by side"], explicit_only: ["فقط موارد صریح", "Explicit only"], evidence_inferred: ["استنتاج محتاطانه از شواهد", "Inferred from evidence"], both: ["هر دو", "Both"], impact: ["اثرگذاری", "Impact"], feasibility: ["امکان‌پذیری", "Feasibility"], novelty: ["نوآوری", "Novelty"], evidence_strength: ["قدرت شواهد", "Evidence strength"],
  narrated: ["روایت تک‌نفره", "Narrated"], scripted: ["دیالوگ کامل و قابل ویرایش", "Scripted & editable"], guided: ["گفت‌وگوی هدایت‌شده", "Producer-guided"], autonomous: ["میزگرد آزاد", "Autonomous roundtable"], deep_dive: ["بررسی عمیق", "Deep dive"], overview: ["مرور سریع", "Overview"], interview: ["مصاحبه", "Interview"], debate: ["مناظره", "Debate"], study_review: ["مرور آموزشی", "Study review"], news_briefing: ["گزارش خبری", "News briefing"], expert: ["متخصص", "Expert"], professional: ["حرفه‌ای", "Professional"], energetic: ["پرانرژی", "Energetic"], calm: ["آرام", "Calm"], critical: ["نقادانه", "Critical"], conversational: ["گفت‌وگومحور", "Conversational"], relaxed: ["آرام", "Relaxed"], natural: ["طبیعی", "Natural"], brisk: ["تند و موجز", "Brisk"], none: ["بدون ارجاع گفتاری", "No spoken citations"], light: ["کوتاه و طبیعی", "Light"], low: ["کم", "Low"], medium: ["متوسط", "Medium"], high: ["زیاد", "High"],
  research_presentation: ["ارائهٔ پژوهشی", "Research presentation"], teaching_deck: ["مجموعهٔ آموزشی", "Teaching deck"], conference_talk: ["سخنرانی کنفرانس", "Conference talk"], pitch_story: ["روایت پیشنهادی", "Pitch story"], editorial: ["تحریریه‌ای", "Editorial"], cinematic: ["سینمایی", "Cinematic"], data_driven: ["داده‌محور", "Data driven"], forest_cream: ["سبز و کرم", "Forest and cream"], warm_neutral: ["خنثی گرم", "Warm neutral"], midnight: ["نیمه‌شب", "Midnight"], ocean: ["اقیانوسی", "Ocean"], wide: ["عریض ۱۶:۹", "Wide 16:9"], airy: ["خلوت", "Airy"], dense: ["فشرده", "Dense"], pexels: ["رسانهٔ Pexels", "Pexels media"], diagrams_only: ["فقط نمودار و دیاگرام", "Diagrams only"], problem_solution: ["مسئله تا راه‌حل", "Problem to solution"], story: ["داستانی", "Story"], landscape: ["افقی", "Landscape"], portrait: ["عمودی", "Portrait"], square: ["مربع", "Square"], documentary: ["مستند", "Documentary"], data_story: ["روایت داده", "Data story"], video_first: ["اولویت ویدیو", "Video first"], photo_first: ["اولویت تصویر", "Photo first"], motion_graphics: ["موشن‌گرافیک", "Motion graphics"], dynamic: ["پویا", "Dynamic"], subtitles: ["زیرنویس بدون گوینده", "Subtitles only"], gemini_voice: ["صدای Gemini", "Gemini voice"], fade: ["محو", "Fade"], dissolve: ["حل‌شونده", "Dissolve"], slide: ["حرکت اسلایدی", "Slide"], off: ["خاموش", "Off"], jamendo: ["موسیقی Jamendo", "Jamendo music"],
  "gemini-3.1-flash-live-preview": ["Gemini 3.1 Flash Live", "Gemini 3.1 Flash Live"], "gemini-2.5-flash-native-audio-preview-12-2025": ["Gemini 2.5 Native Audio", "Gemini 2.5 Native Audio"],
};

const studioFieldPlaceholders = {
  focus: ["مثلاً روش‌ها، محدودیت‌ها یا یک موضوع مشخص", "For example, methods, limitations, or a specific topic"],
  custom_report_type: ["هدف، بخش‌های موردنظر و شکل گزارش اختصاصی را توضیح دهید", "Describe the purpose, sections, and shape of the custom report"],
  learning_focus: ["مفاهیم یا مهارت‌هایی که باید بیشتر تمرین شوند", "Concepts or skills that need extra practice"],
  columns_focus: ["مثلاً روش، دیتاست، معیار، نتیجه و شماره صفحه", "For example: method, dataset, metric, result, and page"],
  sort_by: ["نام ستون یا معیار اولویت", "Column name or priority metric"],
  metric_focus: ["اعداد، معیارها یا روندهایی که باید ترسیم شوند", "Numbers, metrics, or trends to visualize"],
  group_by: ["مثلاً منبع، روش، سال یا دسته", "For example: source, method, year, or category"],
  aspects: ["هر محور را در یک خط بنویسید؛ مثلاً روش، نتایج و محدودیت‌ها", "Enter one aspect per line, such as methods, results, and limitations"],
  custom_instructions: ["هر نکته‌ای که باید در خروجی رعایت شود", "Anything the output should account for"],
  custom_format: ["ساختار، حال‌وهوا و جریان برنامه را توضیح دهید", "Describe the structure, mood, and flow of the episode"],
  topic_focus: ["موضوع یا پرسش محوری پادکست", "The topic or central question for the episode"],
  custom_deck_brief: ["هدف، جریان روایی، بخش‌های ضروری و انتظارتان از ارائه را بنویسید", "Describe the goal, narrative flow, required sections, and expected outcome"],
  custom_colors: ["چهار رنگ HEX با ویرگول؛ برای نمونه #123A35, #F3ECDD, #D8B56A, #1B2622", "Four comma-separated HEX colors, for example #123A35, #F3ECDD, #D8B56A, #1B2622"],
  presenter_name: ["اختیاری", "Optional"], organization: ["اختیاری", "Optional"],
};

function localizedPair(pair, fallback) {
  return pair ? pair[state.locale === "fa" ? 0 : 1] : fallback.replaceAll("_", " ");
}

function podcastParticipantCard(participant = {}, index = 0) {
  const voices = state.podcastVoices || [];
  const thinkingLevels = state.podcastThinkingLevels || ["auto", "minimal", "low", "medium", "high"];
  const voiceOptions = voices.map(voice => `<option value="${escapeHtml(voice.id)}" ${voice.id === (participant.voice || "Kore") ? "selected" : ""}>${escapeHtml(voice.id)} · ${escapeHtml(voice.description || "")}</option>`).join("");
  const thinkingOptions = thinkingLevels.map(level => `<option value="${escapeHtml(level)}" ${level === (participant.thinking_level || "auto") ? "selected" : ""}>${escapeHtml(localizedPair(studioChoiceLabels[level], level))}</option>`).join("");
  return `<article class="podcast-participant-card" data-podcast-participant>
    <header><span class="podcast-participant-number">${faNumber(index + 1)}</span><strong>${escapeHtml(participant.name || l("گویندهٔ تازه", "New participant"))}</strong><div><button type="button" class="icon-button" data-podcast-move-participant="up" aria-label="${escapeHtml(l("انتقال به بالا", "Move up"))}">↑</button><button type="button" class="icon-button" data-podcast-move-participant="down" aria-label="${escapeHtml(l("انتقال به پایین", "Move down"))}">↓</button><button type="button" class="icon-button danger" data-podcast-remove-participant aria-label="${escapeHtml(l("حذف گوینده", "Remove participant"))}">×</button></div></header>
    <input type="hidden" data-podcast-participant-field="participant_id" value="${escapeHtml(participant.participant_id || `speaker-${index + 1}`)}">
    <label><span>${l("نام گوینده", "Display name")}</span><input type="text" maxlength="60" data-podcast-participant-field="name" value="${escapeHtml(participant.name || "")}" required></label>
    <label><span>${l("صدا", "Voice")}</span><select data-podcast-participant-field="voice">${voiceOptions}</select></label>
    <label><span>${l("سطح تفکر شخصیت", "Character thinking level")}</span><select data-podcast-participant-field="thinking_level">${thinkingOptions}</select></label>
    <label><span>${l("خلاقیت شخصیت", "Character temperature")}</span><input type="number" min="0" max="1.5" step="0.05" data-podcast-participant-field="temperature" value="${participant.temperature == null ? "" : Number(participant.temperature)}" placeholder="${escapeHtml(l("خودکار", "Automatic"))}"></label>
    <label class="span-2"><span>${l("نقش در برنامه", "Role in the episode")}</span><input type="text" maxlength="240" data-podcast-participant-field="role" value="${escapeHtml(participant.role || "")}" placeholder="${escapeHtml(l("مثلاً مجری کنجکاو یا متخصص نقاد", "For example: curious host or critical expert"))}"></label>
    <label class="span-2"><span>${l("سبک اجرا", "Delivery style")}</span><textarea rows="2" maxlength="300" data-podcast-participant-field="delivery" placeholder="${escapeHtml(l("لحن، انرژی، نحوهٔ پرسش و واکنش", "Tone, energy, questioning, and reactions"))}">${escapeHtml(participant.delivery || "")}</textarea></label>
  </article>`;
}

function podcastParticipantsMarkup(field, participants) {
  state.podcastVoices = field.voices || [];
  state.podcastThinkingLevels = field.thinking_levels || ["auto", "minimal", "low", "medium", "high"];
  return `<section class="studio-option-field span-2 podcast-participants-editor" data-studio-option="participants" data-option-type="participants">
    <div class="podcast-participants-head"><div><span>${localizedPair(studioFieldLabels.participants, "Participants")}</span><small>${l("برای هر گوینده یک کلید Gemini مجزا لازم است. سهمیه بر اساس پروژه محاسبه می‌شود.", "Each participant needs a distinct Gemini key. Quota is shared at the project level.")}</small></div><button type="button" class="secondary-button" data-podcast-add-participant>${l("افزودن گوینده", "Add participant")}</button></div>
    <div class="podcast-participant-list">${participants.map(podcastParticipantCard).join("")}</div>
    <p class="podcast-participant-requirement" data-podcast-key-requirement></p>
  </section>`;
}

function syncPodcastParticipantControls() {
  const editor = $(".podcast-participants-editor", $("#studio-option-fields"));
  if (!editor) return;
  const cards = $$("[data-podcast-participant]", editor);
  const narrated = $('[data-studio-option="production_mode"]', $("#studio-option-fields"))?.value === "narrated";
  if (narrated && cards.length > 1) cards.slice(1).forEach(card => card.remove());
  const activeCards = $$("[data-podcast-participant]", editor);
  activeCards.forEach((card, index) => {
    $(".podcast-participant-number", card).textContent = faNumber(index + 1);
    const remove = $("[data-podcast-remove-participant]", card);
    remove.disabled = activeCards.length === 1;
    $("[data-podcast-move-participant='up']", card).disabled = index === 0;
    $("[data-podcast-move-participant='down']", card).disabled = index === activeCards.length - 1;
  });
  $("[data-podcast-add-participant]", editor).disabled = narrated || activeCards.length >= 5;
  $("[data-podcast-key-requirement]", editor).textContent = narrated
    ? l("روایت تک‌نفره · یک کلید Gemini", "Single narration · one Gemini key")
    : l(`${faNumber(activeCards.length)} نشست صوتی و ${faNumber(activeCards.length)} کلید مجزا`, `${faNumber(activeCards.length)} live sessions and ${faNumber(activeCards.length)} distinct keys`);
}

function renderStudioOptionFields(entry) {
  $("#studio-option-fields").innerHTML = Object.entries(entry.fields).map(([name, field]) => {
    const label = localizedPair(
      name === "organization" && ["slide_deck", "video_overview"].includes(entry.artifact_type)
        ? ["سازمان یا درس", "Organization or course"]
        : studioFieldLabels[name],
      name,
    );
    const value = entry.defaults[name];
    const attributes = `data-studio-option="${escapeHtml(name)}" data-option-type="${escapeHtml(field.type)}"`;
    if (field.type === "participants") return podcastParticipantsMarkup(field, value || []);
    if (field.type === "font") {
      const options = [
        `<option value="auto" ${value === "auto" ? "selected" : ""}>${escapeHtml(l("انتخاب هوشمند", "Automatic"))}</option>`,
        ...state.fonts.map(font => `<option value="${escapeHtml(font.font_id)}" ${font.font_id === value ? "selected" : ""}>${escapeHtml(font.family)}</option>`),
      ];
      return `<label class="studio-option-field"><span>${escapeHtml(label)}</span><select ${attributes}>${options.join("")}</select></label>`;
    }
    if (field.type === "choice") {
      const options = field.choices.map(choice => `<option value="${escapeHtml(choice)}" ${choice === value ? "selected" : ""}>${escapeHtml(localizedPair(studioChoiceLabels[choice], choice))}</option>`).join("");
      return `<label class="studio-option-field"><span>${escapeHtml(label)}</span><select ${attributes}>${options}</select></label>`;
    }
    if (field.type === "integer") {
      return `<label class="studio-option-field"><span>${escapeHtml(label)}</span><input type="number" value="${Number(value)}" min="${Number(field.minimum)}" max="${Number(field.maximum)}" ${attributes}></label>`;
    }
    if (field.type === "number") {
      return `<label class="studio-option-field"><span>${escapeHtml(label)}</span><input type="number" value="${Number(value)}" min="${Number(field.minimum)}" max="${Number(field.maximum)}" step="${Number(field.step || 0.1)}" ${attributes}></label>`;
    }
    if (field.type === "boolean") {
      return `<label class="studio-option-field studio-boolean"><input type="checkbox" ${value ? "checked" : ""} ${attributes}><span><b>${escapeHtml(label)}</b><small>${l("قابل تغییر برای همین خروجی", "Applied only to this output")}</small></span></label>`;
    }
    const placeholder = localizedPair(studioFieldPlaceholders[name], name);
    return `<label class="studio-option-field ${field.type === "textarea" ? "span-2" : ""}"><span>${escapeHtml(label)}</span>${field.type === "textarea" ? `<textarea rows="3" maxlength="${Number(field.max_length)}" placeholder="${escapeHtml(placeholder)}" ${attributes}>${escapeHtml(value)}</textarea>` : `<input type="text" value="${escapeHtml(value)}" maxlength="${Number(field.max_length)}" placeholder="${escapeHtml(placeholder)}" ${attributes}>`}</label>`;
  }).join("");
  syncStudioConditionalFields();
  syncPodcastParticipantControls();
}

function syncStudioConditionalFields() {
  const root = $("#studio-option-fields");
  const designControl = $('[data-studio-option="design_control"]', root);
  const artifactType = $("#studio-create-type").value;
  const automaticHidden = artifactType === "slide_deck"
    ? ["slide_count_strategy", "slide_count", "content_depth", "media_density", "design_style", "color_theme", "custom_colors", "aspect_ratio", "visual_density", "image_strategy", "narrative_arc", "include_charts", "include_examples", "include_equations", "include_case_studies"]
    : artifactType === "video_overview"
      ? ["scene_count_strategy", "scene_count", "video_style", "resolution", "fps", "media_mode", "pace", "transition_style"]
      : [];
  if (designControl) {
    const automatic = designControl.value === "auto";
    automaticHidden.forEach(name => {
      const field = $(`[data-studio-option="${name}"]`, root);
      if (!field) return;
      field.disabled = automatic;
      field.closest(".studio-option-field").classList.toggle("hidden", automatic);
    });
    root.classList.toggle("studio-options-automatic", automatic);
  }
  const slideCountStrategy = $('[data-studio-option="slide_count_strategy"]', root);
  const slideCount = $('[data-studio-option="slide_count"]', root);
  if (slideCountStrategy && slideCount && designControl?.value !== "auto") {
    const active = slideCountStrategy.value !== "auto";
    slideCount.disabled = !active;
    slideCount.closest(".studio-option-field").classList.toggle("hidden", !active);
  }
  const sceneCountStrategy = $('[data-studio-option="scene_count_strategy"]', root);
  const sceneCount = $('[data-studio-option="scene_count"]', root);
  if (sceneCountStrategy && sceneCount && designControl?.value !== "auto") {
    const active = sceneCountStrategy.value !== "auto";
    sceneCount.disabled = !active;
    sceneCount.closest(".studio-option-field").classList.toggle("hidden", !active);
  }
  const format = $('[data-studio-option="report_format"]', root);
  const customBrief = $('[data-studio-option="custom_report_type"]', root);
  if (format && customBrief) {
    const active = format.value === "custom";
    customBrief.disabled = !active;
    customBrief.closest(".studio-option-field").classList.toggle("hidden", !active);
  }
  const podcastFormat = $('[data-studio-option="format"]', root);
  const customPodcast = $('[data-studio-option="custom_format"]', root);
  if (podcastFormat && customPodcast) {
    const active = podcastFormat.value === "custom";
    customPodcast.disabled = !active;
    customPodcast.closest(".studio-option-field").classList.toggle("hidden", !active);
  }
  const deckFormat = $('[data-studio-option="deck_format"]', root);
  const customDeck = $('[data-studio-option="custom_deck_brief"]', root);
  if (deckFormat && customDeck) {
    const active = deckFormat.value === "custom";
    customDeck.disabled = !active;
    customDeck.closest(".studio-option-field").classList.toggle("hidden", !active);
  }
  const colorTheme = $('[data-studio-option="color_theme"]', root);
  const customColors = $('[data-studio-option="custom_colors"]', root);
  if (colorTheme && customColors) {
    const active = colorTheme.value === "custom";
    customColors.disabled = !active;
    customColors.closest(".studio-option-field").classList.toggle("hidden", !active);
  }
  const narration = $('[data-studio-option="narration"]', root);
  const voice = $('[data-studio-option="voice"]', root);
  const narrationModel = $('[data-studio-option="narration_model"]', root);
  if (narration && voice) {
    const active = narration.value === "gemini_voice";
    voice.disabled = !active;
    voice.closest(".studio-option-field").classList.toggle("hidden", !active);
    if (narrationModel) {
      narrationModel.disabled = !active;
      narrationModel.closest(".studio-option-field").classList.toggle("hidden", !active);
    }
  }
  syncPodcastParticipantControls();
}

async function openStudioCustomization(artifactType) {
  const readyIds = state.documents.filter(item => item.status === "ready").map(item => item.document_id);
  if (!readyIds.length) {
    toast(t("studio.selectSource"));
    return;
  }
  const dialog = $("#studio-create-dialog");
  $("#studio-create-type").value = artifactType;
  $("#studio-create-title").textContent = studioTypeLabel(artifactType);
  $("#studio-create-status").textContent = t("studio.loadingSetup");
  $("#studio-option-fields").innerHTML = `<div class="studio-setup-loading"><i class="status-spinner"></i>${t("studio.loadingSetup")}</div>`;
  const initiallySelected = state.selectedDocumentIds.size ? state.selectedDocumentIds : new Set(readyIds);
  $("#studio-source-options").innerHTML = state.documents.filter(item => item.status === "ready").map(item => `<label class="studio-source-option"><input type="checkbox" data-studio-source value="${escapeHtml(item.document_id)}" ${initiallySelected.has(item.document_id) ? "checked" : ""}><span><b>${escapeHtml(item.title || item.original_filename)}</b><small>${escapeHtml(item.original_filename)} · ${faNumber(item.page_count)} ${l("صفحه", "pages")}</small></span></label>`).join("");
  $("#studio-custom-title").value = "";
  $("#studio-language").value = "auto";
  dialog.showModal();
  try {
    const [catalog] = await Promise.all([loadStudioCatalog(), loadFonts()]);
    const entry = catalog.find(item => item.artifact_type === artifactType);
    if (!entry) throw new Error(l("نسخهٔ backend قدیمی است؛ برنامه را یک‌بار راه‌اندازی مجدد کنید.", "The backend is running an older build; restart the application once."));
    renderStudioOptionFields(entry);
    $("#studio-create-status").textContent = "";
  } catch (error) {
    $("#studio-create-status").textContent = error.message;
    $("#studio-option-fields").innerHTML = `<div class="warning-list"><p>${escapeHtml(error.message)}</p></div>`;
  }
}

function readStudioOptions() {
  return Object.fromEntries($$("[data-studio-option]", $("#studio-option-fields")).filter(field => !field.disabled).map(field => {
    const type = field.dataset.optionType;
    if (type === "participants") {
      const value = $$("[data-podcast-participant]", field).map(card => Object.fromEntries($$("[data-podcast-participant-field]", card).map(input => {
        const name = input.dataset.podcastParticipantField;
        const raw = input.value.trim();
        return [name, name === "temperature" ? (raw === "" ? null : Number(raw)) : raw];
      })));
      return [field.dataset.studioOption, value];
    }
    const value = type === "boolean" ? field.checked : ["integer", "number"].includes(type) ? Number(field.value) : field.value.trim();
    return [field.dataset.studioOption, value];
  }));
}

async function generateStudioArtifact() {
  const documentIds = $$('[data-studio-source]:checked', $("#studio-source-options")).map(field => field.value);
  if (!documentIds.length) {
    $("#studio-create-status").textContent = t("studio.selectSource");
    return;
  }
  const submit = $("#studio-create-submit");
  const workspaceId = state.activeWorkspaceId;
  const workspaceRevision = state.workspaceRevision;
  submit.disabled = true;
  $("#studio-create-status").textContent = t("studio.creating");
  activateContextPanel("studio");
  try {
    const artifact = await api(`/api/workspaces/${encodeURIComponent(workspaceId)}/studio/artifacts`, {
      method: "POST",
      body: {
        artifact_type: $("#studio-create-type").value,
        document_ids: documentIds,
        language: $("#studio-language").value,
        title: $("#studio-custom-title").value.trim() || null,
        options: readStudioOptions(),
      },
    });
    if (!replaceStudioArtifact(artifact, workspaceId, workspaceRevision)) return;
    $("#studio-create-dialog").close();
    toast(t("studio.started"));
  } catch (error) {
    if (isCurrentWorkspaceRequest(state, workspaceId, workspaceRevision)) {
      $("#studio-create-status").textContent = error.message;
    }
  } finally {
    submit.disabled = false;
  }
}

function quizMarkup(data, rendered = {}) {
  const questions = Array.isArray(data.questions) ? data.questions : [];
  return `<div class="quiz-preview">${questions.map((item, index) => {
    const choices = Array.isArray(item.choices) ? item.choices : [];
    const renderedItem = rendered.questions?.[index] || {};
    return `<details class="quiz-question"><summary>${faNumber(index + 1)}. ${studioMarkdown(item.question || "", renderedItem.question)}</summary><div class="quiz-choices">${choices.map((choice, choiceIndex) => `<button class="quiz-choice" type="button" data-quiz-choice data-correct="${choiceIndex === Number(item.correct_index)}">${studioMarkdown(choice, renderedItem.choices?.[choiceIndex])}</button>`).join("")}</div><div class="quiz-explanation"><b>${l("پاسخ و توضیح", "Answer & explanation")}</b><div class="studio-block-content">${studioMarkdown(item.explanation || l("پاسخ صحیح مشخص شده است.", "The correct answer is highlighted."), renderedItem.explanation, "block")}</div>${studioCitationMarkup(item.citations)}</div></details>`;
  }).join("")}</div>`;
}

function answerQuizChoice(button) {
  const question = button.closest(".quiz-question");
  if (!question || question.classList.contains("answered")) return;
  question.classList.add("answered");
  question.querySelectorAll("[data-quiz-choice]").forEach(choice => {
    choice.disabled = true;
    if (choice.dataset.correct === "true") choice.classList.add("correct");
  });
  button.classList.add(button.dataset.correct === "true" ? "selected-correct" : "wrong");
}

function chartLegendMarkup(datasets, renderedDatasets = []) {
  return `<div class="chart-legend">${datasets.map((dataset, index) => `<span><i class="chart-color-${index % 6}"></i>${studioMarkdown(dataset.label || `${l("سری", "Series")} ${index + 1}`, renderedDatasets[index]?.label)}</span>`).join("")}</div>`;
}

function chartBarsMarkup(labels, datasets, renderedLabels = [], renderedDatasets = []) {
  const maximum = Math.max(1, ...datasets.flatMap(dataset => dataset.data.map(value => Math.abs(Number(value) || 0))));
  return `<div class="chart-bars">${labels.map((label, labelIndex) => `<section class="chart-group"><strong>${studioMarkdown(label, renderedLabels[labelIndex])}</strong><div>${datasets.map((dataset, seriesIndex) => {
    const value = Number(dataset.data[labelIndex]) || 0;
    const width = Math.min(100, Math.abs(value) / maximum * 100);
    return `<div class="chart-row"><span>${studioMarkdown(dataset.label || `${l("سری", "Series")} ${seriesIndex + 1}`, renderedDatasets[seriesIndex]?.label)}</span><div class="chart-track"><i class="chart-color-${seriesIndex % 6}" style="width:${width}%"></i></div><b>${escapeHtml(value)}</b></div>`;
  }).join("")}</div></section>`).join("")}</div>`;
}

function chartPlotMarkup(labels, datasets, chartType) {
  const width = 760;
  const height = 330;
  const inset = 42;
  const values = datasets.flatMap(dataset => dataset.data.map(Number)).filter(Number.isFinite);
  const minimum = Math.min(0, ...values);
  const maximum = Math.max(1, ...values);
  const span = Math.max(1, maximum - minimum);
  const x = index => inset + index * ((width - inset * 2) / Math.max(1, labels.length - 1));
  const y = value => height - inset - ((Number(value) - minimum) / span) * (height - inset * 2);
  const series = datasets.map((dataset, seriesIndex) => {
    const points = dataset.data.map((value, index) => `${x(index)},${y(value)}`).join(" ");
    const line = chartType === "line" ? `<polyline class="chart-stroke-${seriesIndex % 6}" points="${points}" fill="none"/>` : "";
    const dots = dataset.data.map((value, index) => `<circle class="chart-fill-${seriesIndex % 6}" cx="${x(index)}" cy="${y(value)}" r="${chartType === "scatter" ? 6 : 4}"><title>${escapeHtml(labels[index])}: ${escapeHtml(value)}</title></circle>`).join("");
    return `${line}${dots}`;
  }).join("");
  const axes = `<line class="chart-axis" x1="${inset}" y1="${height - inset}" x2="${width - inset}" y2="${height - inset}"/><line class="chart-axis" x1="${inset}" y1="${inset}" x2="${inset}" y2="${height - inset}"/>`;
  const ticks = labels.map((label, index) => `<text x="${x(index)}" y="${height - 14}" text-anchor="middle">${escapeHtml(String(label).slice(0, 18))}</text>`).join("");
  return `<div class="chart-plot-scroll"><svg class="chart-plot" viewBox="0 0 ${width} ${height}" role="img" aria-label="${escapeHtml(l("نمودار داده", "Data chart"))}">${axes}${series}${ticks}</svg></div>`;
}

function chartPieMarkup(labels, dataset, renderedLabels = []) {
  const values = dataset.data.map(value => Math.max(0, Number(value) || 0));
  const total = values.reduce((sum, value) => sum + value, 0) || 1;
  let cursor = 0;
  const segments = values.map((value, index) => {
    const start = cursor;
    cursor += value / total * 360;
    return `var(--chart-${index % 6 + 1}) ${start}deg ${cursor}deg`;
  }).join(",");
  return `<div class="chart-pie-layout"><div class="chart-pie" style="background:conic-gradient(${segments})" role="img" aria-label="${escapeHtml(dataTitle(dataset.label))}"></div><div class="chart-pie-values">${labels.map((label, index) => `<span><i class="chart-color-${index % 6}"></i><b>${studioMarkdown(label, renderedLabels[index])}</b><em>${escapeHtml(values[index])}</em></span>`).join("")}</div></div>`;
}

function dataTitle(value) {
  return value || l("نمودار دایره‌ای", "Pie chart");
}

function chartMarkup(data, rendered = {}) {
  const labels = Array.isArray(data.labels) ? data.labels : [];
  const datasets = (Array.isArray(data.datasets) ? data.datasets : []).filter(dataset => Array.isArray(dataset.data));
  const chartType = ["bar", "line", "pie", "scatter"].includes(data.chart_type) ? data.chart_type : "bar";
  const visualization = chartType === "pie"
    ? chartPieMarkup(labels, datasets[0], rendered.labels)
    : chartType === "line" || chartType === "scatter"
      ? chartPlotMarkup(labels, datasets, chartType)
      : chartBarsMarkup(labels, datasets, rendered.labels, rendered.datasets);
  return `<div class="chart-preview"><header><div><span>${escapeHtml(chartType.toLocaleUpperCase())}</span><h3>${studioMarkdown(data.title || l("نمودار داده", "Data chart"), rendered.title)}</h3></div>${chartLegendMarkup(datasets, rendered.datasets)}</header>${visualization}${data.explanation ? `<div class="chart-explanation studio-block-content">${studioMarkdown(data.explanation, rendered.explanation, "block")}</div>` : ""}${studioCitationMarkup(data.citations)}</div>`;
}

function studioCitationMarkup(citations) {
  const items = Array.isArray(citations) ? citations : [];
  return items.length ? `<small class="studio-inline-citations">${items.map(item => escapeHtml(typeof item === "string" ? item : item?.label || "")).filter(Boolean).join(" · ")}</small>` : "";
}

function studioRichText(value) {
  return escapeHtml(value)
    .replace(/\$([^$\n]+)\$/g, '<span class="math-expression" dir="ltr">$1</span>')
    .replace(/\n/g, "<br>");
}

function studioMarkdown(value, rendered, mode = "inline") {
  const html = rendered && typeof rendered === "object" ? rendered[mode] : "";
  const content = typeof html === "string" && html ? html : studioRichText(value);
  const tag = mode === "block" ? "div" : "span";
  return `<${tag} class="studio-bidi-${mode}" dir="auto">${content}</${tag}>`;
}

function buildConceptMapTree(data) {
  const sourceNodes = Array.isArray(data.nodes) ? data.nodes : [];
  const nodes = sourceNodes.map((item, index) => ({...item, id: String(item.id || `node-${index + 1}`), index, children: []}));
  const byId = new Map(nodes.map(item => [item.id, item]));
  const wantedRoot = String(data.root || data.title || "").trim();
  const incoming = new Set((data.edges || []).map(edge => String(edge.target || "")));
  const root = nodes.find(item => item.id === wantedRoot || String(item.label || "").trim() === wantedRoot)
    || nodes.find(item => !incoming.has(item.id))
    || nodes[0];
  if (!root) return null;

  const adjacency = new Map(nodes.map(item => [item.id, []]));
  for (const edge of Array.isArray(data.edges) ? data.edges : []) {
    const source = String(edge.source || "");
    const target = String(edge.target || "");
    if (!byId.has(source) || !byId.has(target) || source === target) continue;
    adjacency.get(source).push({id: target, label: edge.label || ""});
    adjacency.get(target).push({id: source, label: edge.label || ""});
  }
  const visited = new Set([root.id]);
  const queue = [root];
  while (queue.length) {
    const parent = queue.shift();
    for (const relation of adjacency.get(parent.id) || []) {
      if (visited.has(relation.id)) continue;
      const child = byId.get(relation.id);
      visited.add(child.id);
      child.relation = relation.label;
      parent.children.push(child);
      queue.push(child);
    }
  }
  for (const node of nodes) {
    if (visited.has(node.id)) continue;
    node.relation = l("موضوع مرتبط", "Related topic");
    root.children.push(node);
    visited.add(node.id);
  }
  return root;
}

function conceptMapBranchMarkup(node, rendered, depth = 0) {
  const hasChildren = node.children.length > 0;
  const expanded = hasChildren && depth === 0;
  const renderedNode = rendered.nodes?.[node.index] || {};
  return `<li class="mind-map-branch" data-mind-map-branch data-node-id="${escapeHtml(node.id)}">
    ${node.relation ? `<span class="mind-map-relation">${studioMarkdown(node.relation, null)}</span>` : ""}
    <button class="mind-map-node ${depth === 0 ? "root" : ""}" type="button" data-mind-map-node="${escapeHtml(node.id)}" aria-expanded="${hasChildren ? String(expanded) : "false"}" ${hasChildren ? "" : 'data-leaf="true"'}>
      <span>${studioMarkdown(node.label || node.id, renderedNode.label || renderedNode.id)}</span>
      ${hasChildren ? `<i aria-hidden="true">${faNumber(node.children.length)}</i>` : ""}
    </button>
    ${hasChildren ? `<ul class="mind-map-children" ${expanded ? "" : "hidden"}>${node.children.map(child => conceptMapBranchMarkup(child, rendered, depth + 1)).join("")}</ul>` : ""}
  </li>`;
}

function conceptMapMarkup(data, rendered = {}) {
  const root = buildConceptMapTree(data);
  if (!root) return "";
  return `<section class="mind-map-workbench" data-mind-map-root="${escapeHtml(root.id)}">
    <header class="mind-map-header"><div><span>${l("نقشهٔ تعاملی مفاهیم", "Interactive concept map")}</span><h3>${studioMarkdown(data.title || data.root || "", rendered.title || rendered.root)}</h3></div><div class="mind-map-toolbar" aria-label="${escapeHtml(l("کنترل‌های ذهن‌نگاشت", "Mind map controls"))}"><button type="button" data-mind-map-action="zoom-out" aria-label="${l("کوچک‌نمایی", "Zoom out")}">−</button><button type="button" data-mind-map-action="fit">${l("نمایش کامل", "Fit map")}</button><button type="button" data-mind-map-action="zoom-in" aria-label="${l("بزرگ‌نمایی", "Zoom in")}">+</button><button type="button" data-mind-map-action="expand-all">${l("باز کردن همه", "Expand all")}</button><button type="button" data-mind-map-action="collapse-all">${l("جمع کردن", "Collapse")}</button></div></header>
    <aside class="mind-map-detail" data-mind-map-detail aria-live="polite"></aside>
    <div class="mind-map-viewport" tabindex="0" aria-label="${escapeHtml(l("برای جابه‌جایی بکشید و برای بزرگ‌نمایی از چرخ ماوس استفاده کنید", "Drag to pan and use the mouse wheel to zoom"))}"><div class="mind-map-canvas"><ul class="mind-map-tree">${conceptMapBranchMarkup(root, rendered)}</ul></div></div>
  </section>`;
}

function mindMapArtifact() {
  return state.studioArtifacts.find(item => item.artifact_id === state.activeStudioArtifactId);
}

function selectMindMapNode(control) {
  const workbench = control?.closest(".mind-map-workbench");
  if (!workbench) return;
  const artifact = mindMapArtifact();
  const nodes = artifact?.data?.nodes || [];
  const index = nodes.findIndex(item => String(item.id) === control.dataset.mindMapNode);
  const node = nodes[index];
  if (!node) return;
  $$("[data-mind-map-node]", workbench).forEach(item => item.classList.toggle("is-selected", item === control));
  const rendered = artifact.rendered_data?.nodes?.[index] || {};
  $("[data-mind-map-detail]", workbench).innerHTML = `<span>${l("جزئیات مفهوم", "Concept details")}</span><h4>${studioMarkdown(node.label || node.id, rendered.label || rendered.id)}</h4><div class="studio-block-content">${studioMarkdown(node.description || l("برای این گره توضیح جداگانه‌ای ثبت نشده است.", "No separate description was provided for this node."), rendered.description, "block")}</div>${studioCitationMarkup(node.citations)}`;
}

function toggleMindMapNode(control) {
  const branch = control?.closest("[data-mind-map-branch]");
  const children = branch ? $(":scope > .mind-map-children", branch) : null;
  if (!children) return;
  const expanded = control.getAttribute("aria-expanded") === "true";
  control.setAttribute("aria-expanded", String(!expanded));
  children.hidden = expanded;
  requestAnimationFrame(() => fitMindMap(control.closest(".mind-map-workbench")));
}

function setMindMapExpansion(workbench, expanded) {
  const branches = $$('[data-mind-map-node]:not([data-leaf="true"])', workbench);
  branches.forEach((control, index) => {
    const keepRootOpen = !expanded && index === 0;
    control.setAttribute("aria-expanded", String(expanded || keepRootOpen));
    const children = $(":scope > .mind-map-children", control.closest("[data-mind-map-branch]"));
    if (children) children.hidden = !(expanded || keepRootOpen);
  });
  requestAnimationFrame(() => fitMindMap(workbench));
}

function applyMindMapTransform(workbench) {
  const canvas = $(".mind-map-canvas", workbench);
  const viewport = $(".mind-map-viewport", workbench);
  const view = viewport?._mindMapView;
  if (!canvas || !view) return;
  canvas.style.transform = `translate(${view.x}px, ${view.y}px) scale(${view.scale})`;
  viewport.dataset.zoom = String(Math.round(view.scale * 100));
}

function fitMindMap(workbench) {
  const viewport = $(".mind-map-viewport", workbench);
  const canvas = $(".mind-map-canvas", workbench);
  if (!viewport || !canvas || !viewport.clientWidth || !viewport.clientHeight) return;
  const width = canvas.offsetWidth;
  const height = canvas.offsetHeight;
  const padding = 28;
  const scale = Math.min(1, (viewport.clientWidth - padding * 2) / width, (viewport.clientHeight - padding * 2) / height);
  viewport._mindMapView = {
    ...(viewport._mindMapView || {}),
    scale: Math.max(0.24, scale),
    x: (viewport.clientWidth - width * Math.max(0.24, scale)) / 2,
    y: (viewport.clientHeight - height * Math.max(0.24, scale)) / 2,
  };
  applyMindMapTransform(workbench);
}

function zoomMindMap(workbench, factor, clientX, clientY) {
  const viewport = $(".mind-map-viewport", workbench);
  if (!viewport?._mindMapView) return;
  const view = viewport._mindMapView;
  const rect = viewport.getBoundingClientRect();
  const anchorX = (clientX ?? rect.left + rect.width / 2) - rect.left;
  const anchorY = (clientY ?? rect.top + rect.height / 2) - rect.top;
  const nextScale = Math.max(0.24, Math.min(2, view.scale * factor));
  const mapX = (anchorX - view.x) / view.scale;
  const mapY = (anchorY - view.y) / view.scale;
  view.x = anchorX - mapX * nextScale;
  view.y = anchorY - mapY * nextScale;
  view.scale = nextScale;
  applyMindMapTransform(workbench);
}

function initializeMindMapViewport(workbench) {
  const viewport = $(".mind-map-viewport", workbench);
  if (!viewport || viewport._mindMapView) return;
  viewport._mindMapView = {x: 0, y: 0, scale: 1, dragging: false};
  viewport.addEventListener("pointerdown", event => {
    if (event.target.closest("button")) return;
    const view = viewport._mindMapView;
    view.dragging = true;
    view.pointerId = event.pointerId;
    view.startX = event.clientX;
    view.startY = event.clientY;
    view.originX = view.x;
    view.originY = view.y;
    viewport.classList.add("is-panning");
    viewport.setPointerCapture(event.pointerId);
  });
  viewport.addEventListener("pointermove", event => {
    const view = viewport._mindMapView;
    if (!view.dragging || view.pointerId !== event.pointerId) return;
    view.x = view.originX + event.clientX - view.startX;
    view.y = view.originY + event.clientY - view.startY;
    applyMindMapTransform(workbench);
  });
  const finishPan = event => {
    const view = viewport._mindMapView;
    if (!view.dragging || view.pointerId !== event.pointerId) return;
    view.dragging = false;
    viewport.classList.remove("is-panning");
    if (viewport.hasPointerCapture(event.pointerId)) viewport.releasePointerCapture(event.pointerId);
  };
  viewport.addEventListener("pointerup", finishPan);
  viewport.addEventListener("pointercancel", finishPan);
  viewport.addEventListener("wheel", event => {
    event.preventDefault();
    zoomMindMap(workbench, event.deltaY < 0 ? 1.12 : 1 / 1.12, event.clientX, event.clientY);
  }, {passive: false});
  requestAnimationFrame(() => fitMindMap(workbench));
}

function timelineMarkup(data, rendered = {}) {
  const events = Array.isArray(data.events) ? data.events : [];
  return `<section class="timeline-preview"><header><span>${l("خط زمانی مستند", "Source timeline")}</span><h3>${studioMarkdown(data.title || l("خط زمانی", "Timeline"), rendered.title)}</h3></header><ol>${events.map((item, index) => `<li class="${item.uncertain ? "uncertain" : ""}"><i>${faNumber(index + 1)}</i><article><time dir="auto">${studioMarkdown(item.date || "—", rendered.events?.[index]?.date)}</time><h4>${studioMarkdown(item.title || "", rendered.events?.[index]?.title)}</h4><div class="studio-block-content">${studioMarkdown(item.description || "", rendered.events?.[index]?.description, "block")}</div>${item.significance ? `<aside><b>${l("اهمیت", "Why it matters")}</b>${studioMarkdown(item.significance, rendered.events?.[index]?.significance)}</aside>` : ""}${item.uncertain ? `<em>${l("تاریخ یا ترتیب تقریبی است", "Date or order is approximate")}</em>` : ""}${studioCitationMarkup(item.citations)}</article></li>`).join("")}</ol></section>`;
}

function flashcardsMarkup(data, rendered = {}) {
  const cards = Array.isArray(data.cards) ? data.cards : [];
  return `<section class="flashcard-deck" data-flashcard-deck data-current="0" data-total="${cards.length}">
    <header class="flashcard-deck-head"><div><span>${l("دستهٔ مرور", "Study deck")}</span><strong>${studioMarkdown(data.title || l("فلش‌کارت‌ها", "Flashcards"), rendered.title)}</strong></div><div><b data-flashcard-progress aria-live="polite">${faNumber(1)} / ${faNumber(cards.length)}</b><span class="flashcard-progress-track"><i data-flashcard-progress-bar style="width:${100 / cards.length}%"></i></span></div></header>
    <div class="flashcard-stage">${cards.map((item, index) => `<article class="flashcard-slide" data-flashcard-slide data-index="${index}" ${index ? "hidden" : ""}><button class="flashcard-card" type="button" data-flashcard-card aria-pressed="false" aria-label="${escapeHtml(l("نمایش پاسخ فلش‌کارت", "Reveal flashcard answer"))}"><span class="flashcard-face flashcard-front"><small><i>Q</i>${l("پرسش", "Question")}</small><span class="flashcard-question">${studioMarkdown(item.front || "", rendered.cards?.[index]?.front)}</span><em>${l("برای دیدن پاسخ، کارت را انتخاب کنید", "Select the card to reveal the answer")}</em></span><span class="flashcard-face flashcard-back"><small><i>A</i>${l("پاسخ", "Answer")}</small><span class="flashcard-answer studio-block-content">${studioMarkdown(item.back || "", rendered.cards?.[index]?.back, "block")}</span>${studioCitationMarkup(item.citations)}</span></button></article>`).join("")}</div>
    <footer class="flashcard-deck-controls"><button class="secondary-button" type="button" data-flashcard-prev disabled><svg><use href="#i-arrow"></use></svg><span>${l("قبلی", "Previous")}</span></button><button class="primary-button flashcard-flip" type="button" data-flashcard-flip><span>${l("نمایش پاسخ", "Show answer")}</span></button><button class="secondary-button" type="button" data-flashcard-next ${cards.length === 1 ? "disabled" : ""}><span>${l("بعدی", "Next")}</span><svg><use href="#i-arrow"></use></svg></button></footer>
  </section>`;
}

function updateFlashcardDeck(deck, requestedIndex) {
  if (!deck) return;
  const slides = $$('[data-flashcard-slide]', deck);
  const index = Math.max(0, Math.min(slides.length - 1, Number(requestedIndex) || 0));
  deck.dataset.current = String(index);
  slides.forEach((slide, slideIndex) => {
    slide.hidden = slideIndex !== index;
    const card = $('[data-flashcard-card]', slide);
    card?.classList.remove("is-revealed");
    card?.setAttribute("aria-pressed", "false");
    card?.setAttribute("aria-label", l("نمایش پاسخ فلش‌کارت", "Reveal flashcard answer"));
  });
  $('[data-flashcard-progress]', deck).textContent = `${faNumber(index + 1)} / ${faNumber(slides.length)}`;
  $('[data-flashcard-progress-bar]', deck).style.width = `${(index + 1) / slides.length * 100}%`;
  $('[data-flashcard-prev]', deck).disabled = index === 0;
  $('[data-flashcard-next]', deck).disabled = index === slides.length - 1;
  $('[data-flashcard-flip] span', deck).textContent = l("نمایش پاسخ", "Show answer");
}

function navigateFlashcards(control, delta) {
  const deck = control.closest('[data-flashcard-deck]');
  updateFlashcardDeck(deck, Number(deck?.dataset.current || 0) + delta);
}

function toggleFlashcard(control) {
  const deck = control.closest('[data-flashcard-deck]');
  const card = control.matches('[data-flashcard-card]')
    ? control
    : $('[data-flashcard-slide]:not([hidden]) [data-flashcard-card]', deck);
  if (!card) return;
  const revealed = card.classList.toggle("is-revealed");
  card.setAttribute("aria-pressed", String(revealed));
  card.setAttribute("aria-label", revealed
    ? l("نمایش پرسش فلش‌کارت", "Show flashcard question")
    : l("نمایش پاسخ فلش‌کارت", "Reveal flashcard answer"));
  $('[data-flashcard-flip] span', deck).textContent = revealed
    ? l("نمایش پرسش", "Show question")
    : l("نمایش پاسخ", "Show answer");
}

function infographicMarkup(data, rendered = {}) {
  const sections = Array.isArray(data.sections) ? data.sections : [];
  return `<div class="infographic-preview"><header><span>${studioMarkdown(data.subtitle || "", rendered.subtitle)}</span><h3>${studioMarkdown(data.title || "", rendered.title)}</h3></header><div>${sections.map((item, index) => `<article><i>${faNumber(index + 1)}</i><section>${item.metric ? `<strong>${studioMarkdown(item.metric, rendered.sections?.[index]?.metric)}</strong>` : ""}<h4>${studioMarkdown(item.heading || "", rendered.sections?.[index]?.heading)}</h4><div class="studio-block-content">${studioMarkdown(item.body || "", rendered.sections?.[index]?.body, "block")}</div>${studioCitationMarkup(item.citations)}</section></article>`).join("")}</div>${data.key_takeaway ? `<footer><b>${l("جمع‌بندی", "Key takeaway")}</b><div class="studio-block-content">${studioMarkdown(data.key_takeaway, rendered.key_takeaway, "block")}</div></footer>` : ""}</div>`;
}

function dataTableMarkup(data, rendered = {}) {
  const columns = (Array.isArray(data.columns) ? data.columns : []).map(item => typeof item === "string" ? item : item?.label || item?.key || "");
  const rows = Array.isArray(data.rows) ? data.rows : [];
  const cells = (row, rowIndex) => columns.map((column, index) => `<td>${studioMarkdown(Array.isArray(row) ? row[index] : row?.[column] ?? "—", Array.isArray(rendered.rows?.[rowIndex]) ? rendered.rows[rowIndex][index] : rendered.rows?.[rowIndex]?.[column])}</td>`).join("");
  const notes = Array.isArray(data.notes)
    ? data.notes.map((note, index) => studioMarkdown(note, rendered.notes?.[index])).join(" · ")
    : studioMarkdown(data.notes || "", rendered.notes);
  return `<div class="data-table-preview"><div class="studio-table-scroll"><table><thead><tr>${columns.map((column, index) => `<th>${studioMarkdown(column, rendered.columns?.[index])}</th>`).join("")}</tr></thead><tbody>${rows.map((row, index) => `<tr>${cells(row, index)}</tr>`).join("")}</tbody></table></div>${data.notes ? `<div class="data-table-notes">${notes}</div>` : ""}${studioCitationMarkup(data.citations)}</div>`;
}

function podcastTranscriptMarkup(data, config) {
  const names = new Map((config.participants || []).map(item => [item.participant_id, item.name]));
  const turns = data.generated_turns || [];
  if (!turns.length) return "";
  return `<section class="podcast-transcript"><header><div><span>${l("رونوشت", "Transcript")}</span><h3>${l("گفت‌وگوی ضبط‌شده", "Recorded conversation")}</h3></div>${data.transcript_filename ? `<a class="secondary-button" href="${escapeHtml(data.transcript_url || "#")}" data-podcast-download-transcript download>${l("دانلود متن", "Download transcript")}</a>` : ""}</header><div>${turns.map((turn, index) => `<article><span>${faNumber(index + 1)}</span><div><b>${escapeHtml(names.get(turn.participant_id) || turn.participant_id)}</b><p>${escapeHtml(turn.reviewed_text || turn.transcript || "")}</p>${turn.warnings?.length ? `<small>${turn.warnings.map(escapeHtml).join(" · ")}</small>` : ""}</div></article>`).join("")}</div></section>`;
}

function podcastProgressTimeline(events = []) {
  if (!events.length) return "";
  const labels = {
    sources_started: ["در حال گردآوری شواهد", "Gathering evidence"], draft_started: ["در حال ساخت پیش‌نویس", "Drafting episode"], draft_ready: ["دیالوگ آمادهٔ بازبینی", "Dialogue ready for review"], plan_ready: ["طرح برنامه آماده شد", "Episode plan ready"], script_updated: ["ویرایش کاربر ذخیره شد", "User edits saved"], audio_queued: ["در صف تولید صوت", "Audio queued"], audio_started: ["اتصال نشست‌های صوتی", "Connecting live sessions"], turn_completed: ["نوبت صوتی ذخیره شد", "Audio turn checkpointed"], audio_ready: ["اپیزود آماده است", "Episode ready"], cancel_requested: ["درخواست لغو ثبت شد", "Cancellation requested"], audio_cancelled: ["تولید لغو شد", "Generation cancelled"], audio_failed: ["تولید صوت ناموفق بود", "Audio generation failed"], draft_failed: ["ساخت پیش‌نویس ناموفق بود", "Drafting failed"],
  };
  return `<section class="podcast-progress-timeline"><header><span>${l("جریان تولید", "Production activity")}</span><strong>${faNumber(events.length)} ${l("رویداد", "events")}</strong></header><div>${events.map((event, index) => `<article class="${index === events.length - 1 ? "active" : ""}"><i>${index === events.length - 1 && !["audio_ready", "audio_failed", "audio_cancelled", "draft_ready", "plan_ready"].includes(event.kind) ? '<span class="status-spinner"></span>' : ""}</i><div><b>${localizedPair(labels[event.kind], event.kind)}</b><small>${relativeTime(event.created_at)}${event.details?.participant_id ? ` · ${escapeHtml(event.details.participant_id)}` : ""}${event.details?.latency_ms ? ` · ${faNumber(event.details.latency_ms)}ms` : ""}</small></div><em>${faNumber(event.progress)}%</em></article>`).join("")}</div></section>`;
}

function podcastScriptTurnMarkup(turn, index, participants) {
  const speakerOptions = participants.map(item => `<option value="${escapeHtml(item.participant_id)}" ${item.participant_id === turn.participant_id ? "selected" : ""}>${escapeHtml(item.name)}</option>`).join("");
  return `<article class="podcast-script-turn" data-podcast-turn data-turn-id="${escapeHtml(turn.turn_id || `turn-${Date.now()}-${index}`)}">
    <header><span>${faNumber(index + 1)}</span><select data-podcast-turn-field="participant_id">${speakerOptions}</select><input type="text" data-podcast-turn-field="chapter" maxlength="160" value="${escapeHtml(turn.chapter || "")}" placeholder="${escapeHtml(l("عنوان بخش", "Chapter"))}"><div><button class="icon-button" type="button" data-podcast-move-turn="up" aria-label="${escapeHtml(l("بالا", "Move up"))}">↑</button><button class="icon-button" type="button" data-podcast-move-turn="down" aria-label="${escapeHtml(l("پایین", "Move down"))}">↓</button><button class="icon-button danger" type="button" data-podcast-remove-turn aria-label="${escapeHtml(l("حذف", "Remove"))}">×</button></div></header>
    <textarea rows="4" maxlength="4000" data-podcast-turn-field="text" required>${escapeHtml(turn.text || "")}</textarea>
    <label><span>${l("برچسب شواهد", "Evidence labels")}</span><input type="text" data-podcast-turn-field="citations" value="${escapeHtml((turn.citations || []).join(", "))}" placeholder="S1, S2"></label>
  </article>`;
}

function podcastMarkup(item) {
  const data = item.data || {};
  const config = data.config || item.options || {};
  const participants = config.participants || [];
  const script = data.script || [];
  const plan = data.plan || [];
  const running = ["queued", "gathering", "drafting", "generating"].includes(item.status) || item.stage === "audio_queued";
  const required = item.credential_status?.required ?? participants.length;
  const configured = item.credential_status?.configured ?? 0;
  const reserve = item.credential_status?.reserve ?? Math.max(0, configured - required);
  const credentialReady = configured >= required;
  const modeLabels = {narrated: ["روایت تک‌نفره", "Narrated"], scripted: ["دیالوگ کامل", "Scripted"], guided: ["هدایت‌شده", "Producer-guided"], autonomous: ["میزگرد آزاد", "Autonomous"]};
  const duration = Number(data.duration_seconds || 0);
  const generatedCount = (data.generated_turns || []).length;
  const generatedTime = `${faNumber(Math.floor(duration / 60))}:${String(Math.round(duration % 60)).padStart(2, "0")}`;
  const productionMessage = running && generatedCount
    ? l(`تا این لحظه ${faNumber(generatedCount)} نوبت و ${generatedTime} صوت ساخته شده است.`, `${faNumber(generatedCount)} turns and ${generatedTime} of audio have been produced.`)
    : running
      ? l("تولید صوت آغاز شده و وضعیت زنده به‌روزرسانی می‌شود.", "Audio production has started and updates live.")
      : l("آخرین وضعیت ذخیره شده است.", "The latest state is saved.");
  const reviewable = config.production_mode === "scripted";
  const canStart = ["script_ready", "plan_ready"].includes(item.status) || (item.status === "failed" && (script.length || plan.length));
  const controls = canStart
    ? `<div class="podcast-production-actions">${reviewable && script.length ? `<button class="secondary-button" type="button" data-podcast-save-script>${l("ذخیرهٔ پیش‌نویس", "Save draft")}</button>` : ""}<button class="primary-button" type="button" data-podcast-generate ${credentialReady ? "" : "disabled"}><svg><use href="#i-mic"></use></svg>${reviewable ? l("تأیید و ساخت فایل صوتی", "Approve and generate audio") : l("ساخت فایل صوتی", "Generate audio")}</button></div>`
    : item.status === "generating" || item.stage === "audio_queued" ? `<div class="podcast-production-actions"><button class="secondary-button danger" type="button" data-podcast-cancel>${l("لغو تولید", "Cancel generation")}</button></div>` : "";
  return `<div class="podcast-studio" data-podcast-artifact="${escapeHtml(item.artifact_id)}">
    <section class="podcast-hero"><div class="podcast-cover"><svg><use href="#i-mic"></use></svg><span></span></div><div><span>${localizedPair(modeLabels[config.production_mode], config.production_mode || "Podcast")}</span><h3>${escapeHtml(item.title)}</h3><p>${escapeHtml(data.summary || l("مرور صوتی مستند بر پایهٔ منابع انتخاب‌شده", "A source-grounded audio overview"))}</p><div class="podcast-facts"><b>${faNumber(config.duration_minutes)} ${l("دقیقه هدف", "min target")}</b><b>${faNumber(participants.length)} ${l("گوینده", "voices")}</b>${reserve ? `<b>${faNumber(reserve)} ${l("مسیر رزرو", "reserve routes")}</b>` : ""}<b>${escapeHtml(config.model || item.model || "Gemini Live")}</b></div></div></section>
    <section class="podcast-production-status"><div><span>${running ? '<i class="status-spinner"></i>' : '<svg><use href="#i-check"></use></svg>'}</span><div><b>${escapeHtml(item.stage.replaceAll("_", " "))}</b><small>${productionMessage}</small></div></div><strong>${faNumber(item.progress)}%</strong><span class="podcast-progress-bar"><i style="width:${Number(item.progress)}%"></i></span></section>
    ${!credentialReady ? `<div class="podcast-credential-warning"><svg><use href="#i-settings"></use></svg><div><b>${l("کلید کافی برای تولید صوت وجود ندارد", "Not enough credentials for audio generation")}</b><p>${l(`${faNumber(required)} کلید مجزای Gemini لازم است و اکنون ${faNumber(configured)} کلید تنظیم شده. چند کلید از یک پروژه سهمیهٔ مشترک دارند.`, `${faNumber(required)} distinct Gemini keys are required; ${faNumber(configured)} are configured. Keys from one project share quota.`)}</p></div></div>` : ""}
    <section class="podcast-cast"><header><span>${l("گویندگان", "Cast")}</span></header><div>${participants.map(item => `<article><i>${escapeHtml((item.name || "?").slice(0, 1))}</i><div><b>${escapeHtml(item.name)}</b><small>${escapeHtml(item.role || "")} · ${escapeHtml(item.voice)} · ${escapeHtml(localizedPair(studioChoiceLabels[item.thinking_level || "auto"], item.thinking_level || "auto"))}</small></div></article>`).join("")}</div></section>
    ${item.audio_url ? `<section class="podcast-audio-player"><header><div><span>${l("اپیزود آماده", "Episode ready")}</span><h3>${escapeHtml(item.title)}</h3></div><strong>${faNumber(Math.floor(duration / 60))}:${String(Math.round(duration % 60)).padStart(2, "0")}</strong></header><audio controls preload="metadata" src="${escapeHtml(item.audio_url)}"></audio><div><a class="primary-button" href="${escapeHtml(item.download_url)}" download><svg><use href="#i-download"></use></svg>${l("دانلود صوت", "Download audio")}</a>${item.transcript_url ? `<a class="secondary-button" href="${escapeHtml(item.transcript_url)}" download>${l("دانلود رونوشت", "Download transcript")}</a>` : ""}</div></section>` : ""}
    ${reviewable && script.length && !item.audio_url && ["script_ready", "failed"].includes(item.status) ? `<section class="podcast-script-editor"><header><div><span>${l("پیش‌نویس قابل ویرایش", "Editable dialogue")}</span><h3>${l("متن را پیش از ضبط بازبینی کنید", "Review every line before recording")}</h3></div><button class="secondary-button" type="button" data-podcast-add-turn>${l("افزودن نوبت", "Add turn")}</button></header><div data-podcast-turn-list>${script.map((turn, index) => podcastScriptTurnMarkup(turn, index, participants)).join("")}</div></section>` : ""}
    ${plan.length && !item.audio_url ? `<section class="podcast-plan"><header><span>${l("طرح گفت‌وگو", "Discussion plan")}</span><h3>${config.production_mode === "autonomous" ? l("مسیرهای پیشنهادی؛ گویندگان آزادانه تعامل می‌کنند", "Source directions; speakers interact freely") : l("هدف هر بخش برای هدایت گفت‌وگو", "Producer objectives for each segment")}</h3></header><div>${plan.map((segment, index) => `<article><span>${faNumber(index + 1)}</span><div><b>${escapeHtml(segment.title)}</b><p>${escapeHtml(segment.objective)}</p><small>${(segment.citations || []).map(escapeHtml).join(" · ")}</small></div></article>`).join("")}</div></section>` : ""}
    ${controls}
    ${podcastProgressTimeline(data.events || [])}
    ${podcastTranscriptMarkup({...data, transcript_url: item.transcript_url}, config)}
  </div>`;
}

function collectPodcastScript(root) {
  return $$("[data-podcast-turn]", root).map(row => ({
    turn_id: row.dataset.turnId,
    participant_id: $('[data-podcast-turn-field="participant_id"]', row).value,
    chapter: $('[data-podcast-turn-field="chapter"]', row).value.trim(),
    text: $('[data-podcast-turn-field="text"]', row).value.trim(),
    citations: $('[data-podcast-turn-field="citations"]', row).value.split(/[,،\s]+/).map(value => value.trim()).filter(Boolean),
  })).filter(turn => turn.text);
}

function replaceStudioArtifact(item, workspaceId, workspaceRevision) {
  const merged = applyStudioMutation(state, workspaceId, workspaceRevision, item);
  if (!merged) return false;
  renderStudioArtifacts();
  renderStudioArtifact(merged);
  syncStudioStreams();
  return true;
}

async function savePodcastScript({quiet = false} = {}) {
  const root = $("[data-podcast-artifact]", $("#studio-dialog-body"));
  if (!root) return null;
  const workspaceId = state.activeWorkspaceId;
  const workspaceRevision = state.workspaceRevision;
  const artifactId = root.dataset.podcastArtifact;
  const turns = collectPodcastScript(root);
  if (!turns.length) throw new Error(l("متن پادکست نمی‌تواند خالی باشد.", "The podcast script cannot be empty."));
  const item = await api(`/api/workspaces/${encodeURIComponent(workspaceId)}/studio/artifacts/${encodeURIComponent(artifactId)}/script`, {method: "PUT", body: {turns}});
  if (!replaceStudioArtifact(item, workspaceId, workspaceRevision)) return null;
  if (!quiet) toast(l("پیش‌نویس ذخیره شد.", "Draft saved."));
  return item;
}

async function generatePodcastAudio() {
  const root = $("[data-podcast-artifact]", $("#studio-dialog-body"));
  if (!root) return;
  const workspaceId = state.activeWorkspaceId;
  const workspaceRevision = state.workspaceRevision;
  const artifactId = root.dataset.podcastArtifact;
  const current = state.studioArtifacts.find(item => item.artifact_id === artifactId);
  try {
    if (
      current?.data?.config?.production_mode === "scripted"
      && current?.data?.script?.length
    ) {
      const saved = await savePodcastScript({quiet: true});
      if (!saved || !isCurrentWorkspaceRequest(state, workspaceId, workspaceRevision)) return;
    }
    const item = await api(`/api/workspaces/${encodeURIComponent(workspaceId)}/studio/artifacts/${encodeURIComponent(artifactId)}/generate-audio`, {method: "POST"});
    if (!replaceStudioArtifact(item, workspaceId, workspaceRevision)) return;
    if (!state.studioTimer) state.studioTimer = setInterval(loadStudioArtifacts, 1800);
    toast(l("تولید صوت در صف قرار گرفت.", "Audio generation was queued."));
  } catch (error) {
    toast(error.message);
  }
}

async function cancelPodcastAudio() {
  const root = $("[data-podcast-artifact]", $("#studio-dialog-body"));
  if (!root) return;
  const workspaceId = state.activeWorkspaceId;
  const workspaceRevision = state.workspaceRevision;
  const artifactId = root.dataset.podcastArtifact;
  try {
    const item = await api(`/api/workspaces/${encodeURIComponent(workspaceId)}/studio/artifacts/${encodeURIComponent(artifactId)}/cancel-audio`, {method: "POST"});
    if (!replaceStudioArtifact(item, workspaceId, workspaceRevision)) return;
    toast(l("درخواست لغو ثبت شد.", "Cancellation requested."));
  } catch (error) {
    toast(error.message);
  }
}

function slideDeckMarkup(item) {
  const data = item.data || {};
  const rendered = item.rendered_data || {};
  const slides = Array.isArray(data.slides) ? data.slides : [];
  const warnings = data.render_warnings || data.warnings || [];
  const credits = data.media_credits || data.credits || [];
  const slideCards = slides.map((slide, index) => {
    const renderedSlide = rendered.slides?.[index] || {};
    const bullets = (slide.bullets || []).map((bullet, bulletIndex) => `<li>${studioMarkdown(bullet, renderedSlide.bullets?.[bulletIndex])}</li>`).join("");
    const metrics = (slide.metrics || []).map(metric => `<span><b>${escapeHtml(metric.value)}</b><small>${escapeHtml(metric.label)}</small></span>`).join("");
    const chartValues = slide.chart?.values?.length
      ? slide.chart.values
      : (slide.chart?.series?.[0]?.values || []);
    const chart = chartValues.length
      ? `<div class="deck-mini-chart">${chartValues.map((value, valueIndex) => {
        const maximum = Math.max(...chartValues.map(Number), 1);
        return `<span style="--bar:${Math.max(8, Math.round((Number(value) / maximum) * 100))}%"><i></i><small>${escapeHtml(slide.chart.labels?.[valueIndex] || "")}</small></span>`;
      }).join("")}</div>`
      : "";
    return `<article class="deck-slide-preview layout-${escapeHtml(slide.layout || "key_points")}" dir="auto">
      <header><span>${String(index + 1).padStart(2, "0")}</span><em>${escapeHtml((slide.layout || "key_points").replaceAll("_", " "))}</em></header>
      <div class="deck-slide-content"><h3>${studioMarkdown(slide.title, renderedSlide.title)}</h3>${slide.subtitle ? `<p class="deck-subtitle">${studioMarkdown(slide.subtitle, renderedSlide.subtitle)}</p>` : ""}${slide.body ? studioMarkdown(slide.body, renderedSlide.body, "block") : ""}${slide.quote ? `<blockquote>${studioMarkdown(slide.quote, renderedSlide.quote)}</blockquote>` : ""}${bullets ? `<ul>${bullets}</ul>` : ""}${metrics ? `<div class="deck-metrics">${metrics}</div>` : ""}${chart}</div>
      <footer>${slide.image_query ? `<span><svg><use href="#i-image"></use></svg>${escapeHtml(slide.image_query)}</span>` : "<span></span>"}${studioCitationMarkup(slide.citations)}</footer>
      ${slide.speaker_notes ? `<details><summary>${l("یادداشت ارائه", "Speaker notes")}</summary>${studioMarkdown(slide.speaker_notes, renderedSlide.speaker_notes, "block")}</details>` : ""}
    </article>`;
  }).join("");
  return `<div class="slide-deck-studio">
    <section class="visual-artifact-hero"><span class="visual-artifact-icon"><svg><use href="#i-presentation"></use></svg></span><div><span>${l("پاورپوینت قابل ویرایش", "Editable PowerPoint")}</span><h3>${studioMarkdown(data.title || item.title, rendered.title)}</h3>${data.subtitle ? `<p>${studioMarkdown(data.subtitle, rendered.subtitle)}</p>` : ""}<div><b>${faNumber(slides.length)} ${l("اسلاید", "slides")}</b><b>${escapeHtml(item.options?.aspect_ratio || "wide")}</b><b>${escapeHtml(item.options?.design_style || "auto")}</b></div></div>${item.presentation_url ? `<a class="primary-button" href="${escapeHtml(item.presentation_url)}" download><svg><use href="#i-download"></use></svg>${l("دانلود پاورپوینت", "Download PowerPoint")}</a>` : ""}</section>
    ${data.narrative ? `<section class="deck-narrative"><b>${l("روایت ارائه", "Presentation narrative")}</b>${studioMarkdown(data.narrative, rendered.narrative, "block")}</section>` : ""}
    <section class="deck-preview-grid">${slideCards}</section>
    ${item.manifest_url ? `<p><a class="secondary-button" href="${escapeHtml(item.manifest_url)}" target="_blank" rel="noopener">${l("گزارش فنی رندر", "Render manifest")}</a></p>` : ""}
    ${warnings.length ? `<div class="warning-list">${warnings.map(warning => `<p dir="auto">${escapeHtml(warning)}</p>`).join("")}</div>` : ""}
    ${credits.length ? `<details class="visual-credits"><summary>${l("اعتبار رسانه‌ها", "Media credits")}</summary><ul>${credits.map(credit => `<li>${escapeHtml(typeof credit === "string" ? credit : credit.creator || credit.credit || credit.source_url || JSON.stringify(credit))}</li>`).join("")}</ul></details>` : ""}
  </div>`;
}

function videoOverviewMarkup(item) {
  const data = item.data || {};
  const rendered = item.rendered_data || {};
  const scenes = Array.isArray(data.scenes) ? data.scenes : [];
  const warnings = data.render_warnings || data.warnings || [];
  const credits = data.media_credits || data.credits || [];
  const totalSeconds = scenes.reduce((total, scene) => total + Number(scene.duration_seconds || 0), 0);
  const scenesMarkup = scenes.map((scene, index) => {
    const renderedScene = rendered.scenes?.[index] || {};
    return `<article class="video-scene-card" dir="auto"><span class="video-scene-index">${String(index + 1).padStart(2, "0")}</span><div class="video-scene-copy"><small>${escapeHtml(scene.kicker || l("صحنه", "Scene"))} · ${faNumber(Math.round(scene.duration_seconds || 0))}s</small><h4>${studioMarkdown(scene.title, renderedScene.title)}</h4>${scene.on_screen_text ? studioMarkdown(scene.on_screen_text, renderedScene.on_screen_text, "block") : ""}<details><summary>${l("متن روایت", "Narration script")}</summary>${studioMarkdown(scene.narration, renderedScene.narration, "block")}</details><footer>${scene.media_query ? `<span><svg><use href="#i-search"></use></svg>${escapeHtml(scene.media_query)}</span>` : "<span></span>"}${studioCitationMarkup(scene.citations)}</footer></div></article>`;
  }).join("");
  return `<div class="video-overview-studio">
    <section class="visual-artifact-hero video"><span class="visual-artifact-icon"><svg><use href="#i-play"></use></svg></span><div><span>${l("مرور ویدیویی مستند", "Source-grounded video overview")}</span><h3>${studioMarkdown(data.title || item.title, rendered.title)}</h3>${data.summary ? `<p>${studioMarkdown(data.summary, rendered.summary)}</p>` : ""}<div><b>${faNumber(scenes.length)} ${l("صحنه", "scenes")}</b><b>${faNumber(Math.round(totalSeconds))} ${l("ثانیه", "seconds")}</b><b>${escapeHtml(item.options?.resolution || "1080p")}</b></div></div></section>
    ${item.video_url ? `<section class="video-player-shell"><video controls preload="metadata" src="${escapeHtml(item.video_url)}"></video><div><a class="primary-button" href="${escapeHtml(item.download_url)}" download><svg><use href="#i-download"></use></svg>${l("دانلود ویدیو", "Download video")}</a>${item.captions_url ? `<a class="secondary-button" href="${escapeHtml(item.captions_url)}" download>${l("دانلود زیرنویس", "Download captions")}</a>` : ""}${item.timeline_url ? `<a class="secondary-button" href="${escapeHtml(item.timeline_url)}" download>${l("دریافت خط زمانی", "Download timeline")}</a>` : ""}</div></section>` : ""}
    <section class="video-scene-list"><header><div><span class="eyebrow">Production timeline</span><h3>${l("صحنه‌ها و متن روایت", "Scenes and narration")}</h3></div></header>${scenesMarkup}</section>
    ${item.manifest_url ? `<p><a class="secondary-button" href="${escapeHtml(item.manifest_url)}" target="_blank" rel="noopener">${l("گزارش فنی رندر", "Render manifest")}</a></p>` : ""}
    ${warnings.length ? `<div class="warning-list">${warnings.map(warning => `<p dir="auto">${escapeHtml(warning)}</p>`).join("")}</div>` : ""}
    ${credits.length ? `<details class="visual-credits"><summary>${l("اعتبار رسانه‌ها", "Media credits")}</summary><ul>${credits.map(credit => `<li>${escapeHtml(typeof credit === "string" ? credit : credit.creator || credit.credit || credit.source_url || JSON.stringify(credit))}</li>`).join("")}</ul></details>` : ""}
  </div>`;
}

function structuredStudioMarkup(item) {
  const data = item.data || {};
  const rendered = item.rendered_data || {};
  if (item.artifact_type === "audio_overview") return podcastMarkup(item);
  if (item.artifact_type === "slide_deck" && Array.isArray(data.slides) && data.slides.length) return slideDeckMarkup(item);
  if (item.artifact_type === "video_overview" && Array.isArray(data.scenes) && data.scenes.length) return videoOverviewMarkup(item);
  if (item.artifact_type === "quiz" && Array.isArray(data.questions) && data.questions.length) return quizMarkup(data, rendered);
  if (item.artifact_type === "chart" && Array.isArray(data.labels) && data.labels.length && Array.isArray(data.datasets) && data.datasets.length) return chartMarkup(data, rendered);
  if (item.artifact_type === "timeline" && Array.isArray(data.events) && data.events.length) return timelineMarkup(data, rendered);
  if (item.artifact_type === "concept_map" && Array.isArray(data.nodes) && data.nodes.length) return conceptMapMarkup(data, rendered);
  if (item.artifact_type === "flashcards" && Array.isArray(data.cards) && data.cards.length) return flashcardsMarkup(data, rendered);
  if (item.artifact_type === "infographic" && Array.isArray(data.sections) && data.sections.length) return infographicMarkup(data, rendered);
  if (item.artifact_type === "data_table" && Array.isArray(data.columns) && data.columns.length && Array.isArray(data.rows) && data.rows.length) return dataTableMarkup(data, rendered);
  return "";
}

function studioEventHistoryMarkup(events = []) {
  if (!events.length) return "";
  return `<details class="studio-event-history"><summary>${l("تاریخچهٔ تولید", "Production history")} · ${faNumber(events.length)} ${l("رویداد", "events")}</summary><ol>${events.map(event => `<li><span>${escapeHtml(event.message || event.stage || "")}</span><small>${escapeHtml(event.timestamp || "")}</small></li>`).join("")}</ol></details>`;
}

function renderStudioArtifactFull(item) {
  $("#studio-dialog-title").textContent = item.title || studioTypeLabel(item.artifact_type);
  $("#studio-dialog-meta").innerHTML = `<span>${studioTypeLabel(item.artifact_type)}</span><span>${t("studio.sources", {count: faNumber(item.document_ids?.length || 0)})}</span>${item.model ? `<span>${escapeHtml(item.model)}</span>` : ""}`;
  const structured = structuredStudioMarkup(item);
  if (studioArtifactIsRunning(item)) {
    $("#studio-dialog-body").innerHTML = `<div class="studio-running"><i class="status-spinner"></i><strong>${faNumber(item.progress || 0)}%</strong><p>${escapeHtml(t("studio.generating"))}</p><button class="danger-button" type="button" data-studio-cancel="${escapeHtml(item.artifact_id)}"><svg><use href="#i-close"></use></svg><span>${l("لغو تولید", "Cancel generation")}</span></button></div>`;
  } else if (item.status === "cancelled") {
    $("#studio-dialog-body").innerHTML = `<div class="studio-empty"><p>${l("این درخواست لغو شده است.", "This request was cancelled.")}</p></div>`;
  } else if (item.status === "failed" && item.artifact_type !== "audio_overview") {
    $("#studio-dialog-body").innerHTML = `<div class="warning-list"><p>${escapeHtml(item.error || t("studio.failed"))}</p></div>`;
  } else if (structured) {
    $("#studio-dialog-body").innerHTML = structured;
  } else if (item.html) {
    $("#studio-dialog-body").innerHTML = `<article class="studio-markdown-output" dir="auto">${item.html}</article>`;
  } else {
    $("#studio-dialog-body").innerHTML = `<div class="studio-empty"><p>${l("این خروجی محتوای قابل نمایش ندارد.", "This output has no renderable content.")}</p></div>`;
  }
  if (item.artifact_type !== "audio_overview") {
    $("#studio-dialog-body").insertAdjacentHTML("beforeend", studioEventHistoryMarkup(item.live_events || []));
  }
  $("#studio-dialog-download").href = item.download_url || "#";
  $("#studio-dialog-download").classList.toggle("hidden", item.status !== "ready" || item.artifact_type === "audio_overview");
  updateFlashcardDeck($('[data-flashcard-deck]', $("#studio-dialog-body")), 0);
  const firstMindMapNode = $('[data-mind-map-node]', $("#studio-dialog-body"));
  if (firstMindMapNode) {
    selectMindMapNode(firstMindMapNode);
    initializeMindMapViewport(firstMindMapNode.closest(".mind-map-workbench"));
  }
}

function renderStudioArtifact(item) {
  if (item.status === "interrupted") {
    $("#studio-dialog-title").textContent = item.title || studioTypeLabel(item.artifact_type);
    $("#studio-dialog-body").innerHTML = `<div class="studio-empty"><p>${l("برنامه هنگام اجرای این مرحله متوقف شد؛ می‌توانید دوباره تلاش کنید.", "The app stopped during this step; the job can be retried safely.")}</p><button class="primary-button" type="button" data-studio-retry="${escapeHtml(item.artifact_id)}">${l("تلاش دوباره", "Retry")}</button></div>`;
    $("#studio-dialog-download").classList.add("hidden");
    return;
  }
  if (!studioArtifactIsRunning(item)) {
    renderStudioArtifactFull(item);
    return;
  }
  $("#studio-dialog-title").textContent = item.title || studioTypeLabel(item.artifact_type);
  const meta = $("#studio-dialog-meta");
  if (!meta.dataset.liveFields) {
    meta.replaceChildren();
    for (const field of ["type", "sources", "model"]) {
      const span = document.createElement("span");
      span.dataset.studioMeta = field;
      meta.append(span);
    }
    meta.dataset.liveFields = "true";
  }
  $("[data-studio-meta='type']", meta).textContent = studioTypeLabel(item.artifact_type);
  $("[data-studio-meta='sources']", meta).textContent = t("studio.sources", {count: faNumber(item.document_ids?.length || item.document_count || 0)});
  $("[data-studio-meta='model']", meta).textContent = item.model || "";
  const body = $("#studio-dialog-body");
  let running = $("[data-studio-running]", body);
  if (!running) {
    body.innerHTML = `<div class="studio-running" data-studio-running><i class="status-spinner"></i><strong data-studio-live-progress></strong><p data-studio-live-stage></p><details data-studio-events open><summary>${l("رویدادهای زنده", "Live events")}</summary><ol></ol></details><button class="danger-button" type="button" data-studio-cancel><svg><use href="#i-close"></use></svg><span>${l("لغو تولید", "Cancel generation")}</span></button></div>`;
    running = $("[data-studio-running]", body);
  }
  $("[data-studio-live-progress]", running).textContent = `${faNumber(item.progress || 0)}%`;
  $("[data-studio-live-stage]", running).textContent = item.stage || t("studio.generating");
  $("[data-studio-cancel]", running).dataset.studioCancel = item.artifact_id;
  const events = $("[data-studio-events] ol", running);
  (item.live_events || []).forEach(event => {
    let row = $(`[data-event-sequence="${event.sequence}"]`, events);
    if (!row) {
      row = document.createElement("li");
      row.dataset.eventSequence = event.sequence;
      events.append(row);
    }
    row.textContent = event.message;
  });
  $("#studio-dialog-download").classList.add("hidden");
}

async function openStudioArtifact(artifactId) {
  const item = state.studioArtifacts.find(artifact => artifact.artifact_id === artifactId);
  if (!item) return;
  state.activeStudioArtifactId = artifactId;
  renderStudioArtifact(item);
  if (!$("#studio-dialog").open) $("#studio-dialog").showModal();
  await loadStudioArtifactDetail(artifactId).catch(() => markStudioReconnecting(state));
}

async function cancelStudioArtifact(artifactId) {
  const item = state.studioArtifacts.find(artifact => artifact.artifact_id === artifactId);
  if (!item) return;
  const workspaceId = state.activeWorkspaceId;
  const workspaceRevision = state.workspaceRevision;
  try {
    const cancelled = await api(
      `/api/workspaces/${encodeURIComponent(item.workspace_id || workspaceId)}/studio/artifacts/${encodeURIComponent(artifactId)}/cancel`,
      {method: "POST", body: {}},
    );
    if (!replaceStudioArtifact(cancelled, workspaceId, workspaceRevision)) return;
    toast(l("تولید لغو شد.", "Generation cancelled."));
  } catch (error) {
    toast(error.message);
  }
}

async function retryStudioArtifact(artifactId) {
  const item = state.studioArtifacts.find(artifact => artifact.artifact_id === artifactId);
  if (!item) return;
  const workspaceId = state.activeWorkspaceId;
  const workspaceRevision = state.workspaceRevision;
  const queued = await api(
    `/api/workspaces/${encodeURIComponent(item.workspace_id || workspaceId)}/studio/artifacts/${encodeURIComponent(artifactId)}/retry`,
    {method: "POST", body: {}},
  );
  if (!replaceStudioArtifact(queued, workspaceId, workspaceRevision)) return;
  await loadStudioArtifacts();
}

function renderDocuments() {
  if (!state.documents.length) {
    $("#documents-list").innerHTML = `<div class="empty-state"><p>${state.locale === "fa" ? "هنوز منبعی در این فضای کاری اضافه نشده است." : "No sources have been added to this workspace."}</p></div>`;
    return;
  }
  const labels = Object.fromEntries(["uploaded", "processing", "ready", "failed"].map(status => [status, t(`status.${status}`)]));
  const stageLabels = state.locale === "fa"
    ? {queued: "در صف", analysis: "بررسی منبع", ocr: "خواندن متن تصویر", vlm: "تحلیل محتوای بصری", transcription: "رونویسی صوت", page: "تکمیل صفحه", chunking: "آماده‌سازی قطعه‌ها", embedding: "ساخت پایگاه برداری", completed: "تکمیل‌شده", failed: "ناموفق"}
    : {queued: "Queued", analysis: "Inspecting source", ocr: "Reading image text", vlm: "Understanding visuals", transcription: "Transcribing audio", page: "Completing page", chunking: "Creating chunks", embedding: "Building vector index", completed: "Completed", failed: "Failed"};
  const eventMessage = event => {
    const localized = `${stageLabels[event.stage] || event.stage}${event.page_number ? ` · صفحه ${faNumber(event.page_number)}` : ""}${event.status === "failed" ? " · ناموفق" : event.status === "skipped" ? " · رد شد" : event.status === "completed" ? " · کامل شد" : ""}`;
    return state.locale === "en" || !event.message ? event.message || localized : `${localized} — ${event.message}`;
  };
  const sourceLabels = state.locale === "fa" ? {pdf: ["PDF", "▤"], text: ["متن", "T"], image: ["تصویر", "◫"], audio: ["صوت", "◉"]} : {pdf: ["PDF", "▤"], text: ["Text", "T"], image: ["Image", "◫"], audio: ["Audio", "◉"]};
  $("#documents-list").innerHTML = state.documents.map(item => {
    const report = item.extraction_report || {};
    const progress = item.progress || {};
    const events = progress.events || [];
    const recentEvents = events.slice(-6).reverse();
    const previewEvent = [...events].reverse().find(event => event.text_preview || event.visual_preview);
    const source = sourceLabels[item.source_type] || [item.source_type || l("فایل", "File"), "◆"];
    const unitLabel = state.locale === "fa" ? (item.source_type === "audio" ? "رونویسی" : item.source_type === "text" ? "بخش" : "صفحه") : (item.source_type === "audio" ? "transcript" : item.source_type === "text" ? "section" : "page");
    const livePanel = item.status === "processing" ? `<section class="processing-live">
      <div class="processing-head"><strong><span class="status-spinner"></span> ${escapeHtml(stageLabels[progress.stage] || progress.stage || l("آماده‌سازی", "Preparing"))}</strong><span>${faNumber(progress.percent)}${l("٪", "%")}${progress.total_units ? ` · ${faNumber(progress.processed_units)} ${l("از", "of")} ${faNumber(progress.total_units)}` : ""}</span></div>
      <div class="document-progress"><i style="width:${Math.max(2, Number(progress.percent || 0))}%"></i></div>
      <div class="processing-stats"><span>${l("متن", "Text")}: ${faNumber(progress.extraction_counts?.text)}</span><span>OCR: ${faNumber(progress.extraction_counts?.ocr)}</span><span>${l("ترکیبی", "Hybrid")}: ${faNumber(progress.extraction_counts?.hybrid)}</span><span>VLM: ${faNumber(progress.extraction_counts?.vlm)}</span><span>${l("درخواست OCR", "OCR calls")}: ${faNumber(progress.ocr_requests)}</span><span>${l("درخواست VLM", "VLM calls")}: ${faNumber(progress.vlm_requests)}</span><span>${l("رونویسی", "Transcriptions")}: ${faNumber(progress.transcription_requests)}</span><span>${l("جابه‌جایی سرویس", "Provider fallbacks")}: ${faNumber(progress.provider_failovers)}</span><span>${l("انتظار زمان‌بندی", "Scheduled wait")}: ${faNumber(Math.round((progress.rate_wait_ms || 0) / 1000))}${l(" ثانیه", "s")}</span></div>
      ${previewEvent ? `<p class="live-preview">${escapeHtml(previewEvent.text_preview || previewEvent.visual_preview)}</p>` : ""}
      <ol class="processing-events">${recentEvents.map(event => `<li class="processing-event ${escapeHtml(event.status)}"><i></i><span>${escapeHtml(eventMessage(event))}</span><small>${event.page_number ? `${unitLabel} ${faNumber(event.page_number)}` : ""}</small>${(event.requests || []).length ? `<div class="provider-attempts">${event.requests.map(request => `<span class="request-chip ${escapeHtml(request.status)}">${l("درخواست", "Call")} ${faNumber(request.slot)} · ${escapeHtml(request.status === "completed" ? l("موفق", "completed") : l("تلاش مجدد", "retry"))} · ${faNumber(request.duration_ms)}ms${request.wait_ms ? ` · ${l("انتظار", "wait")} ${faNumber(request.wait_ms)}ms` : ""}</span>`).join("")}</div>` : ""}</li>`).join("")}</ol>
    </section>` : "";
    const diagnostics = `<details class="document-diagnostics"><summary>${l("جزئیات کامل پردازش", "Complete processing details")} · ${faNumber(events.length)} ${l("رویداد", "events")} · ${faNumber((item.warnings || []).length)} ${l("هشدار", "warnings")}</summary>
      ${(item.errors || []).length ? `<section class="diagnostic-group error"><h4>${l("خطاها", "Errors")}</h4><ol>${item.errors.map(error => `<li dir="auto">${escapeHtml(error)}</li>`).join("")}</ol></section>` : ""}
      ${(item.warnings || []).length ? `<section class="diagnostic-group warning"><h4>${l("هشدارها", "Warnings")}</h4><ol>${item.warnings.map(warning => `<li dir="auto">${escapeHtml(warning)}</li>`).join("")}</ol></section>` : ""}
      ${events.length ? `<ol class="processing-event-details">${events.map(event => `<li><header><strong>${escapeHtml(eventMessage(event))}</strong><time>${escapeHtml(event.timestamp || "")}</time></header>${event.text_preview ? `<p dir="auto">${escapeHtml(event.text_preview)}</p>` : ""}${event.visual_preview ? `<p dir="auto">${escapeHtml(event.visual_preview)}</p>` : ""}${(event.requests || []).length ? `<pre>${escapeHtml(JSON.stringify(event.requests, null, 2))}</pre>` : ""}</li>`).join("")}</ol>` : `<p>${l("هنوز رویدادی ثبت نشده است.", "No processing events have been recorded yet.")}</p>`}
    </details>`;
    return `<article class="document-card ${item.status}" data-document-id="${escapeHtml(item.document_id)}">
      <header><div class="document-title"><span class="source-icon">${source[1]}</span><div><h3>${escapeHtml(item.title || item.original_filename)}</h3><small>${escapeHtml(source[0])} · ${escapeHtml(item.original_filename)}</small></div></div><span class="status-badge ${item.status}">${item.status === "processing" ? `<i class="status-spinner"></i>` : ""}${labels[item.status] || item.status}</span></header>
      <div class="document-meta"><span>${faNumber(item.page_count)} ${unitLabel}</span><span>${faNumber(item.chunk_count)} ${t("metrics.chunks")}</span><span>${escapeHtml((item.language_hints || []).join(", ") || t("status.unknownLanguage"))}</span></div>
      ${livePanel}
      <div class="extraction-metrics" aria-label="${l("گزارش روش استخراج", "Extraction method report")}">
        <span class="extraction-metric text"><small>${l("متن", "Text")}</small><b>${faNumber(report.text_pages)}</b></span>
        <span class="extraction-metric ocr"><small>OCR</small><b>${faNumber(report.ocr_pages)}</b></span>
        <span class="extraction-metric hybrid"><small>${l("ترکیبی", "Hybrid")}</small><b>${faNumber(report.hybrid_pages)}</b></span>
        <span class="extraction-metric vlm"><small>VLM</small><b>${faNumber(report.vlm_pages)}</b></span>
        <span class="extraction-metric failed"><small>${l("خطا", "Failed")}</small><b>${faNumber(report.failed_pages)}</b></span>
      </div>
      ${diagnostics}
      <div class="document-actions"><a class="secondary-button" href="${escapeHtml(item.source_url)}" target="_blank" rel="noopener">${item.origin_url ? l("متن دریافت‌شده", "Extracted text") : l("بازکردن منبع", "Open source")}</a>${item.origin_url ? `<a class="secondary-button" href="${escapeHtml(item.origin_url)}" target="_blank" rel="noopener"><svg><use href="#i-external"></use></svg>${l("صفحهٔ وب", "Web page")}</a>` : ""}${item.status !== "processing" ? `<button type="button" class="secondary-button" data-process-document="${item.document_id}">${state.locale === "fa" ? "پردازش دوباره" : "Process again"}</button><button type="button" class="danger-button" data-delete-document="${item.document_id}">${l("حذف منبع", "Delete source")}</button>` : ""}</div>
    </article>`;
  }).join("");
}

async function deleteDocument(documentId) {
  const document = state.documents.find(item => item.document_id === documentId);
  if (!document || !confirm(l(`منبع «${document.title || document.original_filename}» و همهٔ داده‌های استخراج‌شدهٔ آن حذف شود؟`, `Delete “${document.title || document.original_filename}” and all extracted data?`))) return;
  await api(`/api/workspaces/${encodeURIComponent(state.activeWorkspaceId)}/documents/${encodeURIComponent(documentId)}`, {method: "DELETE"});
  state.selectedDocumentIds.delete(documentId);
  await Promise.all([loadDocuments(), loadWorkspaceVector(), loadStudioArtifacts()]);
  toast(l("منبع حذف شد.", "Source deleted."));
}

function formatFileSize(bytes) {
  if (bytes < 1024) return `${faNumber(bytes)} ${l("بایت", "bytes")}`;
  if (bytes < 1024 ** 2) return `${faNumber((bytes / 1024).toFixed(1))} ${l("کیلوبایت", "KB")}`;
  return `${faNumber((bytes / 1024 ** 2).toFixed(1))} ${l("مگابایت", "MB")}`;
}

function renderSelectedFiles(files) {
  const items = [...(files || [])];
  $("#selected-files").innerHTML = items.map(file => {
    const extension = (file.name.split(".").pop() || "FILE").slice(0, 5).toUpperCase();
    return `<div class="selected-file"><span>${escapeHtml(extension)}</span><div><strong>${escapeHtml(file.name)}</strong><small>${escapeHtml(file.type || l("نوع فایل ناشناخته", "Unknown file type"))}</small></div><small>${formatFileSize(file.size)}</small></div>`;
  }).join("");
  if (items.length) {
    const total = items.reduce((sum, file) => sum + file.size, 0);
    $("#upload-status").textContent = `${faNumber(items.length)} ${l("فایل", "files")} · ${formatFileSize(total)}`;
  } else {
    $("#upload-status").textContent = l("حداکثر حجم هر فایل: ۱۰۰ مگابایت", "Maximum size per file: 100 MB");
  }
}

function setUploadPending(pending) {
  const button = $("#upload-form button[type=submit]");
  button.disabled = pending;
  button.innerHTML = pending
    ? `<span class="status-spinner"></span><span>${l("در حال بارگذاری…", "Uploading…")}</span>`
    : `<svg><use href="#i-upload"></use></svg><span>${l("بارگذاری و پردازش", "Upload and process")}</span>`;
  $("#upload-form").setAttribute("aria-busy", String(pending));
}

async function uploadDocuments(files) {
  if (!files.length) {
    toast(l("ابتدا یک یا چند فایل انتخاب کنید.", "Choose one or more files first."));
    return;
  }
  const form = new FormData();
  [...files].forEach(file => form.append("files", file));
  $("#upload-status").textContent = `${l("بارگذاری", "Uploading")} ${faNumber(files.length)} ${l("فایل", "files")}…`;
  setUploadPending(true);
  try {
    const result = await api(`/api/workspaces/${encodeURIComponent(state.activeWorkspaceId)}/documents`, {method: "POST", body: form});
    $("#upload-status").textContent = l(
      `${faNumber(result.documents.length)} فایل پذیرفته شد${result.rejected.length ? `؛ ${faNumber(result.rejected.length)} فایل رد شد` : ""}.`,
      `${faNumber(result.documents.length)} files accepted${result.rejected.length ? `; ${faNumber(result.rejected.length)} rejected` : ""}.`,
    );
    $("#pdf-input").value = "";
    $("#selected-files").innerHTML = "";
    await loadDocuments();
  } catch (error) {
    $("#upload-status").textContent = error.message;
  } finally {
    setUploadPending(false);
  }
}

async function processDocument(documentId) {
  await api(`/api/workspaces/${encodeURIComponent(state.activeWorkspaceId)}/documents/${encodeURIComponent(documentId)}/process`, {method: "POST", body: {}});
  toast(l("پردازش سند آغاز شد.", "Document processing started."));
  await loadDocuments();
}

async function addPastedText(title, text) {
  const button = $("#paste-text-form button[type=submit]");
  button.disabled = true;
  button.textContent = l("در حال افزودن…", "Adding…");
  try {
    await api(`/api/workspaces/${encodeURIComponent(state.activeWorkspaceId)}/documents/text`, {method: "POST", body: {title, text}});
    $("#paste-content").value = "";
    toast(l("متن به فضای کاری اضافه شد.", "Text was added to the workspace."));
    await loadDocuments();
  } catch (error) {
    toast(error.message);
  } finally {
    button.disabled = false;
    button.textContent = l("افزودن و پردازش", "Add and process");
  }
}

function splitDomainList(value) {
  return String(value || "").split(/[،,\s]+/).map(item => item.trim()).filter(Boolean);
}

function syncWebSourceMode() {
  const custom = $("#web-source-mode").value === "custom";
  $$('[data-web-source-advanced]', $("#web-source-search-form")).forEach(field => {
    field.classList.toggle("hidden", !custom);
  });
}

function openWebSourceSearch() {
  state.webSearchSession = null;
  $("#web-source-search-form").classList.remove("hidden");
  $("#web-source-selection").classList.add("hidden");
  $("#web-source-search-status").textContent = "";
  syncWebSourceMode();
  $("#web-source-dialog").showModal();
}

function webSourceResultMarkup(source) {
  let host = "";
  try { host = new URL(source.url).hostname.replace(/^www\./, ""); } catch { host = source.url; }
  const metadata = [source.author, source.published_at, host].filter(Boolean).map(escapeHtml).join(" · ");
  const highlights = (source.highlights || []).slice(0, 3).map(item => `<li dir="auto">${escapeHtml(item)}</li>`).join("");
  const badges = (source.providers || [source.provider]).map(provider => `<span>${escapeHtml(provider)}</span>`).join("");
  return `<label class="web-source-result">
    <input type="checkbox" data-web-source-id value="${escapeHtml(source.source_id)}" checked>
    <span class="web-source-check"><svg><use href="#i-check"></use></svg></span>
    <article><header><div class="web-source-badges">${badges}</div><a href="${escapeHtml(source.url)}" target="_blank" rel="noopener" aria-label="${escapeHtml(l("بازکردن منبع", "Open source"))}"><svg><use href="#i-external"></use></svg></a></header><h4 dir="auto">${escapeHtml(source.title || l("بدون عنوان", "Untitled"))}</h4>${metadata ? `<small dir="auto">${metadata}</small>` : ""}${source.summary ? `<p dir="auto">${escapeHtml(source.summary)}</p>` : ""}${highlights ? `<ul>${highlights}</ul>` : ""}<footer><span>${l("امتیاز ارتباط", "Relevance")} ${faNumber(Number(source.score || 0).toFixed(2))}</span><span>${source.content_available ? l("متن کامل آماده", "Full text available") : l("استخراج پس از انتخاب", "Extract after selection")}</span></footer></article>
  </label>`;
}

function renderWebSourceResults(session) {
  state.webSearchSession = session;
  $("#web-source-search-form").classList.add("hidden");
  $("#web-source-selection").classList.remove("hidden");
  $("#web-source-result-title").textContent = l("منابع پیشنهادی", "Suggested sources");
  $("#web-source-result-summary").textContent = l(
    `${faNumber(session.results?.length || 0)} نتیجه از موتورهای انتخاب‌شده پیدا شد. همه به‌صورت پیش‌فرض انتخاب شده‌اند.`,
    `${faNumber(session.results?.length || 0)} results were found. All are selected by default.`,
  );
  $("#web-source-results").innerHTML = (session.results || []).map(webSourceResultMarkup).join("") || `<div class="empty-state"><p>${l("منبعی پیدا نشد؛ عبارت یا فیلترها را تغییر دهید.", "No sources found. Try a different query or filters.")}</p></div>`;
  const errors = Object.entries(session.provider_errors || {});
  $("#web-source-provider-warnings").innerHTML = errors.map(([provider, detail]) => `<p dir="auto"><b>${escapeHtml(provider)}</b> · ${escapeHtml(detail)}</p>`).join("");
  $("#web-source-provider-warnings").classList.toggle("hidden", !errors.length);
  $("#web-source-import").disabled = !(session.results || []).length;
  $("#web-source-import-status").textContent = "";
}

async function searchWebSources(event) {
  event.preventDefault();
  const providers = [];
  if ($("#web-source-exa").checked) providers.push("exa");
  if ($("#web-source-tavily").checked) providers.push("tavily");
  if (!providers.length) {
    $("#web-source-search-status").textContent = l("حداقل یک موتور جست‌وجو را انتخاب کنید.", "Select at least one search provider.");
    return;
  }
  const button = $("#web-source-search-submit");
  button.disabled = true;
  button.innerHTML = `<span class="status-spinner"></span>${l("در حال جست‌وجو…", "Searching…")}`;
  $("#web-source-search-status").textContent = l("جست‌وجوی چندمسیره و حذف نتایج تکراری…", "Searching providers and merging duplicates…");
  const custom = $("#web-source-mode").value === "custom";
  const body = {
    query: $("#web-source-query").value.trim(),
    max_results: Number($("#web-source-count").value),
    providers,
    exa_search_type: custom ? $("#web-source-exa-type").value : "auto",
    tavily_search_depth: custom ? $("#web-source-tavily-depth").value : "basic",
    topic: custom ? $("#web-source-topic").value : "general",
    category: custom ? $("#web-source-category").value : "auto",
    include_domains: custom ? splitDomainList($("#web-source-domains").value) : [],
    exclude_domains: custom ? splitDomainList($("#web-source-excluded-domains").value) : [],
    freshness: custom ? $("#web-source-freshness").value : "balanced",
  };
  if (custom && $("#web-source-start-date").value) body.start_date = $("#web-source-start-date").value;
  if (custom && $("#web-source-end-date").value) body.end_date = $("#web-source-end-date").value;
  try {
    const session = await api(`/api/workspaces/${encodeURIComponent(state.activeWorkspaceId)}/source-searches`, {method: "POST", body});
    renderWebSourceResults(session);
  } catch (error) {
    $("#web-source-search-status").textContent = error.message;
  } finally {
    button.disabled = false;
    button.innerHTML = `<svg><use href="#i-search"></use></svg>${l("جست‌وجو", "Search")}`;
  }
}

async function importSelectedWebSources() {
  const session = state.webSearchSession;
  if (!session) return;
  const sourceIds = $$('[data-web-source-id]:checked', $("#web-source-results")).map(field => field.value);
  if (!sourceIds.length) {
    $("#web-source-import-status").textContent = l("حداقل یک منبع را انتخاب کنید.", "Select at least one source.");
    return;
  }
  const button = $("#web-source-import");
  button.disabled = true;
  button.innerHTML = `<span class="status-spinner"></span>${l("دریافت متن کامل…", "Extracting full text…")}`;
  $("#web-source-import-status").textContent = l("فقط منابع انتخاب‌شده دریافت می‌شوند.", "Only selected sources are being extracted.");
  try {
    const result = await api(`/api/workspaces/${encodeURIComponent(state.activeWorkspaceId)}/source-searches/${encodeURIComponent(session.search_id)}/import`, {method: "POST", body: {source_ids: sourceIds, process: true}});
    await loadDocuments();
    $("#web-source-dialog").close();
    toast(l(`${faNumber(result.documents?.length || 0)} منبع وارد شد${result.errors?.length ? `؛ ${faNumber(result.errors.length)} خطا در گزارش ثبت شد` : ""}.`, `${faNumber(result.documents?.length || 0)} sources imported${result.errors?.length ? `; ${faNumber(result.errors.length)} errors were logged` : ""}.`));
  } catch (error) {
    $("#web-source-import-status").textContent = error.message;
  } finally {
    button.disabled = false;
    button.innerHTML = `<svg><use href="#i-plus"></use></svg>${l("افزودن منابع انتخاب‌شده", "Add selected sources")}`;
  }
}

async function loadWorkspaceVector() {
  try {
    const data = await api(`/api/workspaces/${encodeURIComponent(state.activeWorkspaceId)}/vector-store`);
    $("#hero-chunk-count").textContent = faNumber(data.chunk_count);
    $("#chunking-strategy").textContent = state.locale === "fa" ? t("vector.strategy") : data.chunking_strategy;
    return data;
  } catch {
    $("#hero-chunk-count").textContent = faNumber(0);
    return null;
  }
}

async function loadVectorStore() {
  const data = await loadWorkspaceVector();
  if (!data) {
    $("#vector-kpis").innerHTML = `<div class="empty-state"><p>${l("پایگاه برداری هنوز ساخته نشده است.", "The vector store has not been built yet.")}</p></div>`;
    return;
  }
  $("#vector-kpis").innerHTML = [
    [l("اسناد", "Documents"), data.document_count, l("در مجموعهٔ فعال", "in the active collection")],
    [l("قطعه‌ها", "Chunks"), data.chunk_count, data.collection_name || l("ساخته نشده", "Not built")],
    ["Embedding", data.embedding_model || "—", `${data.dimensions || 0}d`],
    [l("آخرین بازسازی", "Last rebuild"), data.last_rebuild_at ? relativeTime(data.last_rebuild_at) : "—", l("نسخه‌دار", "Versioned")],
  ].map(([label, value, note]) => `<article class="metric-card"><span>${escapeHtml(label)}</span><strong>${escapeHtml(value)}</strong><small>${escapeHtml(note)}</small></article>`).join("");
  renderDistribution(data.extraction_method_distribution || {});
  const locations = {...(data.top_sections || {}), ...(data.top_pages || {})};
  $("#top-locations").innerHTML = Object.entries(locations).slice(0, 16).map(([name, count]) => `<span>${escapeHtml(name)} · ${faNumber(count)}</span>`).join("") || `<p>${l("هنوز داده‌ای نیست.", "No data yet.")}</p>`;
  renderRebuild(data.rebuild);
}

function renderDistribution(distribution) {
  const maximum = Math.max(1, ...Object.values(distribution));
  $("#extraction-distribution").innerHTML = Object.entries(distribution).map(([name, count]) =>
    `<div class="distribution-row"><span>${escapeHtml(name)}</span><div class="bar"><i style="width:${count / maximum * 100}%"></i></div><b>${faNumber(count)}</b></div>`
  ).join("") || `<p>${l("هنوز قطعه‌ای ثبت نشده است.", "No chunks have been indexed yet.")}</p>`;
}

async function searchChunks(query) {
  $("#chunk-list").innerHTML = `<div class="empty-state"><p>${l("در حال بازیابی…", "Retrieving…")}</p></div>`;
  try {
    const rows = await api(`/api/workspaces/${encodeURIComponent(state.activeWorkspaceId)}/vector-store/chunks?query=${encodeURIComponent(query)}&limit=12`);
    $("#chunk-list").innerHTML = rows.map((item, index) =>
      `<button type="button" class="chunk-card" data-chunk-index="${index}"><header><span>${escapeHtml(item.filename || item.paper_title)}</span><span>${l("ص.", "p.")}${faNumber(item.page_start)} · ${Number(item.similarity).toFixed(3)}</span></header><p>${escapeHtml(String(item.text).replace(/\s+/g, " ").slice(0, 350))}</p></button>`
    ).join("") || `<div class="empty-state"><p>${l("قطعه‌ای پیدا نشد.", "No matching chunks found.")}</p></div>`;
    state.chunkEvidence = rows.map(item => ({
      ...item,
      pdf_url: `/api/workspaces/${state.activeWorkspaceId}/documents/${item.document_id || item.paper_id}/pdf#page=${item.page_start}`,
      page_preview_url: `/api/workspaces/${state.activeWorkspaceId}/documents/${item.document_id || item.paper_id}/pages/${item.page_start}`,
    }));
  } catch (error) {
    $("#chunk-list").innerHTML = `<div class="empty-state"><p>${escapeHtml(error.message)}</p></div>`;
  }
}

function renderRebuild(job = {}) {
  const box = $("#rebuild-box");
  if (!job || job.status === "idle") {
    box.classList.add("hidden");
    return;
  }
  box.classList.remove("hidden");
  const labels = state.locale === "fa" ? {running: "بازسازی در حال انجام است", completed: "بازسازی کامل شد", failed: "بازسازی ناموفق بود"} : {running: "Rebuild in progress", completed: "Rebuild complete", failed: "Rebuild failed"};
  box.innerHTML = `<strong>${labels[job.status] || job.status}</strong>${job.error ? `<p>${escapeHtml(job.error)}</p>` : ""}<div class="progress"><i style="width:${job.progress || 0}%"></i></div>`;
  if (job.status === "running" && !state.rebuildTimer) state.rebuildTimer = setInterval(pollRebuild, 2500);
  if (job.status !== "running" && state.rebuildTimer) {
    clearInterval(state.rebuildTimer);
    state.rebuildTimer = null;
  }
}

async function startRebuild() {
  const job = await api(`/api/workspaces/${encodeURIComponent(state.activeWorkspaceId)}/vector-store/rebuild`, {method: "POST", body: {}});
  renderRebuild(job);
  toast(l("بازسازی نسخهٔ جدید پایگاه آغاز شد.", "A new knowledge-base version is being built."));
}

async function pollRebuild() {
  try {
    const job = await api(`/api/workspaces/${encodeURIComponent(state.activeWorkspaceId)}/vector-store/rebuild`);
    renderRebuild(job);
    if (job.status !== "running") await Promise.all([loadVectorStore(), loadDocuments()]);
  } catch (error) {
    renderRebuild({status: "failed", progress: 0, error: error.message});
  }
}

async function loadTraces(selectLatest = true) {
  state.traces = await api(`/api/traces?workspace_id=${encodeURIComponent(state.activeWorkspaceId)}&limit=60`);
  $("#trace-count").textContent = faNumber(state.traces.length);
  renderActivityOverview();
  $("#trace-list").innerHTML = state.traces.map(trace =>
    `<button type="button" class="trace-item ${trace.id === state.activeTraceId ? "active" : ""}" data-trace-id="${trace.id}"><i class="trace-node ${["ok", "completed", "success"].includes(String(trace.status).toLowerCase()) ? "ok" : ""}"></i><div><header><strong>${trace.kind === "chat" ? l("پرسش", "Chat") : trace.kind === "speech" ? l("صوت", "Speech") : l("تابع", "Tool")}</strong><span class="trace-status">${escapeHtml(trace.status)}</span></header><span dir="auto">${escapeHtml(trace.summary)}</span><small>#${trace.id} · ${trace.duration_ms ?? "…"} ms · ${relativeTime(trace.started_at)}</small></div><svg><use href="#i-arrow"></use></svg></button>`
  ).join("") || `<div class="empty-state"><p>${l("هنوز درخواستی ثبت نشده است.", "No requests have been recorded yet.")}</p></div>`;
  if (selectLatest && state.traces.length) selectTrace(state.traces[0].id);
}

function renderActivityOverview() {
  const target = $("#activity-overview");
  if (!target) return;
  const completed = state.traces.filter(trace => ["ok", "completed", "success"].includes(String(trace.status).toLowerCase())).length;
  const fallback = state.traces.filter(trace => (trace.stages || []).some(stage => stage.failover_used || stage.fallback_used)).length;
  const durations = state.traces.map(trace => Number(trace.duration_ms)).filter(Number.isFinite);
  const average = durations.length ? Math.round(durations.reduce((sum, value) => sum + value, 0) / durations.length) : 0;
  const cards = [
    [l("کل درخواست‌ها", "Total requests"), state.traces.length, l("آخرین ۶۰ رویداد", "Latest 60 events")],
    [l("موفق", "Successful"), completed, state.traces.length ? `${Math.round(completed / state.traces.length * 100)}%` : "0%"],
    [l("میانگین زمان", "Average latency"), `${faNumber(average)} ms`, l("سرتاسری", "End to end")],
    ["Fallback", fallback, l("تغییر مسیر مدل یا provider", "Provider or model switch")],
  ];
  target.innerHTML = cards.map(([label, value, note]) => `<article><span>${escapeHtml(label)}</span><strong>${escapeHtml(value)}</strong><small>${escapeHtml(note)}</small></article>`).join("");
}

function selectTrace(traceId) {
  const trace = state.traces.find(item => item.id === traceId);
  if (!trace) return;
  state.activeTraceId = traceId;
  $$(".trace-item").forEach(item => item.classList.toggle("active", item.dataset.traceId === traceId));
  const evidence = trace.selected_evidence || [];
  const detailMarkup = `<div class="trace-detail-content">
    <p class="eyebrow">Request trace #${trace.id}</p><h2 dir="auto">${escapeHtml(trace.summary)}</h2>
    <div class="trace-summary"><span>${trace.duration_ms ?? "…"} ms</span><span>${escapeHtml(trace.provider || "—")} / ${escapeHtml(trace.model || "—")}</span><span>${faNumber(trace.evidence_count)} ${l("شاهد", "evidence")}</span><span>${faNumber(trace.tools?.length)} ${l("ابزار", "tools")}</span><span>token/cost: ${trace.token_usage || trace.cost_estimate || "N/A"}</span></div>
    <div class="trace-timeline">${(trace.stages || []).map(stage => `<article class="trace-stage"><strong>${escapeHtml(stage.label || stage.name || l("مرحله", "Stage"))}</strong><small>${escapeHtml(stage.service || "")}${stage.failover_used || stage.fallback_used ? " · fallback" : ""}</small>${(stage.requests || []).length ? `<div class="provider-attempts">${stage.requests.map(request => `<span class="request-chip ${escapeHtml(request.status || "")}">${l("درخواست", "Call")} ${faNumber(request.slot)} · ${escapeHtml(request.status || "unknown")} · ${faNumber(request.duration_ms || 0)}ms${request.wait_ms ? ` · ${l("انتظار", "wait")} ${faNumber(request.wait_ms)}ms` : ""}</span>`).join("")}</div>` : ""}${(stage.warnings || []).length ? `<div class="trace-warning">${stage.warnings.map(warning => `<p dir="auto">${escapeHtml(warning)}</p>`).join("")}</div>` : ""}<details class="trace-stage-details"><summary>${l("دادهٔ کامل مرحله", "Complete stage data")}</summary><pre class="code-panel">${escapeHtml(JSON.stringify(stage, null, 2))}</pre></details></article>`).join("")}</div>
    ${evidence.length ? `<h3>${l("شواهد انتخاب‌شده", "Selected evidence")}</h3><pre class="code-panel">${escapeHtml(JSON.stringify(evidence, null, 2))}</pre>` : ""}
    ${trace.error ? `<section class="trace-error-detail"><h3>${l("جزئیات کامل خطا", "Complete error details")}</h3><p dir="auto">${escapeHtml(trace.error_detail?.message || trace.error)}</p>${trace.error_detail?.traceback ? `<details open><summary>${l("ردیابی خطا", "Stack trace")}</summary><pre class="code-panel">${escapeHtml(trace.error_detail.traceback)}</pre></details>` : ""}${trace.error_detail?.causes?.length ? `<details><summary>${l("زنجیرهٔ علت‌ها", "Cause chain")}</summary><pre class="code-panel">${escapeHtml(JSON.stringify(trace.error_detail.causes, null, 2))}</pre></details>` : ""}</section>` : ""}
    <details class="trace-raw-detail"><summary>${l("رکورد کامل درخواست", "Complete request record")}</summary><pre class="code-panel">${escapeHtml(JSON.stringify(trace, null, 2))}</pre></details>
    <button class="secondary-button trace-expand" type="button" data-open-trace-dialog>${l("نمایش تمام‌صفحه", "Open full detail")}</button>
  </div>`;
  $("#trace-detail").innerHTML = detailMarkup;
  $("#activity-detail-dialog-body").innerHTML = detailMarkup;
}

async function loadStatus() {
  try {
    const data = await api("/api/status");
    const ready = data.runtime_available !== false;
    $("#sidebar-health").classList.toggle("ready", ready);
    $("#sidebar-health span:last-child").textContent = ready ? `${data.llm || "LLM"} · ${data.embedding || "embedding"}` : (state.locale === "fa" ? "تنظیم provider لازم است" : "Provider setup required");
    return data;
  } catch (error) {
    $("#sidebar-health").classList.remove("ready");
    $("#sidebar-health span:last-child").textContent = state.locale === "fa" ? "سامانه در دسترس نیست" : "System unavailable";
    return null;
  }
}

async function loadProviderSettings() {
  const container = $("#provider-settings");
  if (!container) return;
  container.setAttribute("aria-busy", "true");
  if (!state.providers.length) container.innerHTML = `<div class="provider-loading"><span class="status-spinner"></span><p>${l("در حال دریافت وضعیت اتصال‌ها…", "Loading provider connections…")}</p></div>`;
  try {
    const data = await api("/api/provider-settings");
    state.providers = data.providers || [];
    state.modelRoutes = data.model_routes || providerModelRoutes(state.providers);
    state.modelOrder = Array.isArray(data.model_order) ? data.model_order : state.modelRoutes.map(route => route.route_id);
    state.providerRuntime = data.runtime || {};
    renderProviderSettings();
    renderProviderRuntimeSettings();
    await loadIntegrations();
  } catch (error) {
    container.innerHTML = `<div class="empty-state"><p>${escapeHtml(error.message)}</p></div>`;
  } finally {
    container.setAttribute("aria-busy", "false");
  }
}

function renderIntegrations() {
  const integrations = state.integrations || [];
  const ready = integrations.filter(item => Number(item.configured) > 0).length;
  const counter = $("#integration-configured-count");
  if (counter) counter.textContent = `${faNumber(ready)} / ${faNumber(integrations.length)} ${l("متصل", "connected")}`;
  integrations.forEach(item => {
    const card = document.querySelector(`[data-integration-card="${CSS.escape(item.id)}"]`);
    const status = $(`#integration-status-${item.id}`);
    if (card) card.classList.toggle("connected", Number(item.configured) > 0);
    if (status) status.textContent = Number(item.configured) > 0
      ? `${faNumber(item.configured)} ${l("کلید آماده", "credentials ready")} · ${item.environment_variable}`
      : `${l("تنظیم نشده", "Not configured")} · ${item.environment_variable}`;
  });
  const runtime = state.integrationRuntime || {};
  if ($("#integration-timeout")) $("#integration-timeout").value = runtime.timeout_seconds ?? 35;
  if ($("#integration-cooldown")) $("#integration-cooldown").value = runtime.cooldown_seconds ?? 60;
  if ($("#integration-key-gap")) $("#integration-key-gap").value = runtime.minimum_gap_seconds ?? 0.75;
}

async function loadIntegrations() {
  if (!$("#integration-settings-form")) return;
  try {
    const data = await api("/api/integrations");
    state.integrations = data.integrations || [];
    state.integrationRuntime = data;
    renderIntegrations();
  } catch (error) {
    $("#integration-configured-count").textContent = l("خطا در دریافت وضعیت", "Status unavailable");
  }
}

async function saveIntegrations(event) {
  event.preventDefault();
  const form = event.currentTarget;
  const button = $('button[type="submit"]', form);
  const body = {};
  for (const input of $$('input[type="password"]', form)) {
    const clear = $(`[data-clear-integration="${input.name}"]`, form)?.checked;
    if (clear) body[input.name] = "";
    else if (input.value.trim()) body[input.name] = input.value.trim();
  }
  for (const input of $$('input[type="number"]', form)) {
    if (input.name && input.value !== "") body[input.name] = Number(input.value);
  }
  if (!Object.keys(body).length) {
    toast(l("کلید تازه‌ای وارد نشده است.", "No credential changes were entered."));
    return;
  }
  button.disabled = true;
  button.innerHTML = `<span class="status-spinner"></span>${l("در حال ذخیره…", "Saving…")}`;
  try {
    const data = await api("/api/integrations", {method: "PUT", body});
    state.integrations = data.integrations || [];
    state.integrationRuntime = data;
    form.reset();
    renderIntegrations();
    toast(l("اتصال‌های پژوهش و رسانه ذخیره شدند.", "Research and media integrations were saved."));
    await loadStatus();
  } catch (error) {
    toast(error.message);
  } finally {
    button.disabled = false;
    button.innerHTML = `<svg><use href="#i-check"></use></svg>${l("ذخیره اتصال‌ها", "Save integrations")}`;
  }
}

function providerModelRoutes(providers) {
  return providers.flatMap(provider => (provider.models || [])
    .filter(model => model.capabilities?.includes("chat"))
    .map(model => ({
      route_id: `${provider.id}:${model.id}`,
      provider_id: provider.id,
      provider_label: provider.label,
      model_id: model.id,
      model_label: model.label,
      capabilities: model.capabilities || [],
      enabled: provider.enabled && model.enabled,
      configured: provider.configured,
      credential_count: provider.credential_count || 0,
    })));
}

function renderProviderSettings() {
  const container = $("#provider-settings");
  if (!container) return;
  renderGlobalModelPriority();
  const capabilityLabels = {
    chat: l("گفتگو", "Chat"), vision: l("تصویر", "Vision"), embeddings: l("بردارسازی", "Embeddings"),
    transcription: l("رونویسی", "Transcription"), ocr: "OCR",
  };
  container.innerHTML = state.providers.map(provider => {
    const models = provider.models || [];
    return `<article class="provider-card panel-surface ${provider.configured ? "connected" : ""}">
      <header><span class="provider-logo">${escapeHtml(provider.id === "ocr_space" ? "OCR" : provider.label.slice(0, 2).toUpperCase())}</span><div><small>${provider.built_in ? l("اتصال پیش‌فرض", "Built-in connection") : l("اتصال سفارشی", "Custom connection")}</small><h3>${escapeHtml(provider.label)}</h3><code>${escapeHtml(provider.id)}</code></div><span class="provider-status ${provider.configured ? "ok" : ""}"><i></i>${provider.configured ? l("متصل", "Connected") : l("تنظیم نشده", "Not configured")}</span></header>
      <div class="provider-capabilities">${(provider.capabilities || []).map(capability => `<span>${escapeHtml(capabilityLabels[capability] || capability)}</span>`).join("")}</div>
      <div class="provider-models"><div><strong>${l("مدل‌های این اتصال", "Models in this connection")}</strong><span>${faNumber(models.filter(model => model.enabled).length)} / ${faNumber(models.length)}</span></div><ol>${models.slice(0, 5).map(model => `<li class="${model.enabled ? "" : "disabled"}"><b>AI</b><span><strong>${escapeHtml(model.label)}</strong><small>${escapeHtml(model.id)}</small></span>${model.capabilities?.includes("vision") ? `<i>VLM</i>` : ""}</li>`).join("") || `<li class="disabled">—</li>`}</ol></div>
      <footer><span>${faNumber(provider.credential_count)} ${l("کلید آماده", "configured keys")}</span><div class="provider-order-controls"><button type="button" data-move-provider="${escapeHtml(provider.id)}" data-direction="-1" aria-label="Move up">↑</button><button type="button" data-move-provider="${escapeHtml(provider.id)}" data-direction="1" aria-label="Move down">↓</button></div><button class="secondary-button" type="button" data-edit-provider="${escapeHtml(provider.id)}">${l("مدیریت", "Manage")}</button></footer>
    </article>`;
  }).join("");
}

function renderGlobalModelPriority() {
  const list = $("#global-model-priority-list");
  const counter = $("#global-model-priority-count");
  if (!list || !counter) return;
  $("#global-model-priority-title").textContent = l("اولویت سراسری مدل‌ها", "Global model priority");
  $("#global-model-priority-hint").textContent = l(
    "همه مدل‌ها، مستقل از ارائه‌دهنده، در یک مسیر واحد امتحان می‌شوند. ترتیب را بر اساس کیفیت و نیاز خود بچینید.",
    "All models share one fallback chain across providers. Arrange them by the quality and behavior you want.",
  );
  const routes = state.modelRoutes || [];
  counter.textContent = `${faNumber(routes.filter(route => route.enabled).length)} ${l("مدل فعال", "active models")}`;
  list.innerHTML = routes.map((route, index) => {
    const ready = route.enabled && route.configured;
    const stateLabel = !route.enabled
      ? l("غیرفعال", "Disabled")
      : route.configured
        ? `${faNumber(route.credential_count)} ${l("کلید آماده", "keys ready")}`
        : l("بدون کلید", "No key");
    return `<li class="global-model-route ${ready ? "ready" : ""} ${route.enabled ? "" : "disabled"}" data-global-model-route="${escapeHtml(route.route_id)}">
      <b class="global-model-rank">${String(index + 1).padStart(2, "0")}</b>
      <span class="global-model-identity"><strong>${escapeHtml(route.model_label)}</strong><small>${escapeHtml(route.model_id)}</small></span>
      <span class="global-model-provider">${escapeHtml(route.provider_label)}</span>
      <span class="global-model-capabilities">${route.capabilities?.includes("vision") ? "<i>VLM</i>" : ""}${route.capabilities?.includes("tools") ? `<i>${l("ابزار", "Tools")}</i>` : ""}</span>
      <span class="global-model-state"><i></i>${escapeHtml(stateLabel)}</span>
      <span class="global-model-controls">
        <button type="button" data-move-global-model="${escapeHtml(route.route_id)}" data-direction="-1" aria-label="${l("انتقال مدل به بالا", "Move model up")}" ${index === 0 ? "disabled" : ""}>↑</button>
        <button type="button" data-move-global-model="${escapeHtml(route.route_id)}" data-direction="1" aria-label="${l("انتقال مدل به پایین", "Move model down")}" ${index === routes.length - 1 ? "disabled" : ""}>↓</button>
      </span>
    </li>`;
  }).join("") || `<li class="global-model-empty">${l("هنوز مدل گفتگویی ثبت نشده است.", "No chat models are registered yet.")}</li>`;
}

function renderProviderRuntimeSettings() {
  const form = $("#provider-runtime-form");
  if (!form) return;
  Object.entries(state.providerRuntime).forEach(([name, value]) => {
    const input = form.elements.namedItem(name);
    if (!input) return;
    if (input.type === "checkbox") input.checked = Boolean(value);
    else input.value = value;
  });
}

async function openProviderDialog(providerId = "") {
  if (!state.providers.length) await loadProviderSettings();
  const provider = state.providers.find(item => item.id === providerId);
  state.editingProvider = provider ? structuredClone(provider) : {
    id: "", label: "", adapter: "openai", base_url: "", environment_variable: "",
    enabled: true, built_in: false, capabilities: ["chat", "tools"],
    models: [{id: "", label: "", capabilities: ["chat", "tools"], enabled: true, chat_priority: 1000, vision_priority: 1000}],
  };
  $("#provider-original-id").value = provider?.id || "";
  $("#provider-id").value = provider?.id || "";
  $("#provider-label").value = provider?.label || "";
  $("#provider-adapter").value = "openai";
  $("#provider-base-url").value = provider?.base_url || "";
  $("#provider-env-variable").value = provider?.environment_variable || "";
  $("#provider-enabled").checked = provider?.enabled ?? true;
  $("#provider-key-input").value = "";
  const immutable = Boolean(provider?.built_in);
  for (const selector of ["#provider-id", "#provider-label", "#provider-adapter", "#provider-base-url", "#provider-env-variable"]) $(selector).disabled = immutable;
  $("#provider-delete").classList.toggle("hidden", !provider || immutable);
  $("#provider-dialog-title").textContent = provider ? l("مدیریت ارائه‌دهنده", "Manage provider") : l("افزودن ارائه‌دهنده", "Add provider");
  renderProviderModelEditor();
  $("#provider-dialog").showModal();
  (immutable ? $("#provider-key-input") : $("#provider-id")).focus();
}

function renderProviderModelEditor() {
  const provider = state.editingProvider;
  if (!provider) return;
  $("#provider-capabilities").innerHTML = [...new Set(provider.models.flatMap(model => model.capabilities || []))].map(item => `<span>${escapeHtml(item)}</span>`).join("");
  $("#provider-model-editor").innerHTML = provider.models.map((model, index) => `<article class="provider-model-row" data-provider-model-index="${index}">
    <div class="model-order"><button type="button" data-move-provider-model="-1" aria-label="Move model up">↑</button><b>${String(index + 1).padStart(2, "0")}</b><button type="button" data-move-provider-model="1" aria-label="Move model down">↓</button></div>
    <div class="model-fields"><label>Model ID<input data-model-field="id" value="${escapeHtml(model.id)}" dir="ltr" ${provider.built_in ? "disabled" : ""} required></label><label>Label<input data-model-field="label" value="${escapeHtml(model.label)}" ${provider.built_in ? "disabled" : ""} required></label></div>
    <div class="model-options"><label><input type="checkbox" data-model-capability="vision" ${model.capabilities?.includes("vision") ? "checked" : ""} ${provider.built_in ? "disabled" : ""}>Vision</label><label><input type="checkbox" data-model-capability="tools" ${model.capabilities?.includes("tools") ? "checked" : ""} ${provider.built_in ? "disabled" : ""}>Tools</label><label><input type="checkbox" data-model-field="enabled" ${model.enabled ? "checked" : ""}>${l("فعال", "Enabled")}</label></div>
    ${provider.built_in ? "" : `<button class="model-remove" type="button" data-remove-provider-model aria-label="Remove model">×</button>`}
  </article>`).join("");
  $("#provider-add-model").classList.toggle("hidden", provider.built_in);
}

function readProviderModels() {
  return $$("[data-provider-model-index]", $("#provider-model-editor")).map((row, index) => ({
    id: $("[data-model-field=id]", row).value.trim(),
    label: $("[data-model-field=label]", row).value.trim(),
    capabilities: ["chat", ...$$('[data-model-capability]:checked', row).map(input => input.dataset.modelCapability)],
    enabled: $("[data-model-field=enabled]", row).checked,
    chat_priority: index + 1,
    vision_priority: index + 1,
  }));
}

async function saveProviderCredentials() {
  const originalId = $("#provider-original-id").value;
  const providerId = $("#provider-id").value.trim();
  const apiKeys = $("#provider-key-input").value.trim();
  const existing = state.providers.find(provider => provider.id === originalId);
  const models = readProviderModels();
  if (!providerId || !models.length || models.some(model => !model.id || !model.label)) return;
  const button = $("#save-provider");
  button.disabled = true;
  button.textContent = l("در حال ذخیره…", "Saving…");
  try {
    const payload = {
      label: $("#provider-label").value.trim(), adapter: $("#provider-adapter").value,
      base_url: $("#provider-base-url").value.trim(), environment_variable: $("#provider-env-variable").value.trim(),
      enabled: $("#provider-enabled").checked, models,
    };
    if (apiKeys) payload.api_keys = apiKeys;
    let saved;
    if (!existing) saved = await api("/api/provider-settings", {method: "POST", body: {id: providerId, capabilities: [...new Set(models.flatMap(model => model.capabilities))], ...payload}});
    else if (existing.built_in) saved = apiKeys ? await api(`/api/provider-settings/${encodeURIComponent(providerId)}`, {method: "PUT", body: {api_keys: apiKeys}}) : existing;
    else saved = await api(`/api/provider-settings/${encodeURIComponent(providerId)}`, {method: "PUT", body: payload});
    $("#provider-dialog").close();
    $("#provider-key-input").value = "";
    await loadProviderSettings();
    const target = state.providers.find(provider => provider.id === providerId);
    if (target) {
      target.models = models;
      target.enabled = $("#provider-enabled").checked;
    }
    await saveProviderOrdering();
    toast(l(`${faNumber(saved.credential_count)} کلید آماده، تنظیمات ذخیره شد.`, `Provider saved with ${faNumber(saved.credential_count)} configured keys.`));
    await loadStatus();
  } catch (error) {
    toast(error.message);
  } finally {
    button.disabled = false;
    button.textContent = l("ذخیره تنظیمات", "Save settings");
  }
}

async function saveProviderOrdering() {
  const body = {
    provider_order: state.providers.map(provider => provider.id),
    model_order: state.modelRoutes.map(route => route.route_id),
    disabled_providers: state.providers.filter(provider => !provider.enabled).map(provider => provider.id),
    disabled_models: state.providers.flatMap(provider => (provider.models || []).filter(model => !model.enabled).map(model => `${provider.id}:${model.id}`)),
  };
  const data = await api("/api/provider-settings", {method: "PATCH", body});
  state.providers = data.providers || state.providers;
  state.modelRoutes = data.model_routes || state.modelRoutes;
  state.modelOrder = data.model_order || state.modelRoutes.map(route => route.route_id);
  state.providerRuntime = data.runtime || state.providerRuntime;
  renderProviderSettings();
}

async function moveProvider(providerId, direction) {
  const index = state.providers.findIndex(provider => provider.id === providerId);
  const next = index + Number(direction);
  if (index < 0 || next < 0 || next >= state.providers.length) return;
  [state.providers[index], state.providers[next]] = [state.providers[next], state.providers[index]];
  renderProviderSettings();
  await saveProviderOrdering();
}

async function moveGlobalModel(routeId, direction) {
  const index = state.modelRoutes.findIndex(route => route.route_id === routeId);
  const next = index + Number(direction);
  if (index < 0 || next < 0 || next >= state.modelRoutes.length) return;
  [state.modelRoutes[index], state.modelRoutes[next]] = [state.modelRoutes[next], state.modelRoutes[index]];
  state.modelOrder = state.modelRoutes.map(route => route.route_id);
  renderGlobalModelPriority();
  await saveProviderOrdering();
}

function moveProviderModel(direction, index) {
  const provider = state.editingProvider;
  const current = Number(index);
  const next = current + Number(direction);
  if (!provider || !Number.isInteger(current) || next < 0 || next >= provider.models.length) return;
  provider.models = readProviderModels();
  [provider.models[current], provider.models[next]] = [provider.models[next], provider.models[current]];
  renderProviderModelEditor();
}

async function deleteCurrentProvider() {
  const providerId = $("#provider-original-id").value;
  if (!providerId || !confirm(l("این اتصال حذف شود؟", "Delete this provider connection?"))) return;
  await api(`/api/provider-settings/${encodeURIComponent(providerId)}`, {method: "DELETE"});
  $("#provider-dialog").close();
  await loadProviderSettings();
  toast(l("اتصال حذف شد.", "Provider removed."));
}

async function saveProviderRuntimeSettings(event) {
  event.preventDefault();
  const runtime = {};
  for (const element of event.currentTarget.elements) {
    if (!element.name) continue;
    runtime[element.name] = element.type === "checkbox" ? element.checked : Number(element.value);
  }
  const data = await api("/api/provider-settings", {method: "PATCH", body: {runtime}});
  state.providerRuntime = data.runtime || runtime;
  renderProviderRuntimeSettings();
  toast(l("تنظیمات اجرا ذخیره شد.", "Runtime settings saved."));
  await loadStatus();
}

async function loadSettings() {
  const [data, vector, providerSettings] = await Promise.all([
    loadStatus(),
    loadWorkspaceVector(),
    api("/api/provider-settings"),
  ]);
  if (!data) return;
  state.providers = providerSettings.providers || [];
  state.modelRoutes = providerSettings.model_routes || providerModelRoutes(state.providers);
  state.modelOrder = providerSettings.model_order || state.modelRoutes.map(route => route.route_id);
  state.providerRuntime = providerSettings.runtime || {};
  const runtime = state.providerRuntime;
  const routes = state.modelRoutes;
  const readyRoutes = routes.filter(route => route.enabled && route.configured);
  const readyVisionRoutes = readyRoutes.filter(route => route.capabilities?.includes("vision"));
  const configuredProviders = state.providers.filter(provider => provider.enabled && provider.configured);
  const activeRoute = routes.find(route => route.model_id === data.llm || route.model_label === data.llm) || readyRoutes[0];
  const readyDocuments = state.documents.filter(document => document.status === "ready");
  const selectedCount = state.selectedDocumentIds.size;
  const threshold = runtime.retrieval_min_similarity ?? data.retrieval_threshold ?? 0;
  const topK = runtime.retrieval_top_k ?? data.retrieval_top_k ?? 0;
  const vlmEnabled = runtime.vlm_enabled !== false && data.vlm?.configured;

  $("#arch-ingestion-papers").textContent = `${faNumber(readyDocuments.length)} / ${faNumber(state.documents.length)} ${l("منبع آماده", "sources ready")}`;
  $("#arch-ingestion-chunks").textContent = `${faNumber(vector?.chunk_count)} chunk · ${vector?.embedding_model || data.embedding}`;
  $("#arch-extraction-state").textContent = `${data.ocr?.configured ? "OCR ✓" : l("OCR خاموش", "OCR off")} · ${vlmEnabled ? "VLM ✓" : l("VLM خاموش", "VLM off")}`;
  $("#arch-retrieval-state").textContent = `Top K ${faNumber(topK)} · ${l("آستانه", "threshold")} ${faNumber(threshold)}`;
  $("#arch-scope-state").textContent = selectedCount ? `${faNumber(selectedCount)} ${l("منبع انتخاب‌شده", "selected sources")}` : `${faNumber(readyDocuments.length)} ${l("منبع آماده", "ready sources")}`;
  $("#arch-answer-state").textContent = activeRoute ? `${activeRoute.provider_label} · ${activeRoute.model_label}` : `${data.llm_provider} · ${data.llm}`;
  $("#arch-route-summary").innerHTML = `<ol class="model-route">${routes.map((route, index) => {
    const active = route.route_id === activeRoute?.route_id;
    const stateLabel = !route.enabled ? l("غیرفعال", "disabled") : route.configured ? `${faNumber(route.credential_count)} ${l("کلید آماده", "keys ready")}` : l("بدون کلید", "no key");
    return `<li class="${active ? "active" : ""} ${route.enabled ? "" : "disabled"}"><b>${String(index + 1).padStart(2, "0")}</b><strong>${escapeHtml(route.model_label)}</strong><small>${escapeHtml(route.provider_label)} · ${escapeHtml(stateLabel)}</small></li>`;
  }).join("") || `<li class="active"><strong>${escapeHtml(data.llm)}</strong><small>${escapeHtml(data.llm_provider)}</small></li>`}</ol>`;
  const llmFailover = data.llm_failover || {};
  const embeddingFailover = data.embedding_failover || {};
  const yes = l("بله", "Yes");
  const no = l("خیر", "No");
  const cards = [
    [l("پاسخ‌گویی", "Answer generation"), [[l("سرویس فعال", "Active provider"), activeRoute?.provider_label || data.llm_provider], [l("مدل فعال", "Active model"), activeRoute?.model_label || data.llm], [l("ارائه‌دهندگان متصل", "Connected providers"), configuredProviders.length], [l("مدل‌های آماده در زنجیره", "Ready models in chain"), readyRoutes.length], [l("تغییر مسیر در آخرین درخواست", "Fallback on last request"), llmFailover.last_failover ? l("انجام شد", "Used") : l("انجام نشد", "Not used")]]],
    [l("جست‌وجو و پایگاه برداری", "Retrieval and vector store"), [[l("سرویس بردارسازی", "Embedding provider"), data.embedding_provider], [l("مدل", "Model"), data.embedding], [l("ابعاد", "Dimensions"), data.embedding_dimensions], [l("مجموعهٔ فعال", "Active collection"), vector?.collection_name || data.collection], [l("آخرین مسیر", "Last backend"), embeddingFailover.last_backend || l("اصلی", "Primary")]]],
    [l("استخراج تصویر و اسکن", "Image and scan extraction"), [[l("OCR آماده", "OCR ready"), data.ocr?.configured ? yes : no], [l("کلیدهای OCR", "OCR credentials"), data.ocr?.credential_count || 0], [l("سقف سراسری OCR", "OCR global rate"), `${runtime.ocr_global_requests_per_minute ?? data.ocr?.global_requests_per_minute ?? 0} / min`], [l("تحلیل بصری فعال", "Visual analysis enabled"), vlmEnabled ? yes : no], [l("مدل‌های بصری آماده", "Ready visual models"), readyVisionRoutes.length], [l("سقف سراسری VLM", "VLM global rate"), `${runtime.vlm_global_requests_per_minute ?? data.vlm?.global_requests_per_minute ?? 0} / min`]]],
    [l("تنظیمات اجرای کاربر", "User runtime preferences"), [[l("مهلت پاسخ مدل", "Model timeout"), `${runtime.llm_timeout_seconds ?? "—"} s`], [l("چرخه‌های تلاش مجدد", "Retry rounds"), runtime.llm_pool_retry_rounds ?? "—"], [l("زمان توقف مسیر ناموفق", "Route cooldown"), `${runtime.llm_pool_cooldown_seconds ?? "—"} s`], [l("تعداد شواهد اولیه", "Initial evidence count"), topK], [l("حداقل شباهت", "Minimum similarity"), threshold]]],
    [l("فضای کاری و پردازش", "Workspace and processing"), [[l("پردازش هم‌زمان فایل", "Concurrent file jobs"), runtime.ingestion_worker_count ?? data.processing?.worker_count ?? 1], [l("کارهای فعال", "Active jobs"), data.processing?.active_jobs || 0], [l("منابع آماده", "Ready sources"), `${readyDocuments.length} / ${state.documents.length}`], [l("دامنهٔ انتخاب", "Selected scope"), selectedCount || readyDocuments.length], [l("وضعیت", "Status"), data.runtime_available === false ? l("نیازمند تنظیم", "Setup required") : l("آماده", "Ready")]]],
  ];
  $("#settings-grid").innerHTML = cards.map(([title, rows]) => `<article class="setting-card"><h3>${escapeHtml(title)}</h3><dl>${rows.map(([key, value]) => `<div><dt>${escapeHtml(key)}</dt><dd>${escapeHtml(value)}</dd></div>`).join("")}</dl></article>`).join("");
}

function recordingExtension(mime) {
  if (mime.includes("mp4")) return "m4a";
  if (mime.includes("mpeg")) return "mp3";
  if (mime.includes("ogg")) return "ogg";
  if (mime.includes("wav")) return "wav";
  return "webm";
}

async function toggleRecording() {
  const button = $("#voice-button");
  if (state.recorder?.state === "recording") {
    state.recorder.stop();
    button.classList.remove("recording");
    button.setAttribute("aria-pressed", "false");
    return;
  }
  if (!navigator.mediaDevices?.getUserMedia || typeof MediaRecorder === "undefined") {
    toast(l("مرورگر ضبط صدا را پشتیبانی نمی‌کند.", "This browser does not support audio recording."));
    return;
  }
  try {
    const stream = await navigator.mediaDevices.getUserMedia({audio: true});
    state.recordingStream = stream;
    state.audioChunks = [];
    const recorder = new MediaRecorder(stream);
    state.recorder = recorder;
    recorder.ondataavailable = event => { if (event.data.size) state.audioChunks.push(event.data); };
    recorder.onstop = async () => {
      const mime = recorder.mimeType || state.audioChunks[0]?.type || "audio/webm";
      const blob = new Blob(state.audioChunks, {type: mime});
      stream.getTracks().forEach(track => track.stop());
      state.recordingStream = null;
      state.recorder = null;
      if (!blob.size) return;
      const form = new FormData();
      form.append("audio", blob, `question.${recordingExtension(mime)}`);
      try {
        const result = await api("/api/transcribe", {method: "POST", body: form});
        $("#question-input").value = result.text;
        toast(l(`صوت با ${result.provider} تبدیل شد.`, `Audio was transcribed with ${result.provider}.`));
      } catch (error) {
        toast(error.message);
      }
    };
    recorder.start();
    button.classList.add("recording");
    button.setAttribute("aria-pressed", "true");
  } catch {
    toast(l("اجازهٔ دسترسی به میکروفن داده نشد.", "Microphone permission was not granted."));
  }
}

function setEvidenceTab(name) {
  $$(".evidence-tabs [role=tab]").forEach(button => {
    const active = button.dataset.evidenceTab === name;
    button.classList.toggle("active", active);
    button.setAttribute("aria-selected", String(active));
  });
  $("#evidence-text-preview").classList.toggle("active", name === "text");
  $("#evidence-visual-preview").classList.toggle("active", name === "image");
}

function handleGlobalKeydown(event) {
  if ((event.ctrlKey || event.metaKey) && event.key.toLocaleLowerCase() === "k") {
    event.preventDefault();
    $("#global-search").focus();
  }
  if (event.key === "Escape") {
    closeEvidence();
    $("#global-search-results").classList.add("hidden");
    document.body.classList.remove("menu-open");
  }
}

function closeDialog(dialogId) {
  const dialog = document.getElementById(dialogId);
  if (dialog?.open) dialog.close();
}

document.addEventListener("click", event => {
  const dialogClose = event.target.closest("[data-close-dialog]");
  if (dialogClose) {
    closeDialog(dialogClose.dataset.closeDialog);
    return;
  }
  const dialogBackdrop = event.target.closest("dialog.modal-shell");
  if (dialogBackdrop && event.target === dialogBackdrop) {
    closeDialog(dialogBackdrop.id);
    return;
  }
  const viewLink = event.target.closest("[data-view-link]");
  if (viewLink) {
    event.preventDefault();
    setView(viewLink.dataset.viewLink);
  }
  const evidenceCard = event.target.closest("[data-evidence-index]");
  if (evidenceCard) openEvidence(state.evidence[Number(evidenceCard.dataset.evidenceIndex)]);
  const citation = event.target.closest("[data-citation]");
  if (citation) {
    const item = state.evidence[Number(citation.dataset.citation) - 1];
    if (item) openEvidence(item);
  }
  const chunk = event.target.closest("[data-chunk-index]");
  if (chunk) openEvidence(state.chunkEvidence[Number(chunk.dataset.chunkIndex)]);
  const process = event.target.closest("[data-process-document]");
  if (process) processDocument(process.dataset.processDocument);
  const removeDocument = event.target.closest("[data-delete-document]");
  if (removeDocument) deleteDocument(removeDocument.dataset.deleteDocument).catch(error => toast(error.message));
  const openWorkspace = event.target.closest("[data-open-workspace]");
  if (openWorkspace) selectWorkspace(openWorkspace.dataset.openWorkspace).then(() => setView("chat"));
  const removeWorkspace = event.target.closest("[data-delete-workspace]");
  if (removeWorkspace) deleteWorkspace(removeWorkspace.dataset.deleteWorkspace).catch(error => toast(error.message));
  if (event.target.closest("[data-open-workspace-dialog]")) $("#workspace-dialog").showModal();
  const librarySession = event.target.closest("[data-library-session]");
  if (librarySession) {
    state.activeSessionId = librarySession.dataset.librarySession;
    renderHistory();
    restoreSession();
    setView("chat");
  }
  const searchDocument = event.target.closest("[data-search-document]");
  if (searchDocument) {
    setView("documents");
    $("#global-search-results").classList.add("hidden");
    requestAnimationFrame(() => document.querySelector(`[data-document-id="${CSS.escape(searchDocument.dataset.searchDocument)}"]`)?.scrollIntoView({behavior: "smooth", block: "center"}));
  }
  const editProvider = event.target.closest("[data-edit-provider]");
  if (editProvider) openProviderDialog(editProvider.dataset.editProvider);
  const moveProviderButton = event.target.closest("[data-move-provider]");
  if (moveProviderButton) moveProvider(moveProviderButton.dataset.moveProvider, moveProviderButton.dataset.direction);
  const moveGlobalModelButton = event.target.closest("[data-move-global-model]");
  if (moveGlobalModelButton) moveGlobalModel(moveGlobalModelButton.dataset.moveGlobalModel, moveGlobalModelButton.dataset.direction);
  const moveModelButton = event.target.closest("[data-move-provider-model]");
  if (moveModelButton) moveProviderModel(moveModelButton.dataset.moveProviderModel, moveModelButton.closest("[data-provider-model-index]").dataset.providerModelIndex);
  const removeModelButton = event.target.closest("[data-remove-provider-model]");
  if (removeModelButton && state.editingProvider) {
    state.editingProvider.models = readProviderModels();
    state.editingProvider.models.splice(Number(removeModelButton.closest("[data-provider-model-index]").dataset.providerModelIndex), 1);
    renderProviderModelEditor();
  }
  if (event.target.closest("[data-open-trace-dialog]")) $("#activity-detail-dialog").showModal();
  const filter = event.target.closest("[data-document-filter]");
  if (filter && !filter.disabled) {
    const id = filter.dataset.documentFilter;
    if (!id) state.selectedDocumentIds.clear();
    else if (!state.selectedDocumentIds.size && filter.classList.contains("source-scope-item")) {
      state.documents.filter(item => item.status === "ready" && item.document_id !== id).forEach(item => state.selectedDocumentIds.add(item.document_id));
    }
    else if (state.selectedDocumentIds.has(id)) state.selectedDocumentIds.delete(id);
    else state.selectedDocumentIds.add(id);
    renderDocumentFilters();
  }
  if (event.target.closest("[data-remove-prompt-image]")) clearPromptImage();
  const studioAction = event.target.closest("[data-artifact-type]");
  if (studioAction) openStudioCustomization(studioAction.dataset.artifactType);
  const studioCancel = event.target.closest("[data-studio-cancel]");
  if (studioCancel) {
    event.preventDefault();
    cancelStudioArtifact(studioCancel.dataset.studioCancel);
    return;
  }
  const studioArtifact = event.target.closest("[data-studio-artifact]");
  if (studioArtifact) openStudioArtifact(studioArtifact.dataset.studioArtifact);
  const studioRetry = event.target.closest("[data-studio-retry]");
  if (studioRetry) retryStudioArtifact(studioRetry.dataset.studioRetry).catch(error => toast(error.message));
  const addParticipant = event.target.closest("[data-podcast-add-participant]");
  if (addParticipant) {
    const list = $(".podcast-participant-list", addParticipant.closest(".podcast-participants-editor"));
    const index = $$("[data-podcast-participant]", list).length;
    if (index < 5) list.insertAdjacentHTML("beforeend", podcastParticipantCard({participant_id: `speaker-${Date.now()}`, name: l("گویندهٔ تازه", "New participant"), voice: state.podcastVoices?.[index % state.podcastVoices.length]?.id || "Kore", role: l("همراه گفت‌وگو", "Discussion participant"), delivery: l("طبیعی و شنیدنی", "Natural and engaging"), thinking_level: "auto", temperature: null}, index));
    syncPodcastParticipantControls();
  }
  const removeParticipant = event.target.closest("[data-podcast-remove-participant]");
  if (removeParticipant && !removeParticipant.disabled) {
    removeParticipant.closest("[data-podcast-participant]").remove();
    syncPodcastParticipantControls();
  }
  const moveParticipant = event.target.closest("[data-podcast-move-participant]");
  if (moveParticipant && !moveParticipant.disabled) {
    const card = moveParticipant.closest("[data-podcast-participant]");
    const sibling = moveParticipant.dataset.podcastMoveParticipant === "up" ? card.previousElementSibling : card.nextElementSibling;
    if (sibling) moveParticipant.dataset.podcastMoveParticipant === "up" ? sibling.before(card) : sibling.after(card);
    syncPodcastParticipantControls();
  }
  if (event.target.closest("[data-podcast-save-script]")) savePodcastScript().catch(error => toast(error.message));
  if (event.target.closest("[data-podcast-generate]")) generatePodcastAudio();
  if (event.target.closest("[data-podcast-cancel]")) cancelPodcastAudio();
  const addPodcastTurn = event.target.closest("[data-podcast-add-turn]");
  if (addPodcastTurn) {
    const root = addPodcastTurn.closest("[data-podcast-artifact]");
    const item = state.studioArtifacts.find(artifact => artifact.artifact_id === root.dataset.podcastArtifact);
    const participants = item?.data?.config?.participants || item?.options?.participants || [];
    const list = $("[data-podcast-turn-list]", root);
    const index = $$("[data-podcast-turn]", list).length;
    list.insertAdjacentHTML("beforeend", podcastScriptTurnMarkup({turn_id: `turn-${Date.now()}`, participant_id: participants[index % participants.length]?.participant_id || "host", chapter: "", text: "", citations: []}, index, participants));
  }
  const removePodcastTurn = event.target.closest("[data-podcast-remove-turn]");
  if (removePodcastTurn) {
    const list = removePodcastTurn.closest("[data-podcast-turn-list]");
    if ($$("[data-podcast-turn]", list).length > 1) removePodcastTurn.closest("[data-podcast-turn]").remove();
  }
  const movePodcastTurn = event.target.closest("[data-podcast-move-turn]");
  if (movePodcastTurn) {
    const row = movePodcastTurn.closest("[data-podcast-turn]");
    const sibling = movePodcastTurn.dataset.podcastMoveTurn === "up" ? row.previousElementSibling : row.nextElementSibling;
    if (sibling) movePodcastTurn.dataset.podcastMoveTurn === "up" ? sibling.before(row) : sibling.after(row);
  }
  const quizChoice = event.target.closest("[data-quiz-choice]");
  if (quizChoice) answerQuizChoice(quizChoice);
  const previousFlashcard = event.target.closest("[data-flashcard-prev]");
  if (previousFlashcard) navigateFlashcards(previousFlashcard, -1);
  const nextFlashcard = event.target.closest("[data-flashcard-next]");
  if (nextFlashcard) navigateFlashcards(nextFlashcard, 1);
  const flashcardFlip = event.target.closest("[data-flashcard-flip], [data-flashcard-card]");
  if (flashcardFlip) toggleFlashcard(flashcardFlip);
  const mindMapNode = event.target.closest("[data-mind-map-node]");
  if (mindMapNode) {
    selectMindMapNode(mindMapNode);
    toggleMindMapNode(mindMapNode);
  }
  const mindMapAction = event.target.closest("[data-mind-map-action]");
  if (mindMapAction) {
    const workbench = mindMapAction.closest(".mind-map-workbench");
    const action = mindMapAction.dataset.mindMapAction;
    if (action === "expand-all" || action === "collapse-all") setMindMapExpansion(workbench, action === "expand-all");
    if (action === "fit") fitMindMap(workbench);
    if (action === "zoom-in") zoomMindMap(workbench, 1.18);
    if (action === "zoom-out") zoomMindMap(workbench, 1 / 1.18);
  }
  if (event.target.closest("#web-source-toggle-all")) {
    const fields = $$('[data-web-source-id]', $("#web-source-results"));
    const select = fields.some(field => !field.checked);
    fields.forEach(field => { field.checked = select; });
  }
});

document.addEventListener("change", event => {
  if (event.target.matches('[data-studio-option="report_format"], [data-studio-option="format"], [data-studio-option="production_mode"], [data-studio-option="design_control"], [data-studio-option="deck_format"], [data-studio-option="slide_count_strategy"], [data-studio-option="scene_count_strategy"], [data-studio-option="color_theme"], [data-studio-option="narration"], [data-studio-option="music"]')) syncStudioConditionalFields();
});

document.addEventListener("input", event => {
  if (event.target.matches('[data-podcast-participant-field="name"]')) {
    $("strong", event.target.closest("[data-podcast-participant]")).textContent = event.target.value || l("گویندهٔ تازه", "New participant");
  }
});

document.addEventListener("keydown", handleGlobalKeydown);
document.addEventListener("DOMContentLoaded", async () => {
  const initialView = location.hash.slice(1);
  setLocale(state.locale, {persist: false});
  setView(VIEW_NAMES.has(initialView) ? initialView : "chat", {syncHash: false});
  $$(".nav-item").forEach(button => button.addEventListener("click", () => setView(button.dataset.view)));
  $("#menu-button").addEventListener("click", () => {
    const open = document.body.classList.toggle("menu-open");
    $("#menu-button").setAttribute("aria-expanded", String(open));
  });
  const closeSidebar = () => {
    document.body.classList.remove("menu-open");
    $("#menu-button").setAttribute("aria-expanded", "false");
  };
  $("#sidebar-close").addEventListener("click", closeSidebar);
  $("#sidebar-scrim").addEventListener("click", closeSidebar);
  window.addEventListener("hashchange", () => setView(location.hash.slice(1), {syncHash: false}));
  $("#language-toggle").addEventListener("click", () => setLocale(state.locale === "fa" ? "en" : "fa"));
  $("#top-workspace-select").addEventListener("change", event => selectWorkspace(event.target.value));
  $("#top-new-workspace").addEventListener("click", () => $("#workspace-dialog").showModal());
  $("#global-search").addEventListener("input", event => renderGlobalSearch(event.target.value));
  $("#global-search").addEventListener("focus", event => renderGlobalSearch(event.target.value));
  $("#provider-create").addEventListener("click", () => openProviderDialog());
  $("#provider-form").addEventListener("submit", event => {event.preventDefault(); saveProviderCredentials();});
  $("#provider-runtime-form").addEventListener("submit", saveProviderRuntimeSettings);
  $("#integration-settings-form").addEventListener("submit", saveIntegrations);
  $("#font-upload-form").addEventListener("submit", event => {
    event.preventDefault();
    uploadFont().catch(error => toast(error.message));
  });
  $("#font-manager-list").addEventListener("click", event => {
    const button = event.target.closest("[data-delete-font]");
    if (button) deleteFont(button.dataset.deleteFont).catch(error => toast(error.message));
  });
  $("#provider-add-model").addEventListener("click", () => {
    state.editingProvider.models = readProviderModels();
    state.editingProvider.models.push({id: "", label: "", capabilities: ["chat", "tools"], enabled: true, chat_priority: 1000, vision_priority: 1000});
    renderProviderModelEditor();
  });
  $("#provider-delete").addEventListener("click", deleteCurrentProvider);
  $$("[data-context-panel]").forEach(button => button.addEventListener("click", () => activateContextPanel(button.dataset.contextPanel)));
  $("#workspace-form").addEventListener("submit", event => {
    event.preventDefault();
    createWorkspace();
  });
  $("#new-chat").addEventListener("click", newSession);
  $("#clear-history").addEventListener("click", async () => {
    if (confirm(l("تاریخچهٔ این فضای کاری پاک شود؟", "Clear this workspace's conversation history?"))) {
      try {
        await api(`/api/workspaces/${encodeURIComponent(state.activeWorkspaceId)}/conversations`, {method: "DELETE"});
        state.sessions = [];
        saveSessions();
        newSession();
      } catch (error) {
        toast(error.message);
      }
    }
  });
  $("#chat-history").addEventListener("click", event => {
    const item = event.target.closest("[data-session]");
    if (!item) return;
    state.activeSessionId = item.dataset.session;
    renderHistory();
    restoreSession();
    setView("chat");
  });
  $("#chat-form").addEventListener("submit", event => {
    event.preventDefault();
    submitQuestion($("#question-input").value);
  });
  $("#question-input").addEventListener("input", event => {
    event.target.style.height = "auto";
    event.target.style.height = `${Math.min(event.target.scrollHeight, 130)}px`;
  });
  $("#question-input").addEventListener("keydown", event => {
    if (event.key === "Enter" && !event.shiftKey && !event.isComposing) {
      event.preventDefault();
      $("#chat-form").requestSubmit();
    }
  });
  $("#upload-form").addEventListener("submit", event => {
    event.preventDefault();
    uploadDocuments($("#pdf-input").files);
  });
  $("#pdf-input").addEventListener("change", event => renderSelectedFiles(event.target.files));
  $("#paste-text-form").addEventListener("submit", event => {
    event.preventDefault();
    addPastedText($("#paste-title").value.trim(), $("#paste-content").value.trim());
  });
  $("#open-web-source-search").addEventListener("click", openWebSourceSearch);
  $("#web-source-mode").addEventListener("change", syncWebSourceMode);
  $("#web-source-search-form").addEventListener("submit", searchWebSources);
  $("#web-source-import").addEventListener("click", importSelectedWebSources);
  $("#web-source-back").addEventListener("click", () => {
    $("#web-source-selection").classList.add("hidden");
    $("#web-source-search-form").classList.remove("hidden");
  });
  const dropzone = $("#upload-dropzone");
  ["dragenter", "dragover"].forEach(name => dropzone.addEventListener(name, event => {event.preventDefault(); dropzone.classList.add("dragging");}));
  ["dragleave", "drop"].forEach(name => dropzone.addEventListener(name, event => {event.preventDefault(); dropzone.classList.remove("dragging");}));
  dropzone.addEventListener("drop", event => {
    const transfer = new DataTransfer();
    [...event.dataTransfer.files].forEach(file => transfer.items.add(file));
    $("#pdf-input").files = transfer.files;
    renderSelectedFiles(transfer.files);
  });
  $("#chunk-search").addEventListener("submit", event => {
    event.preventDefault();
    searchChunks($("#chunk-query").value.trim());
  });
  $("#rebuild-vector").addEventListener("click", () => $("#confirm-dialog").showModal());
  $("#confirm-rebuild").addEventListener("click", event => {event.preventDefault(); $("#confirm-dialog").close(); startRebuild();});
  $("#refresh-traces").addEventListener("click", () => loadTraces());
  $("#trace-shortcut").addEventListener("click", () => {setView("traces"); loadTraces();});
  $("#trace-list").addEventListener("click", event => {
    const item = event.target.closest("[data-trace-id]");
    if (item) selectTrace(item.dataset.traceId);
  });
  $("#voice-button").addEventListener("click", toggleRecording);
  $("#prompt-image-button").addEventListener("click", () => $("#prompt-image-input").click());
  $("#prompt-image-input").addEventListener("change", event => setPromptImage(event.target.files?.[0]));
  $("#refresh-studio").addEventListener("click", loadStudioArtifacts);
  $("#cancel-active-requests").addEventListener("click", () => {
    cancelActiveFrontendRequests().catch(error => toast(error.message));
  });
  $("#studio-create-form").addEventListener("submit", event => {
    event.preventDefault();
    generateStudioArtifact();
  });
  $("#studio-toggle-sources").addEventListener("click", () => {
    const sources = $$('[data-studio-source]', $("#studio-source-options"));
    const shouldSelect = sources.some(source => !source.checked);
    sources.forEach(source => { source.checked = shouldSelect; });
  });
  $("#drawer-close").addEventListener("click", closeEvidence);
  $("#drawer-backdrop").addEventListener("click", closeEvidence);
  $$(".evidence-tabs [role=tab]").forEach(button => button.addEventListener("click", () => setEvidenceTab(button.dataset.evidenceTab)));
  await loadWorkspaces();
  await loadStatus();
});
