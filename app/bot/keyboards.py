from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup


def keyboard(*rows: tuple[str, str]) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=label, callback_data=data)] for label, data in rows
        ]
    )
