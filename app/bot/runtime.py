"""Telegram entry point for the live AERA stock portal."""

import asyncio
from functools import lru_cache
from aiogram import Dispatcher, Router
from aiogram.filters import Command
from aiogram.fsm.storage.redis import RedisStorage
from aiogram.types import Message
from app.bot.client import create_bot
from app.config import get_settings
from app.db.session import engine

settings = get_settings()
router = Router(name="bot_commands")


@router.message(Command("myid"))
async def myid(message: Message) -> None:
    if message.chat.type == "private":
        await message.answer(f"Ваш Telegram ID: {message.from_user.id}")


@lru_cache
def create_dispatcher() -> Dispatcher:
    from app.bot.admin import router as admin_router
    from app.bot.news_posts import router as news_router
    from app.bot.middleware import GuardMiddleware
    from app.bot.web_login import router as web_login_router
    from app.bot.payments import router as payment_router
    from app.bot.customer_v2 import router as customer_router
    from app.bot.device_admin import router as device_admin_router
    from app.bot.portal import router as portal_router
    from app.bot.portal_admin import router as portal_admin_router

    dispatcher = Dispatcher(storage=RedisStorage.from_url(settings.redis_url))
    from app.services.bot_metrics import SectionMetricsMiddleware
    dispatcher.callback_query.middleware(SectionMetricsMiddleware())
    guard = GuardMiddleware()
    dispatcher.message.outer_middleware(guard)
    dispatcher.callback_query.outer_middleware(guard)
    dispatcher.shutdown.register(guard.close)
    for routes in (
        news_router,
        web_login_router,
        payment_router,
        device_admin_router,
        customer_router,
        portal_router,
        portal_admin_router,
        admin_router,
        router,
    ):
        dispatcher.include_router(routes)
    return dispatcher


async def main() -> None:
    if not settings.bot_token:
        raise RuntimeError("BOT_TOKEN is required")
    if not settings.manual_sales:
        raise RuntimeError("This bot serves the current stock portal")
    bot = create_bot(settings)
    try:
        await create_dispatcher().start_polling(bot)
    finally:
        await bot.session.close()
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
