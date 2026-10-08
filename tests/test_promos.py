import pytest
from conftest import add_user
from sqlalchemy import select

from app.core.exceptions import PaymentError
from app.db.models import PromoCode
from app.services.commerce import CommerceService


async def make_promo(sessions, **values):
    async with sessions.begin() as db:
        db.add(PromoCode(code="SALE", discount_type="PERCENT", discount_value=10, **values))


async def buy(sessions, vault, user_id, plan_id):
    async with sessions.begin() as db:
        payment = await CommerceService(db, vault).purchase(user_id, plan_id, "mock", "SALE")
        return payment.id


async def confirm(sessions, vault, payment_id):
    async with sessions.begin() as db:
        await CommerceService(db, vault).confirm(payment_id)


async def uses(sessions):
    async with sessions() as db:
        return (await db.scalar(select(PromoCode).where(PromoCode.code == "SALE"))).uses


async def test_unpaid_checkout_does_not_consume_promo(sessions, vault, plan_id):
    await make_promo(sessions, max_uses=1)
    async with sessions.begin() as db:
        first, second = (await add_user(db, 1)).id, (await add_user(db, 2)).id

    await buy(sessions, vault, first, plan_id)  # abandoned
    assert await uses(sessions) == 0

    await buy(sessions, vault, first, plan_id)  # the same user can retry
    paid = await buy(sessions, vault, second, plan_id)
    await confirm(sessions, vault, paid)
    assert await uses(sessions) == 1


async def test_paid_use_still_enforces_limits(sessions, vault, plan_id):
    await make_promo(sessions, max_uses=1)
    async with sessions.begin() as db:
        first, second = (await add_user(db, 1)).id, (await add_user(db, 2)).id

    paid = await buy(sessions, vault, first, plan_id)
    await confirm(sessions, vault, paid)
    await confirm(sessions, vault, paid)  # duplicate provider event
    assert await uses(sessions) == 1

    with pytest.raises(PaymentError):
        await buy(sessions, vault, second, plan_id)


async def test_one_use_per_user_counts_paid_uses(sessions, vault, plan_id):
    await make_promo(sessions, max_uses=10)
    async with sessions.begin() as db:
        user = (await add_user(db, 1)).id

    await confirm(sessions, vault, await buy(sessions, vault, user, plan_id))

    with pytest.raises(PaymentError, match="already used"):
        await buy(sessions, vault, user, plan_id)


@pytest.mark.parametrize(
    "kind, value, amount, bonus",
    [("FIXED_AMOUNT", 900, 19000, 0), ("EXTRA_DAYS", 5, 19900, 5)],
)
async def test_promo_discount_types(sessions, vault, plan_id, kind, value, amount, bonus):
    async with sessions.begin() as db:
        db.add(PromoCode(code="SALE", discount_type=kind, discount_value=value))
        user = (await add_user(db, 1)).id
    async with sessions.begin() as db:
        payment = await CommerceService(db, vault).purchase(user, plan_id, "mock", " sale ")
    assert payment.amount_minor == amount and payment.details["bonus_days"] == bonus
    assert payment.details["duration_days"] == 30 + bonus


@pytest.mark.parametrize(
    "values, message",
    [
        ({"discount_type": "FIXED_AMOUNT", "discount_value": 10**6}, "Zero-price"),
        ({"discount_type": "MYSTERY", "discount_value": 1}, "Unsupported"),
        ({"discount_type": "PERCENT", "discount_value": 10, "is_active": False}, "unavailable"),
    ],
)
async def test_promo_rejections(sessions, vault, plan_id, values, message):
    async with sessions.begin() as db:
        db.add(PromoCode(code="SALE", **values))
        user = (await add_user(db, 1)).id
    with pytest.raises(PaymentError, match=message):
        await buy(sessions, vault, user, plan_id)
