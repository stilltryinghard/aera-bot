from datetime import UTC, datetime, timedelta

import pytest
from conftest import add_user, make_settings
from sqlalchemy import select

from app.core.exceptions import PaymentError
from app.db.models import CryptoInvoice, CryptoMockReceipt, Payment, Plan
from app.integrations.payments.crypto import MockCryptoProvider
from app.services import checkout as checkout_module
from app.services.checkout import CheckoutService
from app.services.crypto import create_invoice, reconcile_invoice


class FakeAdapter:
    """Stands in for both YooKassa and Crypto Pay adapters."""

    def __init__(self, verify=None):
        self.verify = verify
        self.closed = False
        self.created = []

    async def create_payment(self, payment_id, amount, currency, return_url):
        self.created.append(payment_id)
        return {"id": "yk-" + payment_id, "confirmation": {"confirmation_url": "https://yk/pay"}}

    async def create_invoice(self, payment_id, asset, amount):
        self.created.append(payment_id)
        return {"invoice_id": 77, "bot_invoice_url": "https://t.me/CryptoBot?start=x"}

    async def verify_payment(self, provider_id):
        return self.verify(provider_id)

    async def close(self):
        self.closed = True


@pytest.fixture
async def user_id(sessions):
    async with sessions.begin() as db:
        return (await add_user(db, 1)).id


def service(sessions, vault, **settings):
    return CheckoutService(sessions, make_settings(**settings), vault)


async def test_rejects_unknown_method(sessions, vault, user_id, plan_id):
    with pytest.raises(PaymentError):
        await service(sessions, vault).create(user_id, plan_id, "paypal")


async def test_mock_card_in_development_only(sessions, vault, user_id, plan_id):
    payment, url = await service(sessions, vault).create(user_id, plan_id, "card")
    assert payment.provider == "mock" and url is None
    prod = service(sessions, vault)
    prod.settings.app_env = "production"
    with pytest.raises(PaymentError, match="Card provider"):
        await prod.create(user_id, plan_id, "card")
    with pytest.raises(PaymentError, match="Crypto provider"):
        await prod.create(user_id, plan_id, "crypto")


async def test_stars_methods(sessions, vault, user_id, plan_id):
    async with sessions.begin() as db:
        (await db.get(Plan, plan_id)).stars_price = 120
    mock, _ = await service(sessions, vault).create(user_id, plan_id, "stars")
    assert mock.provider == "stars_mock"
    real, url = await service(sessions, vault, payment_provider="telegram_stars").create(
        user_id, plan_id, "stars"
    )
    assert (real.provider, real.currency, url) == ("telegram_stars", "XTR", None)


async def test_mock_crypto_creates_test_invoice(sessions, vault, user_id, plan_id):
    payment, url = await service(sessions, vault).create(user_id, plan_id, "crypto")
    assert url is None and payment.details["test"] is True
    async with sessions() as db:
        invoice = await db.get(CryptoInvoice, payment.details["invoice_id"])
    assert invoice.expected_amount == "1.99" and invoice.asset == "TEST_USDT"


async def test_yookassa_checkout_persists_provider_id(
    sessions, vault, user_id, plan_id, monkeypatch
):
    adapter = FakeAdapter()
    monkeypatch.setattr(checkout_module, "YooKassaProvider", lambda *a: adapter)
    svc = service(sessions, vault, card_provider="yookassa")
    payment, url = await svc.create(user_id, plan_id, "card")
    assert url == "https://yk/pay" and adapter.closed
    async with sessions() as db:
        stored = await db.get(Payment, payment.id)
    assert stored.provider_payment_id == "yk-" + payment.id
    assert stored.details["provider_ready"] is True and stored.details["checkout_url"] == url


async def test_crypto_pay_checkout_requires_plan_price(
    sessions, vault, user_id, plan_id, monkeypatch
):
    adapter = FakeAdapter()
    monkeypatch.setattr(checkout_module, "CryptoPayProvider", lambda *a: adapter)
    svc = service(sessions, vault, crypto_provider="crypto_pay", crypto_pay_token="t")
    with pytest.raises(PaymentError, match="cryptocurrency price"):
        await svc.create(user_id, plan_id, "crypto")
    async with sessions.begin() as db:
        plan = await db.get(Plan, plan_id)
        plan.crypto_asset, plan.crypto_amount = "USDT", "2.5"
    payment, url = await svc.create(user_id, plan_id, "crypto")
    assert url.startswith("https://t.me/CryptoBot")
    assert payment.provider_payment_id == "77"
    assert payment.details["crypto_amount"] == "2.5"


async def make_external(sessions, user_id, plan_id, provider, provider_id="ext-1", **details):
    async with sessions.begin() as db:
        payment = Payment(
            user_id=user_id,
            plan_id=plan_id,
            provider=provider,
            amount_minor=19900,
            currency="RUB",
            provider_payment_id=provider_id,
            details={"duration_days": 30, "provider_ready": True, **details},
        )
        db.add(payment)
        await db.flush()
        return payment.id


def yookassa_body(
    payment_id, status="succeeded", paid=True, value="199.00", test=False, provider_id="ext-1"
):
    return {
        "id": provider_id,
        "status": status,
        "paid": paid,
        "test": test,
        "metadata": {"payment_id": payment_id},
        "amount": {"value": value, "currency": "RUB"},
    }


@pytest.mark.parametrize(
    "body_kwargs, expected",
    [
        ({}, "PAID"),
        ({"status": "canceled", "paid": False}, "CANCELLED"),
        ({"status": "pending", "paid": False}, "PENDING"),
    ],
)
async def test_reconcile_yookassa_outcomes(
    sessions, vault, user_id, plan_id, body_kwargs, expected
):
    payment_id = await make_external(sessions, user_id, plan_id, "yookassa")
    adapter = FakeAdapter(lambda pid: yookassa_body(payment_id, **body_kwargs))
    assert await service(sessions, vault).reconcile(payment_id, adapter) == expected
    async with sessions() as db:
        assert (await db.get(Payment, payment_id)).status == expected


@pytest.mark.parametrize(
    "body_kwargs",
    [{"value": "1.00"}, {"provider_id": "other"}],
)
async def test_reconcile_yookassa_rejects_mismatch(sessions, vault, user_id, plan_id, body_kwargs):
    payment_id = await make_external(sessions, user_id, plan_id, "yookassa")
    adapter = FakeAdapter(lambda pid: yookassa_body(payment_id, **body_kwargs))
    with pytest.raises(PaymentError, match="mismatch"):
        await service(sessions, vault).reconcile(payment_id, adapter)


async def test_reconcile_rejects_test_payment_in_production(sessions, vault, user_id, plan_id):
    payment_id = await make_external(sessions, user_id, plan_id, "yookassa")
    svc = service(sessions, vault)
    svc.settings.app_env = "production"
    adapter = FakeAdapter(lambda pid: yookassa_body(payment_id, test=True))
    with pytest.raises(PaymentError, match="Test transaction"):
        await svc.reconcile(payment_id, adapter)


@pytest.mark.parametrize(
    "status, expected", [("paid", "PAID"), ("expired", "CANCELLED"), ("active", "PENDING")]
)
async def test_reconcile_crypto_pay(sessions, vault, user_id, plan_id, status, expected):
    payment_id = await make_external(
        sessions, user_id, plan_id, "crypto_pay", "77", crypto_asset="USDT", crypto_amount="2.5"
    )
    body = {
        "invoice_id": 77,
        "payload": payment_id,
        "asset": "USDT",
        "amount": "2.50",
        "status": status,
    }
    assert (
        await service(sessions, vault).reconcile(payment_id, FakeAdapter(lambda p: body))
        == expected
    )


async def test_reconcile_crypto_pay_rejects_wrong_asset(sessions, vault, user_id, plan_id):
    payment_id = await make_external(
        sessions, user_id, plan_id, "crypto_pay", "77", crypto_asset="USDT", crypto_amount="2.5"
    )
    body = {"invoice_id": 77, "payload": payment_id, "asset": "TON", "amount": "2.5"}
    with pytest.raises(PaymentError, match="Crypto invoice mismatch"):
        await service(sessions, vault).reconcile(payment_id, FakeAdapter(lambda p: body))


async def test_reconcile_builds_and_closes_own_adapter(
    sessions, vault, user_id, plan_id, monkeypatch
):
    payment_id = await make_external(sessions, user_id, plan_id, "yookassa")
    adapter = FakeAdapter(lambda pid: yookassa_body(payment_id, status="pending", paid=False))
    monkeypatch.setattr(checkout_module, "YooKassaProvider", lambda *a: adapter)
    assert await service(sessions, vault).reconcile(payment_id) == "PENDING"
    assert adapter.closed


async def test_reconcile_unsupported(sessions, vault, user_id, plan_id):
    payment_id = await make_external(sessions, user_id, plan_id, "mock")
    for target in (payment_id, "missing"):
        with pytest.raises(PaymentError, match="Unsupported"):
            await service(sessions, vault).reconcile(target, FakeAdapter())


async def test_poll_swallows_errors(sessions, vault, user_id, plan_id, monkeypatch):
    await make_external(sessions, user_id, plan_id, "yookassa")

    class Broken(FakeAdapter):
        async def verify_payment(self, provider_id):
            raise PaymentError("down")

    monkeypatch.setattr(checkout_module, "YooKassaProvider", lambda *a: Broken())
    await service(sessions, vault).poll()


# ---------- mock crypto service ----------


async def mock_invoice(sessions, vault, user_id, plan_id):
    async with sessions.begin() as db:
        invoice = await create_invoice(db, vault, user_id, plan_id)
        return invoice.provider_invoice_id, invoice.expected_amount


async def set_receipt(sessions, invoice_id, **values):
    async with sessions.begin() as db:
        receipt = await db.scalar(
            select(CryptoMockReceipt).where(CryptoMockReceipt.provider_invoice_id == invoice_id)
        )
        for key, value in values.items():
            setattr(receipt, key, value)


async def test_mock_crypto_requires_rub_plan(sessions, vault, user_id):
    async with sessions.begin() as db:
        plan = Plan(
            name="x",
            slug="xtr",
            price_minor=10,
            currency="XTR",
            traffic_limit_bytes=0,
            device_limit=1,
        )
        db.add(plan)
        await db.flush()
        plan_id = plan.id
    with pytest.raises(PaymentError, match="RUB"):
        async with sessions.begin() as db:
            await create_invoice(db, vault, user_id, plan_id)


@pytest.mark.parametrize(
    "receipt, expected",
    [
        ({}, "PENDING"),
        ({"status": "CONFIRMING"}, "CONFIRMING"),
        ({"status": "UNDERPAID"}, "UNDERPAID"),
        ({"status": "PAID", "received_amount": "1"}, "UNDERPAID"),
        ({"status": "PAID", "received_amount": "5"}, "REVIEW"),
        ({"status": "PAID", "received_amount": "1.99", "paid_at": None}, "REVIEW"),
        (
            {
                "status": "PAID",
                "received_amount": "1.99",
                "paid_at": datetime.now(UTC) + timedelta(hours=1),
            },
            "REVIEW",
        ),
        ({"status": "PAID", "received_amount": "1.99", "paid_at": datetime.now(UTC)}, "PAID"),
    ],
)
async def test_mock_crypto_reconcile_statuses(sessions, vault, user_id, plan_id, receipt, expected):
    invoice_id, _ = await mock_invoice(sessions, vault, user_id, plan_id)
    await set_receipt(sessions, invoice_id, **receipt)
    provider = MockCryptoProvider(sessions, "secret")
    assert await reconcile_invoice(sessions, vault, provider, invoice_id) == expected
    if expected == "PAID":  # duplicate event is a no-op
        assert await reconcile_invoice(sessions, vault, provider, invoice_id) == "PAID"


async def test_mock_crypto_expiry_and_identity_checks(sessions, vault, user_id, plan_id):
    invoice_id, _ = await mock_invoice(sessions, vault, user_id, plan_id)
    async with sessions.begin() as db:
        invoice = await db.scalar(select(CryptoInvoice))
        invoice.expires_at = datetime.now(UTC) - timedelta(minutes=1)
    provider = MockCryptoProvider(sessions, "secret")
    assert await reconcile_invoice(sessions, vault, provider, invoice_id) == "EXPIRED"
    await set_receipt(sessions, invoice_id, asset="REAL_USDT")
    with pytest.raises(PaymentError, match="mismatch"):
        await reconcile_invoice(sessions, vault, provider, invoice_id)
    async with sessions.begin() as db:
        db.add(CryptoMockReceipt(provider_invoice_id="orphan", asset="A", network="N"))
    with pytest.raises(PaymentError, match="Unknown local invoice"):
        await reconcile_invoice(sessions, vault, provider, "orphan")
