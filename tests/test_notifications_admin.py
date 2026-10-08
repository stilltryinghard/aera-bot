from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from aiogram.exceptions import TelegramForbiddenError, TelegramRetryAfter
from conftest import add_order, add_user, make_settings
from sqlalchemy import select

from app.core.exceptions import AccessDeniedError
from app.db.models import (
    AuditLog,
    Broadcast,
    BroadcastDelivery,
    ManualAccess,
    Notification,
    PaidLink,
    Payment,
    Plan,
    ProvisioningJob,
    Referral,
    ReferralCoupon,
    Server,
    Subscription,
    SupportMessage,
    SupportTicket,
    TrialLink,
    TrialUsage,
    User,
    VPNClient,
)
from app.services import notifications
from app.services.admin import AdminService
from app.services.manual_lifecycle import (
    DeletionBusy,
    delete_support,
    mark_connected,
    purge_tickets,
    refuse_request,
)
from app.services.notifications import (
    enqueue,
    schedule_expiry,
    send_broadcasts,
    send_notifications,
)
from app.services.settings import set_setting
from app.services.trial import activate_trial
from app.services.trial_metrics import trial_summary

METHOD = SimpleNamespace()


class FakeBot:
    def __init__(self, error=None):
        self.error = error
        self.sent = []

    async def send_message(self, chat_id, text, reply_markup=None, parse_mode=None):
        if self.error:
            raise self.error
        self.sent.append((chat_id, text, reply_markup))

    async def send_photo(self, chat_id, photo, caption=None, reply_markup=None):
        if self.error:
            raise self.error
        self.sent.append((chat_id, caption, photo))


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch):
    async def instant(_seconds):
        return None

    monkeypatch.setattr(notifications.asyncio, "sleep", instant)


# ---------- notification queue ----------


async def test_enqueue_deduplicates(sessions):
    async with sessions.begin() as db:
        user = await add_user(db, 1)
        await enqueue(db, "k", user.id, "T", "first")
        await enqueue(db, "k", user.id, "T", "second")
        notes = list(await db.scalars(select(Notification)))
        assert [n.text for n in notes] == ["first"]


@pytest.mark.parametrize(
    "offset, kind",
    [
        (timedelta(days=2), "3D"),
        (timedelta(hours=12), "1D"),
        (timedelta(hours=-12), "EXPIRED"),
        (timedelta(days=-2), "AFTER"),
        (timedelta(days=10), None),
    ],
)
async def test_schedule_expiry_windows(sessions, plan_id, offset, kind):
    async with sessions.begin() as db:
        user = await add_user(db, 1)
        db.add(
            Subscription(
                user_id=user.id,
                plan_id=plan_id,
                status="ACTIVE",
                expires_at=datetime.now(UTC) + offset,
                traffic_limit_bytes=0,
                device_limit=1,
            )
        )
        await db.flush()
        await schedule_expiry(db)
        note = await db.scalar(select(Notification))
        assert (note.notification_type if note else None) == kind


async def queue(sessions, kind="T", text="hello", telegram_id=1, key="k", **user_values):
    async with sessions.begin() as db:
        user = User(telegram_id=telegram_id, **user_values)
        db.add(user)
        await db.flush()
        await enqueue(db, key, user.id, kind, text)
        return user.id


async def status_of(sessions, key="k"):
    async with sessions() as db:
        return await db.scalar(select(Notification).where(Notification.dedupe_key == key))


async def test_send_notifications_delivers_once(sessions):
    await queue(sessions)
    bot = FakeBot()
    await send_notifications(sessions, bot)
    await send_notifications(sessions, bot)
    assert bot.sent == [(1, "hello", None)]
    note = await status_of(sessions)
    assert note.status == "SENT" and note.sent_at


async def test_send_notifications_filters(sessions):
    await queue(sessions, kind="A", key="a")
    bot = FakeBot()
    await send_notifications(sessions, bot, keys={"other"})
    await send_notifications(sessions, bot, allowed_types={"B"})
    assert bot.sent == []


async def test_send_notifications_handles_blocked_and_failures(sessions):
    await queue(sessions, key="blocked-user", telegram_id=1, is_blocked=True)
    await send_notifications(sessions, FakeBot())
    assert (await status_of(sessions, "blocked-user")).status == "BLOCKED"

    user_id = await queue(sessions, key="forbidden", telegram_id=2)
    await send_notifications(sessions, FakeBot(TelegramForbiddenError(METHOD, "blocked")))
    assert (await status_of(sessions, "forbidden")).status == "BLOCKED"
    async with sessions() as db:
        assert (await db.get(User, user_id)).is_blocked

    await queue(sessions, key="flaky", telegram_id=3)
    await send_notifications(sessions, FakeBot(RuntimeError("network")))
    note = await status_of(sessions, "flaky")
    assert (note.status, note.attempts) == ("PENDING", 1)

    await send_notifications(sessions, FakeBot(TelegramRetryAfter(METHOD, "slow", 99)))
    assert (await status_of(sessions, "flaky")).attempts == 2


async def test_send_notifications_reclaims_stuck_sending(sessions):
    await queue(sessions)
    async with sessions.begin() as db:
        note = await db.scalar(select(Notification))
        note.status = "SENDING"
    async with sessions.begin() as db:
        note = await db.scalar(select(Notification))
        note.updated_at = datetime.now(UTC) - timedelta(minutes=5)
    await send_notifications(sessions, FakeBot())
    assert (await status_of(sessions)).status == "SENT"


async def test_access_ready_reveals_link_only_at_send_time(sessions, vault, plan_id):
    async with sessions.begin() as db:
        user, _, order = await add_order(db, plan_id, 1)
        link = PaidLink(
            label="k",
            client_uuid="c",
            plan_id=plan_id,
            user_id=user.id,
            order_id=order.id,
            issued_at=datetime.now(UTC),
            link_encrypted=vault.cipher.encrypt(b"vless://secret").decode(),
        )
        db.add(link)
        await db.flush()
        await enqueue(db, "paid-link", user.id, "ACCESS_READY", link.id)
        await enqueue(db, "bogus", user.id, "ACCESS_READY", "missing-link")
    bot = FakeBot()
    await send_notifications(sessions, bot)
    assert len(bot.sent) == 1 and "vless://secret" in bot.sent[0][1]
    note = await status_of(sessions, "paid-link")
    assert "secret" not in note.text
    assert (await status_of(sessions, "bogus")).status == "BLOCKED"


async def test_tariff_request_notice_only_for_paid_requests(sessions, plan_id):
    async with sessions.begin() as db:
        admin = await add_user(db, 999)
        _, unpaid, _ = await add_order(db, plan_id, 1)
        _, paid, order = await add_order(db, plan_id, 2, username="buyer")
        order.paid_at = datetime.now(UTC)
        await enqueue(db, f"request:{unpaid.id}:999", admin.id, "TARIFF_REQUEST", "x")
        await enqueue(db, f"request:{paid.id}:999", admin.id, "TARIFF_REQUEST", "x")
        unpaid_key, paid_key = f"request:{unpaid.id}:999", f"request:{paid.id}:999"
    bot = FakeBot()
    await send_notifications(sessions, bot)
    assert (await status_of(sessions, unpaid_key)).status == "CANCELLED"
    assert (await status_of(sessions, paid_key)).status == "SENT"
    ((chat, text, markup),) = bot.sent
    assert chat == 999 and "Оплаченная покупка" in text
    assert markup.inline_keyboard[1][0].url == "https://t.me/buyer"


async def test_broadcasts(sessions):
    async with sessions.begin() as db:
        users = [await add_user(db, i) for i in (1, 2, 3)]
        users[2].is_blocked = True
        broadcast = Broadcast(
            admin_user_id=9, text="news", total=3, button_text="Open", button_url="https://x"
        )
        db.add(broadcast)
        await db.flush()
        for user in users:
            db.add(BroadcastDelivery(broadcast_id=broadcast.id, user_id=user.id))
    bot = FakeBot()
    await send_broadcasts(sessions, bot)
    async with sessions() as db:
        broadcast = await db.scalar(select(Broadcast))
        assert (broadcast.sent, broadcast.blocked) == (2, 1)
    assert {chat for chat, *_ in bot.sent} == {1, 2}


async def test_broadcast_completes_when_last_recipient_is_blocked(sessions):
    async with sessions.begin() as db:
        user = await add_user(db, 1)
        user.is_blocked = True
        broadcast = Broadcast(admin_user_id=9, text="news", total=1)
        db.add(broadcast)
        await db.flush()
        db.add(BroadcastDelivery(broadcast_id=broadcast.id, user_id=user.id))
    await send_broadcasts(sessions, FakeBot())
    async with sessions() as db:
        assert (await db.scalar(select(Broadcast))).status == "DONE"


@pytest.mark.parametrize(
    "error, field",
    [
        (TelegramForbiddenError(METHOD, "blocked"), "blocked"),
        (RuntimeError("x"), "failed"),
    ],
)
async def test_broadcast_failures(sessions, error, field):
    async with sessions.begin() as db:
        user = await add_user(db, 1)
        broadcast = Broadcast(admin_user_id=9, text="t", total=1, image_file_id="photo")
        db.add(broadcast)
        await db.flush()
        db.add(BroadcastDelivery(broadcast_id=broadcast.id, user_id=user.id))
    await send_broadcasts(sessions, FakeBot(error))
    async with sessions() as db:
        assert getattr(await db.scalar(select(Broadcast)), field) == 1


async def test_broadcast_retry_after_leaves_delivery_pending(sessions):
    async with sessions.begin() as db:
        user = await add_user(db, 1)
        broadcast = Broadcast(admin_user_id=9, text="t", total=1)
        db.add(broadcast)
        await db.flush()
        db.add(BroadcastDelivery(broadcast_id=broadcast.id, user_id=user.id))
    await send_broadcasts(sessions, FakeBot(TelegramRetryAfter(METHOD, "slow", 5)))
    async with sessions() as db:
        assert (await db.scalar(select(BroadcastDelivery))).status == "PENDING"


# ---------- admin service ----------


def admin(db):
    return AdminService(db, make_settings(admin_telegram_ids="999"), 999)


def test_admin_requires_admin_id():
    with pytest.raises(AccessDeniedError):
        AdminService(None, make_settings(admin_telegram_ids="999"), 1)


async def subscriber(db, plan_id, telegram_id=1):
    user = await add_user(db, telegram_id)
    server = Server(name="s", code=f"s{telegram_id}")
    db.add(server)
    await db.flush()
    sub = Subscription(
        user_id=user.id,
        plan_id=plan_id,
        status="ACTIVE",
        expires_at=datetime.now(UTC) + timedelta(days=5),
        traffic_limit_bytes=0,
        device_limit=1,
    )
    db.add(sub)
    await db.flush()
    client = VPNClient(
        subscription_id=sub.id,
        server_id=server.id,
        inbound_id=1,
        xui_client_id=f"x{telegram_id}",
        email=f"e{telegram_id}",
        traffic_limit_bytes=0,
        expires_at=sub.expires_at,
        enabled=True,
        traffic_used_bytes=500,
    )
    db.add(client)
    await db.flush()
    return user, sub, client


async def test_admin_analytics(sessions, plan_id):
    async with sessions.begin() as db:
        user, _, _ = await subscriber(db, plan_id)
        db.add(
            Payment(
                user_id=user.id, plan_id=plan_id, amount_minor=100, currency="RUB", status="PAID"
            )
        )
        db.add(
            Payment(
                user_id=user.id, plan_id=plan_id, amount_minor=50, currency="XTR", status="PAID"
            )
        )
        await db.flush()
        stats = await admin(db).analytics()
    assert stats["users"] == 1 and stats["active_subscriptions"] == 1
    assert stats["revenue"] == {"RUB": 100, "XTR": 50} and stats["traffic_bytes"] == 500


async def test_admin_change_access(sessions, plan_id):
    async with sessions.begin() as db:
        user, sub, client = await subscriber(db, plan_id)
        service = admin(db)
        with pytest.raises(AccessDeniedError):
            await service.change_access(user.id, "extend", confirmed=False)
        before = sub.expires_at
        await service.change_access(user.id, "extend", True, days=10)
        assert sub.expires_at - before == timedelta(days=10)
        await service.change_access(user.id, "disable", True)
        assert user.is_blocked and not client.enabled and sub.status == "SUSPENDED"
        await service.change_access(user.id, "enable", True)
        assert not user.is_blocked
        await service.change_access(user.id, "rotate", True)
        assert client.token_encrypted
        await service.change_access(user.id, "revoke", True)
        assert client.token_encrypted is None
        for bad in (("extend", 0), ("explode", 30)):
            with pytest.raises(ValueError):
                await service.change_access(user.id, bad[0], True, days=bad[1])
        with pytest.raises(ValueError, match="Unknown"):
            await service.change_access("missing", "extend", True)
        nobody = await add_user(db, 5)
        with pytest.raises(ValueError, match="No subscription"):
            await service.change_access(nobody.id, "extend", True)
        await db.flush()
        assert len(list(await db.scalars(select(ProvisioningJob)))) == 3
        assert len(list(await db.scalars(select(AuditLog)))) == 5


async def test_admin_reply_ticket(sessions, plan_id):
    async with sessions.begin() as db:
        user, ticket, _ = await add_order(db, plan_id, 1)
        service = admin(db)
        message_id = await service.reply_ticket(ticket.id, "hi", close=True)
        assert ticket.status == "RESOLVED" and ticket.closed_at
        note = await db.scalar(select(Notification))
        assert note.dedupe_key == f"support:{message_id}" and note.text.endswith("hi")
        with pytest.raises(ValueError, match="closed"):
            await service.reply_ticket(ticket.id, "again")
        for args in (("missing", "x"), (ticket.id, ""), (ticket.id, "x" * 3501)):
            with pytest.raises(ValueError):
                await service.reply_ticket(*args)


async def test_admin_broadcast(sessions):
    async with sessions.begin() as db:
        await add_user(db, 1)
        blocked = await add_user(db, 2)
        blocked.is_blocked = True
        service = admin(db)
        broadcast = await service.broadcast("news", True, button_text="Go", button_url="https://x")
        assert broadcast.total == 1
        with pytest.raises(ValueError, match="HTTPS"):
            await service.broadcast("news", True, button_url="http://x")
        for args in (("news", False), ("", True), ("x" * 1001, True, "photo")):
            with pytest.raises(ValueError):
                await service.broadcast(*args)


# ---------- legacy trial subscription ----------


async def test_activate_trial(sessions, plan_id):
    async with sessions.begin() as db:
        user = await add_user(db, 1)
        with pytest.raises(AccessDeniedError):
            await activate_trial(db, user.id)  # disabled
        await set_setting(db, "trial_enabled", "true")
        await set_setting(db, "trial_days", "99")
        with pytest.raises(ValueError, match="duration"):
            await activate_trial(db, user.id)
        await set_setting(db, "trial_days", "2")
        job_id = await activate_trial(db, user.id)
        assert user.trial_used and await db.get(ProvisioningJob, job_id)
        sub = await db.scalar(select(Subscription))
        assert sub.device_limit == 1 and sub.traffic_limit_bytes == 10 * 1024**3
        with pytest.raises(AccessDeniedError):
            await activate_trial(db, user.id)
        other = await add_user(db, 2)
        (await db.get(Plan, plan_id)).is_active = False
        with pytest.raises(ValueError, match="No trial plan"):
            await activate_trial(db, other.id)


async def test_trial_summary(sessions):
    async with sessions.begin() as db:
        now = datetime.now(UTC)
        db.add_all(
            [
                TrialLink(label="a", client_uuid="a", link_encrypted="x", status="FREE"),
                TrialLink(label="b", client_uuid="b", link_encrypted="x", status="WAITING"),
                TrialLink(
                    label="c",
                    client_uuid="c",
                    link_encrypted="x",
                    status="ACTIVE",
                    expires_at=now + timedelta(days=1),
                    checked_at=now,
                    started_at=now,
                ),
                TrialUsage(fingerprint="f1", activated_at=now),
                TrialUsage(fingerprint="f2"),
            ]
        )
        await db.flush()
        summary = await trial_summary(db)
    assert "Получили пробную: 2" in summary and "Сейчас активны: 1" in summary
    assert "Свободных ссылок: 1 / 3" in summary and "Активировали всего: 1" in summary


# ---------- manual lifecycle ----------


async def test_mark_connected(sessions, plan_id):
    async with sessions.begin() as db:
        _, ticket, _ = await add_order(db, plan_id, 1)
        other = SupportTicket(user_id=ticket.user_id, category="SUPPORT", subject="s")
        db.add(other)
        await db.flush()
        assert await mark_connected(db, ticket.id)
        assert ticket.status == "CONNECTED" and await db.scalar(select(ManualAccess))
        assert await mark_connected(db, ticket.id)
        assert not await mark_connected(db, other.id)
        assert not await mark_connected(db, "missing")


async def test_purge_tickets_guards_payments(sessions, plan_id):
    async with sessions.begin() as db:
        user, ticket, order = await add_order(db, plan_id, 1)
        payment = Payment(user_id=user.id, plan_id=plan_id, amount_minor=1, currency="RUB")
        db.add(payment)
        await db.flush()
        order.payment_id = payment.id
        with pytest.raises(DeletionBusy, match="платёжный счёт"):
            await purge_tickets(db, [ticket.id])
        order.paid_at = datetime.now(UTC)
        with pytest.raises(DeletionBusy, match="Оплата подтверждена"):
            await purge_tickets(db, [ticket.id])


async def test_purge_tickets_waits_for_sending_and_pending_support(sessions, plan_id):
    async with sessions.begin() as db:
        user, ticket, _ = await add_order(db, plan_id, 1)
        message = SupportMessage(ticket_id=ticket.id, sender_type="ADMIN", sender_id="1", text="t")
        db.add(message)
        await db.flush()
        await enqueue(db, f"support:{message.id}", user.id, "SUPPORT", "t")
        with pytest.raises(DeletionBusy, match="не доставлен"):
            await purge_tickets(db, [ticket.id])
        note = await db.scalar(select(Notification))
        note.status = "SENDING"
        with pytest.raises(DeletionBusy, match="отправляется"):
            await purge_tickets(db, [ticket.id])


async def test_delete_support_releases_coupon_and_key(sessions, vault, plan_id):
    async with sessions.begin() as db:
        user = await add_user(db, 1)
        friend = await add_user(db, 2)
        ticket = SupportTicket(user_id=user.id, category="SUPPORT", subject="help")
        request = SupportTicket(user_id=user.id, category="TARIFF_REQUEST", subject="r")
        db.add_all([ticket, request])
        await db.flush()
        referral = Referral(referrer_user_id=user.id, referred_user_id=friend.id, code="c")
        db.add(referral)
        await db.flush()
        coupon = ReferralCoupon(
            user_id=user.id,
            referral_id=referral.id,
            status="RESERVED",
            reserved_ticket_id=ticket.id,
        )
        db.add(coupon)
        await db.flush()
        from app.db.models import SaleOrder

        order = SaleOrder(
            ticket_id=ticket.id, plan_id=plan_id, amount_rub_minor=1, coupon_id=coupon.id
        )
        db.add(order)
        await db.flush()
        link = PaidLink(
            label="k",
            client_uuid="c",
            link_encrypted="x",
            plan_id=plan_id,
            order_id=order.id,
            user_id=user.id,
            status="RESERVED",
        )
        db.add(link)
        await db.flush()
        assert not await delete_support(db, request.id)
        assert await delete_support(db, ticket.id)
        assert await db.get(SupportTicket, ticket.id) is None
        assert coupon.status == "AVAILABLE"
        await db.refresh(link)
        assert (link.order_id, link.status) == (None, "UNVERIFIED")


async def test_refuse_request_forgets_prospect(sessions, plan_id):
    async with sessions.begin() as db:
        user, ticket, order = await add_order(db, plan_id, 5)
        referrer = await add_user(db, 6)
        db.add(Referral(referrer_user_id=referrer.id, referred_user_id=user.id, code="c"))
        db.add(
            TrialLink(
                label="t", client_uuid="t", link_encrypted="x", user_id=user.id, status="WAITING"
            )
        )
        stars = Payment(
            user_id=user.id,
            plan_id=plan_id,
            provider="telegram_stars",
            amount_minor=10,
            currency="XTR",
            details={"manual_order": order.id},
        )
        db.add(stars)
        await db.flush()
        order.payment_id = stars.id
        user_id, ticket_id = user.id, ticket.id
    async with sessions.begin() as db:
        outcome = await refuse_request(db, ticket_id, {999})
    assert outcome == ("forgotten", 5)
    async with sessions() as db:
        assert await db.get(User, user_id) is None
        assert await db.scalar(select(Referral)) is None
        assert (await db.scalar(select(TrialLink))).status == "RETIRED"


async def test_refuse_request_keeps_customers(sessions, plan_id):
    async with sessions.begin() as db:
        user, ticket, _ = await add_order(db, plan_id, 5)
        db.add(
            Payment(user_id=user.id, plan_id=plan_id, amount_minor=1, currency="RUB", status="PAID")
        )
        _, connected, _ = await add_order(db, plan_id, 6, status="CONNECTED")
        support = SupportTicket(user_id=user.id, category="SUPPORT", subject="s")
        db.add(support)
        await db.flush()
        assert await refuse_request(db, ticket.id, set()) == ("request_only", None)
        assert await db.get(User, user.id) is not None
        assert await refuse_request(db, connected.id, set()) == ("connected", None)
        assert await refuse_request(db, support.id, set()) == ("missing", None)
        assert await refuse_request(db, "missing", set()) == ("missing", None)
