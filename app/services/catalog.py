"""Owner-approved AERA tariffs. Zero traffic quota means unlimited."""

FAMILIES = {
    "solo": ("SOLO", "1 устройство", 1, False, 100, 500),
    "plus": ("PLUS", "3 устройства", 3, False, 200, 1000),
    "infinity": ("INFINITY", "10 подключений", 10, False, 500, 3000),
    "business": ("BUSINESS", "Безлимит устройств", 1, True, 5000, 25000),
}


def catalog(mock: bool = False):
    for index, (family, (title, devices, count, unlimited, monthly, halfyear)) in enumerate(
        FAMILIES.items()
    ):
        for months, price in [(1, monthly), (6, halfyear)]:
            yield {
                "slug": f"{family}-{months}m",
                "name": f"AERA {title}",
                "description": "Для компаний, офисов и команд - одна подписка "
                "для всех рабочих устройств"
                if family == "business"
                else f"{devices} · Безлимитный трафик",
                "price_minor": price * 100,
                "currency": "RUB",
                "duration_days": 30 * months,
                "duration_months": months,
                "device_limit": count,
                "unlimited_devices": unlimited,
                "traffic_limit_bytes": 0,
                "is_featured": family == "plus",
                "is_active": price <= 10000,
                "sort_order": index + (len(FAMILIES) if months == 6 else 0),
                # Test-only prices, not a RUB/XTR conversion or approved live Stars price.
                "stars_price": price if mock else None,
            }


def family_of(plan):
    return plan.slug.rsplit("-", 1)[0] if plan.slug.rsplit("-", 1)[0] in FAMILIES else None
