from datetime import UTC, datetime, timedelta

import pytest
from aiogram.types import (
    StarTransaction,
    StarTransactions,
    TransactionPartnerUser,
)
from aiogram.types import User as TgUser
from conftest import add_order, add_user, make_settings
from sqlalchemy import select, update

from app.db.models import (
    AppSetting,
    Notification,
    PaidLink,
    Payment,
    ProvisioningJob,
    SaleOrder,
)
from app.services import manual_bank, notifications, stars
from app.services.notifications import MAX_ATTEMPTS, alert_admins, enqueue, send_notifications

ADMINS = dict(admin_telegram_ids="999")


# ---------- admin alerts and undelivered notifications ----------


class FailingBot:
    async def send_message(self, *args, **kwargs):
        raise RuntimeError("network")


@pytest.fixture
def instant(monkeypatch):
    async def no_sleep(_seconds):
        return None

    monkeypatch.setattr(notifications.asyncio, "sleep", no_sleep)


async def test_alert_admins_is_idempotent(sessions):
    async with sessions.begin() as db:
        await add_user(db, 999)
        await add_user(db, 1)
        for _ in range(2):
            await alert_admins(db, make_settings(admin_telegram_ids="999,998"), "k", "boom")
        notes = list(await db.scalars(select(Notification)))
    # Admin 998 never started the bot, so there is nobody to queue for.
    assert [(n.dedupe_key, n.notification_type) for n in notes] == [("k:999", "ALERT")]


async def test_undelivered_notification_alerts_admins_once(app_sessions, app_settings, instant):
    app_settings(**ADMINS)
    async with app_sessions.begin() as db:
        await add_user(db, 999)
        user = await add_user(db, 7)
        await enqueue(db, "support:1", user.id, "SUPPORT", "reply")
    for _ in range(MAX_ATTEMPTS + 2):
        await send_notifications(app_sessions, FailingBot(), keys={"support:1"})
    async with app_sessions() as db:
        original = await db.scalar(
            select(Notification).where(Notification.dedupe_key == "support:1")
        )
        alerts = list(
            await db.scalars(select(Notification).where(Notification.notification_type == "ALERT"))
        )
    assert (original.status, original.attempts) == ("FAILED", MAX_ATTEMPTS)
    assert len(alerts) == 1 and "Telegram ID 7" in alerts[0].text and "reply" not in alerts[0].text


async def test_undeliverable_alert_does_not_alert_again(app_sessions, app_settings, instant):
    app_settings(**ADMINS)
    async with app_sessions.begin() as db:
        admin = await add_user(db, 999)
        await enqueue(db, "alert:1", admin.id, "ALERT", "x")
    for _ in range(MAX_ATTEMPTS):
        await send_notifications(app_sessions, FailingBot())
    async with app_sessions() as db:
        notes = list(await db.scalars(select(Notification)))
    assert [(n.dedupe_key, n.status) for n in notes] == [("alert:1", "FAILED")]


# ---------- WATA: lost link-creation response ----------


class Wata:
    def __init__(self, during=None):
        self.created, self.during = [], during

    async def create_payment(self, identity, amount, return_url):
        self.created.append(identity)
        if self.during:
            await self.during(identity)
        return {
            "id": "link-" + identity,
            "url": "https://wata/pay/" + identity,
            "orderId": identity,
            "amount": amount / 100,
            "currency": "RUB",
            "terminalPublicId": "t",
        }

    def matches(self, body, identity, amount, link_id=None):
        return body["orderId"] == identity

    async def close(self):
        pass


async def ambiguous_wata_attempt(sessions, plan_id, started_at):
    async with sessions.begin() as db:
        user, _, order = await add_order(db, plan_id, 1)
        details = {"manual_order": order.id, "request_started": True}
        if started_at is not None:
            details["request_started_at"] = started_at.timestamp()
        payment = Payment(
            user_id=user.id,
            plan_id=plan_id,
            provider="manual_wata",
            amount_minor=19900,
            currency="RUB",
            details=details,
        )
        db.add(payment)
        await db.flush()
        order.payment_id = payment.id
        return user.id, order.id, payment.id


async def test_wata_attempt_in_flight_is_not_repeated(sessions, plan_id, monkeypatch):
    user_id, order_id, _ = await ambiguous_wata_attempt(sessions, plan_id, datetime.now(UTC))
    wata = Wata()
    monkeypatch.setattr(manual_bank, "merchant", lambda *_: wata)
    with pytest.raises(ValueError, match="in progress"):
        await manual_bank.checkout(sessions, make_settings(), order_id, user_id, "wata")
    assert wata.created == []


@pytest.mark.parametrize("age", [timedelta(minutes=5), None])  # None: rows from before the fix
async def test_wata_abandons_lost_attempt_and_issues_new_link(sessions, plan_id, monkeypatch, age):
    started = datetime.now(UTC) - age if age else None
    user_id, order_id, old_id = await ambiguous_wata_attempt(sessions, plan_id, started)
    wata = Wata()
    monkeypatch.setattr(manual_bank, "merchant", lambda *_: wata)
    url = await manual_bank.checkout(sessions, make_settings(), order_id, user_id, "wata")
    async with sessions() as db:
        old = await db.get(Payment, old_id)
        order = await db.get(SaleOrder, order_id)
        new = await db.get(Payment, order.payment_id)
    assert old.status == "CANCELLED" and new.id != old_id and new.status == "PENDING"
    assert wata.created == [new.id] and url.endswith(new.id)
    assert new.details["checkout_url"] == url


async def test_wata_link_is_not_handed_out_for_abandoned_payment(sessions, plan_id, monkeypatch):
    async with sessions.begin() as db:
        user, _, order = await add_order(db, plan_id, 1)
        ids = user.id, order.id

    async def abandon(identity):
        # e.g. a second process gave up on this attempt while our POST was still in flight
        async with sessions.begin() as db:
            await db.execute(
                update(Payment).where(Payment.id == identity).values(status="CANCELLED")
            )

    monkeypatch.setattr(manual_bank, "merchant", lambda *_: Wata(abandon))
    with pytest.raises(ValueError, match="abandoned"):
        await manual_bank.checkout(sessions, make_settings(), ids[1], ids[0], "wata")


# ---------- timeouts ----------


def test_database_and_redis_timeouts():
    from app.config import Settings, redis_options
    from app.db.session import engine_options

    postgres = dict(_env_file=None, database_url="postgresql+asyncpg://u@h/db")
    options = engine_options(Settings(**postgres))
    assert options["connect_args"]["command_timeout"] == 30
    disabled = engine_options(Settings(**postgres, db_command_timeout_s=0))
    assert disabled["connect_args"]["command_timeout"] is None
    assert redis_options(make_settings()) == {"socket_timeout": 5, "socket_connect_timeout": 5}
    assert redis_options(make_settings(redis_timeout_s=0))["socket_timeout"] is None


def test_redis_clients_use_timeouts():
    import app.main as main
    from app.bot.runtime import create_dispatcher

    assert main.redis.connection_pool.connection_kwargs["socket_timeout"] == 5
    storage = create_dispatcher().fsm.storage
    pool = getattr(getattr(storage, "redis", None), "connection_pool", None)
    if pool is not None:  # other tests swap in an in-memory storage
        assert pool.connection_kwargs["socket_timeout"] == 5


# ---------- Stars reconciliation ----------


def transaction(charge, payload, amount, user_id=1, date=None, refund=False):
    partner = TransactionPartnerUser(
        transaction_type="invoice_payment",
        user=TgUser(id=user_id, is_bot=False, first_name="U"),
        invoice_payload=payload,
    )
    return StarTransaction(
        id=charge,
        amount=amount,
        date=date or datetime.now(UTC),
        **({"receiver": partner} if refund else {"source": partner}),
    )


class StarsBot:
    def __init__(self, transactions):
        self.transactions, self.calls = transactions, []

    async def get_star_transactions(self, offset=0, limit=100):
        self.calls.append(offset)
        return StarTransactions(transactions=self.transactions[offset : offset + limit])


async def stars_payment(sessions, plan_id, telegram_id=1, manual=False, **values):
    async with sessions.begin() as db:
        if manual:
            user, _, order = await add_order(db, plan_id, telegram_id)
        else:
            user = await add_user(db, telegram_id)
        details = {"duration_days": 30}
        if manual:
            details["manual_order"] = order.id
        payment = Payment(
            user_id=user.id,
            plan_id=plan_id,
            provider="telegram_stars",
            amount_minor=150,
            currency="XTR",
            details=details,
            **values,
        )
        db.add(payment)
        await db.flush()
        if manual:
            order.payment_id = payment.id
            return payment.id, order.id
        return payment.id, None


async def test_reconcile_skips_api_without_candidates(sessions):
    bot = StarsBot([])
    assert await stars.reconcile(sessions, bot, make_settings()) == 0
    assert bot.calls == []


async def test_reconcile_recovers_lost_receipt(sessions, plan_id):
    payment_id, _ = await stars_payment(sessions, plan_id)
    bot = StarsBot([transaction("charge-1", payment_id, 150)])
    assert await stars.reconcile(sessions, bot, make_settings()) == 1
    async with sessions() as db:
        payment = await db.get(Payment, payment_id)
        job = await db.scalar(select(ProvisioningJob))
    assert payment.status == "PAID" and payment.provider_payment_id == "charge-1"
    assert job.payment_id == payment_id
    # Running again finds nothing left to do.
    assert await stars.reconcile(sessions, bot, make_settings()) == 0


async def test_reconcile_fulfils_manual_order_and_tells_customer(sessions, plan_id, vault):
    payment_id, order_id = await stars_payment(sessions, plan_id, manual=True)
    async with sessions.begin() as db:
        db.add(AppSetting(key="paid_pool_enabled", value="true"))
        db.add(
            PaidLink(
                label="k",
                client_uuid="c",
                plan_id=plan_id,
                status="FREE",
                checked_at=datetime.now(UTC),
                link_encrypted=vault.cipher.encrypt(b"vless://x").decode(),
            )
        )
    await stars.reconcile(sessions, StarsBot([transaction("c1", payment_id, 150)]), make_settings())
    async with sessions() as db:
        assert (await db.get(SaleOrder, order_id)).paid_at is not None
        kinds = {n.dedupe_key.split(":")[0] for n in await db.scalars(select(Notification))}
    assert {"paid-link", "stars-recovered"} <= kinds


async def test_reconcile_ignores_refunded_and_foreign_transactions(sessions, plan_id):
    payment_id, _ = await stars_payment(sessions, plan_id)
    bot = StarsBot(
        [
            transaction("refunded", payment_id, 150),
            transaction("refunded", payment_id, 150, refund=True),
            transaction("other-bot-product", "unknown-payload", 150),
        ]
    )
    assert await stars.reconcile(sessions, bot, make_settings()) == 0
    async with sessions() as db:
        assert (await db.get(Payment, payment_id)).status == "PENDING"


async def test_reconcile_alerts_on_mismatch_and_cancelled_orders(sessions, plan_id):
    async with sessions.begin() as db:
        await add_user(db, 999)
    wrong_amount, _ = await stars_payment(sessions, plan_id, telegram_id=1)
    cancelled, _ = await stars_payment(sessions, plan_id, telegram_id=2, status="CANCELLED")
    bot = StarsBot(
        [
            transaction("c-wrong", wrong_amount, 1, user_id=1),
            transaction("c-cancelled", cancelled, 150, user_id=2),
        ]
    )
    assert await stars.reconcile(sessions, bot, make_settings(**ADMINS)) == 0
    async with sessions() as db:
        keys = {n.dedupe_key for n in await db.scalars(select(Notification))}
        statuses = {p.id: p.status for p in await db.scalars(select(Payment))}
    assert keys == {"stars-unapplied:c-wrong:999", "stars-cancelled-paid:c-cancelled:999"}
    assert statuses == {wrong_amount: "PENDING", cancelled: "CANCELLED"}


async def test_reconcile_after_live_receipt_is_a_no_op(sessions, plan_id):
    payment_id, _ = await stars_payment(sessions, plan_id)
    async with sessions.begin() as db:
        await stars.apply_receipt(db, make_settings(), payment_id, 1, 150, "XTR", "c1")
    bot = StarsBot([transaction("c1", payment_id, 150)])
    assert await stars.reconcile(sessions, bot, make_settings()) == 0
    assert bot.calls == []  # nothing pending, Telegram is not even asked


async def test_reconcile_pages_newest_first_until_cutoff(sessions, plan_id):
    now = datetime.now(UTC)
    payment_id, _ = await stars_payment(sessions, plan_id, created_at=now - timedelta(days=6))
    newest_first = [
        transaction(f"n{i}", f"x{i}", 1, date=now - timedelta(hours=i)) for i in range(150)
    ]
    newest_first[120] = transaction("hit", payment_id, 150, date=now - timedelta(hours=120))
    bot = StarsBot(newest_first)
    assert await stars.reconcile(sessions, bot, make_settings()) == 1
    assert bot.calls == [0, 100]


async def test_reconcile_oldest_first_resumes_from_cursor(sessions, plan_id):
    old = datetime.now(UTC) - timedelta(days=60)
    history = [
        transaction(f"o{i}", f"x{i}", 1, date=old + timedelta(minutes=i)) for i in range(230)
    ]
    payment_id, _ = await stars_payment(sessions, plan_id)
    history.append(transaction("late", payment_id, 150))
    bot = StarsBot(history)
    assert await stars.reconcile(sessions, bot, make_settings()) == 1
    assert bot.calls == [0, 0, 100, 200]
    async with sessions() as db:
        assert (await db.get(AppSetting, stars.CURSOR_KEY)).value == "231"
    await stars_payment(sessions, plan_id, telegram_id=2)  # something new to look for
    bot.calls.clear()
    await stars.reconcile(sessions, bot, make_settings())
    # One page of overlap and a final empty read, not the whole history again.
    assert bot.calls == [0, 131, 231]


async def test_worker_runs_stars_reconciliation(app_sessions, app_settings, monkeypatch):
    from bot_harness import make_bot

    from app.workers import run

    called = []

    async def fake_reconcile(sessions, bot, settings):
        called.append(bot)
        raise RuntimeError("telegram down")  # must not stop the loop

    class StopLoop(BaseException):
        pass

    async def stop(_seconds):
        raise StopLoop

    monkeypatch.setattr(stars, "reconcile", fake_reconcile)
    monkeypatch.setattr(run.asyncio, "sleep", stop)
    bot = make_bot()
    monkeypatch.setattr("app.bot.client.create_bot", lambda settings: bot)
    app_settings(bot_token="42:TEST")
    with pytest.raises(StopLoop):
        await run.main()
    assert called == [bot]
