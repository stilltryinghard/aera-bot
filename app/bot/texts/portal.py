"""Current customer copy; each key is defined once in all four languages."""

LANGUAGES = {"ru": "🇷🇺 Русский", "en": "🇬🇧 English", "zh": "🇨🇳 中文", "fa": "🇮🇷 فارسی"}

COPY = {
    "trial_timeout": (
        "⏳ За час пробное подключение не было активировано. Старый ключ отключён и убран из "
        "профиля.\n"
        "🎁 Открой «Бесплатно 5 дней» для нового ключа и ещё одного часа на подключение. Пять дней начнутся после первого подключения.",
        "⏳ Your trial was not activated within one hour. The link has been revoked and removed "
        "from your profile.\n"
        "🎁 Open the free trial section for a new key and another hour to connect. The five days begin after first use.",
        "⏳ 一小时内未连接，旧密钥已停用。\n🎁 重新打开免费试用以获取新密钥和一小时连接时间。首次连接后开始五天试用。",
        "⏳ دوره آزمایشی تا یک ساعت فعال نشد. لینک لغو و از پروفایل حذف شد.\n"
        "🎁 بخش آزمایشی رایگان را دوباره باز کنید تا کلید جدید و یک ساعت برای اتصال دریافت کنید. دوره پنج‌روزه پس از اولین اتصال آغاز می‌شود.",
    ),
    "trial_finished": (
        "⌛ Пробная подписка завершилась. Пробная ссылка отключена.\n"
        "💎 Продолжи пользоваться AERA - выбери тариф в меню 💰",
        "⌛ Your trial has ended. The trial link has been revoked.\n"
        "💎 Keep using AERA by choosing a plan 💰",
        "⌛ 试用已结束，试用链接已失效。\n💎 选择套餐继续使用 AERA 💰",
        "⌛ دوره آزمایشی پایان یافت و لینک لغو شد.\n💎 برای ادامه AERA یک طرح انتخاب کنید 💰",
    ),
    "paid_stock_empty": (
        "⏳ Свободные ссылки этого тарифа закончились или ещё проходят проверку. Новый счёт "
        "пока не выставлен. Напиши в поддержку 💊",
        "⏳ This plan's links are out of stock or being verified. No new invoice was issued. "
        "Contact support 💊",
        "⏳ 此套餐链接已用完或正在验证，尚未创建新账单。请联系支持 💊",
        "⏳ لینک\u200cهای این طرح تمام شده یا در حال بررسی است. فاکتور جدید صادر نشد. با "
        "پشتیبانی تماس بگیرید 💊",
    ),
    "paid_link": ('<b>Спасибо за покупку! 🤝</b>\n<b>🔑 Ваш ключ:</b>\n<blockquote><code>{url}</code></blockquote>\n\n🕊 Не забывайте подписываться на <a href="https://t.me/aeraavpn">наш канал</a>, там всегда актуальные новости!\n\n<b>AERA — На связи. На ваших условиях.</b>', '<b>Thank you for your purchase! 🤝</b>\n<b>🔑 Your key:</b>\n<blockquote><code>{url}</code></blockquote>\n<b>AERA — Stay connected. On your terms.</b>', '<b>感谢购买！🤝</b>\n<b>🔑 您的密钥：</b>\n<blockquote><code>{url}</code></blockquote>\n<b>AERA — 随时在线，自由掌控。</b>', '<b>از خرید شما سپاسگزاریم! 🤝</b>\n<b>🔑 کلید شما:</b>\n<blockquote><code>{url}</code></blockquote>\n<b>AERA — در ارتباط، با شرایط شما.</b>'),
    "paid_ready": (
        "✅ Оплата подтверждена! Бот отправит личную ссылку сюда. Подписка доступна в «Моём профиле» "
        "💎",
        "✅ Payment confirmed! Your personal link will be sent here. The subscription is in My "
        "account 💎",
        "✅ 付款已确认！个人链接将发送至此，订阅信息在我的资料 💎",
        "✅ پرداخت تأیید شد! لینک شخصی اینجا ارسال می\u200cشود. اشتراک در پروفایل من است 💎",
    ),
    "paid_waiting": (
        "⏳ Срок начнётся после первого VPN-подключения. Длительность: {days} дней.",
        "⏳ The period starts with the first VPN connection. Duration: {days} days.",
        "⏳ 首次连接 VPN 后开始计时。时长：{days} 天。",
        "⏳ مدت از اولین اتصال VPN شروع می\u200cشود. مدت: {days} روز.",
    ),
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
    "language": (
        "🌐 Выберите язык / Choose your language\n选择语言 / زبان خود را انتخاب کنید",
        "🌐 Выберите язык / Choose your language\n选择语言 / زبان خود را انتخاب کنید",
        "🌐 Выберите язык / Choose your language\n选择语言 / زبان خود را انتخاب کنید",
        "🌐 Выберите язык / Choose your language\n选择语言 / زبان خود را انتخاب کنید",
    ),
    "welcome": (
        "<b>Твой VPN на каждый день.</b>\n"
        "\n"
        "♾ Безлимитный трафик.\n"
        "🎁 5 дней бесплатно — попробуй перед покупкой.\n"
        "📖 Простая инструкция подключения.\n"
        "💬 Помощь с настройкой — @aerasuppbot.\n"
        "\n"
        "<b>На связи. На ваших условиях.</b>",
        "💎 <b>AERA VPN</b>\n\nConnect and manage your subscription below.",
        "💎 <b>AERA VPN</b>\n\n在下方连接并管理订阅。",
        "💎 <b>AERA VPN</b>\n\nاتصال و مدیریت اشتراک در پایین.",
    ),
    "plans": ("ТАРИФЫ 💰", "PLANS 💰", "套餐 💰", "طرح\u200cها 💰"),
    "menu_trial": ("🎉 БЕСПЛАТНО", "5 days free 🎉", "免费 5 天 🎉", "۵ روز رایگان 🎉"),
    "pick": ("Выбрать тариф 💰", "Choose a plan 💰", "选择套餐 💰", "انتخاب طرح 💰"),
    "connect": ("Подключиться 🔋", "Connect 🔋", "连接 🔋", "اتصال 🔋"),
    "how": ("Как это работает 📡", "How it works 📡", "使用说明 📡", "روش استفاده 📡"),
    "requests": ("История покупок 🧾", "Purchase history 🧾", "购买记录 🧾", "تاریخچه خرید 🧾"),
    "support": ("Поддержка 💊", "Support 💊", "支持 💊", "پشتیبانی 💊"),
    "profile": ("Личный кабинет 👤", "My account 👤", "我的资料 👤", "پروفایل من 👤"),
    "referral": ("Пригласить друга 🎁", "Invite a friend 🎁", "邀请好友 🎁", "دعوت دوستان 🎁"),
    "trial": ("5 дней бесплатно 🎉", "5 days free 🎉", "免费体验 5 天 🎉", "۵ روز رایگان 🎉"),
    "privacy": ("Конфиденциальность 🛡️", "Privacy 🛡️", "隐私政策 🛡️", "حریم خصوصی 🛡️"),
    "lang": ("Язык 🌐", "Language 🌐", "语言 🌐", "زبان 🌐"),
    "home": ("Главное меню 🏠", "Main menu 🏠", "主菜单 🏠", "منوی اصلی 🏠"),
    "back": ("Назад 🧭", "Back 🧭", "返回 🧭", "بازگشت 🧭"),
    "traffic": (
        "БЕЗЛИМИТНЫЙ ТРАФИК НА ВСЕХ ТАРИФАХ 🚀🔥",
        "UNLIMITED TRAFFIC ON EVERY PLAN 🚀🔥",
        "所有套餐均提供无限流量 🚀🔥",
        "ترافیک نامحدود در تمام طرح\u200cها 🚀🔥",
    ),
    "devices": ("{n} устройств", "{n} devices", "{n} 台设备", "{n} دستگاه"),
    "unlimited": ("Безлимит устройств", "Unlimited devices", "设备数量不限", "دستگاه نامحدود"),
    "month": ("{n} мес.", "{n} month(s)", "{n} 个月", "{n} ماه"),
    "business": (
        "🏢 Для компаний, офисов и команд - одна подписка для всех рабочих устройств.",
        "🏢 For businesses, offices and teams - one subscription for all work devices.",
        "🏢 适合企业、办公室和团队，一份订阅覆盖所有办公设备。",
        "🏢 برای شرکت\u200cها، دفترها و تیم\u200cها؛ یک اشتراک برای تمام دستگاه\u200cهای کاری.",
    ),
    "choose_term": (
        "📅 Выбери срок подписки",
        "📅 Choose your subscription period",
        "📅 选择订阅时长",
        "📅 مدت اشتراک را انتخاب کنید",
    ),
    "connect_info": ("🚀 <b>ПОДКЛЮЧЕНИЕ ЗА 5 МИНУТ</b>\n<b>В 3 простых шага 👇</b>\n\n1️⃣ Установи INCY / Happ для своего устройства.\n2️⃣ Скопируй ссылку в личном кабинете.\n3️⃣ Добавь её в INCY / Happ из буфера и включи VPN.\n\n🎁 Первый раз с нами? Начни бесплатно!\n📱 Выбери своё устройство:",
        "<b>🚀 Connect</b>\n\n1. Install INCY / Happ for your device.\n2. Copy your personal link from My account.\n3. Import it from the clipboard and enable VPN.\n\nChoose your device:",
        "<b>🚀 连接</b>\n\n安装 INCY / Happ，复制账户中的 个人链接，从剪贴板导入并开启 VPN。",
        "<b>🚀 اتصال</b>\n\nINCY / Happ را نصب کنید، لینک شخصی حساب را کپی و از کلیپ‌بورد وارد کنید، سپس VPN را روشن کنید.",),
    "how_info": ("<b>Как это работает 📡</b>\n\n🚀 INCY / Happ - приложение для запуска твоего VPN. AERA подключается через него на iPhone, Android, Windows и macOS.\n\n💰 Выбери тариф и оплати подписку.\n✅ После подтверждения оплаты бот выдаст личную HTTPS-ссылку. С настройкой поможет @aerasuppbot.\n🔗 Добавь ссылку в INCY / Happ и включи подключение.\n\n🛡️ INCY / Happ работает вместе с VPN-сервером; защита и скорость зависят также от настройки сервера и сети.",
        "<b>How it works 📡</b>\n\n🚀 INCY / Happ is the app that connects your VPN on iPhone, Android, Windows and macOS.\n💰 Choose and pay for a plan.\n✅ After confirmed payment, the bot sends your personal HTTPS link. Setup help: @aerasuppbot.\n🔗 Import the link into INCY / Happ and connect.\n🛡️ Privacy and speed also depend on the VPN server configuration and your network.",
        "<b>使用说明 📡</b>\n\n🚀 INCY / Happ 是用于连接 VPN 的客户端，支持 iPhone、Android、Windows 和 macOS。\n💰 选择套餐并付款。\n✅ 确认付款后，机器人会发送个人 HTTPS 链接。设置帮助：@aerasuppbot。\n🔗 在 INCY / Happ 中导入链接并连接。\n🛡️ 隐私保护和速度也取决于服务器配置及网络条件。",
        "<b>روش استفاده 📡</b>\n\n🚀 INCY / Happ برنامه اتصال VPN برای iPhone، Android، Windows و macOS است.\n💰 طرح را انتخاب و پرداخت کنید.\n✅ پس از تأیید پرداخت، ربات لینک شخصی HTTPS را می‌فرستد. راهنمای تنظیم: @aerasuppbot.\n🔗 لینک را در INCY / Happ وارد و اتصال را روشن کنید.\n🛡️ امنیت و سرعت به تنظیمات سرور و شبکه هم بستگی دارد.",),
    "windows": ("<b>📲 INCY и Happ для Windows</b>\n\n1. Установи INCY или Happ по кнопке ниже.\n2. Скопируй личную HTTPS-ссылку из кабинета AERA.\n3. В приложении нажми «+» и добавь ссылку из буфера обмена.\n4. Выбери сервер AERA, включи VPN и разреши подключение, если система спросит.\n\n💬 Помощь: @aerasuppbot.",
        "<b>📲 INCY and Happ for Windows</b>\n\n1. Install INCY or Happ using a button below.\n2. Copy your personal HTTPS link from your AERA account.\n3. Tap + in the app and import from clipboard.\n4. Select an AERA server, connect and approve the system VPN prompt.\n\nHelp: @aerasuppbot.",
        "<b>📲 Windows：INCY / Happ</b>\n\n1. 点击下方按钮安装 INCY 或 Happ。\n2. 复制 AERA 账户中的个人 HTTPS 链接。\n3. 在客户端点击 +，从剪贴板导入。\n4. 选择 AERA 服务器，开启 VPN 并允许系统连接请求。\n\n支持：@aerasuppbot。",
        "<b>📲 INCY و Happ برای Windows</b>\n\n۱. با دکمه پایین INCY یا Happ را نصب کنید.\n۲. لینک شخصی HTTPS حساب AERA را کپی کنید.\n۳. در برنامه + را بزنید و از کلیپ‌بورد وارد کنید.\n۴. سرور AERA را انتخاب، اتصال را روشن و درخواست VPN سیستم را تأیید کنید.\n\nپشتیبانی: @aerasuppbot.",),
    "download": (
        "Скачать установщик 📥",
        "Download installer 📥",
        "下载安装程序 📥",
        "دریافت نصب\u200cکننده 📥",
    ),
    "installer": ("<b>📲 INCY и Happ для Windows</b>\n\n1. Установи INCY или Happ по кнопке ниже.\n2. Скопируй личную HTTPS-ссылку из кабинета AERA.\n3. В приложении нажми «+» и добавь ссылку из буфера обмена.\n4. Выбери сервер AERA, включи VPN и разреши подключение, если система спросит.\n\n💬 Помощь: @aerasuppbot.",
        "<b>📲 INCY and Happ for Windows</b>\n\n1. Install INCY or Happ using a button below.\n2. Copy your personal HTTPS link from your AERA account.\n3. Tap + in the app and import from clipboard.\n4. Select an AERA server, connect and approve the system VPN prompt.\n\nHelp: @aerasuppbot.",
        "<b>📲 Windows：INCY / Happ</b>\n\n1. 点击下方按钮安装 INCY 或 Happ。\n2. 复制 AERA 账户中的个人 HTTPS 链接。\n3. 在客户端点击 +，从剪贴板导入。\n4. 选择 AERA 服务器，开启 VPN 并允许系统连接请求。\n\n支持：@aerasuppbot。",
        "<b>📲 INCY و Happ برای Windows</b>\n\n۱. با دکمه پایین INCY یا Happ را نصب کنید.\n۲. لینک شخصی HTTPS حساب AERA را کپی کنید.\n۳. در برنامه + را بزنید و از کلیپ‌بورد وارد کنید.\n۴. سرور AERA را انتخاب، اتصال را روشن و درخواست VPN سیستم را تأیید کنید.\n\nپشتیبانی: @aerasuppbot.",),
    "ios": ("<b>📲 INCY и Happ для iPhone / iPad</b>\n\n1. Установи INCY или Happ по кнопке ниже.\n2. Скопируй личную HTTPS-ссылку из кабинета AERA.\n3. В приложении нажми «+» и добавь ссылку из буфера обмена.\n4. Выбери сервер AERA, включи VPN и разреши подключение, если система спросит.\n\n💬 Помощь: @aerasuppbot.",
        "<b>📲 INCY and Happ for iPhone / iPad</b>\n\n1. Install INCY or Happ using a button below.\n2. Copy your personal HTTPS link from your AERA account.\n3. Tap + in the app and import from clipboard.\n4. Select an AERA server, connect and approve the system VPN prompt.\n\nHelp: @aerasuppbot.",
        "<b>📲 iPhone / iPad：INCY / Happ</b>\n\n1. 点击下方按钮安装 INCY 或 Happ。\n2. 复制 AERA 账户中的个人 HTTPS 链接。\n3. 在客户端点击 +，从剪贴板导入。\n4. 选择 AERA 服务器，开启 VPN 并允许系统连接请求。\n\n支持：@aerasuppbot。",
        "<b>📲 INCY و Happ برای iPhone / iPad</b>\n\n۱. با دکمه پایین INCY یا Happ را نصب کنید.\n۲. لینک شخصی HTTPS حساب AERA را کپی کنید.\n۳. در برنامه + را بزنید و از کلیپ‌بورد وارد کنید.\n۴. سرور AERA را انتخاب، اتصال را روشن و درخواست VPN سیستم را تأیید کنید.\n\nپشتیبانی: @aerasuppbot.",),
    "android": ("<b>📲 INCY и Happ для Android</b>\n\n1. Установи INCY или Happ по кнопке ниже.\n2. Скопируй личную HTTPS-ссылку из кабинета AERA.\n3. В приложении нажми «+» и добавь ссылку из буфера обмена.\n4. Выбери сервер AERA, включи VPN и разреши подключение, если система спросит.\n\n💬 Помощь: @aerasuppbot.",
        "<b>📲 INCY and Happ for Android</b>\n\n1. Install INCY or Happ using a button below.\n2. Copy your personal HTTPS link from your AERA account.\n3. Tap + in the app and import from clipboard.\n4. Select an AERA server, connect and approve the system VPN prompt.\n\nHelp: @aerasuppbot.",
        "<b>📲 Android：INCY / Happ</b>\n\n1. 点击下方按钮安装 INCY 或 Happ。\n2. 复制 AERA 账户中的个人 HTTPS 链接。\n3. 在客户端点击 +，从剪贴板导入。\n4. 选择 AERA 服务器，开启 VPN 并允许系统连接请求。\n\n支持：@aerasuppbot。",
        "<b>📲 INCY و Happ برای Android</b>\n\n۱. با دکمه پایین INCY یا Happ را نصب کنید.\n۲. لینک شخصی HTTPS حساب AERA را کپی کنید.\n۳. در برنامه + را بزنید و از کلیپ‌بورد وارد کنید.\n۴. سرور AERA را انتخاب، اتصال را روشن و درخواست VPN سیستم را تأیید کنید.\n\nپشتیبانی: @aerasuppbot.",),
    "macos": ("<b>📲 INCY и Happ для macOS</b>\n\n1. Установи INCY или Happ по кнопке ниже.\n2. Скопируй личную HTTPS-ссылку из кабинета AERA.\n3. В приложении нажми «+» и добавь ссылку из буфера обмена.\n4. Выбери сервер AERA, включи VPN и разреши подключение, если система спросит.\n\n💬 Помощь: @aerasuppbot.",
        "<b>📲 INCY and Happ for macOS</b>\n\n1. Install INCY or Happ using a button below.\n2. Copy your personal HTTPS link from your AERA account.\n3. Tap + in the app and import from clipboard.\n4. Select an AERA server, connect and approve the system VPN prompt.\n\nHelp: @aerasuppbot.",
        "<b>📲 macOS：INCY / Happ</b>\n\n1. 点击下方按钮安装 INCY 或 Happ。\n2. 复制 AERA 账户中的个人 HTTPS 链接。\n3. 在客户端点击 +，从剪贴板导入。\n4. 选择 AERA 服务器，开启 VPN 并允许系统连接请求。\n\n支持：@aerasuppbot。",
        "<b>📲 INCY و Happ برای macOS</b>\n\n۱. با دکمه پایین INCY یا Happ را نصب کنید.\n۲. لینک شخصی HTTPS حساب AERA را کپی کنید.\n۳. در برنامه + را بزنید و از کلیپ‌بورد وارد کنید.\n۴. سرور AERA را انتخاب، اتصال را روشن و درخواست VPN سیستم را تأیید کنید.\n\nپشتیبانی: @aerasuppbot.",),
    "pay": (
        "<b>Оплата подписки</b>\n"
        "\n"
        "📦 Ваш тариф: {plan}\n"
        "📅 {period}\n"
        "💰 {price} ₽\n"
        "🎁 Скидка: {discount}%\n"
        "\n"
        "📤 Заказ № {number}\n"
        "Оплата любым удобным для вас способом — выберите его ниже. После подтверждения бот автоматически "
        "отправит личную ссылку 💎",
        "<b>Subscription payment</b>\n"
        "\n"
        "📦 {plan}\n"
        "📅 {period}\n"
        "💰 {price} RUB\n"
        "🎁 Discount: {discount}%\n"
        "\n"
        "📤 Order #{number}\n"
        "Pay using any available method below. After confirmation, your personal link arrives automatically "
        "💎",
        "<b>订阅付款</b>\n"
        "\n"
        "📦 {plan}\n"
        "📅 {period}\n"
        "💰 {price} 卢布\n"
        "🎁 折扣：{discount}%\n"
        "\n"
        "📤 订单 #{number}\n"
        "请选择下方可用付款方式。确认后自动发送个人 HTTPS 链接 💎",
        "<b>پرداخت اشتراک</b>\n"
        "\n"
        "📦 {plan}\n"
        "📅 {period}\n"
        "💰 {price} روبل\n"
        "🎁 تخفیف: {discount}%\n"
        "\n"
        "📤 سفارش #{number}\n"
        "یکی از روش\u200cهای پرداخت زیر را انتخاب کنید. پس از تأیید، لینک شخصی HTTPS ارسال می\u200cشود 💎",
    ),
    "sbp": ("СБП 💳", "SBP bank payment 💳", "SBP 银行付款 💳", "پرداخت بانکی SBP 💳"),
    "soon": (
        "<b>Скоро в AERA ✨</b>\n"
        "\n"
        "Этот способ оплаты пока подключается. Администратор поможет с оплатой - напиши в поддержку 🤝",
        "<b>Coming to AERA soon ✨</b>\n"
        "\n"
        "This payment method is being connected. Contact support for help paying 🤝",
        "<b>AERA 即将推出 ✨</b>\n\n此付款方式正在接入。请联系支持人员协助付款 🤝",
        "<b>به\u200cزودی در AERA ✨</b>\n"
        "\n"
        "این روش پرداخت در حال آماده\u200cسازی است. برای پرداخت با پشتیبانی تماس بگیرید 🤝",
    ),
    "crypto": (
        "Криптовалюта - СКОРО 🪙",
        "Crypto - COMING SOON 🪙",
        "加密货币，即将推出 🪙",
        "رمزارز، به\u200cزودی 🪙",
    ),
    "terms": ('<b>Условия подписки</b>\nСрок: 30, 90, 180 или 360 дней; от 1 до 10 устройств. Выбранные параметры и итоговая сумма показаны до оплаты. После подтверждённого платежа выдаётся отдельная личная HTTPS-ссылка. Срок новой подписки начинается с первого подтверждённого подключения. Один заказ — не более 10 000 ₽. Ранее оплаченные подписки сохраняют свои условия. Нажимая кнопку оплаты, вы принимаете пользовательское соглашение.\n<a href="https://aera-reserve.duckdns.org/terms?lang=ru">Полное соглашение</a> · <a href="https://aera-reserve.duckdns.org/privacy?lang=ru">Политика конфиденциальности</a>\n@aerasuppbot', '<b>Subscription terms</b>\nChoose 30, 90, 180 or 360 days and 1 to 10 devices. The selected parameters and final amount are shown before payment. Confirmed payment delivers a separate personal HTTPS link. A new subscription begins after confirmed first use. Each order is limited to RUB 10,000. Previously paid subscriptions retain their terms. Pressing Pay accepts the user agreement.\n<a href="https://aera-reserve.duckdns.org/terms?lang=en">Full agreement</a> · <a href="https://aera-reserve.duckdns.org/privacy?lang=en">Privacy policy</a>\n@aerasuppbot', '<b>订阅条款</b>\n\n付款前显示套餐、期限和最终金额。确认付款后提供个人链接。每笔订单不超过 10,000 卢布。点击付款即接受用户协议。\n\n<a href="https://aera-reserve.duckdns.org/terms?lang=zh">用户协议</a> · <a href="https://aera-reserve.duckdns.org/privacy?lang=zh">隐私政策</a>\n\n@aerasuppbot', '<b>شرایط اشتراک</b>\n\nطرح، مدت و مبلغ نهایی پیش از پرداخت نمایش داده می\u200cشود. پس از تأیید پرداخت لینک شخصی ارائه می\u200cشود. سقف هر سفارش ۱۰٬۰۰۰ روبل است. با پرداخت توافق\u200cنامه را می\u200cپذیرید.\n\n<a href="https://aera-reserve.duckdns.org/terms?lang=fa">توافق\u200cنامه</a> · <a href="https://aera-reserve.duckdns.org/privacy?lang=fa">سیاست حریم خصوصی</a>\n\n@aerasuppbot'),
    "accept": (
        "Прочитал и согласен ✅",
        "Read and agree ✅",
        "已阅读并同意 ✅",
        "خواندم و موافقم ✅",
    ),
    "invoice": (
        "⭐ К оплате: {n} Stars\n"
        "Нажми кнопку, чтобы оплатить подписку. Рублёвая цена покупки Stars зависит от магазина и "
        "региона.",
        "⭐ Total: {n} Stars\nPress below to pay. The cost of buying Stars varies by store and region.",
        "⭐ 应付：{n} Stars\n点击下方付款。购买 Stars 的实际费用因商店和地区而异。",
        "⭐ مبلغ: {n} Stars\nبرای پرداخت دکمه را بزنید. هزینه خرید Stars به فروشگاه و منطقه بستگی دارد.",
    ),
    "pay_button": ("Оплатить ⭐", "Pay ⭐", "付款 ⭐", "پرداخت ⭐"),
    "paid": (
        "✅ Оплата подтверждена! Администратор получил уведомление и поможет подключиться 🤝",
        "✅ Payment confirmed! The administrator has been notified and will help you connect 🤝",
        "✅ 付款已确认！管理员已收到通知，会帮助您连接 🤝",
        "✅ پرداخت تأیید شد! مدیر مطلع شده و برای اتصال کمک می\u200cکند 🤝",
    ),
    "trial_info": ("<b>5 дней AERA бесплатно 🎉</b>\n\n💎 Личная пробная ссылка до 3 устройств.\n⏳ 120 часов начинаются после первого подключения VPN, а не после получения ссылки.\n🚀 Трафик безлимитный.\n🎁 Одна пробная подписка на Telegram-аккаунт.\n\n⚠️ Подключись в течение часа после получения ссылки. Иначе она будет аннулирована.\n\nНажми ниже и добавь ссылку в INCY / Happ.",
        "<b>5 days of AERA free 🎉</b>\n\n💎 A personal trial link for up to 3 devices.\n⏳ 120 hours begin with the first VPN connection, not when the link is received.\n🚀 Unlimited traffic.\n🎁 One trial per Telegram account.\n\n⚠️ Connect within one hour of receiving the link or it will be revoked.\n\nPress below and import the link into INCY / Happ.",
        "<b>免费体验 AERA 5 天 🎉</b>\n\n💎 最多三台设备使用的个人体验链接。\n⏳ 120 小时从首次连接 VPN 开始，而非领取链接时。\n🚀 流量不限。\n🎁 每个 Telegram 账户仅可体验一次。\n\n⚠️ 领取后请在一小时内连接，否则链接将失效。\n\n点击下方领取并在 INCY / Happ 导入。",
        "<b>۵ روز AERA رایگان 🎉</b>\n\n💎 لینک آزمایشی شخصی برای یک دستگاه.\n⏳ ۱۶۸ ساعت از اولین اتصال VPN شروع می‌شود، نه دریافت لینک.\n🚀 ترافیک نامحدود.\n🎁 یک دوره آزمایشی برای هر حساب Telegram.\n\n⚠️ تا یک ساعت پس از دریافت لینک متصل شوید؛ در غیر این صورت لینک لغو می‌شود.\n\nدکمه را بزنید و لینک را در INCY / Happ وارد کنید.",),
    "get_trial": (
        "Получить ссылку 🎁",
        "Get trial link 🎁",
        "领取体验链接 🎁",
        "دریافت لینک آزمایشی 🎁",
    ),
    "trial_empty": (
        "⏳ Все пробные ссылки заняты или ещё проверяются. Администратор получил запрос - напиши в "
        "поддержку, поможем 🤝",
        "⏳ Trial links are currently unavailable or being verified. The administrator has been "
        "notified. Contact support 🤝",
        "⏳ 体验链接已用完或正在验证。管理员已收到请求，请联系支持 🤝",
        "⏳ لینک\u200cهای آزمایشی تمام شده یا در حال بررسی هستند. مدیر مطلع شد؛ با پشتیبانی تماس "
        "بگیرید 🤝",
    ),
    "trial_used": (
        "🎁 Ты уже использовал пробную подписку. Для продолжения выбери тариф 💰",
        "🎁 You have already used your trial. Choose a plan to continue 💰",
        "🎁 您已经使用过体验订阅，请选择套餐继续使用 💰",
        "🎁 دوره آزمایشی شما قبلاً استفاده شده است. برای ادامه طرحی انتخاب کنید 💰",
    ),
    "trial_key": ("<b>Твоя пробная ссылка 🎁</b>\n\n🔗 <code>{link}</code>\n\n📋 Нажми на ссылку, чтобы скопировать. Добавь её в INCY / Happ через «+».\n⏳ Таймер включится после первого VPN-подключения. Срок появится в профиле после проверки сервера. ⚠️ Подключись в течение часа, иначе ссылка будет аннулирована.",
        "<b>Your trial link 🎁</b>\n\n🔗 <code>{link}</code>\n\n📋 Tap to copy. Import it using + in INCY / Happ.\n⏳ The timer starts with the first VPN connection. Your profile will update after the server check. ⚠️ Connect within one hour or the link will be revoked.",
        "<b>您的体验链接 🎁</b>\n\n🔗 <code>{link}</code>\n\n📋 点击复制，在 INCY / Happ 中使用 + 导入。\n⏳ 首次连接 VPN 后开始计时，服务器检查后资料页会更新。⚠️ 请在一小时内连接，否则链接将失效。",
        "<b>لینک آزمایشی شما 🎁</b>\n\n🔗 <code>{link}</code>\n\n📋 برای کپی لمس کنید و با + در INCY / Happ وارد کنید.\n⏳ زمان از اولین اتصال VPN آغاز می‌شود و پس از بررسی سرور در پروفایل نمایش داده می‌شود. ⚠️ تا یک ساعت متصل شوید؛ وگرنه لینک لغو می‌شود.",),
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
        "⚙️ Сервер подтверждает 120 часов с первого использования. Обнови профиль через минуту.",
        "⚙️ Confirming 120 hours from first use. Refresh your profile in a minute.",
        "⚙️ 正在确认首次使用起的 120 小时。请一分钟后刷新资料。",
        "⚙️ در حال تأیید ۱۶۸ ساعت از اولین استفاده. یک دقیقه دیگر پروفایل را تازه کنید.",
    ),
    "UNKNOWN": (
        "Проверяем состояние сервера ⏳",
        "Checking the server ⏳",
        "正在检查服务器 ⏳",
        "در حال بررسی سرور ⏳",
    ),
    "profile_info": (
        "<b>💎 Личный кабинет</b>\n\n{status}\n📦 Ваш тариф: {plan}\n{expiry}",
        "<b>💎 My account</b>\n\n{status}\n📦 {plan}\n{expiry}",
        "<b>💎 我的账户</b>\n\n{status}\n📦 {plan}\n{expiry}",
        "<b>💎 حساب من</b>\n\n{status}\n📦 {plan}\n{expiry}",
    ),
    "expiry": (
        "📅 Действует до: {date} UTC\n⏰ Осталось: {days} д. {hours} ч.",
        "📅 Valid until: {date} UTC\n⏰ Remaining: {days} d {hours} h",
        "📅 有效至：{date} UTC\n⏰ 剩余：{days} 天 {hours} 小时",
        "📅 اعتبار تا: {date} UTC\n⏰ باقی\u200cمانده: {days} روز {hours} ساعت",
    ),
    "waiting_time": (
        "⏳ 120 часов начнутся после первого VPN-подключения.",
        "⏳ 120 hours start after your first VPN connection.",
        "⏳ 首次连接 VPN 后开始 120 小时计时。",
        "⏳ ۱۶۸ ساعت از اولین اتصال VPN شروع می\u200cشود.",
    ),
    "my_link": ("Моя ссылка 🔗", "My connection link 🔗", "我的连接链接 🔗", "لینک اتصال من 🔗"),
    "no_link": (
        "🔗 Администратор добавит твою личную ссылку после подключения. Напиши в поддержку, если нужна "
        "помощь 🤝",
        "🔗 The administrator will add your personal link after connection. Contact support if needed 🤝",
        "🔗 管理员会在连接后添加您的个人链接，需要帮助请联系支持 🤝",
        "🔗 مدیر پس از اتصال لینک شخصی را اضافه می\u200cکند. برای کمک با پشتیبانی تماس بگیرید 🤝",
    ),
    "ref_info": (
        "<b>Приглашай друзей с AERA 🎁</b>\n"
        "\n"
        "👥 Приглашено: {invited}\n"
        "✅ Оплатили от 500 ₽: {qualified}\n"
        "🎟️ Доступных скидок: {coupons}\n"
        "\n"
        "Друг оплатил подписку от 500 ₽ — тебе скидка на следующую покупку:\n"
        "💎 50% на тариф до 500 ₽;\n"
        "💎 25% на тариф от 501 до 1000 ₽.\n"
        "Одна скидка за друга. Скидки не суммируются и не действуют на тарифы дороже 1000 ₽.\n"
        "\n"
        "🎁 <b>Месяц SOLO в подарок!</b>\n"
        "Пригласи 3 разных друзей. Каждый покупает подписку с подтверждённой оплатой от 100 ₽ — тебе 1 "
        "месяц AERA SOLO бесплатно. Бонус начисляется один раз.\n"
        "Повторные покупки одного друга не увеличивают счётчик.\n"
        "👥 Прогресс: {solo_count}/3\n"
        "{solo_status}\n"
        "\n"
        "🔗 Твоя ссылка:\n"
        "{url}",
        "<b>Invite friends with AERA 🎁</b>\n"
        "\n"
        "👥 Invited: {invited}\n"
        "✅ Paid 500+ RUB: {qualified}\n"
        "🎟️ Discounts available: {coupons}\n"
        "\n"
        "A friend pays 500+ RUB: get 50% off a plan up to 500 RUB, or 25% off a plan over 500 and up "
        "to 1000 RUB. One discount per friend; discounts do not stack or apply above 1000 RUB.\n"
        "\n"
        "🎁 <b>A free month of SOLO!</b>\n"
        "Invite 3 different friends who each buy a subscription with a confirmed payment of 100+ RUB. "
        "Get 1 month of AERA SOLO free, once. Repeat purchases by one friend do not increase the "
        "count.\n"
        "👥 Progress: {solo_count}/3\n"
        "{solo_status}\n"
        "\n"
        "🔗 Your invitation:\n"
        "{url}",
        "<b>邀请好友加入 AERA 🎁</b>\n"
        "\n"
        "👥 已邀请：{invited}\n"
        "✅ 支付满500卢布：{qualified}\n"
        "🎟️ 可用折扣：{coupons}\n"
        "\n"
        "好友支付满500卢布后，您可获下次购买折扣：不超过500卢布的套餐享50%折扣，501至1000卢布享25%折扣。每位好友一次，不可叠加，不适用于超过1000卢布的套餐。\n"
        "\n"
        "🎁 <b>免费一个月SOLO！</b>\n"
        "邀请3位不同好友，每人购买订阅并确认支付至少100卢布，即可免费获得一个月AERA SOLO，仅奖励一次。同一好友重复购买不增加人数。\n"
        "👥 进度：{solo_count}/3\n"
        "{solo_status}\n"
        "\n"
        "🔗 邀请链接：\n"
        "{url}",
        "<b>دوستان را به AERA دعوت کنید 🎁</b>\n"
        "\n"
        "👥 دعوت\u200cشده: {invited}\n"
        "✅ پرداخت ۵۰۰ روبل یا بیشتر: {qualified}\n"
        "🎟️ تخفیف موجود: {coupons}\n"
        "\n"
        "پس از پرداخت ۵۰۰ روبل توسط دوست: ۵۰٪ تخفیف طرح تا ۵۰۰ روبل یا ۲۵٪ طرح ۵۰۱ تا ۱۰۰۰ روبل. یک "
        "تخفیف برای هر دوست؛ قابل جمع نیست و برای طرح بالای ۱۰۰۰ روبل نیست.\n"
        "\n"
        "🎁 <b>یک ماه SOLO رایگان!</b>\n"
        "۳ دوست متفاوت دعوت کنید که هر کدام اشتراک با پرداخت تأییدشده حداقل ۱۰۰ روبل بخرند. یک ماه "
        "AERA SOLO رایگان، فقط یک بار. خرید مجدد یک دوست شمارنده را زیاد نمی\u200cکند.\n"
        "👥 پیشرفت: {solo_count}/3\n"
        "{solo_status}\n"
        "\n"
        "🔗 لینک دعوت:\n"
        "{url}",
    ),
    "renew_subscription": (
        "💳 Продлить подписку",
        "💳 Renew subscription",
        "💳 续订",
        "💳 تمدید اشتراک",
    ),
    "solo_collecting": (
        "До подарка осталось пригласить оплативших друзей.",
        "Invite paying friends to earn your gift.",
        "邀请付费好友即可获得奖励。",
        "برای دریافت هدیه دوستان پرداخت\u200cکننده دعوت کنید.",
    ),
    "solo_pending": (
        "✅ Бонус заработан и сохранён. Выдадим после текущей платной подписки, когда будет "
        "свободный ключ.",
        "✅ Gift earned and saved. Issued after current paid access ends, when stock is available.",
        "✅ 奖励已保存。当前付费订阅结束且有可用密钥时发放。",
        "✅ هدیه ذخیره شد؛ پس از پایان اشتراک فعلی و موجود شدن کلید صادر می\u200cشود.",
    ),
    "solo_issued": (
        "✅ Подарочный месяц SOLO выдан. Ссылка — в личном кабинете.",
        "✅ SOLO gift issued. Your link is in My account.",
        "✅ SOLO奖励已发放，链接在个人账户中。",
        "✅ هدیه SOLO صادر شد. لینک در حساب شماست.",
    ),
    "support_info": (
        "<b>🛟 Поддержка</b>\n"
        "\n"
        "Напишите вопрос в @aerasuppbot — ответ придёт в чат поддержки.\n"
        "\n"
        "Инструкции и документы — ниже.",
        "<b>🛟 Support</b>\n"
        "\n"
        "Send your question to @aerasuppbot. The reply will arrive in that support chat.\n"
        "\n"
        "Guides and documents are below.",
        "<b>🛟 支持</b>\n\n请联系 @aerasuppbot，我们会在支持聊天中回复。",
        "<b>🛟 پشتیبانی</b>\n"
        "\n"
        "به @aerasuppbot پیام دهید. پاسخ در همان گفتگوی پشتیبانی ارسال می\u200cشود.",
    ),
    "ticket_sent": (
        "📤 Обращение № {number} отправлено. Ответ придёт сюда 🤝",
        "📤 Request #{number} sent. The reply will arrive here 🤝",
        "📤 请求 #{number} 已发送，回复将在此显示 🤝",
        "📤 درخواست #{number} ارسال شد. پاسخ همین\u200cجا می\u200cآید 🤝",
    ),
    "empty_requests": (
        "📤 Оплаченных заявок пока нет. После оплаты заявка появится здесь 💎",
        "📤 No paid requests yet. Your request will appear here after payment 💎",
        "📤 暂无已付款申请。付款后申请将显示在这里 💎",
        "📤 هنوز درخواست پرداخت\u200cشده\u200cای ندارید. پس از پرداخت، درخواست اینجا نمایش داده "
        "می\u200cشود 💎",
    ),
    "purchase_record": (
        "✅ Оплачено · № {number}\n📦 {plan} · {period}\n💳 {amount}\n🏦 {method}\n📅 {date}",
        "✅ Paid · #{number}\n📦 {plan} · {period}\n💳 {amount}\n🏦 {method}\n📅 {date}",
        "✅ 已付款 · #{number}\n📦 {plan} · {period}\n💳 {amount}\n🏦 {method}\n📅 {date}",
        "✅ پرداخت شده · #{number}\n📦 {plan} · {period}\n💳 {amount}\n🏦 {method}\n📅 {date}",
    ),
    "previous": ("📄 Предыдущие", "📄 Previous", "📄 上一页", "📄 قبلی"),
    "next": ("Следующие 📄", "Next 📄", "下一页 📄", "بعدی 📄"),
    "refresh": ("Обновить 🔄", "Refresh 🔄", "刷新 🔄", "تازه\u200cسازی 🔄"),
    "error": (
        "⚙️ Не получилось выполнить действие. Попробуй ещё раз или напиши в поддержку 💊",
        "⚙️ Unable to complete this action. Retry or contact support 💊",
        "⚙️ 操作未完成，请重试或联系支持 💊",
        "⚙️ انجام نشد. دوباره تلاش کنید یا با پشتیبانی تماس بگیرید 💊",
    ),
    "renewal_pay": (
        "<b>Оплата подписки</b>\n"
        "\n"
        "📦 Ваш тариф: {plan}\n"
        "📅 {period}\n"
        "💰 {price} ₽\n"
        "🎁 Скидка: {discount}%\n"
        "\n"
        "📤 Заказ № {number}\n"
        "Оплата любым удобным для вас способом — выберите его ниже. После подтверждения оплаты срок "
        "вашей подписки продлится, ссылка сохранится.",
        "<b>Subscription payment</b>\n"
        "\n"
        "📦 {plan}\n"
        "📅 {period}\n"
        "💰 {price} RUB\n"
        "🎁 Discount: {discount}%\n"
        "\n"
        "📤 Order #{number}\n"
        "Pay using any available method below. After payment confirmation, your subscription is "
        "extended and your link stays the same.",
        "<b>订阅付款</b>\n"
        "\n"
        "📦 {plan}\n"
        "📅 {period}\n"
        "💰 {price} 卢布\n"
        "🎁 折扣：{discount}%\n"
        "\n"
        "📤 订单 #{number}\n"
        "请选择下方可用付款方式。确认付款后，将延长现有订阅，链接保持不变。",
        "<b>پرداخت اشتراک</b>\n"
        "\n"
        "📦 {plan}\n"
        "📅 {period}\n"
        "💰 {price} روبل\n"
        "🎁 تخفیف: {discount}%\n"
        "\n"
        "📤 سفارش #{number}\n"
        "یکی از روش\u200cهای پرداخت زیر را انتخاب کنید. پس از تأیید پرداخت، اشتراک فعلی تمدید "
        "می\u200cشود و لینک تغییری نمی\u200cکند.",
    ),
    "change_plan_note": (
        "Вы можете сменить тариф: нажмите «Выбрать тариф». Покупка другого тарифа создаст "
        "отдельную подписку; текущий доступ сохранится.",
        "You can choose a different plan below. It creates a separate subscription; your "
        "current access is kept.",
        "可以在下方选择其他套餐。这将创建独立订阅，现有订阅保持不变。",
        "می\u200cتوانید طرح دیگری انتخاب کنید. اشتراک جداگانه ایجاد می\u200cشود و دسترسی فعلی "
        "حفظ می\u200cشود.",
    ),
    "cancelled": ("Покупка отменена.", "Purchase cancelled.", "购买已取消。", "خرید لغو شد."),
    "days": ("{n} дней", "{n} days", "{n} 天", "{n} روز"),
}


def text(key, lang="ru", **values):
    return COPY[key][("ru", "en", "zh", "fa").index(lang if lang in LANGUAGES else "ru")].format(
        **values
    )


def period(plan, lang):
    return (
        text("month", lang, n=plan.duration_months)
        if plan.duration_months
        else text("days", lang, n=plan.duration_days)
    )


def devices(plan, lang):
    if lang == "ru" and not plan.unlimited_devices:
        from app.bot.presentation import devices_label

        return devices_label(plan.device_limit)
    return (
        text("unlimited", lang)
        if plan.unlimited_devices
        else text("devices", lang, n=plan.device_limit)
    )
