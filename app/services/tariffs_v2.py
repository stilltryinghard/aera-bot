"""Approved device/term matrix. Preserve historical plan rows and Stars policy."""
from decimal import Decimal, ROUND_CEILING

from sqlalchemy import select
from app.db.models import Plan

TERMS = (30, 90, 180, 360)


def price(days, devices):
    if days not in TERMS or not 1 <= devices <= 10:
        raise ValueError("Invalid tariff")
    monthly = 100 + 50 * (devices - 1)
    return {30: monthly, 90: monthly * 3, 180: devices * 500, 360: devices * 1000}[days]


def slug(days, devices):
    price(days, devices)
    return f"devices-{devices}-{days}d"


def stars(rubles):
    # Same owner-installed pricing rule as scripts.bootstrap_portal; not live FX.
    return int((Decimal(rubles) / Decimal("1.0852907")).to_integral_value(rounding=ROUND_CEILING))


async def ensure_catalog(db):
    existing = {p.slug: p for p in await db.scalars(select(Plan))}
    for days in TERMS:
        for devices in range(1, 11):
            key = slug(days, devices)
            values = dict(name=f"AERA · {devices} устр. · {days} дней", slug=key,
                          description="Безлимитный трафик", price_minor=price(days, devices) * 100,
                          currency="RUB", stars_price=stars(price(days, devices)),
                          duration_days=days, duration_months=None, device_limit=devices,
                          unlimited_devices=False, traffic_limit_bytes=0, is_active=True,
                          sort_order=100 + TERMS.index(days) * 10 + devices)
            if key not in existing:
                db.add(Plan(**values))
            elif any(getattr(existing[key], field) != value for field, value in values.items()):
                raise ValueError("Existing v2 tariff differs; review before changing purchases")
    await db.flush()


async def get_plan(db, days, devices):
    plan = await db.scalar(select(Plan).where(Plan.slug == slug(days, devices), Plan.is_active.is_(True)))
    if not plan or plan.price_minor != price(days, devices) * 100:
        raise ValueError("Tariff unavailable")
    return plan
