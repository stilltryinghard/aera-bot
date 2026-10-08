"""Customer copy in Russian, English, Simplified Chinese and Persian."""

LANGUAGES = {"ru": "🇷🇺 Русский", "en": "🇬🇧 English", "zh": "🇨🇳 中文", "fa": "🇮🇷 فارسی"}
# Each entry is complete in all four languages; no silent mixed-language fallback.
COPY = {
    "trial_timeout": (
        "⏳ За час пробное подключение не было активировано. Ссылка аннулирована и убрана из профиля.\n💎 Для подключения выбери платный тариф. Если нужна помощь, напиши в поддержку 💊",
        "⏳ Your trial was not activated within one hour. The link has been revoked and removed from your profile.\n💎 Choose a paid plan or contact support for help 💊",
        "⏳ 一小时内未激活试用，链接已失效并从资料移除。\n💎 请选择付费套餐或联系支持 💊",
        "⏳ دوره آزمایشی تا یک ساعت فعال نشد. لینک لغو و از پروفایل حذف شد.\n💎 طرح پولی را انتخاب کنید یا با پشتیبانی تماس بگیرید 💊",
    ),
    "trial_finished": (
        "⌛ Два дня пробной подписки завершились. Пробная ссылка отключена.\n💎 Продолжи пользоваться AERA - выбери тариф в меню 💰",
        "⌛ Your two-day trial has ended. The trial link has been revoked.\n💎 Keep using AERA by choosing a plan 💰",
        "⌛ 两天试用已结束，试用链接已失效。\n💎 选择套餐继续使用 AERA 💰",
        "⌛ دوره آزمایشی دو روزه پایان یافت و لینک لغو شد.\n💎 برای ادامه AERA یک طرح انتخاب کنید 💰",
    ),
    "terms_auto": (
        "<b>Условия подписки 📋</b>\n\n💎 Оплата за выбранный тариф и срок. После подтверждённого платежа бот отправляет личную ссылку из готового запаса.\n⏳ Месяц - 30 дней, полгода - 180 дней. Таймер начинается после первого VPN-подключения; точная дата доступна в профиле.\n🚀 Трафик безлимитный. Лимит подключений зависит от тарифа. Подключение через Hiddify.\n\n💬 По оплате, подключению и возврату обращайся в поддержку или @AERAVP. Telegram не обслуживает покупки AERA.\n\nНажимая ниже, ты принимаешь эти условия и подтверждаешь ознакомление с политикой конфиденциальности.",
        "<b>Subscription terms 📋</b>\n\n💎 Payment is for the chosen plan and period. After confirmed payment, the bot sends a personal link from available stock.\n⏳ One month is 30 days; six months are 180 days. Time begins with the first VPN connection; check My profile for expiry.\n🚀 Traffic is unlimited. Connection limits depend on the plan. Use Hiddify.\n\n💬 For payment, connection or refunds, contact support or @AERAVP. Telegram does not support AERA purchases.\n\nPressing below accepts these terms and acknowledges the privacy policy.",
        "<b>订阅条款 📋</b>\n\n💎 按所选套餐和时长付款，确认付款后机器人从库存发送个人链接。\n⏳ 一个月为 30 天，六个月为 180 天。从首次连接 VPN 开始计时，到期时间见我的资料。\n🚀 流量不限，连接数量取决于套餐。请使用 Hiddify。\n\n💬 付款、连接和退款请联系支持或 @AERAVP。Telegram 不处理 AERA 购买支持。\n\n点击下方表示接受条款并已阅读隐私政策。",
        "<b>شرایط اشتراک 📋</b>\n\n💎 پرداخت برای طرح و مدت انتخابی است. پس از تأیید پرداخت، ربات لینک شخصی از موجودی می‌فرستد.\n⏳ یک ماه ۳۰ روز و شش ماه ۱۸۰ روز است. زمان از اولین اتصال VPN شروع می‌شود؛ پایان را در پروفایل ببینید.\n🚀 ترافیک نامحدود است. تعداد اتصال به طرح بستگی دارد. از Hiddify استفاده کنید.\n\n💬 برای پرداخت، اتصال یا بازپرداخت با پشتیبانی یا @AERAVP تماس بگیرید. Telegram پشتیبانی خرید AERA نیست.\n\nبا زدن دکمه شرایط را می‌پذیرید و مطالعه حریم خصوصی را تأیید می‌کنید.",
    ),
    "paid_stock_empty": (
        "⏳ Свободные ссылки этого тарифа закончились или ещё проходят проверку. Новый счёт пока не выставлен. Напиши в поддержку 💊",
        "⏳ This plan's links are out of stock or being verified. No new invoice was issued. Contact support 💊",
        "⏳ 此套餐链接已用完或正在验证，尚未创建新账单。请联系支持 💊",
        "⏳ لینک‌های این طرح تمام شده یا در حال بررسی است. فاکتور جدید صادر نشد. با پشتیبانی تماس بگیرید 💊",
    ),
    "paid_link": (
        "💎 AERA готова!\n\n📦 {plan}\n🔗 Твоя личная ссылка:\n{url}\n\n⚙️ Скопируй ссылку в Hiddify: «+», затем «Добавить из буфера обмена».\n⏳ Срок начнётся после первого VPN-подключения. Он отображается в «Моём профиле». Не передавай ссылку другим людям.",
        "💎 AERA is ready!\n\n📦 {plan}\n🔗 Your personal link:\n{url}\n\n⚙️ Copy it into Hiddify: +, then Add from clipboard.\n⏳ The period starts with your first VPN connection. Check My profile for its status. Do not share your link.",
        "💎 AERA 已就绪！\n\n📦 {plan}\n🔗 您的个人链接：\n{url}\n\n⚙️ 在 Hiddify 点击 +，然后从剪贴板添加。\n⏳ 首次连接 VPN 后开始计时，在我的资料查看状态。请勿分享链接。",
        "💎 AERA آماده است!\n\n📦 {plan}\n🔗 لینک شخصی شما:\n{url}\n\n⚙️ در Hiddify روی + و افزودن از کلیپ‌بورد بزنید.\n⏳ مدت از اولین اتصال VPN شروع می‌شود. وضعیت را در پروفایل من ببینید. لینک را به دیگران ندهید.",
    ),
    "paid_ready": (
        "✅ Оплата подтверждена! Бот отправит личную ссылку сюда. Подписка доступна в «Моём профиле» 💎",
        "✅ Payment confirmed! Your personal link will be sent here. The subscription is in My profile 💎",
        "✅ 付款已确认！个人链接将发送至此，订阅信息在我的资料 💎",
        "✅ پرداخت تأیید شد! لینک شخصی اینجا ارسال می‌شود. اشتراک در پروفایل من است 💎",
    ),
    "paid_waiting": (
        "⏳ Срок начнётся после первого VPN-подключения. Длительность: {days} дней.",
        "⏳ The period starts with the first VPN connection. Duration: {days} days.",
        "⏳ 首次连接 VPN 后开始计时。时长：{days} 天。",
        "⏳ مدت از اولین اتصال VPN شروع می‌شود. مدت: {days} روز.",
    ),
    "pay_bank": ("Оплатить через СБП 💳", "Pay via SBP 💳", "通过 SBP 付款 💳", "پرداخت با SBP 💳"),
    "support_reply": (
        "💊 Ответ поддержки AERA",
        "💊 AERA support reply",
        "💊 AERA 支持回复",
        "💊 پاسخ پشتیبانی AERA",
    ),
    "coupon_notice": (
        "🎁 Друг оплатил подписку! Скидка доступна в разделе приглашений.",
        "🎁 Your friend paid! Your discount is available in the invitations section.",
        "🎁 好友已付款！您的折扣已在邀请页面中可用。",
        "🎁 دوست شما پرداخت کرد! تخفیف در بخش دعوت در دسترس است.",
    ),
    "language": ("🌐 Выберите язык / Choose your language\n选择语言 / زبان خود را انتخاب کنید",)
    * 4,
    "welcome": (
        "<b>Добро пожаловать в AERA 💎</b>\n\nAERA - VPN для ваших устройств 🌏\n\nВам понадобится HIDDIFY - клиент для запуска VPN 🚀\n\nПОМОЖЕМ С ЕГО УСТАНОВКОЙ ⚙️\n\nВЫБЕРИ ТАРИФ И МЫ ПРОВЕДЕМ ТЕБЯ ПО ШАГАМ 🔥",
        "<b>Welcome to AERA 💎</b>\n\nAERA - VPN for your devices 🌏\n\nYou will need HIDDIFY to connect your VPN 🚀\n\nWE WILL HELP YOU INSTALL IT ⚙️\n\nCHOOSE A PLAN AND WE WILL GUIDE YOU STEP BY STEP 🔥",
        "<b>欢迎来到 AERA 💎</b>\n\nAERA - 适用于您的设备的 VPN 🌏\n\n您需要 HIDDIFY 客户端来连接 VPN 🚀\n\n我们会帮助您安装 ⚙️\n\n选择套餐，我们将一步步指导您 🔥",
        "<b>به AERA خوش آمدید 💎</b>\n\nAERA - VPN برای دستگاه‌های شما 🌏\n\nبرای اتصال VPN به برنامه HIDDIFY نیاز دارید 🚀\n\nبرای نصب آن کمکتان می‌کنیم ⚙️\n\nطرح را انتخاب کنید، قدم به قدم راهنمایی‌تان می‌کنیم 🔥",
    ),
    "plans": ("ТАРИФЫ 💰", "PLANS 💰", "套餐 💰", "طرح‌ها 💰"),
    # Compact home labels fit two columns on phones. Other screens keep full titles.
    "menu_trial": ("Бесплатно 🎉", "2 days free 🎉", "免费 2 天 🎉", "۲ روز رایگان 🎉"),
    "menu_referral": ("Пригласить 🎁", "Invite 🎁", "邀请好友 🎁", "دعوت 🎁"),
    "menu_how": ("Инструкция 📡", "Guide 📡", "使用说明 📡", "راهنما 📡"),
    "menu_privacy": ("Конфиденциальность 🛡️", "Privacy 🛡️", "隐私政策 🛡️", "حریم خصوصی 🛡️"),
    "pick": ("ВЫБРАТЬ ТАРИФ 💰", "Choose a plan 💰", "选择套餐 💰", "انتخاب طرح 💰"),
    "connect": ("Подключиться 🔋", "Connect 🔋", "连接 🔋", "اتصال 🔋"),
    "how": ("Как это работает 📡", "How it works 📡", "使用说明 📡", "روش استفاده 📡"),
    "requests": ("Мои заявки 📤", "My requests 📤", "我的申请 📤", "درخواست‌های من 📤"),
    "support": ("Поддержка 💊", "Support 💊", "支持 💊", "پشتیبانی 💊"),
    "profile": ("Мой профиль 👤", "My profile 👤", "我的资料 👤", "پروفایل من 👤"),
    "referral": ("Пригласить друга 🎁", "Invite a friend 🎁", "邀请好友 🎁", "دعوت دوستان 🎁"),
    "trial": ("2 дня бесплатно 🎉", "2 days free 🎉", "免费体验 2 天 🎉", "۲ روز رایگان 🎉"),
    "privacy": ("Конфиденциальность 🛡️", "Privacy 🛡️", "隐私政策 🛡️", "حریم خصوصی 🛡️"),
    "lang": ("Язык 🌐", "Language 🌐", "语言 🌐", "زبان 🌐"),
    "home": ("Главное меню 🏠", "Main menu 🏠", "主菜单 🏠", "منوی اصلی 🏠"),
    "back": ("Назад 🧭", "Back 🧭", "返回 🧭", "بازگشت 🧭"),
    "traffic": (
        "БЕЗЛИМИТНЫЙ ТРАФИК НА ВСЕХ ТАРИФАХ 🚀🔥",
        "UNLIMITED TRAFFIC ON EVERY PLAN 🚀🔥",
        "所有套餐均提供无限流量 🚀🔥",
        "ترافیک نامحدود در تمام طرح‌ها 🚀🔥",
    ),
    "devices": ("{n} устройств", "{n} devices", "{n} 台设备", "{n} دستگاه"),
    "unlimited": ("Безлимит устройств", "Unlimited devices", "设备数量不限", "دستگاه نامحدود"),
    "month": ("{n} мес.", "{n} month(s)", "{n} 个月", "{n} ماه"),
    "business": (
        "🏢 Для компаний, офисов и команд - одна подписка для всех рабочих устройств.",
        "🏢 For businesses, offices and teams - one subscription for all work devices.",
        "🏢 适合企业、办公室和团队，一份订阅覆盖所有办公设备。",
        "🏢 برای شرکت‌ها، دفترها و تیم‌ها؛ یک اشتراک برای تمام دستگاه‌های کاری.",
    ),
    "choose_term": (
        "📅 Выбери срок подписки",
        "📅 Choose your subscription period",
        "📅 选择订阅时长",
        "📅 مدت اشتراک را انتخاب کنید",
    ),
    "connect_info": (
        "<b>AERA - VPN ДЛЯ ВАШИХ УСТРОЙСТВ 🌏🚀</b>\n\nВЫБЕРИТЕ УСТРОЙСТВО! МЫ ПОКАЖЕМ КАК УСТАНОВИТЬ КЛИЕНТ HIDDIFY И ПОЛУЧИТЬ ПОДПИСКУ 💎",
        "<b>AERA - VPN FOR YOUR DEVICES 🌏🚀</b>\n\nCHOOSE YOUR DEVICE! WE WILL SHOW YOU HOW TO INSTALL HIDDIFY AND GET YOUR SUBSCRIPTION 💎",
        "<b>AERA - 适用于您的设备的 VPN 🌏🚀</b>\n\n请选择设备！我们会指导您安装 HIDDIFY 并获取订阅 💎",
        "<b>AERA - VPN برای دستگاه‌های شما 🌏🚀</b>\n\nدستگاه خود را انتخاب کنید! نصب HIDDIFY و دریافت اشتراک را به شما نشان می‌دهیم 💎",
    ),
    "how_info": (
        "<b>Как это работает 📡</b>\n\n🚀 Hiddify - приложение для запуска твоего VPN. AERA подключается через него на iPhone, Android, Windows и macOS.\n\n💰 Выбери тариф и оплати подписку.\n🤝 Администратор поможет установить клиент и выдаст личную ссылку.\n🔗 Добавь ссылку в Hiddify и включи подключение.\n\n🛡️ Hiddify работает вместе с VPN-сервером; защита и скорость зависят также от настройки сервера и сети.",
        "<b>How it works 📡</b>\n\n🚀 Hiddify is the app that connects your VPN on iPhone, Android, Windows and macOS.\n💰 Choose and pay for a plan.\n🤝 The administrator helps you install the app and provides your personal link.\n🔗 Import the link into Hiddify and connect.\n🛡️ Privacy and speed also depend on the VPN server configuration and your network.",
        "<b>使用说明 📡</b>\n\n🚀 Hiddify 是用于连接 VPN 的客户端，支持 iPhone、Android、Windows 和 macOS。\n💰 选择套餐并付款。\n🤝 管理员会帮助您安装并提供个人链接。\n🔗 在 Hiddify 中导入链接并连接。\n🛡️ 隐私保护和速度也取决于服务器配置及网络条件。",
        "<b>روش استفاده 📡</b>\n\n🚀 Hiddify برنامه اتصال VPN برای iPhone، Android، Windows و macOS است.\n💰 طرح را انتخاب و پرداخت کنید.\n🤝 مدیر در نصب برنامه کمک می‌کند و لینک شخصی را می‌دهد.\n🔗 لینک را در Hiddify وارد و اتصال را روشن کنید.\n🛡️ امنیت و سرعت به تنظیمات سرور و شبکه هم بستگی دارد.",
    ),
    "windows": (
        "<b>💻 Hiddify для Windows</b>\n\n📥 Нажми «Скачать установщик», когда будешь готов. Файл придёт сюда.\n\n1. Скачай файл на ПК с Windows x64 и установи приложение.\n2. Скопируй свою ссылку AERA.\n3. В Hiddify нажми «+», затем «Добавить из буфера обмена».\n4. Выбери профиль AERA и включи подключение.\n\n🤝 Администратор поможет со скачиванием и настройкой.",
        "<b>💻 Hiddify for Windows</b>\n\n📥 Press Download installer when ready. The file will arrive here.\n\n1. Save it to a Windows x64 PC and install the app.\n2. Copy your AERA link.\n3. In Hiddify press +, then Import from clipboard.\n4. Select AERA and connect.\n\n🤝 The administrator will help you install and configure it.",
        "<b>💻 Windows 版 Hiddify</b>\n\n📥 准备好后点击“下载安装程序”，文件将发送到此聊天。\n1. 在 Windows x64 电脑上下载并安装。\n2. 复制您的 AERA 链接。\n3. 在 Hiddify 中点击 +，从剪贴板导入。\n4. 选择 AERA 并连接。\n🤝 管理员会帮助您安装和配置。",
        "<b>💻 Hiddify برای Windows</b>\n\n📥 وقتی آماده بودید «دریافت نصب‌کننده» را بزنید تا فایل همین‌جا ارسال شود.\n1. فایل را روی رایانه Windows x64 ذخیره و نصب کنید.\n2. لینک AERA را کپی کنید.\n3. در Hiddify روی + و وارد کردن از کلیپ‌بورد بزنید.\n4. پروفایل AERA را انتخاب و متصل شوید.\n🤝 مدیر در نصب و تنظیم کمک می‌کند.",
    ),
    "download": (
        "Скачать установщик 📥",
        "Download installer 📥",
        "下载安装程序 📥",
        "دریافت نصب‌کننده 📥",
    ),
    "installer": (
        "💻 Hiddify для Windows x64\nУстанови файл и добавь свою ссылку AERA. Администратор поможет с настройкой 🤝",
        "💻 Hiddify for Windows x64\nInstall the file and import your AERA link. The administrator can help 🤝",
        "💻 Windows x64 版 Hiddify\n安装后导入 AERA 链接，管理员可协助设置 🤝",
        "💻 Hiddify برای Windows x64\nبرنامه را نصب و لینک AERA را وارد کنید. مدیر کمک می‌کند 🤝",
    ),
    "ios": (
        "<b>🍏 Hiddify для iPhone / iPad</b>\n\n🔎 Найди Hiddify в App Store. Если приложение недоступно, для его скачивания понадобится регион «Соединённые Штаты».\n⚙️ Настройки iPhone: твоё имя, «Медиаматериалы и покупки», «Просмотреть», «Страна/регион». Смена региона может потребовать завершить подписки и потратить остаток средств.\n🤝 Когда администратор свяжется, он поможет со скачиванием и подключением. Обычно настройка занимает до 5 минут; смена региона может занять больше.\n🔗 Скопируй ссылку AERA и импортируй в Hiddify через «+».",
        "<b>🍏 Hiddify for iPhone / iPad</b>\n\n🔎 Find Hiddify in the App Store. If unavailable, use the United States store region.\n⚙️ Settings: your name, Media & Purchases, View Account, Country/Region. Changing region may require ending subscriptions and spending remaining credit.\n🤝 The administrator helps with download and connection, usually within 5 minutes. Region changes can take longer.\n🔗 Import your AERA link using + in Hiddify.",
        "<b>🍏 iPhone / iPad 版 Hiddify</b>\n\n🔎 在 App Store 搜索 Hiddify。如无法找到，请使用美国商店地区。\n⚙️ 设置：您的姓名、媒体与购买项目、查看账户、国家/地区。更改地区可能需要先结束订阅并用完余额。\n🤝 管理员会协助下载和连接，设置通常在 5 分钟内完成，地区变更可能更久。\n🔗 在 Hiddify 中点击 + 导入 AERA 链接。",
        "<b>🍏 Hiddify برای iPhone / iPad</b>\n\n🔎 Hiddify را در App Store جستجو کنید. اگر موجود نبود، منطقه فروشگاه را روی ایالات متحده بگذارید.\n⚙️ تنظیمات: نام شما، Media & Purchases، View Account، Country/Region. تغییر منطقه ممکن است نیاز به پایان اشتراک‌ها و مصرف اعتبار داشته باشد.\n🤝 مدیر برای دانلود و اتصال کمک می‌کند. تنظیم معمولاً تا ۵ دقیقه طول می‌کشد؛ تغییر منطقه ممکن است بیشتر طول بکشد.\n🔗 لینک AERA را با + در Hiddify وارد کنید.",
    ),
    "android": (
        "<b>🤖 Hiddify для Android</b>\n\n🔎 Найди Hiddify в Google Play и установи его.\n🔗 Скопируй свою ссылку AERA, нажми «+» в Hiddify и добавь её из буфера обмена.\n🚀 Выбери профиль и включи подключение.\n🤝 Администратор поможет с установкой.",
        "<b>🤖 Hiddify for Android</b>\n\n🔎 Find and install Hiddify in Google Play.\n🔗 Copy your AERA link, press + in Hiddify and import from clipboard.\n🚀 Select the profile and connect.\n🤝 The administrator can help.",
        "<b>🤖 Android 版 Hiddify</b>\n\n🔎 在 Google Play 搜索并安装 Hiddify。\n🔗 复制 AERA 链接，在 Hiddify 中点击 + 从剪贴板导入。\n🚀 选择配置并连接。\n🤝 管理员可以提供帮助。",
        "<b>🤖 Hiddify برای Android</b>\n\n🔎 Hiddify را در Google Play پیدا و نصب کنید.\n🔗 لینک AERA را کپی کنید و با + از کلیپ‌بورد وارد کنید.\n🚀 پروفایل را انتخاب و اتصال را روشن کنید.\n🤝 مدیر کمک می‌کند.",
    ),
    "macos": (
        "<b>💻 Hiddify для macOS</b>\n\n📥 Скачай версию для Mac с официального сайта Hiddify.\n🔗 Добавь свою ссылку AERA через «+» и включи подключение.\n🤝 Администратор поможет с установкой.",
        "<b>💻 Hiddify for macOS</b>\n\n📥 Download the Mac version from the official Hiddify website.\n🔗 Import your AERA link using + and connect.\n🤝 The administrator can help.",
        "<b>💻 macOS 版 Hiddify</b>\n\n📥 从 Hiddify 官方网站下载 Mac 版本。\n🔗 使用 + 导入 AERA 链接并连接。\n🤝 管理员可以协助安装。",
        "<b>💻 Hiddify برای macOS</b>\n\n📥 نسخه Mac را از وب‌سایت رسمی Hiddify دریافت کنید.\n🔗 لینک AERA را با + وارد و اتصال را روشن کنید.\n🤝 مدیر کمک می‌کند.",
    ),
    "pay": (
        "<b>Оплата подписки ⭐</b>\n\n📦 {plan}\n📅 {period}\n💰 {price} ₽\n🎁 Скидка: {discount}%\n\n📤 Заказ № {number}\n⭐ Оплата через Telegram Stars. После подтверждения бот автоматически отправит личную ссылку 💎",
        "<b>Subscription payment ⭐</b>\n\n📦 {plan}\n📅 {period}\n💰 {price} RUB\n🎁 Discount: {discount}%\n\n📤 Order #{number}\n⭐ Pay with Telegram Stars. After confirmation, your personal link arrives automatically 💎",
        "<b>订阅付款 ⭐</b>\n\n📦 {plan}\n📅 {period}\n💰 {price} 卢布\n🎁 折扣：{discount}%\n\n📤 订单 #{number}\n⭐ 使用 Telegram Stars 付款，确认后自动发送个人链接 💎",
        "<b>پرداخت اشتراک ⭐</b>\n\n📦 {plan}\n📅 {period}\n💰 {price} روبل\n🎁 تخفیف: {discount}%\n\n📤 سفارش #{number}\n⭐ با Telegram Stars پرداخت کنید. پس از تأیید لینک شخصی خودکار ارسال می‌شود 💎",
    ),
    "sbp_soon": ("СБП - СКОРО 💳", "SBP - COMING SOON 💳", "SBP - 即将推出 💳", "SBP - به‌زودی 💳"),
    "payments_soon": (
        "<b>СКОРО ✨</b>\n\n💳 СБП и 🪙 криптовалюта пока готовятся.\n⭐ Сейчас подписку можно оплатить через Telegram Stars. Личная ссылка придёт автоматически после подтверждения платежа 💎",
        "<b>COMING SOON ✨</b>\n\n💳 Bank payments and 🪙 crypto are being prepared.\n⭐ Pay with Telegram Stars now. Your personal link arrives automatically after confirmed payment 💎",
        "<b>即将推出 ✨</b>\n\n💳 银行付款和 🪙 加密货币正在准备中。\n⭐ 目前可使用 Telegram Stars，确认付款后自动发送个人链接 💎",
        "<b>به‌زودی ✨</b>\n\n💳 پرداخت بانکی و 🪙 رمزارز در حال آماده‌سازی است.\n⭐ اکنون با Telegram Stars پرداخت کنید. لینک شخصی پس از تأیید خودکار ارسال می‌شود 💎",
    ),
    "sbp": ("СБП 💳", "SBP bank payment 💳", "SBP 银行付款 💳", "پرداخت بانکی SBP 💳"),
    "sbp_info": (
        "<b>СБП 💳</b>\n\nВыбери платёжный сервис. Реквизиты и подтверждение будут доступны после подключения сервисов.",
        "<b>SBP bank payment 💳</b>\n\nChoose a provider. Payment details will be available once providers are connected.",
        "<b>SBP 银行付款 💳</b>\n\n选择付款服务商。服务接入后将提供付款信息。",
        "<b>پرداخت بانکی SBP 💳</b>\n\nارائه‌دهنده را انتخاب کنید. اطلاعات پرداخت پس از اتصال سرویس‌ها در دسترس خواهد بود.",
    ),
    "soon": (
        "<b>Скоро в AERA ✨</b>\n\nЭтот способ оплаты пока подключается. Администратор поможет с оплатой - напиши в поддержку 🤝",
        "<b>Coming to AERA soon ✨</b>\n\nThis payment method is being connected. Contact support for help paying 🤝",
        "<b>AERA 即将推出 ✨</b>\n\n此付款方式正在接入。请联系支持人员协助付款 🤝",
        "<b>به‌زودی در AERA ✨</b>\n\nاین روش پرداخت در حال آماده‌سازی است. برای پرداخت با پشتیبانی تماس بگیرید 🤝",
    ),
    "crypto": (
        "Криптовалюта - СКОРО 🪙",
        "Crypto - COMING SOON 🪙",
        "加密货币，即将推出 🪙",
        "رمزارز، به‌زودی 🪙",
    ),
    "crypto_info": (
        "<b>Криптовалюта 🪙</b>\n\nГотовим оплату криптовалютой. Поддерживаемые монеты, сети и адрес кошелька появятся здесь после подключения.",
        "<b>Cryptocurrency 🪙</b>\n\nCrypto payments are being prepared. Supported coins, networks and wallet details will appear once connected.",
        "<b>加密货币 🪙</b>\n\n正在准备加密货币付款。接入后将显示支持的币种、网络和钱包地址。",
        "<b>رمزارز 🪙</b>\n\nپرداخت رمزارزی در حال آماده‌سازی است. ارزها، شبکه‌ها و نشانی کیف پول پس از اتصال نمایش داده می‌شوند.",
    ),
    "terms": (
        "<b>Условия подписки 📋</b>\n\nПодписка AERA оплачивается на выбранный срок. Устройства ограничены тарифом, трафик безлимитный. Подключение через Hiddify выполняется с помощью администратора после подтверждённой оплаты. Срок платной подписки начинается при отметке администратором «Клиент подключён».\n\nПо оплате, подключению и возвратам обращайся в поддержку бота или @AERAVP. Telegram не ведёт поддержку покупок AERA.\n\nНажимая кнопку ниже, ты принимаешь эти условия и подтверждаешь ознакомление с политикой конфиденциальности.",
        "<b>Subscription terms 📋</b>\n\nAERA is purchased for the selected period. Device limits depend on the plan; traffic is unlimited. The administrator helps connect Hiddify after confirmed payment. The paid period begins when the administrator marks you connected.\n\nFor payments, connection and refunds, contact bot support or @AERAVP. Telegram does not support AERA purchases.\n\nPressing below accepts these terms and acknowledges the privacy policy.",
        "<b>订阅条款 📋</b>\n\nAERA 按所选时长购买，设备限制取决于套餐，流量不限。确认付款后，管理员帮助您连接 Hiddify。付费时长从管理员标记已连接时开始。\n\n付款、连接和退款问题请联系机器人支持或 @AERAVP，Telegram 不处理 AERA 购买支持。\n\n点击下方按钮表示接受条款并已阅读隐私政策。",
        "<b>شرایط اشتراک 📋</b>\n\nAERA برای مدت انتخابی خریداری می‌شود. تعداد دستگاه به طرح بستگی دارد و ترافیک نامحدود است. مدیر پس از تأیید پرداخت در اتصال Hiddify کمک می‌کند. مدت اشتراک از ثبت اتصال توسط مدیر شروع می‌شود.\n\nبرای پرداخت، اتصال و بازپرداخت با پشتیبانی یا @AERAVP تماس بگیرید. Telegram پشتیبانی خرید AERA را انجام نمی‌دهد.\n\nبا زدن دکمه، شرایط را می‌پذیرید و مطالعه سیاست حریم خصوصی را تأیید می‌کنید.",
    ),
    "accept": (
        "Прочитал и согласен ✅",
        "Read and agree ✅",
        "已阅读并同意 ✅",
        "خواندم و موافقم ✅",
    ),
    "invoice": (
        "⭐ К оплате: {n} Stars\nНажми кнопку, чтобы оплатить подписку. Рублёвая цена покупки Stars зависит от магазина и региона.",
        "⭐ Total: {n} Stars\nPress below to pay. The cost of buying Stars varies by store and region.",
        "⭐ 应付：{n} Stars\n点击下方付款。购买 Stars 的实际费用因商店和地区而异。",
        "⭐ مبلغ: {n} Stars\nبرای پرداخت دکمه را بزنید. هزینه خرید Stars به فروشگاه و منطقه بستگی دارد.",
    ),
    "pay_button": ("Оплатить ⭐", "Pay ⭐", "付款 ⭐", "پرداخت ⭐"),
    "paid": (
        "✅ Оплата подтверждена! Администратор получил уведомление и поможет подключиться 🤝",
        "✅ Payment confirmed! The administrator has been notified and will help you connect 🤝",
        "✅ 付款已确认！管理员已收到通知，会帮助您连接 🤝",
        "✅ پرداخت تأیید شد! مدیر مطلع شده و برای اتصال کمک می‌کند 🤝",
    ),
    "trial_info": (
        "<b>2 дня AERA бесплатно 🎉</b>\n\n💎 Личная пробная ссылка для одного устройства.\n⏳ 48 часов начинаются после первого подключения VPN, а не после получения ссылки.\n🚀 Трафик безлимитный.\n🎁 Одна пробная подписка на Telegram-аккаунт.\n\n⚠️ Подключись в течение часа после получения ссылки. Иначе она будет аннулирована.\n\nНажми ниже и добавь ссылку в Hiddify.",
        "<b>2 days of AERA free 🎉</b>\n\n💎 A personal trial link for one device.\n⏳ 48 hours begin with the first VPN connection, not when the link is received.\n🚀 Unlimited traffic.\n🎁 One trial per Telegram account.\n\n⚠️ Connect within one hour of receiving the link or it will be revoked.\n\nPress below and import the link into Hiddify.",
        "<b>免费体验 AERA 2 天 🎉</b>\n\n💎 一台设备专用的个人体验链接。\n⏳ 48 小时从首次连接 VPN 开始，而非领取链接时。\n🚀 流量不限。\n🎁 每个 Telegram 账户仅可体验一次。\n\n⚠️ 领取后请在一小时内连接，否则链接将失效。\n\n点击下方领取并在 Hiddify 导入。",
        "<b>۲ روز AERA رایگان 🎉</b>\n\n💎 لینک آزمایشی شخصی برای یک دستگاه.\n⏳ ۴۸ ساعت از اولین اتصال VPN شروع می‌شود، نه دریافت لینک.\n🚀 ترافیک نامحدود.\n🎁 یک دوره آزمایشی برای هر حساب Telegram.\n\n⚠️ تا یک ساعت پس از دریافت لینک متصل شوید؛ در غیر این صورت لینک لغو می‌شود.\n\nدکمه را بزنید و لینک را در Hiddify وارد کنید.",
    ),
    "get_trial": (
        "Получить ссылку 🎁",
        "Get trial link 🎁",
        "领取体验链接 🎁",
        "دریافت لینک آزمایشی 🎁",
    ),
    "trial_empty": (
        "⏳ Все пробные ссылки заняты или ещё проверяются. Администратор получил запрос - напиши в поддержку, поможем 🤝",
        "⏳ Trial links are currently unavailable or being verified. The administrator has been notified. Contact support 🤝",
        "⏳ 体验链接已用完或正在验证。管理员已收到请求，请联系支持 🤝",
        "⏳ لینک‌های آزمایشی تمام شده یا در حال بررسی هستند. مدیر مطلع شد؛ با پشتیبانی تماس بگیرید 🤝",
    ),
    "trial_used": (
        "🎁 Ты уже использовал пробную подписку. Для продолжения выбери тариф 💰",
        "🎁 You have already used your trial. Choose a plan to continue 💰",
        "🎁 您已经使用过体验订阅，请选择套餐继续使用 💰",
        "🎁 دوره آزمایشی شما قبلاً استفاده شده است. برای ادامه طرحی انتخاب کنید 💰",
    ),
    "trial_key": (
        "<b>Твоя пробная ссылка 🎁</b>\n\n🔗 <code>{link}</code>\n\n📋 Нажми на ссылку, чтобы скопировать. Добавь её в Hiddify через «+».\n⏳ Таймер включится после первого VPN-подключения. Срок появится в профиле после проверки сервера. ⚠️ Подключись в течение часа, иначе ссылка будет аннулирована.",
        "<b>Your trial link 🎁</b>\n\n🔗 <code>{link}</code>\n\n📋 Tap to copy. Import it using + in Hiddify.\n⏳ The timer starts with the first VPN connection. Your profile will update after the server check. ⚠️ Connect within one hour or the link will be revoked.",
        "<b>您的体验链接 🎁</b>\n\n🔗 <code>{link}</code>\n\n📋 点击复制，在 Hiddify 中使用 + 导入。\n⏳ 首次连接 VPN 后开始计时，服务器检查后资料页会更新。⚠️ 请在一小时内连接，否则链接将失效。",
        "<b>لینک آزمایشی شما 🎁</b>\n\n🔗 <code>{link}</code>\n\n📋 برای کپی لمس کنید و با + در Hiddify وارد کنید.\n⏳ زمان از اولین اتصال VPN آغاز می‌شود و پس از بررسی سرور در پروفایل نمایش داده می‌شود. ⚠️ تا یک ساعت متصل شوید؛ وگرنه لینک لغو می‌شود.",
    ),
    "none": ("Пока нет подписки", "No subscription yet", "暂无订阅", "هنوز اشتراکی ندارید"),
    "ACTIVE": ("Активна ✅", "Active ✅", "有效 ✅", "فعال ✅"),
    "WAITING": (
        "Ожидает первого подключения ⏳",
        "Awaiting first connection ⏳",
        "等待首次连接 ⏳",
        "در انتظار اولین اتصال ⏳",
    ),
    "EXPIRED": ("Срок закончился ⌛", "Expired ⌛", "已到期 ⌛", "منقضی شده ⌛"),
    "BLOCKED": (
        "Ссылка временно отключена в панели ⚙️",
        "The link is temporarily disabled on the server ⚙️",
        "服务器上的链接暂时停用 ⚙️",
        "لینک در سرور موقتاً غیرفعال است ⚙️",
    ),
    "ACTIVATING": (
        "Подключение обнаружено, подтверждаем срок ⚙️",
        "Connection detected, confirming your expiry ⚙️",
        "已检测到连接，正在确认到期时间 ⚙️",
        "اتصال شناسایی شد، در حال تأیید زمان پایان ⚙️",
    ),
    "trial_confirming": (
        "⚙️ Сервер подтверждает 48 часов с первого использования. Обнови профиль через минуту.",
        "⚙️ Confirming 48 hours from first use. Refresh your profile in a minute.",
        "⚙️ 正在确认首次使用起的 48 小时。请一分钟后刷新资料。",
        "⚙️ در حال تأیید ۴۸ ساعت از اولین استفاده. یک دقیقه دیگر پروفایل را تازه کنید.",
    ),
    "UNKNOWN": (
        "Проверяем состояние сервера ⏳",
        "Checking the server ⏳",
        "正在检查服务器 ⏳",
        "در حال بررسی سرور ⏳",
    ),
    "profile_info": (
        "<b>Мой профиль 👤</b>\n\n👋 {name}\n💎 {status}\n📦 {plan}\n🚀 Трафик: безлимитный\n\n{expiry}",
        "<b>My profile 👤</b>\n\n👋 {name}\n💎 {status}\n📦 {plan}\n🚀 Traffic: unlimited\n\n{expiry}",
        "<b>我的资料 👤</b>\n\n👋 {name}\n💎 {status}\n📦 {plan}\n🚀 流量：无限\n\n{expiry}",
        "<b>پروفایل من 👤</b>\n\n👋 {name}\n💎 {status}\n📦 {plan}\n🚀 ترافیک: نامحدود\n\n{expiry}",
    ),
    "expiry": (
        "📅 Действует до: {date} UTC\n⏰ Осталось: {days} д. {hours} ч.",
        "📅 Valid until: {date} UTC\n⏰ Remaining: {days} d {hours} h",
        "📅 有效至：{date} UTC\n⏰ 剩余：{days} 天 {hours} 小时",
        "📅 اعتبار تا: {date} UTC\n⏰ باقی‌مانده: {days} روز {hours} ساعت",
    ),
    "waiting_time": (
        "⏳ 48 часов начнутся после первого VPN-подключения.",
        "⏳ 48 hours start after your first VPN connection.",
        "⏳ 首次连接 VPN 后开始 48 小时计时。",
        "⏳ ۴۸ ساعت از اولین اتصال VPN شروع می‌شود.",
    ),
    "my_link": ("Моя ссылка 🔗", "My connection link 🔗", "我的连接链接 🔗", "لینک اتصال من 🔗"),
    "no_link": (
        "🔗 Администратор добавит твою личную ссылку после подключения. Напиши в поддержку, если нужна помощь 🤝",
        "🔗 The administrator will add your personal link after connection. Contact support if needed 🤝",
        "🔗 管理员会在连接后添加您的个人链接，需要帮助请联系支持 🤝",
        "🔗 مدیر پس از اتصال لینک شخصی را اضافه می‌کند. برای کمک با پشتیبانی تماس بگیرید 🤝",
    ),
    "ref_info": (
        "<b>Приглашай друзей с AERA 🎁</b>\n\n👥 Приглашено: {invited}\n✅ Оплатили от 500 ₽: {qualified}\n🎟️ Доступных скидок: {coupons}\n\nДруг покупает подписку на 500 ₽ или больше - после подтверждения оплаты тебе доступна скидка на следующую покупку:\n💎 50% на тариф до 500 ₽ включительно;\n💎 25% на тариф от 501 до 1000 ₽.\n\nОдна награда за друга. Скидки не суммируются и не применяются к тарифам дороже 1000 ₽.\n\n🔗 Твоя ссылка:\n{url}",
        "<b>Invite friends with AERA 🎁</b>\n\n👥 Invited: {invited}\n✅ Paid at least 500 RUB: {qualified}\n🎟️ Available discounts: {coupons}\n\nAfter a friend pays at least 500 RUB, you receive a next-purchase discount:\n💎 50% on plans up to 500 RUB;\n💎 25% on plans over 500 and up to 1000 RUB.\n\nOne reward per friend. Discounts do not stack or apply to plans above 1000 RUB.\n\n🔗 Your invitation:\n{url}",
        "<b>邀请好友加入 AERA 🎁</b>\n\n👥 已邀请：{invited}\n✅ 已支付至少 500 卢布：{qualified}\n🎟️ 可用折扣：{coupons}\n\n好友支付至少 500 卢布后，您可获下一次购买折扣：\n💎 不超过 500 卢布的套餐享 50% 折扣；\n💎 超过 500 且不超过 1000 卢布的套餐享 25% 折扣。\n\n每位好友仅奖励一次。折扣不可叠加，不适用于超过 1000 卢布的套餐。\n\n🔗 您的邀请链接：\n{url}",
        "<b>دوستان را به AERA دعوت کنید 🎁</b>\n\n👥 دعوت‌شده: {invited}\n✅ پرداخت حداقل ۵۰۰ روبل: {qualified}\n🎟️ تخفیف موجود: {coupons}\n\nپس از پرداخت حداقل ۵۰۰ روبل توسط دوست، برای خرید بعدی تخفیف دارید:\n💎 ۵۰٪ برای طرح تا ۵۰۰ روبل؛\n💎 ۲۵٪ برای طرح بیش از ۵۰۰ تا ۱۰۰۰ روبل.\n\nیک پاداش برای هر دوست. تخفیف‌ها جمع نمی‌شوند و برای طرح بالای ۱۰۰۰ روبل نیستند.\n\n🔗 لینک دعوت شما:\n{url}",
    ),
    "support_info": (
        "<b>Мы рядом 💊</b>\n\n💬 Напиши вопрос одним сообщением. Администратор ответит здесь.\n🔐 Не отправляй пароли от банков и коды подтверждения.",
        "<b>We are here to help 💊</b>\n\n💬 Send your question in one message. The administrator will reply here.\n🔐 Do not send banking passwords or verification codes.",
        "<b>我们随时协助您 💊</b>\n\n💬 请在一条消息中描述问题，管理员将在此回复。\n🔐 请勿发送银行密码或验证码。",
        "<b>برای کمک کنار شما هستیم 💊</b>\n\n💬 سوال را در یک پیام بفرستید. مدیر همین‌جا پاسخ می‌دهد.\n🔐 رمز بانکی یا کد تأیید نفرستید.",
    ),
    "ticket_sent": (
        "📤 Обращение № {number} отправлено. Ответ придёт сюда 🤝",
        "📤 Request #{number} sent. The reply will arrive here 🤝",
        "📤 请求 #{number} 已发送，回复将在此显示 🤝",
        "📤 درخواست #{number} ارسال شد. پاسخ همین‌جا می‌آید 🤝",
    ),
    "empty_requests": (
        "📤 Оплаченных заявок пока нет. После оплаты заявка появится здесь 💎",
        "📤 No paid requests yet. Your request will appear here after payment 💎",
        "📤 暂无已付款申请。付款后申请将显示在这里 💎",
        "📤 هنوز درخواست پرداخت‌شده‌ای ندارید. پس از پرداخت، درخواست اینجا نمایش داده می‌شود 💎",
    ),
    "purchase_record": (
        "✅ Оплачено · № {number}\n📦 {plan} · {period}\n💳 {amount}\n📅 {date}",
        "✅ Paid · #{number}\n📦 {plan} · {period}\n💳 {amount}\n📅 {date}",
        "✅ 已付款 · #{number}\n📦 {plan} · {period}\n💳 {amount}\n📅 {date}",
        "✅ پرداخت شده · #{number}\n📦 {plan} · {period}\n💳 {amount}\n📅 {date}",
    ),
    "previous": ("📄 Предыдущие", "📄 Previous", "📄 上一页", "📄 قبلی"),
    "next": ("Следующие 📄", "Next 📄", "下一页 📄", "بعدی 📄"),
    "request_open": ("В работе ⏳", "In progress ⏳", "处理中 ⏳", "در حال بررسی ⏳"),
    "request_connected": ("Подключён ✅", "Connected ✅", "已连接 ✅", "متصل ✅"),
    "refresh": ("Обновить 🔄", "Refresh 🔄", "刷新 🔄", "تازه‌سازی 🔄"),
    "error": (
        "⚙️ Не получилось выполнить действие. Попробуй ещё раз или напиши в поддержку 💊",
        "⚙️ Unable to complete this action. Retry or contact support 💊",
        "⚙️ 操作未完成，请重试或联系支持 💊",
        "⚙️ انجام نشد. دوباره تلاش کنید یا با پشتیبانی تماس بگیرید 💊",
    ),
}


# Public review documents and contact are available without a purchase.
COPY["terms"] = COPY["terms_auto"] = (
    '<b>Условия подписки</b>\n\nОплата за выбранный тариф и срок. Итоговая сумма показана до оплаты. После подтверждения платежа выдаётся личная ссылка. Один заказ — не более 10 000 ₽. Нажимая кнопку оплаты, вы принимаете пользовательское соглашение.\n\n<a href="https://aera-reserve.duckdns.org/terms?lang=ru">Полное соглашение</a> · <a href="https://aera-reserve.duckdns.org/privacy?lang=ru">Политика конфиденциальности</a>\n\n@AERAVP',
    '<b>Subscription terms</b>\n\nPay for the selected plan and period. The final amount is shown before payment. A personal link is delivered after confirmed payment. Each order is limited to RUB 10,000. Pressing Pay accepts the user agreement.\n\n<a href="https://aera-reserve.duckdns.org/terms?lang=en">Full agreement</a> · <a href="https://aera-reserve.duckdns.org/privacy?lang=en">Privacy policy</a>\n\n@AERAVP',
    '<b>订阅条款</b>\n\n付款前显示套餐、期限和最终金额。确认付款后提供个人链接。每笔订单不超过 10,000 卢布。点击付款即接受用户协议。\n\n<a href="https://aera-reserve.duckdns.org/terms?lang=zh">用户协议</a> · <a href="https://aera-reserve.duckdns.org/privacy?lang=zh">隐私政策</a>\n\n@AERAVP',
    '<b>شرایط اشتراک</b>\n\nطرح، مدت و مبلغ نهایی پیش از پرداخت نمایش داده می\u200cشود. پس از تأیید پرداخت لینک شخصی ارائه می\u200cشود. سقف هر سفارش ۱۰٬۰۰۰ روبل است. با پرداخت توافق\u200cنامه را می\u200cپذیرید.\n\n<a href="https://aera-reserve.duckdns.org/terms?lang=fa">توافق\u200cنامه</a> · <a href="https://aera-reserve.duckdns.org/privacy?lang=fa">سیاست حریم خصوصی</a>\n\n@AERAVP',
)


def text(key, lang="ru", **values):
    return COPY[key][("ru", "en", "zh", "fa").index(lang if lang in LANGUAGES else "ru")].format(
        **values
    )


def period(plan, lang):
    return text("month", lang, n=plan.duration_months)


def devices(plan, lang):
    if lang == "ru" and not plan.unlimited_devices:
        from app.bot.presentation import devices_label

        return devices_label(plan.device_limit)
    return (
        text("unlimited", lang)
        if plan.unlimited_devices
        else text("devices", lang, n=plan.device_limit)
    )
