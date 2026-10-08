import asyncio
import logging

from app.config import get_settings
from app.core.security import TokenVault
from app.db.session import engine, sessions
from app.integrations.xui.factory import create_adapter
from app.services.provisioning import ProvisioningService


async def main() -> None:
    settings = get_settings()
    from app.bot.client import create_bot
    from app.services.notifications import schedule_expiry, send_broadcasts, send_notifications

    adapter = create_adapter(sessions, settings)
    service = ProvisioningService(sessions, adapter, TokenVault(settings.app_secret))
    from datetime import UTC, datetime, timedelta

    from app.services.checkout import CheckoutService

    checkout = CheckoutService(sessions, settings, TokenVault(settings.app_secret))
    next_poll = next_stars_check = datetime.now(UTC)
    bot = create_bot(settings) if settings.bot_token else None
    try:
        while True:
            try:
                if settings.manual_sales and datetime.now(UTC) >= next_poll:
                    next_poll = datetime.now(UTC) + timedelta(seconds=30)
                    try:
                        from app.services.trial_pool import synchronize

                        await synchronize(sessions, TokenVault(settings.app_secret), settings)
                    except Exception as error:
                        logging.getLogger("aera").error(
                            "trial_observer_failed type=%s", type(error).__name__
                        )
                    from app.services.manual_bank import poll

                    await poll(sessions, settings)
                    from app.services.paid_pool import retry_fulfillment

                    async with sessions.begin() as db:
                        await retry_fulfillment(db, settings)
                if not settings.manual_sales and datetime.now(UTC) >= next_poll:
                    await checkout.poll()
                    next_poll = datetime.now(UTC) + timedelta(seconds=30)
                if not settings.manual_sales:
                    await service.tick()
                    async with sessions.begin() as db:
                        await schedule_expiry(db)
                if bot and datetime.now(UTC) >= next_stars_check:
                    next_stars_check = datetime.now(UTC) + timedelta(minutes=10)
                    try:
                        from app.services.stars import reconcile

                        await reconcile(sessions, bot, settings)
                    except Exception as error:
                        logging.getLogger("aera").error(
                            "stars_reconcile_failed type=%s", type(error).__name__
                        )
                if bot:
                    await send_notifications(
                        sessions,
                        bot,
                        allowed_types={
                            "SUPPORT",
                            "SUPPORT_ADMIN",
                            "PORTAL",
                            "TRIAL_ACTIVE",
                            "ACCESS_READY",
                            "ALERT",
                        }
                        if settings.manual_sales
                        else None,
                    )
                    await send_broadcasts(sessions, bot)
            except Exception as error:
                logging.getLogger("aera").error("worker_tick_failed type=%s", type(error).__name__)
            await asyncio.sleep(5)
    finally:
        if bot:
            await bot.session.close()
        if hasattr(adapter, "close"):
            await adapter.close()
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
