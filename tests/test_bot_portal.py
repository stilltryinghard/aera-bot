from datetime import UTC, datetime, timedelta

import pytest
from conftest import add_order
from sqlalchemy import select

from app.bot.texts.portal import text as tr
from app.db.models import (
    AppSetting,
    ManualAccess,
    Notification,
    PaidLink,
    Payment,
    Plan,
    Referral,
    SaleOrder,
    SupportTicket,
    TrialLink,
    User,
)

FK = dict(
    freekassa_enabled=True,
    card_provider="freekassa",
    freekassa_merchant_id="123",
    freekassa_secret1="s1",
    freekassa_secret2="s2",
)


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
                name="AERA BUSINESS",
                slug="business-1m",
                price_minor=500000,
                duration_months=1,
                traffic_limit_bytes=0,
                device_limit=1,
                unlimited_devices=True,
            ),
        ]
        db.add_all(records)
        await db.flush()
        return {plan.slug: plan.id for plan in records}


async def user_of(app_sessions, telegram_id=1):
    async with app_sessions() as db:
        return await db.scalar(select(User).where(User.telegram_id == telegram_id))


async def order_of(app_sessions, telegram_id=1):
    async with app_sessions() as db:
        user = await db.scalar(select(User).where(User.telegram_id == telegram_id))
        return await db.scalar(
            select(SaleOrder).join(SupportTicket).where(SupportTicket.user_id == user.id)
        )


# ---------- entry and navigation ----------


async def test_start_registers_user_with_referral_and_shows_languages(bot_driver, app_sessions):
    await bot_driver.send(2, "/start")
    referrer = await user_of(app_sessions, 2)
    bot_driver.api.clear()
    await bot_driver.send(1, f"/start ref_{referrer.referral_code}")
    (photo,) = bot_driver.api.named("sendPhoto")
    assert photo.caption == tr("language", "ru")
    assert "language:en" in {
        b.callback_data for row in photo.reply_markup.inline_keyboard for b in row
    }
    async with app_sessions() as db:
        assert (await db.scalar(select(Referral))).referrer_user_id == referrer.id


async def test_language_selection(bot_driver, app_sessions):
    await bot_driver.click(1, "language:en")
    assert bot_driver.api.last_text() == tr("welcome", "en")
    assert (await user_of(app_sessions)).language_code == "en"
    await bot_driver.click(1, "language")
    assert bot_driver.api.last_text() == tr("language", "en")
    await bot_driver.click(1, "language:xx")
    assert bot_driver.api.last_text() == tr("error", "xx")
    assert (await user_of(app_sessions)).language_code == "en"


@pytest.mark.parametrize(
    "data",
    [
        "menu",
        "privacy",
        "privacy:1",
        "privacy:2",
        "how",
        "connect",
        "devices",
        "trial",
        "referral",
        "support",
    ],
)
async def test_static_screens(bot_driver, data):
    await bot_driver.click(1, data)
    assert bot_driver.api.last_text() not in (None, tr("error", "ru"))


@pytest.mark.parametrize("platform", ["ios", "android", "windows", "macos"])
async def test_platform_guides(bot_driver, platform):
    await bot_driver.click(1, f"guide:{platform}")
    assert bot_driver.api.last_text() == tr(platform, "ru")
    assert ("download:windows" in bot_driver.api.buttons().values()) == (platform == "windows")


@pytest.mark.parametrize("data", ["privacy:7", "guide:linux", "family:nope", "order:missing"])
async def test_invalid_actions_show_error(bot_driver, data):
    await bot_driver.click(1, data)
    assert bot_driver.api.last_text() == tr("error", "ru")


async def test_blocked_user_gets_error(bot_driver, app_sessions):
    await bot_driver.send(1, "/start")
    async with app_sessions.begin() as db:
        (await db.scalar(select(User).where(User.telegram_id == 1))).is_blocked = True
    await bot_driver.click(1, "menu")
    assert bot_driver.api.last_text() == tr("error", "ru")


async def test_windows_installer_unavailable(bot_driver):
    await bot_driver.click(1, "download:windows")
    assert bot_driver.api.last_text() == tr("error", "ru")


# ---------- catalog and order ----------


async def test_catalog_and_family(bot_driver, plans):
    await bot_driver.click(1, "plans")
    text = bot_driver.api.last_text()
    assert "AERA PLUS" in text and "AERA BUSINESS" in text and tr("business", "ru") in text
    assert set(bot_driver.api.buttons().values()) >= {"family:plus", "family:business", "menu"}
    await bot_driver.click(1, "family:plus")
    assert {f"plan:{plans['plus-1m']}", f"plan:{plans['plus-6m']}"} <= set(
        bot_driver.api.buttons().values()
    )


async def test_plan_creates_order_with_payment_options(bot_driver, app_sessions, plans):
    await bot_driver.click(1, f"plan:{plans['plus-1m']}")
    order = await order_of(app_sessions)
    buttons = bot_driver.api.buttons()
    assert buttons["⭐ Telegram Stars · 200"] == f"terms:{order.id}"
    assert f"cancelorder:{order.id}" in buttons.values()
    assert f"sbp:{order.id}" in buttons.values()
    await bot_driver.click(1, f"order:{order.id}")  # reopening shows the same order
    assert (await order_of(app_sessions)).id == order.id


async def test_freekassa_buttons_open_signed_checkout(
    bot_driver, app_sessions, app_settings, plans
):
    app_settings(**FK)
    await bot_driver.click(1, f"plan:{plans['plus-1m']}")
    order = await order_of(app_sessions)
    urls = [v for v in bot_driver.api.buttons().values() if v and v.startswith("https://")]
    assert len(urls) == 2 and all("checkout?intent=" in url for url in urls)
    await bot_driver.click(1, f"termsfk:42:{order.id}")
    assert bot_driver.api.last_text() == tr("terms", "ru")
    await bot_driver.click(1, f"fkpay:42:{order.id}")
    assert "К оплате: 200 ₽" in bot_driver.api.last_text()
    assert any("method=42" in (v or "") for v in bot_driver.api.buttons().values())
    assert (await user_of(app_sessions)).terms_version == "portal-2"


async def test_terms_and_payment_placeholders(bot_driver, app_sessions, plans):
    await bot_driver.click(1, f"plan:{plans['plus-1m']}")
    order = await order_of(app_sessions)
    await bot_driver.click(1, f"terms:{order.id}")
    assert bot_driver.api.last_text() == tr("terms", "ru")
    for data in (f"sbp:{order.id}", f"cryptosoon:{order.id}"):
        await bot_driver.click(1, data)
        assert bot_driver.api.last_text() == tr("payments_soon", "ru")
    await bot_driver.click(2, f"terms:{order.id}")  # someone else's order
    assert bot_driver.api.last_text() == tr("error", "ru")


async def test_stars_invoice_from_order(bot_driver, app_sessions, plans):
    await bot_driver.click(1, f"plan:{plans['plus-1m']}")
    order = await order_of(app_sessions)
    await bot_driver.click(1, f"stars:{order.id}")
    assert bot_driver.api.last_text() == tr("soon", "ru")  # Stars disabled
    async with app_sessions.begin() as db:
        db.add(AppSetting(key="manual_stars_enabled", value="true"))
    await bot_driver.click(1, f"stars:{order.id}")
    (invoice,) = bot_driver.api.named("createInvoiceLink")
    assert invoice.prices[0].amount == 200
    assert bot_driver.api.buttons()[tr("pay_button", "ru")].startswith("https://t.me/$invoice")


async def test_cancel_order(bot_driver, app_sessions, plans):
    await bot_driver.click(1, f"plan:{plans['plus-1m']}")
    order = await order_of(app_sessions)
    await bot_driver.click(1, f"cancelorder:{order.id}")
    assert bot_driver.api.last_text() == "Покупка отменена."
    async with app_sessions() as db:
        assert (await db.get(SupportTicket, order.ticket_id)).status == "CLOSED"


async def test_cancel_refused_after_precheckout(bot_driver, app_sessions, plans):
    await bot_driver.click(1, f"plan:{plans['plus-1m']}")
    order = await order_of(app_sessions)
    async with app_sessions.begin() as db:
        user = await db.scalar(select(User).where(User.telegram_id == 1))
        payment = Payment(
            user_id=user.id,
            plan_id=order.plan_id,
            provider="telegram_stars",
            amount_minor=200,
            currency="XTR",
            details={"precheckout_accepted": True},
        )
        db.add(payment)
        await db.flush()
        (await db.get(SaleOrder, order.id)).payment_id = payment.id
    await bot_driver.click(1, f"cancelorder:{order.id}")
    assert "обрабатывается" in bot_driver.api.last_text()


async def test_paid_order_screen(bot_driver, app_sessions, plans):
    await bot_driver.click(1, f"plan:{plans['plus-1m']}")
    order = await order_of(app_sessions)
    async with app_sessions.begin() as db:
        (await db.get(SaleOrder, order.id)).paid_at = datetime.now(UTC)
    await bot_driver.click(1, f"order:{order.id}")
    assert bot_driver.api.last_text() == tr("paid", "ru")


# ---------- profile, purchases, links ----------


async def test_profile_without_access(bot_driver):
    await bot_driver.click(1, "profile")
    assert tr("none", "ru") in bot_driver.api.last_text()


async def paid_customer(app_sessions, plan_id, vault, **link_values):
    async with app_sessions.begin() as db:
        user, ticket, order = await add_order(db, plan_id, 1)
        order.paid_at = datetime.now(UTC)
        values = dict(status="WAITING", issued_at=datetime.now(UTC), checked_at=datetime.now(UTC))
        values.update(link_values)
        db.add(
            PaidLink(
                label="k",
                client_uuid="c",
                plan_id=plan_id,
                order_id=order.id,
                user_id=user.id,
                link_encrypted=vault.cipher.encrypt(b"vless://mine").decode(),
                **values,
            )
        )
        return user.id


async def test_profile_and_link_for_paid_customer(bot_driver, app_sessions, plans, vault):
    await paid_customer(app_sessions, plans["plus-1m"], vault)
    await bot_driver.click(1, "profile")
    assert tr("WAITING", "ru") in bot_driver.api.last_text()
    await bot_driver.click(1, "link")
    assert "vless://mine" in bot_driver.api.last_text()


async def test_profile_with_active_expiry(bot_driver, app_sessions, plans, vault):
    await paid_customer(
        app_sessions,
        plans["plus-1m"],
        vault,
        status="ACTIVE",
        expires_at=datetime.now(UTC) + timedelta(days=3),
    )
    await bot_driver.click(1, "profile")
    assert tr("ACTIVE", "ru") in bot_driver.api.last_text()


async def test_profile_and_link_for_legacy_manual_access(bot_driver, app_sessions, plans, vault):
    async with app_sessions.begin() as db:
        user, _, _ = await add_order(db, plans["plus-1m"], 1)
        db.add(
            ManualAccess(
                user_id=user.id,
                plan_id=plans["plus-1m"],
                started_at=datetime.now(UTC),
                expires_at=datetime.now(UTC) + timedelta(days=10),
                link_encrypted=vault.cipher.encrypt(b"vless://legacy").decode(),
            )
        )
    await bot_driver.click(1, "profile")
    assert tr("ACTIVE", "ru") in bot_driver.api.last_text()
    await bot_driver.click(1, "qr")
    assert "vless://legacy" in bot_driver.api.last_text()


async def test_no_link_message(bot_driver):
    await bot_driver.click(1, "link")
    assert bot_driver.api.last_text() == tr("no_link", "ru")


async def test_purchase_history_pagination(bot_driver, app_sessions, plans):
    async with app_sessions.begin() as db:
        user, _, first = await add_order(db, plans["plus-1m"], 1)
        first.paid_at = datetime.now(UTC)
        for index in range(6):
            ticket = SupportTicket(user_id=user.id, category="TARIFF_REQUEST", subject="s")
            db.add(ticket)
            await db.flush()
            db.add(
                SaleOrder(
                    ticket_id=ticket.id,
                    plan_id=plans["plus-1m"],
                    amount_rub_minor=100,
                    paid_at=datetime.now(UTC),
                )
            )
    await bot_driver.click(1, "subscription")
    assert "subscription:1" in bot_driver.api.buttons().values()
    await bot_driver.click(1, "subscription:1")
    assert "subscription:0" in bot_driver.api.buttons().values()


async def test_empty_purchase_history(bot_driver):
    await bot_driver.click(1, "subscription")
    assert tr("empty_requests", "ru") in bot_driver.api.last_text()


# ---------- trial ----------


async def test_trial_claim_flow(bot_driver, app_sessions, vault):
    await bot_driver.send(999, "/start")  # admin account exists
    await bot_driver.click(1, "trial:get")
    assert bot_driver.api.last_text() == tr("trial_empty", "ru")
    async with app_sessions() as db:
        assert (await db.scalar(select(Notification))).dedupe_key.startswith("trial-empty:")
    async with app_sessions.begin() as db:
        db.add(
            TrialLink(
                label="t",
                client_uuid="t1",
                status="FREE",
                checked_at=datetime.now(UTC),
                link_encrypted=vault.cipher.encrypt(b"vless://trial").decode(),
            )
        )
    await bot_driver.click(2, "trial:get")
    assert "vless://trial" in bot_driver.api.last_text()
    await bot_driver.click(2, "profile")
    assert tr("trial", "ru") in bot_driver.api.last_text()
    await bot_driver.click(2, "link")
    assert "vless://trial" in bot_driver.api.last_text()


# ---------- support and commands ----------


async def test_support_conversation(bot_driver, app_sessions):
    await bot_driver.click(1, "support")
    await bot_driver.send(1, "Не работает VPN")
    assert bot_driver.api.last_text().startswith(tr("ticket_sent", "ru", number="")[:10])
    async with app_sessions() as db:
        ticket = await db.scalar(select(SupportTicket))
        assert ticket.category == "Поддержка"
        note = await db.scalar(select(Notification))
        assert note.notification_type == "SUPPORT_ADMIN"


async def test_support_command_and_long_message(bot_driver):
    await bot_driver.send(1, "/support")
    assert bot_driver.api.last_text() == tr("support_info", "ru")
    await bot_driver.send(1, "x" * 4001)
    assert bot_driver.api.last_text() == tr("error", "ru")


@pytest.mark.parametrize("command, key", [("/terms", "terms")])
async def test_legal_commands(bot_driver, command, key):
    await bot_driver.send(1, command)
    assert bot_driver.api.last_text() == tr(key, "ru")


async def test_privacy_command(bot_driver):
    from app.bot.texts.privacy import privacy_page

    await bot_driver.send(1, "/privacy")
    assert bot_driver.api.last_text() == privacy_page("ru", 0)


# ---------- website-selected checkout ----------


async def website_message(bot_driver, user_id=1):
    from aiogram.types import Message
    from bot_harness import message_payload

    return Message.model_validate(
        message_payload(user_id, "/start"), context={"bot": bot_driver.bot}
    )


async def test_send_selected_checkout_stars(bot_driver, app_sessions, plans):
    from app.bot.portal import send_selected_checkout

    message = await website_message(bot_driver)
    await send_selected_checkout(message, plans["plus-1m"])
    assert "⭐ 200 Stars" in bot_driver.api.last_text()  # terms first
    async with app_sessions.begin() as db:
        db.add(AppSetting(key="manual_stars_enabled", value="true"))
        (
            await db.scalar(select(User).where(User.telegram_id == 1))
        ).terms_accepted_at = datetime.now(UTC)
    await send_selected_checkout(message, plans["plus-1m"])
    assert bot_driver.api.named("createInvoiceLink")


async def test_send_selected_checkout_errors(bot_driver, plans):
    from app.bot.portal import send_selected_checkout

    message = await website_message(bot_driver)
    await send_selected_checkout(message, "missing")
    assert bot_driver.api.last_text().startswith("Не удалось открыть оплату")
    await send_selected_checkout(message, plans["plus-1m"], "rub")  # FreeKassa disabled
    assert bot_driver.api.last_text().startswith("Не удалось открыть оплату")


async def test_send_selected_rub(bot_driver, app_settings, plans):
    from app.bot.portal import send_selected_checkout

    app_settings(**FK)
    message = await website_message(bot_driver)
    await send_selected_checkout(message, plans["plus-1m"], "rub")
    assert "К оплате: 200 ₽" in bot_driver.api.last_text()
    assert sum("checkout?intent=" in (v or "") for v in bot_driver.api.buttons().values()) == 2
