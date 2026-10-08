from datetime import UTC, datetime, timedelta

import pytest
from conftest import add_user
from sqlalchemy import select

from app.core.exceptions import AccessDeniedError, PaymentError, ProvisioningError
from app.db.models import (
    Payment,
    Plan,
    ProvisioningJob,
    Referral,
    Server,
    Subscription,
    User,
    VPNClient,
)
from app.services.commerce import CommerceService, add_months, utc


def test_add_months_clamps_to_month_end():
    assert add_months(datetime(2026, 1, 31), 1) == datetime(2026, 2, 28)
    assert add_months(datetime(2024, 1, 31), 1) == datetime(2024, 2, 29)
    assert add_months(datetime(2026, 11, 15), 3) == datetime(2027, 2, 15)


def test_utc_marks_naive_datetimes():
    assert utc(datetime(2026, 1, 1)).tzinfo is UTC
    aware = datetime(2026, 1, 1, tzinfo=UTC)
    assert utc(aware) is aware


async def test_user_upsert_updates_profile(sessions, vault):
    async with sessions.begin() as db:
        first = await CommerceService(db, vault).user(10, username="old")
    async with sessions.begin() as db:
        second = await CommerceService(db, vault).user(10, username="new")
    assert first.id == second.id and second.username == "new"


async def test_plans_lists_active_in_order(sessions, vault):
    async with sessions.begin() as db:
        for slug, order, active in (("b", 2, True), ("a", 1, True), ("off", 0, False)):
            db.add(
                Plan(
                    name=slug,
                    slug=slug,
                    price_minor=100,
                    traffic_limit_bytes=0,
                    device_limit=1,
                    sort_order=order,
                    is_active=active,
                )
            )
    async with sessions() as db:
        assert [p.slug for p in await CommerceService(db, vault).plans()] == ["a", "b"]


async def purchase(sessions, vault, user_id, plan_id, provider="mock", promo=None):
    async with sessions.begin() as db:
        return await CommerceService(db, vault).purchase(user_id, plan_id, provider, promo)


async def test_purchase_snapshots_plan(sessions, vault, plan_id):
    async with sessions.begin() as db:
        user_id = (await add_user(db, 1)).id
    payment = await purchase(sessions, vault, user_id, plan_id)
    assert payment.amount_minor == 19900 and payment.currency == "RUB"
    assert payment.details["duration_days"] == 30 and payment.details["device_limit"] == 3


async def test_purchase_access_checks(sessions, vault, plan_id):
    async with sessions.begin() as db:
        blocked = User(telegram_id=1, is_blocked=True)
        inactive = User(telegram_id=2, is_active=False)
        fine = User(telegram_id=3)
        db.add_all([blocked, inactive, fine])
        await db.flush()
        off = Plan(
            name="off",
            slug="off",
            price_minor=1,
            traffic_limit_bytes=0,
            device_limit=1,
            is_active=False,
        )
        db.add(off)
        await db.flush()
        ids = blocked.id, inactive.id, fine.id, off.id
    for user_id in ids[:2] + ("missing",):
        with pytest.raises(AccessDeniedError):
            await purchase(sessions, vault, user_id, plan_id)
    for bad_plan in (ids[3], "missing"):
        with pytest.raises(PaymentError, match="Plan unavailable"):
            await purchase(sessions, vault, ids[2], bad_plan)


async def test_purchase_stars_pricing_and_terms(sessions, vault, plan_id):
    async with sessions.begin() as db:
        accepted = (await add_user(db, 1)).id
        no_terms = User(telegram_id=2)
        db.add(no_terms)
        await db.flush()
        no_terms = no_terms.id
    with pytest.raises(PaymentError, match="Stars price"):
        await purchase(sessions, vault, accepted, plan_id, "telegram_stars")
    async with sessions.begin() as db:
        (await db.get(Plan, plan_id)).stars_price = 150
    payment = await purchase(sessions, vault, accepted, plan_id, "telegram_stars")
    assert (payment.amount_minor, payment.currency) == (150, "XTR")
    with pytest.raises(PaymentError, match="Terms"):
        await purchase(sessions, vault, no_terms, plan_id, "telegram_stars")
    # Stars mock does not require terms.
    assert (await purchase(sessions, vault, no_terms, plan_id, "stars_mock")).currency == "XTR"


async def test_purchase_rejects_promo_in_other_currency(sessions, vault, plan_id):
    async with sessions.begin() as db:
        user_id = (await add_user(db, 1)).id
        (await db.get(Plan, plan_id)).stars_price = 150
    with pytest.raises(PaymentError, match="currency"):
        await purchase(sessions, vault, user_id, plan_id, "telegram_stars", "SALE")


async def confirm(sessions, vault, payment_id):
    async with sessions.begin() as db:
        return await CommerceService(db, vault).confirm(payment_id)


async def test_confirm_creates_subscription_and_job(sessions, vault, plan_id):
    async with sessions.begin() as db:
        user_id = (await add_user(db, 1)).id
    payment = await purchase(sessions, vault, user_id, plan_id)
    before = datetime.now(UTC)
    sub = await confirm(sessions, vault, payment.id)
    assert sub.status == "PENDING_PROVISIONING"
    assert timedelta(days=29) < utc(sub.expires_at) - before <= timedelta(days=30, seconds=5)
    async with sessions() as db:
        stored = await db.get(Payment, payment.id)
        assert stored.status == "PAID" and stored.applied_at is not None
        jobs = list(await db.scalars(select(ProvisioningJob)))
        assert len(jobs) == 1 and jobs[0].payment_id == payment.id


async def test_confirm_is_idempotent_and_extends_from_current_expiry(sessions, vault, plan_id):
    async with sessions.begin() as db:
        user_id = (await add_user(db, 1)).id
        (await db.get(Plan, plan_id)).duration_months = 1
    first = await purchase(sessions, vault, user_id, plan_id)
    sub = await confirm(sessions, vault, first.id)
    expiry = utc(sub.expires_at)
    again = await confirm(sessions, vault, first.id)
    assert utc(again.expires_at) == expiry  # duplicate event does not extend twice
    second = await purchase(sessions, vault, user_id, plan_id)
    extended = await confirm(sessions, vault, second.id)
    assert utc(extended.expires_at) == add_months(expiry, 1)


async def test_confirm_rejects_unknown_or_cancelled(sessions, vault, plan_id):
    async with sessions.begin() as db:
        user_id = (await add_user(db, 1)).id
    payment = await purchase(sessions, vault, user_id, plan_id)
    async with sessions.begin() as db:
        (await db.get(Payment, payment.id)).status = "CANCELLED"
    for payment_id in (payment.id, "missing"):
        with pytest.raises(PaymentError):
            await confirm(sessions, vault, payment_id)


async def test_confirm_marks_pending_referral_eligible(sessions, vault, plan_id):
    async with sessions.begin() as db:
        referrer = await add_user(db, 1)
        referred = await add_user(db, 2)
        db.add(Referral(referrer_user_id=referrer.id, referred_user_id=referred.id, code="c"))
        referred_id = referred.id
    payment = await purchase(sessions, vault, referred_id, plan_id)
    await confirm(sessions, vault, payment.id)
    async with sessions() as db:
        assert (await db.scalar(select(Referral))).status == "ELIGIBLE"


async def test_select_server_respects_capacity_and_priority(sessions, vault):
    async with sessions.begin() as db:
        full = Server(name="full", code="full", capacity=0, priority=0)
        spare = Server(name="spare", code="spare", capacity=5, priority=1)
        off = Server(name="off", code="off", capacity=5, priority=-1, is_active=False)
        db.add_all([full, spare, off])
    async with sessions.begin() as db:
        assert (await CommerceService(db, vault).select_server()).code == "spare"
        (await db.scalar(select(Server).where(Server.code == "spare"))).capacity = 0
        with pytest.raises(ProvisioningError):
            await CommerceService(db, vault).select_server()


async def test_subscription_lookup_and_token_rotation(sessions, vault, plan_id):
    async with sessions.begin() as db:
        user = await add_user(db, 1)
        server = Server(name="s", code="s")
        db.add(server)
        await db.flush()
        sub = Subscription(
            user_id=user.id,
            plan_id=plan_id,
            expires_at=datetime.now(UTC),
            traffic_limit_bytes=0,
            device_limit=1,
        )
        db.add(sub)
        await db.flush()
        db.add(
            VPNClient(
                subscription_id=sub.id,
                server_id=server.id,
                inbound_id=1,
                xui_client_id="x",
                email="e",
                traffic_limit_bytes=0,
                expires_at=datetime.now(UTC),
            )
        )
        user_id = user.id
        nobody = (await add_user(db, 2)).id
    async with sessions.begin() as db:
        service = CommerceService(db, vault)
        found, client = await service.subscription(user_id)
        assert found and client
        token = await service.rotate_token(user_id)
        assert vault.reveal(client.token_encrypted) == token
        assert await service.rotate_token(user_id, revoke=True) is None
        assert client.subscription_token_hash is None and client.token_encrypted is None
        assert await service.subscription(nobody) == (None, None)
        with pytest.raises(AccessDeniedError):
            await service.rotate_token(nobody)
