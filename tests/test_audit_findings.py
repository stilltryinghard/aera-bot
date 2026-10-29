"""Reproductions for the 2026-10-29 audit of e365b9d.

Each test asserts the CURRENT (defective) behaviour so it passes on e365b9d and
documents the trigger. A fix must flip the marked assertion.
No network, production database, real bot token or payment is used.
"""

import asyncio
import json
import time
from datetime import UTC, datetime, timedelta

import pytest
from conftest import add_order
from sqlalchemy import select

from app.db.models import (
    AffiliateCommission,
    AffiliateWithdrawal,
    AppSetting,
    ManualAccess,
    PaidLink,
    Payment,
    Plan,
    Referral,
    SaleOrder,
    SupportTicket,
    User,
)


@pytest.fixture
def plan_id(app_plan_id):
    return app_plan_id


def forbidden():
    from aiogram.exceptions import TelegramForbiddenError
    from aiogram.methods import SendMessage

    return TelegramForbiddenError(
        method=SendMessage(chat_id=1, text="x"), message="Forbidden: bot was blocked by the user"
    )


async def paying_customer(db, plan_id, vault, telegram_id=1):
    user, ticket, order = await add_order(db, plan_id, telegram_id, status="CONNECTED")
    payment = Payment(
        user_id=user.id,
        plan_id=plan_id,
        provider="platega",
        amount_minor=19900,
        currency="RUB",
        status="PAID",
        paid_at=datetime.now(UTC),
        details={"channel": "bot", "method": "sbp"},
    )
    db.add(payment)
    await db.flush()
    order.payment_id, order.paid_at = payment.id, datetime.now(UTC)
    link = PaidLink(
        label="k",
        client_uuid="11111111-1111-1111-1111-111111111111",
        plan_id=plan_id,
        order_id=order.id,
        user_id=user.id,
        status="ACTIVE",
        issued_at=datetime.now(UTC),
        started_at=datetime.now(UTC),
        expires_at=datetime.now(UTC) + timedelta(days=25),
        checked_at=datetime.now(UTC),
        link_encrypted=vault.cipher.encrypt(
            b"vless://11111111-1111-1111-1111-111111111111@1.2.3.4:443?security=reality#k"
        ).decode(),
    )
    db.add(link)
    await db.flush()
    return user, link


# ---------------------------------------------------------------- F1 (P1)
async def test_f1_bot_block_on_any_notice_permanently_bans_paying_customer(
    bot_driver, app_sessions, plan_id, vault, monkeypatch
):
    from app.services.notifications import enqueue, send_notifications
    from app.subscription.multinode import PREFIX, customer_nodes

    async with app_sessions.begin() as db:
        user, link = await paying_customer(db, plan_id, vault)
        await enqueue(db, "any-notice", user.id, "PORTAL", "Новости")
        state = {"checked_at": time.time(), "entries": []}
        db.add(
            AppSetting(
                key=PREFIX + user.id,
                value=vault.cipher.encrypt(json.dumps(state).encode()).decode(),
            )
        )
    # The customer had the bot blocked for a moment while a notice was sent.
    bot_driver.api.fail["sendMessage"] = forbidden()
    await send_notifications(app_sessions, bot_driver.bot)
    del bot_driver.api.fail["sendMessage"]

    async with app_sessions() as db:
        user = await db.get(User, user.id)
        assert user.is_blocked is True  # the admin "ban" flag, not a delivery flag
        # Mirror subscription access is withheld for the paid, unexpired customer.
        assert await customer_nodes(db, user, vault) == []

    # The customer unblocks the bot and presses /start: still locked out.
    from bot_harness import message_payload

    await bot_driver.feed({"message": message_payload(1, "/start")})
    assert bot_driver.api.last_text() == "Доступ ограничен. Поддержка: @aerasuppbot"
    async with app_sessions() as db:
        assert (await db.get(User, user.id)).is_blocked is True  # never self-heals

    # New purchases and renewals are refused too.
    from app.services import platega

    monkeypatch.setattr(
        platega,
        "configuration",
        lambda: {
            "bot": {"merchant_id": "m", "secret": "s"},
            "site": {"merchant_id": "m", "secret": "s"},
        },
    )
    with pytest.raises(ValueError, match="Account unavailable"):
        await platega.create_payment(app_sessions, None, user.id, "sbp", "bot", plan_id=plan_id)


# ---------------------------------------------------------------- F2 (P1)
async def test_f2_stars_receipt_is_confirmed_to_telegram_before_handler_and_lost_on_error(
    app_sessions, app_settings, monkeypatch
):
    """aiogram polling acknowledges the update offset whether or not the handler succeeds."""
    from aiogram import Bot, Dispatcher
    from aiogram.client.session.base import BaseSession
    from aiogram.fsm.storage.memory import MemoryStorage

    from app.bot import payments

    receipt_update = {
        "update_id": 500,
        "message": {
            "message_id": 1,
            "date": 0,
            "chat": {"id": 1, "type": "private"},
            "from": {"id": 1, "is_bot": False, "first_name": "U"},
            "successful_payment": {
                "currency": "XTR",
                "total_amount": 150,
                "invoice_payload": "p",
                "telegram_payment_charge_id": "c",
                "provider_payment_charge_id": "x",
            },
        },
    }
    offsets, handled = [], []
    done = asyncio.Event()

    class Session(BaseSession):
        async def close(self):
            pass

        async def stream_content(self, *a, **k):
            yield b""

        async def make_request(self, bot, method, timeout=None):
            name = method.__api_method__
            if name == "getMe":
                from aiogram.types import User as TgUser

                return TgUser(id=42, is_bot=True, first_name="AERA")
            if name == "getUpdates":
                offsets.append(method.offset)
                if len(offsets) == 1:
                    from aiogram.types import Update

                    return [Update.model_validate(receipt_update)]
                if len(offsets) >= 2:
                    done.set()
                await asyncio.sleep(0.05)
                return []
            return True

    async def failing(*args, **kwargs):
        handled.append(True)
        raise RuntimeError("database is locked")  # any transient error in the handler

    monkeypatch.setattr(payments, "validate_invoice", failing)
    dispatcher = Dispatcher(storage=MemoryStorage())
    from aiogram import F, Router

    router = Router()  # the production handler, on a fresh router
    router.message.register(payments.successful_payment, F.successful_payment)
    dispatcher.include_router(router)
    bot = Bot("42:TEST", session=Session())
    task = asyncio.create_task(
        dispatcher.start_polling(bot, handle_signals=False, polling_timeout=0)
    )
    await asyncio.wait_for(done.wait(), 5)
    await dispatcher.stop_polling()
    await asyncio.gather(task, return_exceptions=True)
    assert handled == [True]  # the handler ran and failed ...
    assert offsets[1] == 501  # ... yet update 500 was confirmed: Telegram drops it.
    assert all(o == 501 for o in offsets[1:])  # never redelivered


# ---------------------------------------------------------------- F3 (P2)
async def test_f3_renewal_holds_sqlite_write_lock_during_panel_io(
    bot_driver, app_sessions, plan_id, vault, monkeypatch
):
    """A slow panel read inside create_renewal blocks every other writer (> 5 s busy timeout)."""
    from sqlalchemy.exc import OperationalError

    from app.db.session import engine
    from app.services import renewals

    if engine.dialect.name != "sqlite":
        pytest.skip("production runs SQLite; PostgreSQL locks only the user's row")

    async with app_sessions.begin() as db:
        user, _, _ = await add_order(db, plan_id, 7, status="CONNECTED")
        db.add(
            ManualAccess(
                user_id=user.id,
                plan_id=plan_id,
                started_at=datetime.now(UTC),
                expires_at=datetime.now(UTC) + timedelta(days=3),
                link_encrypted=vault.cipher.encrypt(
                    b"vless://22222222-2222-2222-2222-222222222222@h:1#x"
                ).decode(),
            )
        )
        other, _, other_order = await add_order(db, plan_id, 8)
    async with app_sessions() as db:
        access = await db.scalar(select(ManualAccess))

    class Reader:
        async def close(self):
            pass

    async def reader_for(db, settings):
        return Reader()

    async def slow_panel(reader, identity):
        await asyncio.sleep(6.5)  # panel timeout is 15 s per request
        return {
            "id": identity,
            "email": "e",
            "expiryTime": int(time.time() * 1000) + 10**8,
            "enable": True,
        }

    monkeypatch.setattr(renewals, "reader_for", reader_for)
    monkeypatch.setattr(renewals, "read_client", slow_panel)

    async def renew():
        async with app_sessions.begin() as db:
            u = await db.get(User, user.id)
            await renewals.create_renewal(db, u, "manual", access.id)

    async def unrelated_payment_confirmation():
        await asyncio.sleep(0.5)
        async with app_sessions.begin() as db:  # e.g. a Platega/Stars confirmation for user 8
            order = await db.get(SaleOrder, other_order.id)
            order.paid_at = datetime.now(UTC)

    results = await asyncio.gather(
        renew(), unrelated_payment_confirmation(), return_exceptions=True
    )
    assert results[0] is None
    assert isinstance(results[1], OperationalError) and "locked" in str(results[1])


# ---------------------------------------------------------------- F4 (P1)
async def test_f4_web_login_deep_link_approves_without_confirmation(bot_driver, monkeypatch):
    from bot_harness import message_payload

    from app.bot import web_login

    calls = []

    async def operation(script, request, *args):
        calls.append((script, args))
        if script == web_login.APPROVE:
            return 1
        if script == web_login.CLAIM:
            return "123456"
        return json.dumps({"status": "pending"})

    monkeypatch.setattr(web_login, "operation", operation)
    request = "A" * 43  # created by whoever opened the website login page
    await bot_driver.feed({"message": message_payload(1, "/start web_" + request)})
    assert (web_login.APPROVE, ("1", "approved")) in calls  # approved on a single tap
    assert "Вход на сайт AERA выполнен" in bot_driver.api.last_text()
    assert not bot_driver.api.buttons()  # no "approve / reject" step was shown


# ---------------------------------------------------------------- F5 (P2)
async def test_f5_platega_timeout_leaks_reserved_key_and_coupon_forever(
    app_sessions, plan_id, vault, monkeypatch
):
    from app.services import platega
    from app.services.paid_pool import apply_paid_snapshot
    from app.services.payment_timeout import expire_open_orders

    monkeypatch.setattr(
        platega,
        "configuration",
        lambda: {
            "bot": {"merchant_id": "m", "secret": "s"},
            "site": {"merchant_id": "m", "secret": "s"},
        },
    )

    async def timeout(*args, **kwargs):
        raise TimeoutError

    monkeypatch.setattr(platega, "api", timeout)
    async with app_sessions.begin() as db:
        user, ticket, order = await add_order(db, plan_id, 9)
        db.add(AppSetting(key="paid_pool_enabled", value="true"))
        db.add(
            PaidLink(
                label="k",
                client_uuid="33333333-3333-3333-3333-333333333333",
                plan_id=plan_id,
                status="FREE",
                checked_at=datetime.now(UTC),
                link_encrypted="x",
            )
        )
    with pytest.raises(ValueError, match="Не удалось получить ссылку оплаты"):
        await platega.create_payment(app_sessions, None, user.id, "sbp", "bot", order_id=order.id)
    later = datetime.now(UTC) + timedelta(minutes=16)
    async with app_sessions.begin() as db:
        await expire_open_orders(db, now=later)
    await platega.poll(app_sessions, None)
    async with app_sessions.begin() as db:
        # Even a fully verified, unused panel snapshot does not release it.
        plan = await db.get(Plan, plan_id)
        snapshot = {
            "settings": {
                "clients": [
                    {
                        "id": "33333333-3333-3333-3333-333333333333",
                        "email": "e",
                        "enable": True,
                        "totalGB": 0,
                        "limitIp": plan.device_limit,
                        "expiryTime": -plan.duration_days * 86400000,
                    }
                ]
            },
            "clientStats": [{"email": "e", "up": 0, "down": 0, "lastOnline": 0}],
        }
        await apply_paid_snapshot(db, snapshot, None)
    async with app_sessions() as db:
        payment = await db.scalar(select(Payment))
        link = await db.scalar(select(PaidLink))
        assert payment.status == "UNKNOWN"
        assert (await db.get(SupportTicket, ticket.id)).status == "CLOSED"
        assert link.status == "RESERVED" and link.order_id == order.id  # never returns to FREE


# ---------------------------------------------------------------- F6 (P3)
async def test_f6_one_empty_panel_snapshot_hides_every_paid_customer(app_sessions, plan_id, vault):
    from app.services.paid_pool import apply_paid_snapshot, current

    async with app_sessions.begin() as db:
        user, link = await paying_customer(db, plan_id, vault)
        await apply_paid_snapshot(db, {"id": 1, "settings": {"clients": []}}, None)
    async with app_sessions() as db:
        assert (await db.get(PaidLink, link.id)).status == "UNAVAILABLE"
        assert await current(db, user.id) is None


# ---------------------------------------------------------------- positive checks
def test_analytics_day_boundary_is_22_00_yekaterinburg():
    from app.services.daily_metrics import day

    # 16:59:59 UTC == 21:59:59 YEKT, 17:00 UTC == 22:00 YEKT
    assert day(datetime(2026, 10, 1, 16, 59, 59, tzinfo=UTC)) == "2026-10-01"
    assert day(datetime(2026, 10, 1, 17, 0, 0, tzinfo=UTC)) == "2026-10-02"
    assert day(datetime(2026, 10, 2, 16, 59, 59, tzinfo=UTC)) == "2026-10-02"


async def test_commission_once_and_concurrent_withdrawals_reserve_once(
    app_sessions, plan_id, app_settings
):
    from app.services.affiliates import accrue_first, request_withdrawal
    from app.services.portal import confirm_order

    settings = app_settings(admin_telegram_ids="999")
    async with app_sessions.begin() as db:
        referrer, _, _ = await add_order(db, plan_id, 100)
        orders = []
        friend = None
        for amount in (300000, 400000):
            if friend is None:
                friend, _, order = await add_order(
                    db, plan_id, 101, amount=amount, referred_by_id=referrer.id
                )
            else:
                ticket = SupportTicket(user_id=friend.id, category="TARIFF_REQUEST", subject="p")
                db.add(ticket)
                await db.flush()
                order = SaleOrder(ticket_id=ticket.id, plan_id=plan_id, amount_rub_minor=amount)
                db.add(order)
                await db.flush()
            payment = Payment(
                user_id=friend.id,
                plan_id=plan_id,
                provider="platega",
                amount_minor=amount,
                currency="RUB",
                status="PAID",
                paid_at=datetime.now(UTC),
            )
            db.add(payment)
            await db.flush()
            order.payment_id = payment.id
            orders.append(order.id)
        db.add(Referral(referrer_user_id=referrer.id, referred_user_id=friend.id, code="c"))

    async def confirm(identity):
        async with app_sessions.begin() as db:
            await confirm_order(db, await db.get(SaleOrder, identity), settings)

    await asyncio.gather(*(confirm(i) for i in orders * 2), return_exceptions=True)
    async with app_sessions() as db:
        rows = list(await db.scalars(select(AffiliateCommission)))
        assert len(rows) == 1 and rows[0].amount_minor == 300000 * 35 // 100

    async def withdraw():
        async with app_sessions.begin() as db:
            user = await db.get(User, referrer.id)
            return await request_withdrawal(db, user, "USDT\nTRC20\nTXabcdefghijklmnop", settings)

    await asyncio.gather(*(withdraw() for _ in range(4)), return_exceptions=True)
    async with app_sessions() as db:
        assert len(list(await db.scalars(select(AffiliateWithdrawal)))) == 1
        assert await accrue_first(db, await db.get(SaleOrder, orders[1]), settings) is False
