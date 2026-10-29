"""Owned subscription cards; selected status, dates and link always share one row."""

import math

from datetime import UTC, datetime, timedelta

from html import escape

from sqlalchemy import select

from app.db.models import FamilyAccess, ManualAccess, PaidLink, Plan, TrialLink

from app.services.commerce import utc

from app.config import get_settings

from app.core.security import TokenVault

from app.bot.texts.portal import text as tr, devices


def key(entry):

    kind, row = entry

    return kind + ":" + row.id


def expired(row):

    deadline = getattr(row, "expires_at", None)

    return bool(deadline and utc(deadline) <= datetime.now(UTC))


async def entries(db, user):

    records = [
        ("paid", r)
        for r in await db.scalars(
            select(PaidLink).where(PaidLink.user_id == user.id, PaidLink.issued_at.is_not(None))
        )
    ]

    family = await db.scalar(
        select(FamilyAccess).where(
            FamilyAccess.user_id == user.id, FamilyAccess.is_active.is_(True)
        )
    )

    if family:
        records.append(("family", family))

    manual = await db.scalar(select(ManualAccess).where(ManualAccess.user_id == user.id))

    if manual:
        records.append(("manual", manual))

    trial = await db.scalar(select(TrialLink).where(TrialLink.user_id == user.id))

    if trial and (trial.pending_uuid or trial.status not in {"ACTIVE", "WAITING", "ACTIVATING"}
                  or expired(trial) or (not trial.started_at and trial.issued_at
                      and utc(trial.issued_at) <= datetime.now(UTC)-timedelta(hours=1))):
        trial = None

    superseded = any(not expired(row) and getattr(row, "status", "ACTIVE") in {"ACTIVE", "WAITING", "ACTIVATING"}
                     for kind, row in records)

    if trial and not superseded and (
        not records or (not expired(trial) and trial.status in {"ACTIVE", "WAITING", "ACTIVATING"})
    ):
        records.append(("trial", trial))

    def rank(entry):

        kind, row = entry

        priority = (
            2
            if expired(row)
            else 0
            if kind in {"manual", "family"} or row.status == "ACTIVE"
            else 1
        )

        # Paid/manual cards before trials at the same priority; stable within each group.

        return priority, kind == "trial", utc(row.created_at), row.id

    return sorted(records, key=rank)


def selected(records, selection=None):

    if not records:
        if selection:
            raise ValueError("Subscription unavailable")

        return None

    if selection is None:
        return records[0]

    for entry in records:
        if key(entry) == selection:
            return entry

    raise ValueError("Subscription unavailable")


def personal_link(entry, user):

    if not entry or user.is_blocked or not user.is_active:
        return None

    kind, row = entry

    if not row.link_encrypted or expired(row):
        return None

    if kind not in {"manual", "family"}:
        if not row.checked_at or utc(row.checked_at) <= datetime.now(UTC) - timedelta(minutes=2):
            return None

        if row.status not in {"ACTIVE", "WAITING", "ACTIVATING"}:
            return None

    raw = TokenVault(get_settings().app_secret).reveal(row.link_encrypted)

    if not raw.startswith("vless://"):
        return None
    if kind == "trial":
        from app.subscription.links import customer_url
        return customer_url(user.id, get_settings())
    return raw


def expiry_text(deadline, lang):

    days = max(0, math.ceil((utc(deadline) - datetime.now(UTC)).total_seconds() / 86400))

    date = utc(deadline).strftime("%d.%m.%Y %H:%M")

    if lang == "ru":
        return f"📅 Действует до: {date} UTC\n⏰ Осталось: {days} д."

    return f"📅 Valid until: {date} UTC\n⏰ Remaining: {days} days"


async def profile_text(db, user, lang, records, entry):

    status, plan, expiry = tr("none", lang), "AERA", ""

    if entry:
        kind, row = entry

        if kind == "family":
            limit = (
                f"📱 Устройств: {row.device_limit}"
                if row.device_limit
                else "📱 Устройства: безлимит"
            )

            return f"💎 Личный кабинет\n\n✅ Активна\nID: {user.telegram_id}\n\nУ Вас безлимит на использование\n⏳ Срок: бессрочно\n{limit}"

        p = await db.get(Plan, row.plan_id) if kind != "trial" else None

        plan = (p.name + " · " + devices(p, lang)) if p else tr("trial", lang)
        if kind == "manual" and p and row.link_encrypted:
            from urllib.parse import urlsplit
            from app.subscription.multinode import panel_state

            vault = TokenVault(get_settings().app_secret)
            snapshot = await panel_state(db, vault)
            identity = urlsplit(vault.reveal(row.link_encrypted)).username
            actual = (snapshot or {}).get("entries", {}).get(identity)
            if actual and isinstance(actual.get("limit_ip"), int) and actual["limit_ip"] >= 0:
                label = {
                    "ru": "Лимит IP",
                    "en": "IP limit",
                    "zh": "IP 限制",
                    "fa": "محدودیت IP",
                }.get(lang, "IP limit")
                limit = actual["limit_ip"] or {
                    "ru": "без лимита",
                    "en": "unlimited",
                    "zh": "无限",
                    "fa": "نامحدود",
                }.get(lang, "unlimited")
                plan = p.name + " · " + label + ": " + str(limit)

        recent = kind == "manual" or (
            row.checked_at and utc(row.checked_at) > datetime.now(UTC) - timedelta(minutes=2)
        )

        code = (
            "EXPIRED"
            if expired(row)
            else "ACTIVE"
            if kind == "manual"
            else row.status
            if recent and row.status in {"ACTIVE", "WAITING", "EXPIRED", "ACTIVATING", "BLOCKED"}
            else "UNKNOWN"
        )

        status = tr(code, lang)

        if row.expires_at:
            expiry = expiry_text(row.expires_at, lang)

        elif kind == "paid":
            expiry = tr(
                "paid_waiting",
                lang,
                days=(row.duration_ms or p.duration_days * 86400000) // 86400000,
            )

        else:
            expiry = tr("trial_confirming" if row.status == "ACTIVATING" else "waiting_time", lang)

    body = tr(
        "profile_info",
        lang,
        name=escape(user.first_name or user.username or "AERA"),
        status=f"{status}\nID: {user.telegram_id}",
        plan=escape(plan),
        expiry=expiry,
    )

    if len(records) > 1:
        number = next(i for i, e in enumerate(records) if key(e) == key(entry)) + 1

        heading = {
            "ru": f"📚 Подписка {number} из {len(records)}",
            "en": f"📚 Subscription {number} of {len(records)}",
            "zh": f"📚 订阅 {number}/{len(records)}",
            "fa": f"📚 اشتراک {number}/{len(records)}",
        }[lang if lang in {"ru", "en", "zh", "fa"} else "ru"]

        body = body.replace("\n\n", "\n\n" + heading + "\n\n", 1)

    return body


def payment_method(payment, lang="ru"):

    labels = {
        "sbp": ("СБП", "SBP", "SBP", "SBP"),
        "crypto": ("Криптовалюта", "Cryptocurrency", "加密货币", "رمزارز"),
        "stars": ("Telegram Stars", "Telegram Stars", "Telegram Stars", "Telegram Stars"),
        "card": ("Банковская карта", "Bank card", "银行卡", "کارت بانکی"),
        "unknown": ("Не указан", "Not recorded", "未记录", "ثبت نشده"),
    }

    method = "unknown"

    if payment:
        if payment.provider == "telegram_stars" or payment.currency == "XTR":
            method = "stars"

        elif payment.provider == "platega":
            stored = (payment.details or {}).get("method")

            method = stored if stored in {"sbp", "crypto", "card"} else "unknown"

        elif payment.provider == "crypto_pay":
            method = "crypto"

        elif payment.provider == "yookassa":
            method = "card"

    return labels[method][
        ("ru", "en", "zh", "fa").index(lang if lang in {"ru", "en", "zh", "fa"} else "ru")
    ]
