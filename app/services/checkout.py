from decimal import Decimal

from sqlalchemy import select

from app.config import Settings
from app.core.exceptions import PaymentError
from app.core.security import TokenVault
from app.db.models import Payment, Plan
from app.integrations.payments.crypto_pay import CryptoPayProvider
from app.integrations.payments.yookassa import YooKassaProvider
from app.services.commerce import CommerceService


class CheckoutService:
    def __init__(self, sessions, settings: Settings, vault: TokenVault):
        self.sessions, self.settings, self.vault = sessions, settings, vault

    async def create(self, user_id: str, plan_id: str, method: str) -> tuple[Payment, str | None]:
        if method not in {"card", "crypto", "stars"}:
            raise PaymentError("Unsupported payment method")
        if method == "crypto" and self.settings.crypto_provider == "mock":
            if self.settings.app_env == "production":
                raise PaymentError("Crypto provider not configured")
            from app.services.crypto import create_invoice

            async with self.sessions.begin() as db:
                invoice = await create_invoice(db, self.vault, user_id, plan_id)
                payment = await db.get(Payment, invoice.payment_id)
                payment.details = {
                    **payment.details,
                    "method": method,
                    "invoice_id": invoice.id,
                    "test": True,
                }
            return payment, None
        provider = {"card": "yookassa", "crypto": "crypto_pay", "stars": "telegram_stars"}[method]
        if method == "card" and self.settings.card_provider == "mock":
            if self.settings.app_env == "production":
                raise PaymentError("Card provider not configured")
            provider = "mock"
        if method == "stars" and self.settings.payment_provider == "mock":
            provider = "stars_mock"
        async with self.sessions.begin() as db:
            service = CommerceService(db, self.vault)
            payment = await service.purchase(user_id, plan_id, provider=provider)
            payment.details = {**payment.details, "method": method}
            plan = await db.get(Plan, plan_id)
            if method == "crypto":
                if not plan.crypto_asset or not plan.crypto_amount:
                    raise PaymentError("Set an explicit cryptocurrency price for the plan")
                payment.details = {
                    **payment.details,
                    "crypto_asset": plan.crypto_asset,
                    "crypto_amount": plan.crypto_amount,
                    "crypto_network": "Crypto Pay internal balance",
                }
        # Payment intent is durable before any external API call.
        if provider == "yookassa":
            adapter = YooKassaProvider(
                self.settings.yookassa_shop_id, self.settings.yookassa_secret_key
            )
            try:
                body = await adapter.create_payment(
                    payment.id,
                    payment.amount_minor,
                    payment.currency,
                    self.settings.public_base_url,
                )
            finally:
                await adapter.close()
            provider_id, url = body["id"], body["confirmation"]["confirmation_url"]
        elif provider == "crypto_pay":
            adapter = CryptoPayProvider(
                self.settings.crypto_pay_token, self.settings.crypto_pay_testnet
            )
            try:
                body = await adapter.create_invoice(
                    payment.id, plan.crypto_asset, plan.crypto_amount
                )
            finally:
                await adapter.close()
            provider_id, url = str(body["invoice_id"]), body["bot_invoice_url"]
        else:
            return payment, None
        async with self.sessions.begin() as db:
            stored = await db.get(Payment, payment.id)
            stored.provider_payment_id = provider_id
            stored.details = {**stored.details, "checkout_url": url, "provider_ready": True}
        payment.provider_payment_id = provider_id
        return payment, url

    async def reconcile(self, payment_id: str, adapter=None) -> str:
        async with self.sessions() as db:
            payment = await db.get(Payment, payment_id)
        if not payment or payment.provider not in {"yookassa", "crypto_pay"}:
            raise PaymentError("Unsupported external payment")
        owned = adapter is None
        if owned:
            adapter = (
                YooKassaProvider(self.settings.yookassa_shop_id, self.settings.yookassa_secret_key)
                if payment.provider == "yookassa"
                else CryptoPayProvider(
                    self.settings.crypto_pay_token, self.settings.crypto_pay_testnet
                )
            )
        try:
            body = await adapter.verify_payment(payment.provider_payment_id)
        finally:
            if owned:
                await adapter.close()
        if payment.provider == "yookassa":
            if (
                body["id"] != payment.provider_payment_id
                or body.get("metadata", {}).get("payment_id") != payment.id
                or body["amount"]["currency"] != payment.currency
                or Decimal(body["amount"]["value"]) != Decimal(payment.amount_minor) / 100
            ):
                raise PaymentError("Card invoice mismatch")
            if self.settings.app_env == "production" and body.get("test") is not False:
                raise PaymentError("Test transaction rejected in production")
            paid = body.get("status") == "succeeded" and body.get("paid") is True
            cancelled = body.get("status") == "canceled"
        else:
            if (
                str(body["invoice_id"]) != payment.provider_payment_id
                or body.get("payload") != payment.id
                or body.get("asset") != payment.details["crypto_asset"]
                or Decimal(body["amount"]) != Decimal(payment.details["crypto_amount"])
            ):
                raise PaymentError("Crypto invoice mismatch")
            paid = body.get("status") == "paid"
            cancelled = body.get("status") == "expired"
        if paid:
            async with self.sessions.begin() as db:
                await CommerceService(db, self.vault).confirm(payment.id)
            return "PAID"
        if cancelled:
            async with self.sessions.begin() as db:
                stored = await db.get(Payment, payment.id)
                if stored.status == "PENDING":
                    stored.status = "CANCELLED"
            return "CANCELLED"
        return "PENDING"

    async def poll(self) -> None:
        async with self.sessions() as db:
            records = list(
                await db.scalars(
                    select(Payment)
                    .where(
                        Payment.status == "PENDING",
                        Payment.provider.in_(["yookassa", "crypto_pay"]),
                        # Intents whose provider call failed can never be paid; skip them in SQL
                        # so they cannot crowd payable invoices out of the batch.
                        Payment.details["provider_ready"].as_boolean().is_(True),
                    )
                    .order_by(Payment.created_at)
                    .limit(50)
                )
            )
        for payment in records:
            try:
                await self.reconcile(payment.id)
            except Exception:
                continue
