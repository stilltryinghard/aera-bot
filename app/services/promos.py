from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import PaymentError
from app.db.models import Payment, Plan, PromoCode, PromoUse
from app.services.commerce import utc


async def reserve_promo(
    db: AsyncSession, code: str, user_id: str, plan: Plan
) -> tuple[PromoCode, int, int]:
    promo = await db.scalar(
        select(PromoCode).where(PromoCode.code == code.strip().upper()).with_for_update()
    )
    current = datetime.now(UTC)
    if (
        not promo
        or not promo.is_active
        or promo.uses >= promo.max_uses
        or utc(promo.valid_from) > current
        or (promo.valid_until and utc(promo.valid_until) <= current)
        or (promo.plan_id and promo.plan_id != plan.id)
    ):
        raise PaymentError("Promo unavailable")
    # Only paid uses count: an abandoned checkout must not burn the code.
    if promo.one_use_per_user and await db.scalar(
        select(PromoUse.id)
        .join(Payment, Payment.id == PromoUse.payment_id)
        .where(
            PromoUse.user_id == user_id,
            PromoUse.promo_id == promo.id,
            Payment.status == "PAID",
        )
    ):
        raise PaymentError("Promo already used")
    price, extra_days = plan.price_minor, 0
    if promo.discount_type == "PERCENT":
        price = price * (100 - promo.discount_value) // 100
    elif promo.discount_type == "FIXED_AMOUNT":
        price = max(0, price - promo.discount_value)
    elif promo.discount_type == "EXTRA_DAYS":
        extra_days = promo.discount_value
    else:
        raise PaymentError("Unsupported promo type")
    if price <= 0:
        raise PaymentError("Zero-price payment is not supported; use trial or extra days")
    # uses is incremented by CommerceService.confirm once the payment is applied.
    return promo, price, extra_days
