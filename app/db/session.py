from sqlalchemy import event
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.config import Settings, get_settings


def engine_options(settings: Settings) -> dict:
    options = {"pool_pre_ping": True}
    if settings.database_url.startswith("postgresql+asyncpg"):
        options["connect_args"] = {
            "server_settings": {
                "lock_timeout": str(settings.db_lock_timeout_ms),
                "idle_in_transaction_session_timeout": str(
                    settings.db_idle_in_transaction_timeout_ms
                ),
            }
        }
    return options


engine = create_async_engine(get_settings().database_url, **engine_options(get_settings()))

if engine.dialect.name == "sqlite":

    @event.listens_for(engine.sync_engine, "connect")
    def sqlite_privacy(connection, _record):
        cursor = connection.cursor()
        cursor.execute("PRAGMA secure_delete=ON")
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()


sessions = async_sessionmaker(engine, expire_on_commit=False)
