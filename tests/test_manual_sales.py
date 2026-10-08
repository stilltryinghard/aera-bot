from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from conftest import add_order, add_user, make_settings
from sqlalchemy import select

from app.core.exceptions import PaymentError
from app.db.models import (
    AppSetting,
    ManualAccess,
    Notification,
    PaidLink,
    Payment,
    Plan,
    Referral,
    ReferralCoupon,
    SaleOrder,
    SupportMessage,
    SupportTicket,
    TrialLink,
    User,
)
from app.integrations.payments.freekassa import FreeKassaProvider, digest
from app.services import manual_bank, paid_pool
from app.services.commerce import CommerceService, add_months, utc
from app.services.freekassa import accept_event
from app.services.manual_checkout import stars_payment
from app.services.manual_freekassa import checkout as manual_fk_checkout
from app.services.portal import (
    backfill_access,
    confirm_order,
    confirm_stars,
    connected_access,
    discount_percent,
    ensure_order,
)
from app.services.portal_freekassa import checkout as portal_fk_checkout
from app.services.requests import paid_requests, select_tariff
from app.services.settings import set_setting

FK = dict(
    freekassa_enabled=True,
    card_provider="freekassa",
    freekassa_merchant_id="123",
    freekassa_secret1="s1",
    freekassa_secret2="s2",
)


def vless(identity=None, label="Key"):
    return f"vless://{identity or uuid4()}@vpn.example:443?security=reality#{label}"


async def add_link(db, vault, plan_id, status="FREE", **values):
    raw = vless()
    link = PaidLink(
        label="k",
        client_uuid=str(uuid4()),
        link_encrypted=vault.cipher.encrypt(raw.encode()).decode(),
        plan_id=plan_id,
        status=status,
        checked_at=datetime.now(UTC),
        **values,
    )
    db.add(link)
    await db.flush()
    return link


async def enable_pool(db):
    db.add(AppSetting(key="paid_pool_enabled", value="true"))
    await db.flush()


# ---------- orders and coupons ----------


@pytest.mark.parametrize("amount, percent", [(50000, 50), (100000, 25), (100001, 0)])
def test_discount_percent(amount, percent):
    assert discount_percent(amount) == percent


async def test_ensure_order_applies_oldest_coupon_once(sessions, plan_id):
    async with sessions.begin() as db:
        user = await add_user(db, 1)
        friend = await add_user(db, 2)
        referral = Referral(referrer_user_id=user.id, referred_user_id=friend.id, code="c")
        db.add(referral)
        await db.flush()
        db.add(ReferralCoupon(user_id=user.id, referral_id=referral.id))
        ticket = SupportTicket(user_id=user.id, category="TARIFF_REQUEST", subject="s")
        db.add(ticket)
        await db.flush()
        plan = await db.get(Plan, plan_id)
        order = await ensure_order(db, ticket, plan)
        assert (order.amount_rub_minor, order.discount_percent) == (9950, 50)
        assert await ensure_order(db, ticket, plan) is order
        coupon = await db.get(ReferralCoupon, order.coupon_id)
        assert coupon.status == "RESERVED" and coupon.reserved_ticket_id == ticket.id


async def test_ensure_order_rejects_expensive_or_inactive(sessions, plan_id):
    async with sessions.begin() as db:
        user = await add_user(db, 1)
        ticket = SupportTicket(user_id=user.id, category="TARIFF_REQUEST", subject="s")
        db.add(ticket)
        plan = await db.get(Plan, plan_id)
        plan.price_minor = 1000001
        with pytest.raises(ValueError):
            await ensure_order(db, ticket, plan)


async def test_confirm_order_rewards_referrer_and_notifies_admin(sessions, vault, plan_id):
    async with sessions.begin() as db:
        await add_user(db, 999)  # admin
        referrer = await add_user(db, 2)
        user, ticket, order = await add_order(db, plan_id, 1, amount=60000)
        friend_referral = Referral(referrer_user_id=referrer.id, referred_user_id=user.id, code="c")
        db.add(friend_referral)
        await db.flush()
        settings = make_settings(admin_telegram_ids="999")
        await confirm_order(db, order, settings)
        await confirm_order(db, order, settings)  # idempotent
        assert order.paid_at is not None
        assert friend_referral.status == "COUPON"
        coupons = list(await db.scalars(select(ReferralCoupon)))
        assert len(coupons) == 1 and coupons[0].user_id == referrer.id
        keys = {n.dedupe_key for n in await db.scalars(select(Notification))}
        assert f"coupon:{friend_referral.id}" in keys
        assert f"sale-paid:{order.id}:999" in keys


async def test_confirm_order_small_purchase_no_reward_marks_coupon_used(sessions, plan_id):
    async with sessions.begin() as db:
        referrer = await add_user(db, 2)
        user, ticket, order = await add_order(db, plan_id, 1, amount=10000)
        referral = Referral(referrer_user_id=referrer.id, referred_user_id=user.id, code="c")
        own = Referral(referrer_user_id=user.id, referred_user_id=referrer.id, code="d")
        db.add_all([referral, own])
        await db.flush()
        coupon = ReferralCoupon(user_id=user.id, referral_id=own.id, status="RESERVED")
        db.add(coupon)
        await db.flush()
        order.coupon_id = coupon.id
        await confirm_order(db, order, make_settings())
        assert coupon.status == "USED" and referral.status == "PENDING"


async def test_confirm_stars_records_charge(sessions, plan_id):
    async with sessions.begin() as db:
        user, _, order = await add_order(db, plan_id, 1)
        payment = Payment(
            user_id=user.id,
            plan_id=plan_id,
            provider="telegram_stars",
            amount_minor=100,
            currency="XTR",
        )
        db.add(payment)
        await db.flush()
        order.payment_id = payment.id
        await confirm_stars(db, payment, "charge-1", make_settings())
        assert payment.status == "PAID" and payment.provider_payment_id == "charge-1"
        with pytest.raises(ValueError, match="Charge mismatch"):
            await confirm_stars(db, payment, "charge-2", make_settings())
        orphan = Payment(user_id=user.id, plan_id=plan_id, amount_minor=1, currency="XTR")
        db.add(orphan)
        await db.flush()
        with pytest.raises(ValueError, match="Missing"):
            await confirm_stars(db, orphan, "c", make_settings())


async def test_connected_access_creates_and_extends(sessions, plan_id):
    async with sessions.begin() as db:
        (await db.get(Plan, plan_id)).duration_months = 1
        user, ticket, _ = await add_order(db, plan_id, 1, status="CONNECTED")
        closed = datetime(2026, 1, 31, tzinfo=UTC)
        ticket.closed_at = closed
        await connected_access(db, ticket)
        access = await db.scalar(select(ManualAccess))
        assert utc(access.expires_at) == add_months(closed, 1)
        await connected_access(db, ticket)
        assert utc(access.expires_at) == add_months(add_months(closed, 1), 1)


async def test_connected_access_from_tariff_selection(sessions, plan_id):
    async with sessions.begin() as db:
        user = await add_user(db, 1)
        ticket = SupportTicket(
            user_id=user.id, category="TARIFF_REQUEST", subject="s", status="CONNECTED"
        )
        empty = SupportTicket(user_id=user.id, category="TARIFF_REQUEST", subject="e")
        db.add_all([ticket, empty])
        await db.flush()
        db.add(
            SupportMessage(
                ticket_id=ticket.id, sender_type="TARIFF_SELECTION", sender_id=plan_id, text="t"
            )
        )
        await connected_access(db, empty)
        assert await db.scalar(select(ManualAccess)) is None
        await connected_access(db, ticket)
        assert await db.scalar(select(ManualAccess)) is not None
        assert await db.scalar(select(SaleOrder).where(SaleOrder.ticket_id == ticket.id))


async def test_backfill_access_for_connected_legacy_users(sessions, plan_id):
    async with sessions.begin() as db:
        _, ticket, _ = await add_order(db, plan_id, 1, status="CONNECTED")
        ticket.closed_at = datetime.now(UTC)
        await add_order(db, plan_id, 2)  # open request: not a customer
        await backfill_access(db)
        assert len(list(await db.scalars(select(ManualAccess)))) == 1


# ---------- paid key pool ----------


async def test_pool_disabled_by_default(sessions, vault, plan_id):
    async with sessions.begin() as db:
        _, _, order = await add_order(db, plan_id, 1)
        assert not await paid_pool.enabled(db)
        assert await paid_pool.available(db, order)
        assert await paid_pool.reserve(db, order) is None
        assert not await paid_pool.fulfill(db, order, make_settings())


async def test_import_paid_links(sessions, vault, plan_id):
    identity = str(uuid4())
    async with sessions.begin() as db:
        slug = (await db.get(Plan, plan_id)).slug
        await paid_pool.import_paid_links(db, {slug: [vless(identity), vless(identity)]}, vault)
        assert await paid_pool.enabled(db)
        link = await db.scalar(select(PaidLink))
        assert link.client_uuid == identity and link.status == "UNVERIFIED"
        assert vault.cipher.decrypt(link.link_encrypted.encode()).decode().startswith("vless://")
        with pytest.raises(ValueError, match="Unknown"):
            await paid_pool.import_paid_links(db, {"nope": [vless()]}, vault)
        db.add(TrialLink(label="t", client_uuid=(trial := str(uuid4())), link_encrypted="x"))
        await db.flush()
        with pytest.raises(ValueError, match="Trial"):
            await paid_pool.import_paid_links(db, {slug: [vless(trial)]}, vault)


async def test_import_rejects_key_of_another_plan(sessions, vault, plan_id):
    identity = str(uuid4())
    async with sessions.begin() as db:
        other = Plan(name="o", slug="other", price_minor=1, traffic_limit_bytes=0, device_limit=1)
        db.add(other)
        await db.flush()
        slug = (await db.get(Plan, plan_id)).slug
        await paid_pool.import_paid_links(db, {slug: [vless(identity)]}, vault)
        with pytest.raises(ValueError, match="another plan"):
            await paid_pool.import_paid_links(db, {"other": [vless(identity)]}, vault)


async def test_reserve_and_fulfill(sessions, vault, plan_id):
    async with sessions.begin() as db:
        await enable_pool(db)
        stale = await add_link(db, vault, plan_id)
        stale.checked_at = datetime.now(UTC) - timedelta(minutes=10)
        fresh = await add_link(db, vault, plan_id)
        user, ticket, order = await add_order(db, plan_id, 1)
        assert await paid_pool.available(db, order)
        reserved = await paid_pool.reserve(db, order)
        assert reserved.id == fresh.id and reserved.status == "RESERVED"
        assert await paid_pool.reserve(db, order) is reserved
        assert not await paid_pool.fulfill(db, order, make_settings())  # unpaid
        order.paid_at = datetime.now(UTC)
        assert await paid_pool.fulfill(db, order, make_settings())
        assert reserved.issued_at and reserved.status == "WAITING"
        assert ticket.status == "CONNECTED"
        note = await db.scalar(select(Notification))
        assert (note.notification_type, note.text) == ("ACCESS_READY", reserved.id)
        assert await paid_pool.latest(db, user.id) is reserved
        assert await paid_pool.current(db, user.id) is reserved
        customers = await db.scalars(select(User.id).where(paid_pool.current_customer_condition()))
        assert list(customers) == [user.id]


async def test_fulfill_without_stock_alerts_admin_and_retries(sessions, vault, plan_id):
    async with sessions.begin() as db:
        await enable_pool(db)
        await add_user(db, 999)
        _, ticket, order = await add_order(db, plan_id, 1)
        order.paid_at = datetime.now(UTC)
        settings = make_settings(admin_telegram_ids="999")
        assert not await paid_pool.fulfill(db, order, settings)
        note = await db.scalar(select(Notification))
        assert note.dedupe_key == f"paid-no-stock:{order.id}:999"
        await add_link(db, vault, plan_id)
        await paid_pool.retry_fulfillment(db, settings)
        assert ticket.status == "CONNECTED"


async def test_apply_paid_snapshot_validates_stock_and_tracks_use(sessions, vault, plan_id):
    now_ms = int(datetime.now(UTC).timestamp() * 1000)
    async with sessions.begin() as db:
        await enable_pool(db)
        plan = await db.get(Plan, plan_id)
        duration = plan.duration_days * 86400000
        good = await add_link(db, vault, plan_id, status="UNVERIFIED")
        wrong_limits = await add_link(db, vault, plan_id, status="UNVERIFIED")
        missing = await add_link(db, vault, plan_id)
        _, _, order = await add_order(db, plan_id, 1)
        order.paid_at = datetime.now(UTC)
        issued = await add_link(
            db,
            vault,
            plan_id,
            status="WAITING",
            order_id=order.id,
            issued_at=datetime.now(UTC),
            duration_ms=duration,
        )
        snapshot = {
            "settings": {
                "clients": [
                    {
                        "id": good.client_uuid,
                        "email": "g",
                        "enable": True,
                        "limitIp": 3,
                        "expiryTime": -duration,
                    },
                    {
                        "id": wrong_limits.client_uuid,
                        "email": "w",
                        "enable": True,
                        "limitIp": 1,
                        "expiryTime": -duration,
                    },
                    {"id": issued.client_uuid, "email": "i", "enable": True},
                ]
            },
            "clientStats": [{"email": "i", "expiryTime": now_ms + 86400000}],
        }
        await paid_pool.apply_paid_snapshot(db, snapshot, make_settings())
        assert good.status == "FREE" and good.duration_ms == duration
        assert wrong_limits.status == "UNAVAILABLE" and missing.status == "UNAVAILABLE"
        assert issued.status == "ACTIVE" and issued.started_at is not None
        # Expired, disabled and stringified settings.
        snapshot = {
            "settings": '{"clients": [{"id": "%s", "email": "i", "enable": false}]}'
            % issued.client_uuid,
            "clientStats": [],
        }
        issued.expires_at = None
        await paid_pool.apply_paid_snapshot(db, snapshot, make_settings())
        assert issued.status == "EXPIRED"


# ---------- FreeKassa notifications ----------


def fk_fields(payment_id, amount="199.00", intid="555"):
    return {
        "MERCHANT_ID": "123",
        "AMOUNT": amount,
        "MERCHANT_ORDER_ID": payment_id,
        "intid": intid,
        "CUR_ID": "42",
        "SIGN": digest(f"123:{amount}:s2:{payment_id}"),
    }


def fk_provider():
    return FreeKassaProvider("123", "s1", "s2")


async def fk_payment(db, user, plan_id, **details):
    payment = Payment(
        user_id=user.id,
        plan_id=plan_id,
        provider="freekassa",
        amount_minor=19900,
        currency="RUB",
        details={"duration_days": 30, **details},
    )
    db.add(payment)
    await db.flush()
    payment.provider_payment_id = "fk-order:" + payment.id
    return payment


async def test_freekassa_event_confirms_automatic_purchase(sessions, vault, plan_id):
    async with sessions.begin() as db:
        user = await add_user(db, 1)
        payment = await fk_payment(db, user, plan_id)
        payment_id = payment.id
    for _ in range(2):  # FreeKassa retries until it gets YES
        async with sessions.begin() as db:
            await accept_event(
                db, vault, fk_provider(), fk_fields(payment_id), make_settings(manual_sales=False)
            )
    async with sessions() as db:
        payment = await db.get(Payment, payment_id)
        assert payment.status == "PAID" and payment.provider_payment_id == "fk:555"


async def test_freekassa_event_rejections(sessions, vault, plan_id):
    async with sessions.begin() as db:
        user = await add_user(db, 1)
        first = await fk_payment(db, user, plan_id)
        second = await fk_payment(db, user, plan_id)
        first.provider_payment_id = "fk:555"
        ids = first.id, second.id
    cases = [
        (fk_fields("missing"), "mismatch"),
        (fk_fields(ids[0], amount="1.00"), "mismatch"),
        (fk_fields(ids[0], intid="777"), "different transaction"),
        (fk_fields(ids[1]), "already assigned"),
    ]
    for fields, message in cases:
        with pytest.raises(PaymentError, match=message):
            async with sessions.begin() as db:
                await accept_event(db, vault, fk_provider(), fields, make_settings())
    with pytest.raises(PaymentError, match="route mismatch"):
        async with sessions.begin() as db:
            await accept_event(
                db,
                vault,
                fk_provider(),
                fk_fields(ids[0], intid="555"),
                make_settings(manual_sales=True),
            )


async def test_freekassa_event_fulfills_manual_order(sessions, vault, plan_id):
    async with sessions.begin() as db:
        user, _, order = await add_order(db, plan_id, 1)
        payment = await fk_payment(db, user, plan_id, manual_order=order.id)
        order.payment_id = payment.id
        payment_id, order_id = payment.id, order.id
    async with sessions.begin() as db:
        await accept_event(db, vault, fk_provider(), fk_fields(payment_id), make_settings())
    async with sessions() as db:
        assert (await db.get(SaleOrder, order_id)).paid_at is not None
        assert await db.scalar(
            select(Notification).where(Notification.dedupe_key == "freekassa-paid:" + payment_id)
        )


async def test_freekassa_event_rejects_foreign_manual_order(sessions, vault, plan_id):
    async with sessions.begin() as db:
        user, _, order = await add_order(db, plan_id, 1)
        payment = await fk_payment(db, user, plan_id, manual_order="someone-else")
        order.payment_id = payment.id
        payment_id = payment.id
    with pytest.raises(PaymentError, match="Sale order mismatch"):
        async with sessions.begin() as db:
            await accept_event(db, vault, fk_provider(), fk_fields(payment_id), make_settings())


# ---------- checkout entry points ----------


async def test_manual_stars_payment(sessions, vault, plan_id):
    async with sessions.begin() as db:
        user, ticket, order = await add_order(db, plan_id, 1)
        plan = await db.get(Plan, plan_id)
        plan.stars_price = 101
        assert await stars_payment(db, order.id, user) is None  # disabled by setting
        await set_setting(db, "manual_stars_enabled", "true")
        order.discount_percent = 50
        payment = await stars_payment(db, order.id, user)
        assert (payment.amount_minor, payment.currency) == (51, "XTR")
        assert await stars_payment(db, order.id, user) is payment
        payment.status = "CANCELLED"
        with pytest.raises(ValueError, match="already has"):
            await stars_payment(db, order.id, user)
        stranger = await add_user(db, 2)
        with pytest.raises(ValueError, match="unavailable"):
            await stars_payment(db, order.id, stranger)


async def test_manual_stars_respects_stock(sessions, vault, plan_id):
    async with sessions.begin() as db:
        user, _, order = await add_order(db, plan_id, 1)
        (await db.get(Plan, plan_id)).stars_price = 10
        await set_setting(db, "manual_stars_enabled", "true")
        await enable_pool(db)
        assert await stars_payment(db, order.id, user) is None
        user.terms_accepted_at = None
        await add_link(db, vault, plan_id)
        with pytest.raises(ValueError, match="Stars invoice"):
            await stars_payment(db, order.id, user)


async def test_manual_freekassa_checkout(sessions, vault, plan_id):
    settings = make_settings(**FK)
    async with sessions.begin() as db:
        user, _, order = await add_order(db, plan_id, 1)
        with pytest.raises(ValueError, match="FreeKassa unavailable"):
            await manual_fk_checkout(db, make_settings(), order.id, user)
        assert await manual_fk_checkout(db, settings, order.id, user) is None  # no pool
        await enable_pool(db)
        await add_link(db, vault, plan_id)
        url = await manual_fk_checkout(db, settings, order.id, user)
        assert url.startswith("https://pay.fk.money/")
        payment = await db.get(Payment, order.payment_id)
        assert payment.provider_payment_id == "fk-order:" + payment.id
        card = await manual_fk_checkout(db, settings, order.id, user, method="4")
        assert "i=4" in card and payment.details["method"] == "4"
        payment.status = "PAID"
        with pytest.raises(ValueError, match="Another payment"):
            await manual_fk_checkout(db, settings, order.id, user)


async def test_portal_freekassa_checkout(sessions, vault, plan_id):
    settings = make_settings(**FK)
    async with sessions.begin() as db:
        user, _, order = await add_order(db, plan_id, 1)
        assert await portal_fk_checkout(db, make_settings(), order.id, user.id) is None
        await enable_pool(db)
        assert await portal_fk_checkout(db, settings, order.id, user.id) is None  # no stock
        await add_link(db, vault, plan_id)
        url = await portal_fk_checkout(db, settings, order.id, user.id)
        assert await portal_fk_checkout(db, settings, order.id, user.id) == url
        (await db.get(Payment, order.payment_id)).status = "CANCELLED"
        with pytest.raises(PaymentError, match="Another"):
            await portal_fk_checkout(db, settings, order.id, user.id)
        stranger = await add_user(db, 2)
        with pytest.raises(PaymentError, match="unavailable"):
            await portal_fk_checkout(db, settings, order.id, stranger.id)


class FakeMerchant:
    def __init__(self, fail=False):
        self.fail = fail
        self.created = 0

    async def create_payment(self, identity, amount, *args, **kwargs):
        self.created += 1
        if self.fail:
            raise PaymentError("down")
        return {
            "id": "m-" + identity,
            "metadata": {"payment_id": identity},
            "amount": {"value": f"{amount / 100:.2f}", "currency": "RUB"},
            "test": False,
            "confirmation": {"confirmation_url": "https://merchant/pay"},
        }

    async def close(self):
        pass


async def test_manual_bank_checkout_creates_and_reuses_link(sessions, vault, plan_id, monkeypatch):
    merchant = FakeMerchant()
    monkeypatch.setattr(manual_bank, "merchant", lambda *_: merchant)
    settings = make_settings(bot_username="AeraBot")
    async with sessions.begin() as db:
        user, _, order = await add_order(db, plan_id, 1)
        ids = user.id, order.id
    url = await manual_bank.checkout(sessions, settings, ids[1], ids[0], "yookassa")
    again = await manual_bank.checkout(sessions, settings, ids[1], ids[0], "yookassa")
    assert url == again == "https://merchant/pay" and merchant.created == 1
    with pytest.raises(ValueError, match="Another payment"):
        await manual_bank.checkout(sessions, settings, ids[1], ids[0], "wata")


async def test_manual_bank_checkout_guards(sessions, vault, plan_id, monkeypatch):
    settings = make_settings()
    async with sessions.begin() as db:
        user, _, order = await add_order(db, plan_id, 1)
        ids = user.id, order.id
    # No merchant configured.
    assert await manual_bank.checkout(sessions, settings, ids[1], ids[0], "yookassa") is None
    monkeypatch.setattr(manual_bank, "merchant", lambda *_: FakeMerchant(fail=True))
    with pytest.raises(PaymentError):
        await manual_bank.checkout(sessions, settings, ids[1], ids[0], "wata")
    # A WATA POST with an unknown outcome is never repeated while it may be in flight.
    with pytest.raises(ValueError, match="in progress"):
        await manual_bank.checkout(sessions, settings, ids[1], ids[0], "wata")
    with pytest.raises(ValueError, match="unavailable"):
        await manual_bank.checkout(sessions, settings, ids[1], "stranger", "wata")


def test_merchant_selection():
    assert manual_bank.merchant(make_settings(), "yookassa") is None
    on = dict(manual_sbp_enabled=True)
    assert manual_bank.merchant(make_settings(**on), "yookassa") is None
    yk = manual_bank.merchant(
        make_settings(**on, yookassa_shop_id="1", yookassa_secret_key="k"), "yookassa"
    )
    wata = manual_bank.merchant(make_settings(**on, wata_token="t", wata_terminal_id="x"), "wata")
    assert type(yk).__name__ == "YooKassaProvider" and type(wata).__name__ == "WATAProvider"


# ---------- tariff requests ----------


async def test_select_tariff_creates_one_request_and_notifies(sessions, vault, plan_id):
    async with sessions.begin() as db:
        user = await add_user(db, 1)
        service = CommerceService(db, vault)
        with pytest.raises(ValueError, match="recipient"):
            await select_tariff(db, service, user, plan_id, set())
        ticket, plan = await select_tariff(db, service, user, plan_id, {999})
        again, _ = await select_tariff(db, service, user, plan_id, {999})
        assert again.id == ticket.id
        notes = list(await db.scalars(select(Notification)))
        assert len(notes) == 1 and "ЗАЯВКА НА ТАРИФ" in notes[0].text
        assert list(await db.scalars(paid_requests())) == []
        user.is_blocked = True
        with pytest.raises(ValueError, match="unavailable"):
            await select_tariff(db, service, user, plan_id, {999})
