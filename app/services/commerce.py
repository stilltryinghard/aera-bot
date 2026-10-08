from calendar import monthrange
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import AccessDeniedError, PaymentError, ProvisioningError
from app.core.security import TokenVault
from app.db.models import Payment, Plan, ProvisioningJob, Server, Subscription, User, VPNClient


def utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value


def add_months(value: datetime, months: int) -> datetime:
    year, month = divmod(value.year * 12 + value.month - 1 + months, 12)
    month += 1
    return value.replace(year=year, month=month, day=min(value.day, monthrange(year, month)[1]))


class CommerceService:
    def __init__(self, session: AsyncSession, vault: TokenVault):
        self.db = session
        self.vault = vault

    async def user(self, telegram_id: int, **profile: str | None) -> User:
        # PostgreSQL upsert also covers concurrent /start requests.
        from sqlalchemy.dialects.postgresql import insert as pg_insert
        from sqlalchemy.dialects.sqlite import insert as sqlite_insert

        insert = pg_insert if self.db.bind.dialect.name == "postgresql" else sqlite_insert
        values = {"telegram_id": telegram_id, **profile}
        statement = (
            insert(User)
            .values(**values)
            .on_conflict_do_update(
                index_elements=[User.telegram_id],
                set_={**profile, "last_seen_at": datetime.now(UTC)},
            )
        )
        await self.db.execute(statement)
        return await self.db.scalar(select(User).where(User.telegram_id == telegram_id))

    async def plans(self) -> list[Plan]:
        return list(
            await self.db.scalars(
                select(Plan).where(Plan.is_active.is_(True)).order_by(Plan.sort_order)
            )
        )

    async def purchase(
        self, user_id: str, plan_id: str, provider: str = "mock", promo_code: str | None = None
    ) -> Payment:
        user = await self.db.get(User, user_id)
        plan = await self.db.get(Plan, plan_id)
        if not user or user.is_blocked or not user.is_active:
            raise AccessDeniedError("User disabled")
        if not plan or not plan.is_active:
            raise PaymentError("Plan unavailable")
        amount, extra_days, promo = plan.price_minor, 0, None
        currency = plan.currency
        if provider in {"telegram_stars", "stars_mock"} and plan.currency != "XTR":
            if not plan.stars_price or plan.stars_price <= 0:
                raise PaymentError("Configure an explicit Stars price")
            amount, currency = plan.stars_price, "XTR"
        if provider == "telegram_stars" and not user.terms_accepted_at:
            raise PaymentError("Terms must be accepted before purchase")
        if promo_code:
            if currency != plan.currency:
                raise PaymentError("Promo currency differs from the payment currency")
            from app.services.promos import reserve_promo

            promo, amount, extra_days = await reserve_promo(self.db, promo_code, user_id, plan)
        payment = Payment(
            user_id=user_id,
            plan_id=plan.id,
            provider=provider,
            amount_minor=amount,
            currency=currency,
            details={
                "duration_days": plan.duration_days + extra_days,
                "duration_months": plan.duration_months,
                "bonus_days": extra_days,
                "traffic_limit_bytes": plan.traffic_limit_bytes,
                "device_limit": plan.device_limit,
                "unlimited_devices": plan.unlimited_devices,
            },
        )
        self.db.add(payment)
        await self.db.flush()
        if promo:
            from app.db.models import PromoUse

            self.db.add(PromoUse(promo_id=promo.id, user_id=user_id, payment_id=payment.id))
        return payment

    async def confirm(self, payment_id: str) -> Subscription:
        payment = await self.db.scalar(
            select(Payment).where(Payment.id == payment_id).with_for_update()
        )
        if not payment or payment.status not in {"PENDING", "PAID"}:
            raise PaymentError("Payment unavailable")
        # Lock user to serialize different purchases and extensions for the same account.
        user = await self.db.scalar(
            select(User).where(User.id == payment.user_id).with_for_update()
        )
        sub = await self.db.scalar(select(Subscription).where(Subscription.user_id == user.id))
        if payment.applied_at:
            return sub
        plan = await self.db.get(Plan, payment.plan_id)
        current = datetime.now(UTC)
        if not sub:
            sub = Subscription(
                user_id=user.id,
                plan_id=plan.id,
                expires_at=current,
                traffic_limit_bytes=plan.traffic_limit_bytes,
                device_limit=plan.device_limit,
            )
            self.db.add(sub)
            await self.db.flush()
        snapshot = payment.details
        base = max(utc(sub.expires_at), current)
        months = snapshot.get("duration_months")
        sub.expires_at = (
            add_months(base, months) + timedelta(days=snapshot.get("bonus_days", 0))
            if months
            else base + timedelta(days=snapshot.get("duration_days", plan.duration_days))
        )
        sub.plan_id = plan.id
        sub.traffic_limit_bytes = snapshot.get("traffic_limit_bytes", plan.traffic_limit_bytes)
        sub.device_limit = snapshot.get("device_limit", plan.device_limit)
        sub.unlimited_devices = snapshot.get("unlimited_devices", plan.unlimited_devices)
        sub.status = "PENDING_PROVISIONING"
        payment.status = "PAID"
        payment.paid_at = current
        payment.applied_at = current
        self.db.add(ProvisioningJob(payment_id=payment.id, subscription_id=sub.id))
        from app.db.models import PromoCode, PromoUse

        promo_id = await self.db.scalar(
            select(PromoUse.promo_id).where(PromoUse.payment_id == payment.id)
        )
        if promo_id:
            promo = await self.db.scalar(
                select(PromoCode).where(PromoCode.id == promo_id).with_for_update()
            )
            promo.uses += 1
        from app.db.models import Referral

        referral = await self.db.scalar(
            select(Referral).where(Referral.referred_user_id == user.id).with_for_update()
        )
        if referral and referral.status == "PENDING":
            referral.status = "ELIGIBLE"
        return sub

    async def select_server(self) -> Server:
        servers = await self.db.scalars(
            select(Server)
            .where(Server.is_active.is_(True))
            .order_by(Server.priority, Server.id)
            .with_for_update()
        )
        for server in servers:
            count = await self.db.scalar(
                select(func.count()).select_from(VPNClient).where(VPNClient.server_id == server.id)
            )
            if count < server.capacity:
                return server
        raise ProvisioningError("No server capacity")

    async def subscription(self, user_id: str) -> tuple[Subscription | None, VPNClient | None]:
        sub = await self.db.scalar(select(Subscription).where(Subscription.user_id == user_id))
        client = None
        if sub:
            client = await self.db.scalar(
                select(VPNClient).where(VPNClient.subscription_id == sub.id)
            )
        return sub, client

    async def rotate_token(self, user_id: str, revoke: bool = False) -> str | None:
        await self.db.scalar(select(User).where(User.id == user_id).with_for_update())
        _, client = await self.subscription(user_id)
        if not client:
            raise AccessDeniedError("No access")
        if revoke:
            client.subscription_token_hash = None
            client.token_encrypted = None
            return None
        token, digest, encrypted = self.vault.create()
        client.subscription_token_hash = digest
        client.token_encrypted = encrypted
        return token
