"""The automated checkout journey (manual_sales disabled)."""

from datetime import UTC, datetime, timedelta

import pytest
from conftest import add_user
from sqlalchemy import select

from app.bot.texts import ru
from app.db.models import (
    AppSetting,
    CryptoInvoice,
    Payment,
    Plan,
    Server,
    Subscription,
    SupportTicket,
    User,
    VPNClient,
)


@pytest.fixture
async def legacy(bot_driver, app_settings, app_sessions):
    app_settings(manual_sales=False)
    async with app_sessions.begin() as db:
        db.add(Server(name="NL", code="nl"))
    return bot_driver


@pytest.fixture
async def plans(app_sessions):
    async with app_sessions.begin() as db:
        records = [
            Plan(
                name="AERA PLUS",
                slug="plus-1m",
                price_minor=20000,
                duration_months=1,
                traffic_limit_bytes=0,
                device_limit=3,
                stars_price=200,
            ),
            Plan(
                name="AERA PLUS",
                slug="plus-6m",
                price_minor=100000,
                duration_months=6,
                duration_days=180,
                traffic_limit_bytes=0,
                device_limit=3,
            ),
            Plan(
                name="Custom",
                slug="custom",
                price_minor=5000,
                traffic_limit_bytes=0,
                device_limit=1,
            ),
        ]
        db.add_all(records)
        await db.flush()
        return {plan.slug: plan.id for plan in records}


async def user_of(app_sessions, telegram_id=1):
    async with app_sessions() as db:
        return await db.scalar(select(User).where(User.telegram_id == telegram_id))


# ---------- commands ----------


async def test_start_and_commands(legacy, app_sessions):
    await legacy.send(1, "/start")
    assert legacy.api.named("sendPhoto")[0].caption == ru.WELCOME
    await legacy.send(1, "/myid")
    assert legacy.api.last_text() == "Ваш Telegram ID: 1"
    await legacy.send(1, "/terms")
    assert legacy.api.last_text() == ru.TERMS
    async with app_sessions.begin() as db:
        db.add(AppSetting(key="privacy_text", value="Custom privacy"))
    await legacy.send(1, "/privacy")
    assert legacy.api.last_text() == "Custom privacy"


async def test_start_with_referral(legacy, app_sessions):
    await legacy.send(2, "/start")
    code = (await user_of(app_sessions, 2)).referral_code
    await legacy.send(1, f"/start ref_{code}")
    assert (await user_of(app_sessions, 1)).referred_by_id is not None


async def test_support_ticket_via_command_and_category(legacy, app_sessions):
    await legacy.send(1, "/support")
    assert legacy.api.last_text() == ru.SUPPORT
    await legacy.send(1, "")  # empty text is rejected but keeps the state
    await legacy.send(1, "x" * 4001)
    assert legacy.api.last_text() == ru.ERROR
    await legacy.click(1, "support")
    await legacy.click(1, "support:0")
    await legacy.send(1, "Нужна помощь")
    async with app_sessions() as db:
        ticket = await db.scalar(select(SupportTicket))
    assert ticket.category == ru.CATEGORIES[0]
    assert legacy.api.last_text() == ru.TICKET.format(id=ticket.id[:8])


# ---------- catalog ----------


async def test_catalog_family_and_plan(legacy, app_sessions, plans):
    async with app_sessions.begin() as db:
        db.add(AppSetting(key="trial_enabled", value="true"))
    await legacy.click(1, "plans")
    buttons = legacy.api.buttons()
    assert buttons["🎁 Попробовать AERA"] == "trial"
    assert "family:plus" in buttons.values() and f"plan:{plans['custom']}" in buttons.values()
    await legacy.click(1, "family:plus")
    assert f"plan:{plans['plus-6m']}" in legacy.api.buttons().values()
    await legacy.click(1, f"plan:{plans['plus-1m']}")
    assert legacy.api.buttons()["💳 Купить"] == f"checkout:{plans['plus-1m']}"


@pytest.mark.parametrize("data", ["family:nope", "plan:missing", "crypto:x", "pay:missing"])
async def test_invalid_actions_report_error(legacy, data):
    await legacy.click(1, data)
    assert legacy.api.last_text() == ru.ERROR


@pytest.mark.parametrize(
    "data, expected",
    [
        ("onboarding", ru.ONBOARDING),
        ("how", ru.HOW),
        ("connect", ru.DEVICES),
        ("connected", ru.CONNECTED),
        ("revoke-confirm", "Отключить текущую ссылку?"),
        ("subscription", ru.NO_SUB),
        ("devices", ru.NO_SUB),
        ("menu", ru.MENU),
    ],
)
async def test_static_screens(legacy, data, expected):
    await legacy.click(1, data)
    assert legacy.api.last_text() == expected


@pytest.mark.parametrize("device", ["ios", "android", "windows", "macos"])
async def test_device_instructions(legacy, device):
    await legacy.click(1, f"device:{device}")
    buttons = legacy.api.buttons()
    assert ("download:windows" in buttons.values()) == (device == "windows")
    assert ("📲 Установить Hiddify" in buttons) == (device == "macos")


async def test_referral_screen(legacy, app_sessions):
    await legacy.click(1, "referral")
    code = (await user_of(app_sessions)).referral_code
    assert f"start=ref_{code}" in legacy.api.last_text()


# ---------- purchase with mock payments ----------


async def test_buy_and_confirm_mock_payment(legacy, app_sessions, plans):
    await legacy.click(1, f"buy:{plans['plus-1m']}")
    async with app_sessions() as db:
        payment = await db.scalar(select(Payment))
    assert legacy.api.buttons()["Подтвердить тестовую оплату"] == f"pay:{payment.id}"
    await legacy.click(1, f"pay:{payment.id}")
    assert legacy.api.last_text() == ru.READY
    await legacy.click(1, "subscription")
    assert "AERA PLUS" in legacy.api.last_text()
    await legacy.click(1, "devices")
    assert "3 устройства" in legacy.api.last_text()


async def test_link_qr_rotate_and_revoke(legacy, app_sessions, plans):
    await legacy.click(1, f"buy:{plans['plus-1m']}")
    async with app_sessions() as db:
        payment_id = (await db.scalar(select(Payment))).id
    await legacy.click(1, f"pay:{payment_id}")
    await legacy.click(1, "link")
    first = legacy.api.last_text()
    assert "/sub/" in first and ru.ACCESS_NOTICE in first
    await legacy.click(1, "rotate")
    assert legacy.api.last_text() != first
    await legacy.click(1, "qr")
    assert legacy.api.named("sendPhoto")[-1].caption.startswith(ru.CLIENT_NOTE)
    await legacy.click(1, "revoke")
    assert legacy.api.last_text() == ru.REVOKED
    await legacy.click(1, "link")
    assert legacy.api.last_text() == ru.REVOKED


async def test_link_without_subscription(legacy):
    await legacy.click(1, "link")
    assert legacy.api.last_text() == ru.NO_SUB


async def test_promo_flow(legacy, app_sessions, plans):
    from app.db.models import PromoCode

    async with app_sessions.begin() as db:
        db.add(PromoCode(code="SALE", discount_type="PERCENT", discount_value=50))
    await legacy.click(1, f"promo:{plans['plus-1m']}")
    assert legacy.api.last_text() == ru.PROMO_PROMPT
    await legacy.send(1, "sale")
    async with app_sessions() as db:
        assert (await db.scalar(select(Payment))).amount_minor == 10000


async def test_trial_activation(legacy, app_sessions, plans):
    async with app_sessions.begin() as db:
        db.add(AppSetting(key="trial_enabled", value="true"))
    await legacy.click(1, "trial")
    assert legacy.api.last_text() == ru.READY
    async with app_sessions() as db:
        assert (await db.scalar(select(Subscription))).status == "TRIAL"


# ---------- Stars terms gate ----------


async def test_stars_purchase_requires_terms(legacy, app_settings, app_sessions, plans):
    app_settings(payment_provider="telegram_stars")
    await legacy.click(1, f"buy:{plans['plus-1m']}")
    assert legacy.api.last_text() == ru.TERMS_PENDING
    async with app_sessions.begin() as db:
        db.add(AppSetting(key="terms_text", value="Условия"))
    await legacy.click(1, f"buy:{plans['plus-1m']}")
    assert legacy.api.last_text() == "Условия"
    await legacy.click(1, f"accept:{plans['plus-1m']}")
    assert legacy.api.last_text() == ru.PAYMENT_PROMPT
    (invoice,) = legacy.api.named("createInvoiceLink")
    assert invoice.currency == "XTR" and invoice.prices[0].amount == 200


async def test_stars_promo_invoice(legacy, app_settings, app_sessions, plans):
    from app.db.models import PromoCode

    app_settings(payment_provider="telegram_stars")
    async with app_sessions.begin() as db:
        db.add(PromoCode(code="DAYS", discount_type="EXTRA_DAYS", discount_value=3))
        user = await add_user(db, 1)
        user.terms_accepted_at = datetime.now(UTC)
    await legacy.click(1, f"promo:{plans['plus-1m']}")
    await legacy.send(1, "days")
    # Stars invoices reject promo codes priced in another currency.
    assert legacy.api.last_text() == ru.ERROR


# ---------- crypto simulation ----------


async def test_crypto_simulation(legacy, app_settings, app_sessions, plans):
    app_settings(crypto_mock_enabled=True)
    await legacy.click(1, f"crypto:{plans['plus-1m']}")
    async with app_sessions() as db:
        invoice = await db.scalar(select(CryptoInvoice))
    assert legacy.api.buttons()["Проверить оплату"] == f"crypto-check:{invoice.id}"
    await legacy.click(1, f"crypto-check:{invoice.id}")
    assert legacy.api.last_text() == ru.CRYPTO_STATUSES["PENDING"]
    await legacy.click(2, f"crypto-check:{invoice.id}")
    assert legacy.api.last_text() == ru.ERROR


# ---------- checkout router ----------


async def test_checkout_methods_with_mocks(legacy, app_sessions, plans):
    await legacy.click(1, f"checkout:{plans['plus-1m']}")
    assert "Тестовая оплата" in legacy.api.last_text()
    assert f"method:card:{plans['plus-1m']}" in legacy.api.buttons().values()
    await legacy.click(1, f"method:card:{plans['plus-1m']}")
    async with app_sessions() as db:
        card = await db.scalar(select(Payment).where(Payment.provider == "mock"))
    assert legacy.api.buttons()["Подтвердить тестовую оплату"] == f"pay:{card.id}"
    await legacy.click(1, f"method:crypto:{plans['plus-1m']}")
    assert "crypto-check:" in str(legacy.api.buttons().values())
    await legacy.click(1, f"method:stars:{plans['plus-1m']}")
    async with app_sessions() as db:
        assert await db.scalar(select(Payment).where(Payment.provider == "stars_mock"))


async def test_checkout_with_real_providers(legacy, app_settings, app_sessions, plans, monkeypatch):
    from app.services import checkout as checkout_module

    class YooKassa:
        def __init__(self, *args):
            pass

        async def create_payment(self, payment_id, *args):
            return {
                "id": "yk-" + payment_id,
                "confirmation": {"confirmation_url": "https://yoomoney/pay"},
            }

        async def verify_payment(self, provider_id):
            return {
                "id": provider_id,
                "status": "pending",
                "paid": False,
                "test": False,
                "metadata": {"payment_id": provider_id[3:]},
                "amount": {"value": "200.00", "currency": "RUB"},
            }

        async def close(self):
            pass

    monkeypatch.setattr(checkout_module, "YooKassaProvider", YooKassa)
    app_settings(payment_provider="telegram_stars", card_provider="yookassa")
    await legacy.click(1, f"method:card:{plans['plus-1m']}")
    assert legacy.api.last_text() == ru.TERMS_PENDING
    async with app_sessions.begin() as db:
        db.add(AppSetting(key="terms_text", value="Условия"))
    await legacy.click(1, f"method:card:{plans['plus-1m']}")
    assert legacy.api.buttons()["Прочитал и согласен"].startswith("agreeorder:card:")
    await legacy.click(1, f"agreeorder:card:{plans['plus-1m']}")
    assert legacy.api.buttons()["Оплатить у провайдера"] == "https://yoomoney/pay"
    check = legacy.api.buttons()["Проверить оплату"]
    await legacy.click(1, check)
    assert legacy.api.last_text() == ru.EXTERNAL_PAYMENT_STATUS["PENDING"]
    await legacy.click(1, f"method:stars:{plans['plus-1m']}")
    assert "Проверить оплату" not in legacy.api.buttons()
    assert legacy.api.named("createInvoiceLink")


async def test_checkorder_rejects_foreign_payment(legacy, app_sessions, plans):
    async with app_sessions.begin() as db:
        owner = await add_user(db, 2)
        payment = Payment(
            user_id=owner.id,
            plan_id=plans["plus-1m"],
            provider="yookassa",
            amount_minor=1,
            currency="RUB",
        )
        db.add(payment)
        await db.flush()
        payment_id = payment.id
    await legacy.click(1, f"checkorder:{payment_id}")
    alert = legacy.api.named("answerCallbackQuery")[-1]
    assert alert.text == ru.ERROR and alert.show_alert
    async with app_sessions() as db:
        assert (await db.get(Payment, payment_id)).status == "PENDING"


async def test_expired_subscription_link_hidden(legacy, app_sessions, plans):
    await legacy.click(1, f"buy:{plans['plus-1m']}")
    async with app_sessions() as db:
        payment_id = (await db.scalar(select(Payment))).id
    await legacy.click(1, f"pay:{payment_id}")
    async with app_sessions.begin() as db:
        (await db.scalar(select(VPNClient))).enabled = False
        (await db.scalar(select(Subscription))).expires_at = datetime.now(UTC) - timedelta(1)
    await legacy.click(1, "link")
    assert legacy.api.last_text() == ru.NO_SUB
