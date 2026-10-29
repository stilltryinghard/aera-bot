"""Telegram polling for the live stock portal; generic provisioning is disabled."""

import asyncio
import logging
import os

from scripts.local_mock import root

os.environ["XUI_ALLOW_WRITES"] = "false"
os.environ["TELEGRAM_HTTP_CLIENT"] = "httpx"


async def run():
    from aiogram.types import BotCommand
    from redis.asyncio import Redis
    from sqlalchemy import text

    from app.bot.client import create_bot
    from app.bot.runtime import main, settings
    from app.db.session import sessions

    # Abort rather than starting against a different infrastructure or existing webhook.
    assert settings.xui_mock_mode and not settings.xui_allow_writes
    async with sessions() as db:
        await db.execute(text("SELECT 1"))
    cache = Redis.from_url(settings.redis_url)
    try:
        await cache.ping()
    finally:
        await cache.aclose()
    bot = create_bot(settings)
    try:
        me = await bot.get_me(request_timeout=15)
        if me.username.casefold() != settings.bot_username.casefold():
            raise RuntimeError("Bot identity mismatch")
        if (await bot.get_webhook_info(request_timeout=15)).url:
            raise RuntimeError("Existing webhook requires a coordinated switch")
        await bot.set_my_commands(
            [
                BotCommand(command="start", description="Начать работу с AERA"),
                BotCommand(command="help", description="Меню и помощь"),
                BotCommand(command="myid", description="Мой Telegram ID"),
                BotCommand(command="support", description="Поддержка"),
                BotCommand(command="paysupport", description="Помощь с оплатой"),
                BotCommand(command="terms", description="Условия"),
                BotCommand(command="privacy", description="Конфиденциальность"),
            ]
        )
        print(f"AERA bot ready: @{me.username}; stock portal", flush=True)
    finally:
        await bot.session.close()
    await main()


if __name__ == "__main__":
    os.chdir(root)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    try:
        asyncio.run(run())
    except Exception as error:
        print(f"AERA bot stopped: {type(error).__name__}", flush=True)
        raise SystemExit(1) from None
