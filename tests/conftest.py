import os
import tempfile

# app.db.session builds an engine at import time; point it at a throwaway SQLite file
# (an in-memory URL would give every pooled connection its own empty database).
# Set TEST_DATABASE_URL to run the whole suite against PostgreSQL instead.
_TEST_DB = os.environ.get("TEST_DATABASE_URL")
_APP_DB = os.path.join(tempfile.mkdtemp(prefix="aera-tests-"), "app.db")
os.environ["DATABASE_URL"] = _TEST_DB or f"sqlite+aiosqlite:///{_APP_DB}"
os.environ.setdefault("AERA_CHECKOUT_BRIDGE_KEY", "test-bridge-key")

from datetime import UTC, datetime  # noqa: E402

import pytest  # noqa: E402
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine  # noqa: E402

from app.config import Settings  # noqa: E402
from app.core.security import TokenVault  # noqa: E402
from app.db.models import Base, Plan, User  # noqa: E402


@pytest.fixture
async def sessions(tmp_path):
    engine = create_async_engine(_TEST_DB or f"sqlite+aiosqlite:///{tmp_path / 'test.db'}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.drop_all)
        await connection.run_sync(Base.metadata.create_all)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


@pytest.fixture
def vault():
    return TokenVault("development-only-change-before-production")


@pytest.fixture
def settings():
    return Settings(_env_file=None, database_url=os.environ["DATABASE_URL"])


@pytest.fixture
async def plan_id(sessions):
    async with sessions.begin() as db:
        plan = Plan(
            name="Month", slug="month", price_minor=19900, traffic_limit_bytes=0, device_limit=3
        )
        db.add(plan)
        await db.flush()
        return plan.id


async def add_user(db, telegram_id):
    user = User(telegram_id=telegram_id, terms_accepted_at=datetime.now(UTC))
    db.add(user)
    await db.flush()
    return user


def make_settings(**values):
    return Settings(_env_file=None, database_url=os.environ["DATABASE_URL"], **values)


async def add_order(db, plan_id, telegram_id, status="OPEN", amount=19900, **user_values):
    """A tariff request ticket with its sale order, as the manual sales flow creates them."""
    from app.db.models import SaleOrder, SupportTicket

    user = User(telegram_id=telegram_id, terms_accepted_at=datetime.now(UTC), **user_values)
    db.add(user)
    await db.flush()
    ticket = SupportTicket(
        user_id=user.id, category="TARIFF_REQUEST", subject="plan", status=status
    )
    db.add(ticket)
    await db.flush()
    order = SaleOrder(ticket_id=ticket.id, plan_id=plan_id, amount_rub_minor=amount)
    db.add(order)
    await db.flush()
    return user, ticket, order


@pytest.fixture
async def app_sessions():
    """The application's own session factory, on a fresh schema for each test."""
    from app.db.session import engine, sessions

    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.drop_all)
        await connection.run_sync(Base.metadata.create_all)
    yield sessions
    await engine.dispose()


@pytest.fixture
def app_settings(monkeypatch):
    """The cached settings object the app reads; attributes are restored after each test."""
    from app.config import get_settings

    current = get_settings()

    def apply(**values):
        for key, value in values.items():
            monkeypatch.setattr(current, key, value)
        return current

    return apply


def asgi_client(app, peer="127.0.0.1"):
    import httpx

    transport = httpx.ASGITransport(app=app, client=(peer, 50000))
    return httpx.AsyncClient(transport=transport, base_url="http://aera.test")


@pytest.fixture
async def bot_driver(app_sessions, app_settings):
    """The production dispatcher with in-memory FSM, fake Redis and a recording bot."""
    import fakeredis.aioredis
    from aiogram.fsm.storage.memory import MemoryStorage
    from bot_harness import Driver, make_bot

    from app.bot.middleware import GuardMiddleware
    from app.bot.runtime import create_dispatcher

    app_settings(admin_telegram_ids="999", bot_username="AeraBot")
    dispatcher = create_dispatcher()
    dispatcher.fsm.storage = MemoryStorage()
    for manager in (
        dispatcher.message.outer_middleware,
        dispatcher.callback_query.outer_middleware,
    ):
        for middleware in manager._middlewares:
            if isinstance(middleware, GuardMiddleware):
                middleware.redis = fakeredis.aioredis.FakeRedis()
    yield Driver(dispatcher, make_bot())


@pytest.fixture
async def app_plan_id(app_sessions):
    async with app_sessions.begin() as db:
        plan = Plan(
            name="Month", slug="month", price_minor=19900, traffic_limit_bytes=0, device_limit=3
        )
        db.add(plan)
        await db.flush()
        return plan.id
