"""Remove the retired review marker from public bot descriptions only."""

import asyncio
import logging
import re

from aiogram.exceptions import TelegramNetworkError

from app.bot.client import create_bot
from app.config import get_settings


def clean(value):
    return re.sub(r"\s*(?:[·|—-]\s*)?plat[\s_-]*che[ck]+\b", "", value, flags=re.I).strip()


async def request(method, **kwargs):
    for attempt in range(3):
        try:
            return await method(request_timeout=10, **kwargs)
        except TelegramNetworkError:
            if attempt == 2:
                raise


async def main():
    settings = get_settings()
    bot = create_bot(settings)
    changed = 0
    try:
        identity = await request(bot.get_me)
        if identity.username.casefold() != settings.bot_username.casefold():
            raise RuntimeError("Bot identity mismatch")
        for language in (None, "ru", "en", "zh", "fa"):
            for field, read, write in (
                ("description", bot.get_my_description, bot.set_my_description),
                (
                    "short_description",
                    bot.get_my_short_description,
                    bot.set_my_short_description,
                ),
            ):
                original = getattr(await request(read, language_code=language), field)
                updated = clean(original)
                if original == updated:
                    continue
                await request(write, language_code=language, **{field: updated})
                if getattr(await request(read, language_code=language), field) != updated:
                    raise RuntimeError("Public copy verification failed")
                changed += 1
        print(f"Public descriptions cleaned and verified: {changed} fields updated.")
    finally:
        await bot.session.close()


if __name__ == "__main__":
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    try:
        asyncio.run(main())
    except Exception as error:
        print(f"Public copy cleanup stopped: {type(error).__name__}")
        raise SystemExit(1) from None
