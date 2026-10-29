"""Independent recovery jobs for real payments, inventory and queued delivery."""

import asyncio
import logging
import time
from app.bot.client import create_bot
from app.config import get_settings
from app.core.security import TokenVault
from app.db.session import engine, sessions
from app.services.notifications import send_broadcasts, send_notifications
from app.services.paid_pool import retry_fulfillment
from app.services.platega import poll
from app.services.affiliates import reconcile_reversals
from app.services.renewals import process_pending
from app.services.stock_v2 import replenish
from app.services.trial_stock import replenish_trials
from app.services.device_access import provision as device_provision
from app.services.trial_pool import synchronize
from app.services.analytics_report import capture_stock
from app.services.payment_timeout import expire_open_orders
from app.services.trial_upgrade import process_pending as upgrade_trials

log = logging.getLogger("aera")


async def run_job(name, operation):
    try:
        await operation
    except Exception as error:
        log.error("%s_failed type=%s", name, type(error).__name__)


async def in_transaction(operation, settings):
    async with sessions.begin() as db:
        await operation(db, settings)


async def run_periodic(jobs, tick=5):
    running, due = {}, {}
    try:
        while True:
            now = time.monotonic()
            for name, interval, operation in jobs:
                task = running.get(name)
                if now >= due.get(name, 0) and (task is None or task.done()):
                    due[name] = now + interval
                    running[name] = asyncio.create_task(run_job(name, operation()))
            await asyncio.sleep(tick)
    finally:
        for task in running.values():
            task.cancel()
        await asyncio.gather(*running.values(), return_exceptions=True)


async def main() -> None:
    settings = get_settings()
    if not settings.manual_sales:
        raise RuntimeError("This worker serves the current stock portal")
    vault = TokenVault(settings.app_secret)
    bot = create_bot(settings) if settings.bot_token else None
    jobs = [
        ("trial_paid_upgrade", 5, lambda: upgrade_trials(sessions, settings)),
        ("checkout_timeout", 15, lambda: in_transaction(expire_open_orders, settings)),
        ("analytics_stock", 300, lambda: in_transaction(capture_stock, settings)),
        ("v2_stock", 30, lambda: replenish(sessions, settings, target=5)),
        ("trial_stock", 30, lambda: replenish_trials(sessions, settings)),
        ("device_keys", 20, lambda: device_provision(sessions, settings)),
        ("trial_observer", 30, lambda: synchronize(sessions, vault, settings)),
        ("platega_poll", 30, lambda: poll(sessions, settings)),
        ("paid_fulfillment", 30, lambda: in_transaction(retry_fulfillment, settings)),
        ("renewal_worker", 30, lambda: process_pending(sessions, settings)),
        ("referral_worker", 30, lambda: in_transaction(reconcile_reversals, settings)),
    ]
    if bot:
        from app.services.news_links import reconcile as sync_news, edit_tracked
        from app.services.trial_reminders import queue_reminders
        from app.services.trial_expiry import queue_expiry_notices
        from app.services.paid_expiry import queue_expiry_notices as queue_paid_expiry
        jobs += [
            ("news_channel_pointer", 60, lambda: sync_news(sessions, bot)),
            ("news_notice_links", 5, lambda: edit_tracked(sessions, bot)),
            ("trial_help_reminder", 30, lambda: queue_reminders(sessions, settings)),
            ("trial_expiry_notice", 30, lambda: queue_expiry_notices(sessions)),
            ("paid_expiry_notice", 30, lambda: queue_paid_expiry(sessions, settings)),
            (
                "notification_delivery",
                5,
                lambda: send_notifications(
                    sessions,
                    bot,
                    allowed_types={
                        "SUPPORT",
                        "SUPPORT_ADMIN",
                        "PORTAL",
                        "TRIAL_ACTIVE",
                        "ACCESS_READY",
                        "SUBSCRIPTION_UPDATE",
                        "TRIAL_REMINDER",
                        "TRIAL_EXPIRY",
                        "PAID_EXPIRY",
                    },
                ),
            ),
            ("broadcast_delivery", 5, lambda: send_broadcasts(sessions, bot)),
        ]
    try:
        await run_periodic(jobs)
    finally:
        if bot:
            await bot.session.close()
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())

