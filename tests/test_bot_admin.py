from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from conftest import add_order, add_user
from sqlalchemy import select

from app.bot.texts import ru
from app.db.models import (
    AppSetting,
    ManualAccess,
    Notification,
    PaidLink,
    Payment,
    Plan,
    SaleOrder,
    Server,
    Subscription,
    SupportMessage,
    SupportTicket,
    TrialLink,
    User,
)

ADMIN = 999


@pytest.fixture
def plan_id(app_plan_id):
    return app_plan_id


async def edited(driver):
    return driver.api.named("editMessageText")[-1]


# ---------- access and overview ----------


async def test_admin_command_requires_admin(bot_driver):
    await bot_driver.send(1, "/admin")
    assert bot_driver.api.last_text() == ru.ACCESS_DENIED
    await bot_driver.send(ADMIN, "/admin")
    assert bot_driver.api.last_text().startswith(ru.ADMIN_TITLE)
    assert "ПРОБНЫЕ ПОДПИСКИ" in bot_driver.api.last_text()


async def test_admin_callbacks_require_admin(bot_driver):
    for data in ("ad:menu", "ad:portal:stock"):
        await bot_driver.click(1, data)
    alerts = bot_driver.api.named("answerCallbackQuery")
    assert all(alert.show_alert for alert in alerts) and len(alerts) == 2


async def test_legacy_sections_redirect_in_manual_mode(bot_driver):
    for data in ("ad:analytics", "ad:requests", "ad:payments"):
        await bot_driver.click(ADMIN, data)
    texts = bot_driver.api.texts()
    assert "автоматически" in texts[0] and texts[1].startswith(ru.ADMIN_TITLE)


async def test_automatic_mode_sections(bot_driver, app_settings, app_sessions, plan_id):
    app_settings(manual_sales=False)
    async with app_sessions.begin() as db:
        user = await add_user(db, 1)
        db.add(Server(name="NL", code="nl"))
        db.add(
            Payment(
                user_id=user.id, plan_id=plan_id, amount_minor=100, currency="RUB", status="PAID"
            )
        )
        db.add(
            Subscription(
                user_id=user.id,
                plan_id=plan_id,
                expires_at=datetime.now(UTC),
                traffic_limit_bytes=0,
                device_limit=1,
            )
        )
    await bot_driver.send(ADMIN, "/admin")
    assert bot_driver.api.last_text() == ru.ADMIN_TITLE
    await bot_driver.click(ADMIN, "ad:analytics")
    assert "revenue: {'RUB': 100}" in bot_driver.api.last_text()
    for section in ("servers", "payments", "subscriptions"):
        await bot_driver.click(ADMIN, f"ad:{section}")
        assert bot_driver.api.last_text() != ru.ADMIN_EMPTY
    await bot_driver.click(ADMIN, f"ad:user:{user.id}")
    assert f"ad:confirm:extend:{user.id}" in bot_driver.api.buttons().values()
    await bot_driver.click(ADMIN, f"ad:confirm:extend:{user.id}")
    assert bot_driver.api.last_text() == ru.ADMIN_CONFIRM
    await bot_driver.click(ADMIN, f"ad:apply:extend:{user.id}")
    assert bot_driver.api.last_text() == ru.ADMIN_SAVED


async def test_plans_listing(bot_driver, app_sessions, plan_id):
    async with app_sessions.begin() as db:
        db.add(
            Plan(
                name="Old",
                slug="old",
                price_minor=1,
                traffic_limit_bytes=1024**3,
                device_limit=1,
                is_active=False,
            )
        )
    await bot_driver.click(ADMIN, "ad:plans")
    text = bot_driver.api.last_text()
    assert text.startswith("ТАРИФЫ") and "Отключён для выбора" in text and "Трафик: 1 ГБ" in text


# ---------- customers ----------


async def paid_customer(app_sessions, plan_id, telegram_id=1, **link_values):
    async with app_sessions.begin() as db:
        user, ticket, order = await add_order(db, plan_id, telegram_id, username="client")
        order.paid_at = datetime.now(UTC)
        values = dict(status="WAITING", issued_at=datetime.now(UTC))
        values.update(link_values)
        db.add(
            PaidLink(
                label="key-1",
                client_uuid=str(uuid4()),
                link_encrypted="x",
                plan_id=plan_id,
                order_id=order.id,
                user_id=user.id,
                **values,
            )
        )
        return user.id, ticket.id


async def test_users_list_and_card(bot_driver, app_sessions, plan_id):
    await bot_driver.click(ADMIN, "ad:users")
    assert "пока нет" in bot_driver.api.last_text()
    user_id, _ = await paid_customer(app_sessions, plan_id)
    await bot_driver.click(ADMIN, "ad:users")
    assert f"ad:user:{user_id}" in bot_driver.api.buttons().values()
    await bot_driver.click(ADMIN, f"ad:user:{user_id}")
    text = bot_driver.api.last_text()
    assert "Ожидает первого подключения" in text and "key-1" in text
    assert bot_driver.api.buttons()["Написать пользователю"] == "https://t.me/client"


async def test_active_customer_card_shows_expiry(bot_driver, app_sessions, plan_id):
    user_id, _ = await paid_customer(
        app_sessions, plan_id, status="ACTIVE", expires_at=datetime.now(UTC) + timedelta(days=3)
    )
    await bot_driver.click(ADMIN, f"ad:user:{user_id}")
    assert "Подписка действует" in bot_driver.api.last_text()


async def test_user_card_for_missing_or_non_customer(bot_driver, app_sessions):
    async with app_sessions.begin() as db:
        user_id = (await add_user(db, 1)).id
    await bot_driver.click(ADMIN, f"ad:user:{user_id}")
    assert "нет действующей" in bot_driver.api.last_text()
    await bot_driver.click(ADMIN, "ad:user:missing")
    assert bot_driver.api.last_text() == "Пользователь уже удалён."


async def test_legacy_manual_access_card(bot_driver, app_sessions, plan_id):
    async with app_sessions.begin() as db:
        user, _, order = await add_order(db, plan_id, 1)
        order.paid_at = datetime.now(UTC)
        db.add(
            ManualAccess(
                user_id=user.id,
                plan_id=plan_id,
                started_at=datetime.now(UTC),
                expires_at=datetime.now(UTC) + timedelta(days=5),
            )
        )
        user_id = user.id
    await bot_driver.click(ADMIN, f"ad:user:{user_id}")
    assert "📅 До:" in bot_driver.api.last_text()


# ---------- support tickets ----------


async def support_ticket(app_sessions, telegram_id=1):
    async with app_sessions.begin() as db:
        user = await add_user(db, telegram_id)
        ticket = SupportTicket(user_id=user.id, category="Поддержка", subject="Помогите")
        db.add(ticket)
        await db.flush()
        db.add(
            SupportMessage(
                ticket_id=ticket.id, sender_type="USER", sender_id=user.id, text="Не подключается"
            )
        )
        return ticket.id


async def test_support_reply_flow(bot_driver, app_sessions):
    ticket_id = await support_ticket(app_sessions)
    await bot_driver.click(ADMIN, "ad:support")
    assert f"ad:ticket:{ticket_id}" in bot_driver.api.buttons().values()
    await bot_driver.click(ADMIN, f"ad:ticket:{ticket_id}")
    assert "Не подключается" in bot_driver.api.last_text()
    await bot_driver.send(ADMIN, "Перезапустите Hiddify")
    assert bot_driver.api.last_text().startswith("Ответ сохранён")
    (reply,) = [m for m in bot_driver.api.named("sendMessage") if m.chat_id == 1]
    assert reply.text.endswith("Перезапустите Hiddify")
    async with app_sessions() as db:
        assert (await db.get(SupportTicket, ticket_id)).status == "IN_PROGRESS"


async def test_reply_to_deleted_ticket(bot_driver, app_sessions):
    ticket_id = await support_ticket(app_sessions)
    await bot_driver.click(ADMIN, f"ad:ticket:{ticket_id}")
    async with app_sessions.begin() as db:
        ticket = await db.get(SupportTicket, ticket_id)
        for message in await db.scalars(select(SupportMessage)):
            await db.delete(message)
        await db.flush()
        await db.delete(ticket)
    await bot_driver.send(ADMIN, "hello")
    assert bot_driver.api.last_text() == "Обращение уже удалено."


async def test_delete_support_ticket(bot_driver, app_sessions):
    ticket_id = await support_ticket(app_sessions)
    await bot_driver.click(ADMIN, f"ad:delete-support:{ticket_id}")
    assert "Удалить это обращение" in bot_driver.api.last_text()
    await bot_driver.click(ADMIN, f"ad:erase:{ticket_id}")
    assert bot_driver.api.last_text() == "Обращение и переписка удалены из данных бота."
    async with app_sessions() as db:
        assert await db.get(SupportTicket, ticket_id) is None
    await bot_driver.click(ADMIN, f"ad:ticket:{ticket_id}")
    assert bot_driver.api.last_text() == "Заявка не найдена."
    await bot_driver.click(ADMIN, f"ad:delete-support:{ticket_id}")
    assert bot_driver.api.last_text() == "Обращение уже удалено."
    await bot_driver.click(ADMIN, f"ad:refuse:{ticket_id}")  # manual mode: overview instead
    assert bot_driver.api.last_text().startswith(ru.ADMIN_TITLE)


async def test_delete_busy_ticket_offers_retry(bot_driver, app_sessions):
    ticket_id = await support_ticket(app_sessions)
    async with app_sessions.begin() as db:
        message = await db.scalar(select(SupportMessage))
        user = await db.scalar(select(User).where(User.telegram_id == 1))
        db.add(
            Notification(
                dedupe_key=f"support:{message.id}",
                user_id=user.id,
                notification_type="SUPPORT",
                text="t",
                status="SENDING",
            )
        )
    await bot_driver.click(ADMIN, f"ad:erase:{ticket_id}")
    assert "отправляется" in bot_driver.api.last_text()
    assert f"ad:erase:{ticket_id}" in bot_driver.api.buttons().values()


async def test_tariff_requests_are_hidden_in_manual_mode(bot_driver, app_sessions, plan_id):
    _, ticket_id = await paid_customer(app_sessions, plan_id)
    await bot_driver.click(ADMIN, f"ad:ticket:{ticket_id}")
    assert bot_driver.api.last_text().startswith(ru.ADMIN_TITLE)


# ---------- tariff requests in automatic mode ----------


async def test_request_lifecycle_in_automatic_mode(bot_driver, app_settings, app_sessions, plan_id):
    app_settings(manual_sales=False)
    async with app_sessions.begin() as db:
        user, ticket, order = await add_order(db, plan_id, 1)
        db.add(
            SupportMessage(
                ticket_id=ticket.id,
                sender_type="TARIFF_SELECTION",
                sender_id=plan_id,
                text="ЗАЯВКА НА ТАРИФ",
            )
        )
        ticket_id = ticket.id
    await bot_driver.click(ADMIN, f"ad:ticket:{ticket_id}")
    assert "после подтверждения оплаты" in bot_driver.api.last_text()
    await bot_driver.click(ADMIN, f"ad:connected:{ticket_id}")
    assert "Оплата пока не подтверждена" in bot_driver.api.last_text()
    async with app_sessions.begin() as db:
        (await db.get(SaleOrder, order.id)).paid_at = datetime.now(UTC)
    await bot_driver.click(ADMIN, f"ad:ticket:{ticket_id}")
    assert bot_driver.api.last_text().startswith("✅ ОПЛАЧЕНО")
    await bot_driver.click(ADMIN, f"ad:close:{ticket_id}")
    assert bot_driver.api.last_text() == "Чем завершилась заявка?"
    await bot_driver.click(ADMIN, f"ad:connected:{ticket_id}")
    assert bot_driver.api.last_text().startswith("Клиент подключён")
    await bot_driver.click(ADMIN, f"ad:ticket:{ticket_id}")
    assert bot_driver.api.last_text().startswith("Архивная заявка")


async def test_refuse_request_forgets_prospect(bot_driver, app_settings, app_sessions, plan_id):
    app_settings(manual_sales=False)
    async with app_sessions.begin() as db:
        user, ticket, _ = await add_order(db, plan_id, 5)
        ticket_id, user_id = ticket.id, user.id
    await bot_driver.click(ADMIN, f"ad:refuse:{ticket_id}")
    assert "Клиент отказался" in bot_driver.api.last_text()
    await bot_driver.click(ADMIN, f"ad:erase:{ticket_id}")
    assert bot_driver.api.last_text().startswith("Заявки и данные отказавшегося")
    async with app_sessions() as db:
        assert await db.get(User, user_id) is None


# ---------- portal admin: stock, trials, pricing ----------


async def test_paid_stock_overview_and_import(bot_driver, app_sessions, plan_id):
    await bot_driver.click(ADMIN, "ad:portal:stock")
    assert "свободно 0 / всего 0" in bot_driver.api.last_text()
    await bot_driver.click(ADMIN, f"ad:portal:stock-plan:{plan_id}")
    assert "Пока нет ссылок." in bot_driver.api.last_text()
    await bot_driver.click(ADMIN, f"ad:portal:add-stock:{plan_id}")
    await bot_driver.send(ADMIN, "not a link")
    assert bot_driver.api.last_text().startswith("⚙️ Проверь формат")
    await bot_driver.send(ADMIN, "\n".join(f"vless://{uuid4()}@h:1#k{i}" for i in range(2)))
    assert bot_driver.api.last_text().startswith("✅ Ссылки добавлены")
    async with app_sessions() as db:
        assert len(list(await db.scalars(select(PaidLink)))) == 2
    await bot_driver.click(ADMIN, f"ad:portal:add-stock:{plan_id}")
    await bot_driver.send(ADMIN, "\n".join(["vless://x@h"] * 31))
    assert bot_driver.api.last_text().startswith("🔗 Пришли от 1 до 30")


async def test_trial_stock_views(bot_driver, app_sessions, vault):
    async with app_sessions.begin() as db:
        user = await add_user(
            db,
            1,
        )
        user.username = "tester"
        record = TrialLink(
            label="T1",
            client_uuid="t",
            status="UNAVAILABLE",
            user_id=user.id,
            expires_at=datetime.now(UTC),
            link_encrypted=vault.cipher.encrypt(b"vless://trial").decode(),
        )
        db.add(record)
        await db.flush()
        record_id = record.id
    await bot_driver.click(ADMIN, "ad:portal:trials")
    assert "⚙️ Недоступно: 1" in bot_driver.api.last_text()
    await bot_driver.click(ADMIN, f"ad:portal:trial:{record_id}")
    text = bot_driver.api.last_text()
    assert "@tester" in text and "vless://trial" in text and "📅 До:" in text


async def test_stars_pricing(bot_driver, app_sessions, plan_id):
    await bot_driver.click(ADMIN, "ad:portal:pricing")
    assert "Счета: выключены" in bot_driver.api.last_text()
    await bot_driver.click(ADMIN, "ad:portal:toggle-stars")
    async with app_sessions() as db:
        assert (await db.get(AppSetting, "manual_stars_enabled")).value == "true"
    await bot_driver.click(ADMIN, f"ad:portal:price:{plan_id}")
    await bot_driver.send(ADMIN, "0")
    assert bot_driver.api.last_text().startswith("⭐ Пришли целое число")
    await bot_driver.send(ADMIN, "250")
    async with app_sessions() as db:
        assert (await db.get(Plan, plan_id)).stars_price == 250


async def test_manual_access_link_and_expiry(bot_driver, app_sessions, plan_id):
    async with app_sessions.begin() as db:
        user = await add_user(db, 1)
        access = ManualAccess(
            user_id=user.id,
            plan_id=plan_id,
            started_at=datetime.now(UTC),
            expires_at=datetime.now(UTC),
        )
        db.add(access)
        nobody = await add_user(db, 2)
        user_id, nobody_id = user.id, nobody.id
    await bot_driver.click(ADMIN, f"ad:portal:link:{nobody_id}")
    assert bot_driver.api.last_text() == "Сначала отметь клиента подключённым."
    await bot_driver.click(ADMIN, f"ad:portal:link:{user_id}")
    await bot_driver.send(ADMIN, "ftp://bad")
    assert bot_driver.api.last_text().startswith("🔗 Пришли личную VLESS")
    await bot_driver.send(ADMIN, "vless://personal@h:1")
    assert bot_driver.api.last_text().startswith("✅ Ссылка сохранена")
    await bot_driver.click(ADMIN, f"ad:portal:expiry:{user_id}")
    await bot_driver.send(ADMIN, "tomorrow")
    assert bot_driver.api.last_text().startswith("📅 Формат")
    await bot_driver.send(ADMIN, "05.11.2026 18:00")
    async with app_sessions() as db:
        access = await db.scalar(select(ManualAccess))
        assert access.link_encrypted and access.expires_at.day == 5
        assert await db.scalar(select(Notification))


async def test_paid_order_status(bot_driver, app_sessions, plan_id):
    await bot_driver.click(ADMIN, "ad:portal:paid:missing")
    assert bot_driver.api.last_text().startswith("Заявка без нового счёта")
    async with app_sessions.begin() as db:
        _, ticket, order = await add_order(db, plan_id, 1)
        ticket_id = ticket.id
    await bot_driver.click(ADMIN, f"ad:portal:paid:{ticket_id}")
    assert bot_driver.api.last_text().startswith("⭐ Сейчас работает оплата")
