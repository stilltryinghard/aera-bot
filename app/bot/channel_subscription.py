"""Optional channel link; trial allocation does not require membership."""

from aiogram.types import InlineKeyboardButton

CHANNEL_URL = "https://t.me/aeraavpn"


def channel_button(lang):
    label = {
        "ru": "📣 Перейти в канал",
        "en": "📣 Open channel",
        "zh": "📣 打开频道",
        "fa": "📣 باز کردن کانال",
    }
    return InlineKeyboardButton(text=label.get(lang, label["ru"]), url=CHANNEL_URL)
