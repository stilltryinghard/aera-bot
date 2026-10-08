from datetime import UTC, datetime

import pytest
from conftest import add_order, add_user
from sqlalchemy import select

from app.db.models import (
    AppSetting,
    Notification,
    PaidLink,
    Payment,
    Plan,
    SaleOrder,
    Server,
    Subscription,
)


@pytest.fixture
def plan_id(app_plan_id):
    return app_plan_id


async def stars_payment(app_sessions, plan_id, telegram_id=1, manual=False, **values):
    async with app_sessions.begin() as db:
        if manual:
            user, _, order = await add_order(db, plan_id, telegram_id)
        else:
            user = await add_user(db, telegram_id)
        payment = Payment(
            user_id=user.id,
            plan_id=plan_id,
            provider="telegram_stars",
            amount_minor=150,
            currency="XTR",
            details={"duration_days": 30, **({"manual_order": order.id} if manual else {})},
            **values,
        )
        db.add(payment)
        await db.flush()
        if manual:
            order.payment_id = payment.id
            return payment.id, order.id
        return payment.id, None


def pre_checkout(user_id, payload, amount=150, currency="XTR"):
    return {
        "pre_checkout_query": {
            "id": "pcq",
            "from": {"id": user_id, "is_bot": False, "first_name": "U"},
            "currency": currency,
            "total_amount": amount,
            "invoice_payload": payload,
        }
    }


def receipt(user_id, payload, charge="charge-1", amount=150):
    from bot_harness import message_payload

    message = message_payload(user_id)
    message["successful_payment"] = {
        "currency": "XTR",
        "total_amount": amount,
        "invoice_payload": payload,
        "telegram_payment_charge_id": charge,
        "provider_payment_charge_id": "",
    }
    return {"message": message}


async def answers(driver):
    return [(m.ok, m.error_message) for m in driver.api.named("answerPreCheckoutQuery")]


async def test_pre_checkout_accepts_matching_invoice(bot_driver, app_sessions, plan_id):
    payment_id, _ = await stars_payment(app_sessions, plan_id)
    await bot_driver.feed(pre_checkout(1, payment_id))
    assert await answers(bot_driver) == [(True, None)]


async def test_pre_checkout_rejects_mismatches(bot_driver, app_sessions, plan_id):
    payment_id, _ = await stars_payment(app_sessions, plan_id)
    for update in (
        pre_checkout(1, payment_id, amount=1),
        pre_checkout(1, payment_id, currency="USD"),
        pre_checkout(2, payment_id),  # someone else's invoice
        pre_checkout(1, "missing"),
    ):
        await bot_driver.feed(update)
    assert all(ok is False for ok, _ in await answers(bot_driver))
    assert len(await answers(bot_driver)) == 4


async def test_pre_checkout_reserves_paid_stock_for_manual_order(bot_driver, app_sessions, plan_id):
    payment_id, order_id = await stars_payment(app_sessions, plan_id, manual=True)
    async with app_sessions.begin() as db:
        db.add(AppSetting(key="paid_pool_enabled", value="true"))
    await bot_driver.feed(pre_checkout(1, payment_id))
    assert (await answers(bot_driver))[-1][0] is False  # no stock
    async with app_sessions.begin() as db:
        db.add(
            PaidLink(
                label="k",
                client_uuid="c",
                link_encrypted="x",
                plan_id=plan_id,
                status="FREE",
                checked_at=datetime.now(UTC),
            )
        )
    await bot_driver.feed(pre_checkout(1, payment_id))
    assert (await answers(bot_driver))[-1][0] is True
    async with app_sessions() as db:
        assert (await db.get(Payment, payment_id)).details["precheckout_accepted"] is True
        assert (await db.scalar(select(PaidLink))).order_id == order_id


async def test_successful_payment_activates_subscription(bot_driver, app_sessions, plan_id):
    async with app_sessions.begin() as db:
        db.add(Server(name="s", code="s"))
    payment_id, _ = await stars_payment(app_sessions, plan_id)
    await bot_driver.feed(receipt(1, payment_id))
    async with app_sessions() as db:
        payment = await db.get(Payment, payment_id)
        assert payment.status == "PAID" and payment.provider_payment_id == "charge-1"
        assert (await db.scalar(select(Subscription))).status == "ACTIVE"
    from app.bot.texts import ru

    assert bot_driver.api.last_text() == ru.READY


async def test_successful_payment_for_manual_order_issues_link(
    bot_driver, app_sessions, plan_id, vault
):
    payment_id, order_id = await stars_payment(app_sessions, plan_id, manual=True)
    async with app_sessions.begin() as db:
        db.add(AppSetting(key="paid_pool_enabled", value="true"))
        db.add(
            PaidLink(
                label="k",
                client_uuid="c",
                plan_id=plan_id,
                status="FREE",
                checked_at=datetime.now(UTC),
                link_encrypted=vault.cipher.encrypt(b"vless://paid").decode(),
            )
        )
    await bot_driver.feed(receipt(1, payment_id))
    async with app_sessions() as db:
        assert (await db.get(SaleOrder, order_id)).paid_at is not None
        note = await db.scalar(
            select(Notification).where(Notification.notification_type == "ACCESS_READY")
        )
        assert note.status == "SENT"
    assert any("vless://paid" in (text or "") for text in bot_driver.api.texts())


async def test_duplicate_receipt_with_other_charge_is_rejected(bot_driver, app_sessions, plan_id):
    payment_id, _ = await stars_payment(
        app_sessions, plan_id, status="PAID", provider_payment_id="charge-1"
    )
    with pytest.raises(ValueError):
        await bot_driver.feed(receipt(1, payment_id, charge="charge-2"))
    async with app_sessions() as db:
        assert await db.scalar(select(Subscription)) is None


async def test_receipt_bypasses_maintenance(bot_driver, app_sessions, plan_id):
    async with app_sessions.begin() as db:
        db.add(AppSetting(key="maintenance_mode", value="true"))
        db.add(Server(name="s", code="s"))
        (await db.get(Plan, plan_id)).stars_price = 150
    payment_id, _ = await stars_payment(app_sessions, plan_id)
    await bot_driver.feed(receipt(1, payment_id))
    async with app_sessions() as db:
        assert (await db.get(Payment, payment_id)).status == "PAID"
