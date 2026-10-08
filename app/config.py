from functools import lru_cache

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore", hide_input_in_errors=True)
    app_env: str = "development"
    app_secret: str = Field(default="development-only-change-before-production", repr=False)
    database_url: str = "postgresql+asyncpg://aera:aera@localhost:5432/aera"
    # PostgreSQL only; 0 disables. Bounds how long a request waits on a row lock and how
    # long an abandoned transaction may keep its locks.
    db_lock_timeout_ms: int = Field(default=10000, ge=0)
    db_idle_in_transaction_timeout_ms: int = Field(default=60000, ge=0)
    redis_url: str = "redis://localhost:6379/0"
    public_base_url: str = "http://localhost:8000"
    bot_token: str = Field(default="", repr=False)
    bot_username: str = "AeraVpnBot"
    telegram_http_client: str = "aiohttp"
    admin_telegram_ids: str = ""
    admin_api_token: str = Field(default="", repr=False)
    telegram_webhook_secret: str = ""
    xui_mock_mode: bool = True
    xui_version: str = "3.2.8"
    xui_allow_writes: bool = False
    xui_protected_hosts: str = ""
    xui_credentials_json: str = Field(default="{}", repr=False)
    payment_provider: str = "mock"
    card_provider: str = "mock"
    freekassa_enabled: bool = False
    freekassa_merchant_id: str = ""
    freekassa_secret1: str = Field(default="", repr=False)
    freekassa_secret2: str = Field(default="", repr=False)
    crypto_provider: str = "mock"
    yookassa_shop_id: str = ""
    yookassa_secret_key: str = Field(default="", repr=False)
    crypto_pay_token: str = Field(default="", repr=False)
    crypto_pay_testnet: bool = True
    mock_payment_secret: str = "local-mock-only"
    crypto_mock_enabled: bool = False
    crypto_mock_secret: str = Field(default="local-crypto-test-only", repr=False)
    support_username: str = ""
    manual_sales: bool = True
    manual_sbp_enabled: bool = False
    wata_token: str = Field(default="", repr=False)
    wata_terminal_id: str = ""
    ios_client_url: str = ""
    android_client_url: str = ""
    windows_client_url: str = ""
    macos_client_url: str = ""

    @property
    def admin_ids(self) -> set[int]:
        return {int(x.strip()) for x in self.admin_telegram_ids.split(",") if x.strip()}

    @model_validator(mode="after")
    def production_guard(self) -> "Settings":
        if self.telegram_http_client not in {"aiohttp", "httpx"}:
            raise ValueError("Unsupported Telegram HTTP client")
        if self.payment_provider not in {"mock", "telegram_stars", "live"}:
            raise ValueError("Unsupported payment provider")
        if self.card_provider not in {"mock", "yookassa", "freekassa"}:
            raise ValueError("Unsupported card provider")
        if self.freekassa_enabled and (
            self.card_provider != "freekassa"
            or not self.freekassa_merchant_id.isdigit()
            or not self.freekassa_secret1
            or not self.freekassa_secret2
            or self.freekassa_secret1 == self.freekassa_secret2
        ):
            raise ValueError("FreeKassa settings incomplete")
        if self.crypto_provider not in {"mock", "crypto_pay"}:
            raise ValueError("Unsupported crypto provider")
        if not self.xui_mock_mode and self.xui_version not in {"2.8.5", "3.2.8"}:
            raise ValueError("A verified adapter for the installed XUI version is required")
        if self.app_env == "production":
            if self.crypto_provider == "crypto_pay" and self.crypto_pay_testnet:
                raise ValueError("Crypto Pay testnet is forbidden in production")
            if self.crypto_mock_enabled:
                raise ValueError("Crypto simulation is forbidden in production")
            if len(self.app_secret) < 32 or self.app_secret.startswith("development"):
                raise ValueError("Production requires a unique APP_SECRET of 32+ characters")
            if self.xui_mock_mode or self.payment_provider == "mock":
                raise ValueError("Mock integrations cannot run in production")
            if not self.public_base_url.startswith("https://"):
                raise ValueError("Production requires HTTPS")
            # SQLite ignores SELECT ... FOR UPDATE, which payment and stock handling rely on.
            if not self.database_url.startswith("postgresql"):
                raise ValueError("Production requires PostgreSQL")
            if len(self.telegram_webhook_secret) < 32:
                raise ValueError("Production requires TELEGRAM_WEBHOOK_SECRET")
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
