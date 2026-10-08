from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup


def keyboard(*rows: tuple[str, str]) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=label, callback_data=data)] for label, data in rows
        ]
    )


def button(label, data, style=None):
    return InlineKeyboardButton(text=label, callback_data=data, style=style)


def welcome_menu():
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [button("✦ Выбрать тариф", "plans", "primary")],
            [
                button("Подключиться", "connect"),
                button("◉ Моя AERA", "subscription"),
            ],
            [button("Как это работает", "how"), button("Поддержка", "support")],
        ]
    )


def menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [button("✦ Купить / продлить", "plans", "primary")],
            [button("Подключиться", "connect"), button("◉ Моя AERA", "subscription")],
            [button("Мои устройства", "devices"), button("Пригласить друга", "referral")],
            [button("Поддержка", "support")],
        ]
    )
