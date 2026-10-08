from datetime import UTC, datetime, timedelta

from conftest import add_user

from app.config import Settings
from app.db.models import Payment, SaleOrder, SupportTicket
from app.services import checkout as checkout_module
from app.services import manual_bank


class FakeYooKassa:
    def __init__(self, paid=None, *_args, **_kwargs):
        self.paid = paid or {}
        self.asked = []

    async def verify_payment(self, provider_id):
        self.asked.append(provider_id)
        payment_id = self.paid.get(provider_id)
        return {
            "id": provider_id,
            "status": "succeeded" if payment_id else "canceled",
            "paid": bool(payment_id),
            "test": False,
            "metadata": {"payment_id": payment_id},
            "amount": {"value": "199.00", "currency": "RUB"},
        }

    async def close(self):
        pass


class FakeWata:
    def __init__(self):
        self.asked = []

    async def paid_transaction(self, identity, amount, link_id):
        self.asked.append(identity)
        return None

    async def close(self):
        pass


async def manual_order(
    db, plan_id, telegram_id, provider, provider_id, created_at=None, details=None
):
    user = await add_user(db, telegram_id)
    ticket = SupportTicket(user_id=user.id, category="REQUEST", subject="plan")
    db.add(ticket)
    await db.flush()
    payment = Payment(
        user_id=user.id,
        plan_id=plan_id,
        provider=provider,
        amount_minor=19900,
        currency="RUB",
        provider_payment_id=provider_id,
        details={"checkout_url": "https://pay.example"} if details is None else details,
        created_at=created_at or datetime.now(UTC),
    )
    db.add(payment)
    await db.flush()
    order = SaleOrder(
        ticket_id=ticket.id, plan_id=plan_id, amount_rub_minor=19900, payment_id=payment.id
    )
    db.add(order)
    await db.flush()
    return payment.id, order.id


async def test_abandoned_sbp_checkouts_do_not_starve_paid_one(
    sessions, settings, plan_id, monkeypatch
):
    old = datetime.now(UTC) - timedelta(days=10)
    async with sessions.begin() as db:
        for i in range(40):
            await manual_order(
                db,
                plan_id,
                1000 + i,
                "manual_yookassa",
                f"abandoned-{i}",
                created_at=old + timedelta(minutes=i),
            )
        paid_id, order_id = await manual_order(db, plan_id, 5000, "manual_yookassa", "paid")
    merchant = FakeYooKassa({"paid": paid_id})
    monkeypatch.setattr(manual_bank, "merchant", lambda *_: merchant)

    await manual_bank.poll(sessions, settings)  # closes the 40 cancelled checkouts
    await manual_bank.poll(sessions, settings)

    async with sessions() as db:
        assert (await db.get(Payment, paid_id)).status == "PAID"
        assert (await db.get(SaleOrder, order_id)).paid_at is not None


async def test_cancelled_sbp_checkout_is_closed_and_detached(
    sessions, settings, plan_id, monkeypatch
):
    async with sessions.begin() as db:
        payment_id, order_id = await manual_order(db, plan_id, 1, "manual_yookassa", "gone")
    monkeypatch.setattr(manual_bank, "merchant", lambda *_: FakeYooKassa())

    await manual_bank.poll(sessions, settings)

    async with sessions() as db:
        assert (await db.get(Payment, payment_id)).status == "CANCELLED"
        order = await db.get(SaleOrder, order_id)
        assert order.payment_id is None and order.paid_at is None


async def test_checkouts_without_link_are_not_polled(sessions, settings, plan_id, monkeypatch):
    async with sessions.begin() as db:
        for i in range(40):
            await manual_order(
                db,
                plan_id,
                1000 + i,
                "manual_yookassa",
                f"no-link-{i}",
                details={"request_started": True},
            )
        paid_id, _ = await manual_order(db, plan_id, 5000, "manual_yookassa", "paid")
    merchant = FakeYooKassa({"paid": paid_id})
    monkeypatch.setattr(manual_bank, "merchant", lambda *_: merchant)

    await manual_bank.poll(sessions, settings)

    assert merchant.asked == ["paid"]
    async with sessions() as db:
        assert (await db.get(Payment, paid_id)).status == "PAID"


async def test_wata_checkout_expires_only_after_link_lifetime(
    sessions, settings, plan_id, monkeypatch
):
    async with sessions.begin() as db:
        stale_id, _ = await manual_order(
            db,
            plan_id,
            1,
            "manual_wata",
            "link-old",
            created_at=datetime.now(UTC) - timedelta(days=5),
        )
        fresh_id, _ = await manual_order(
            db,
            plan_id,
            2,
            "manual_wata",
            "link-new",
            created_at=datetime.now(UTC) - timedelta(days=2),
        )
    monkeypatch.setattr(manual_bank, "merchant", lambda *_: FakeWata())

    await manual_bank.poll(sessions, settings)

    async with sessions() as db:
        assert (await db.get(Payment, stale_id)).status == "CANCELLED"
        assert (await db.get(Payment, fresh_id)).status == "PENDING"


async def test_failed_intents_do_not_starve_automatic_poll(sessions, vault, plan_id, monkeypatch):
    async with sessions.begin() as db:
        user = await add_user(db, 7000)
        for i in range(50):
            db.add(
                Payment(
                    user_id=user.id,
                    plan_id=plan_id,
                    provider="yookassa",
                    amount_minor=19900,
                    currency="RUB",
                    provider_payment_id=f"never-created-{i}",
                    details={},
                )
            )
        await db.flush()
        paid = Payment(
            user_id=user.id,
            plan_id=plan_id,
            provider="yookassa",
            amount_minor=19900,
            currency="RUB",
            provider_payment_id="yk-paid",
            details={"provider_ready": True, "duration_days": 30},
        )
        db.add(paid)
        await db.flush()
        paid_id = paid.id
    merchant = FakeYooKassa({"yk-paid": paid_id})
    monkeypatch.setattr(checkout_module, "YooKassaProvider", lambda *_: merchant)
    settings = Settings(
        _env_file=None,
        database_url="sqlite+aiosqlite:///:memory:",
        card_provider="yookassa",
        yookassa_shop_id="1",
        yookassa_secret_key="s",
    )

    await checkout_module.CheckoutService(sessions, settings, vault).poll()

    assert merchant.asked == ["yk-paid"]
    async with sessions() as db:
        assert (await db.get(Payment, paid_id)).status == "PAID"
