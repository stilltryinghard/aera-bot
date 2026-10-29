"""Branded photo screens and customer-facing tariff formatting."""

from pathlib import Path

from aiogram.exceptions import TelegramBadRequest
from aiogram.types import FSInputFile, InputMediaPhoto, Message


ASSETS = Path(__file__).resolve().parents[1] / "assets" / "ui"
FILES = {
    "welcome": "redesign-oct17/01-personal-account.png",
    "plans": "redesign-oct17/03-aera-vpn.png",
    "profile": "redesign-oct17/01-personal-account.png",
    "trial": "redesign-oct17/03-aera-vpn.png",
    "referral": "redesign-oct17/02-partner-program.png",
    "payments": "redesign-oct17/04-payment.png",
    "connect": "redesign-oct17/03-aera-vpn.png",
    "support": "redesign-oct17/03-aera-vpn.png",
    "payment_sbp": "redesign-oct17/04-payment.png",
    "payment_card": "redesign-oct17/04-payment.png",
    "payment_crypto": "redesign-oct17/04-payment.png",
    "payment_stars": "redesign-oct17/04-payment.png",
    "guide_ios": "editorial-oct16/guide_ios.png",
    "guide_android": "editorial-oct16/guide_android.png",
    "guide_windows": "editorial-oct16/guide_windows.png",
    "guide_macos": "editorial-oct16/guide_macos.png",
    "history": "redesign-oct17/01-personal-account.png",
}
_file_ids = {}


def image_source(message, art):
    path = ASSETS / FILES[art]
    key = (message.bot.id, str(path), path.stat().st_mtime_ns)
    return key, _file_ids.get(key) or FSInputFile(path)


def remember(key, result):
    if isinstance(result, Message) and result.photo:
        _file_ids[key] = result.photo[-1].file_id


async def send_screen(message, text, reply_markup=None, art="welcome"):
    if len(text) > 1024:
        return await message.answer(text, parse_mode="HTML", reply_markup=reply_markup)
    key, source = image_source(message, art)
    result = await message.answer_photo(
        source, caption=text, parse_mode="HTML", reply_markup=reply_markup
    )
    remember(key, result)
    return result


async def edit_screen(message, text, reply_markup=None, art=None):
    try:
        if len(text) > 1024 and message.photo:
            return await message.answer(text, parse_mode="HTML", reply_markup=reply_markup)
        if art and len(text) <= 1024:
            key, source = image_source(message, art)
            if not message.photo:
                result = await message.answer_photo(
                    source, caption=text, parse_mode="HTML", reply_markup=reply_markup
                )
                remember(key, result)
                return result
            if message.photo and isinstance(source, str) and message.photo[-1].file_id == source:
                return await message.edit_caption(
                    caption=text, parse_mode="HTML", reply_markup=reply_markup
                )
            result = await message.edit_media(
                InputMediaPhoto(media=source, caption=text, parse_mode="HTML"),
                reply_markup=reply_markup,
            )
            remember(key, result)
            return result
        if message.photo:
            return await message.edit_caption(
                caption=text, parse_mode="HTML", reply_markup=reply_markup
            )
        return await message.edit_text(text, parse_mode="HTML", reply_markup=reply_markup)
    except TelegramBadRequest as error:
        if "message is not modified" not in error.message.lower():
            raise


def devices_label(count, unlimited=False):
    if unlimited:
        return "Безлимит устройств"
    if count == 1:
        return "1 устройство"
    if 2 <= count % 10 <= 4 and not 12 <= count % 100 <= 14:
        return f"{count} устройства"
    return f"{count} устройств"


def traffic_label(limit):
    return "Безлимитный трафик" if limit == 0 else f"Трафик: {limit / 1024**3:g} ГБ"


def period_label(plan):
    return {1: "1 месяц", 6: "6 месяцев"}.get(plan.duration_months, f"{plan.duration_days} дней")


def price_label(plan):
    return f"{plan.price_minor} ⭐" if plan.currency == "XTR" else f"{plan.price_minor / 100:g} ₽"
