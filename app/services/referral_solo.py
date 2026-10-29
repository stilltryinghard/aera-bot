"""One durable SOLO gift for three distinct verified paying referrals.

Gift orders never become paid orders. The worker grants an existing stock key,
after current paid/manual access ends, and retries when stock is replenished.
"""
from datetime import UTC, datetime

from sqlalchemy import and_, exists, or_, select, update

from app.db.models import (
    FamilyAccess, ManualAccess, PaidLink, Payment, Plan, Referral, ReferralSoloReward,
    SaleOrder, SupportTicket, User,
)
from app.services.commerce import utc
from app.services.notifications import enqueue


async def qualifying_referrals(db, user_id):
    """A purchase of at least 100 RUB, not cumulative spending or ticket status."""
    paid = exists().where(
        SupportTicket.user_id == Referral.referred_user_id,
        SaleOrder.ticket_id == SupportTicket.id,
        SaleOrder.paid_at.is_not(None),
        SaleOrder.amount_rub_minor >= 10000,
        Payment.id == SaleOrder.payment_id,
        Payment.user_id == Referral.referred_user_id,
        Payment.plan_id == SaleOrder.plan_id,
        Payment.status == "PAID",
        Payment.paid_at.is_not(None),
        or_(
            and_(Payment.currency == "RUB", Payment.amount_minor == SaleOrder.amount_rub_minor),
            and_(Payment.currency == "XTR", Payment.provider == "telegram_stars"),
        ),
    )
    return list(await db.scalars(
        select(Referral.id).where(
            Referral.referrer_user_id == user_id,
            Referral.referred_user_id != user_id,
            paid,
        ).order_by(Referral.created_at, Referral.id)
    ))


async def solo_progress(db, user_id):
    ids = await qualifying_referrals(db, user_id)
    reward = await db.scalar(select(ReferralSoloReward).where(
        ReferralSoloReward.user_id == user_id
    ))
    return len(ids), reward.status if reward else None


async def ensure_reward(db, user_id):
    # Serialize callbacks/workers on SQLite as well as databases with row locks.
    await db.execute(update(User).where(User.id == user_id).values(is_active=User.is_active))
    reward = await db.scalar(select(ReferralSoloReward).where(
        ReferralSoloReward.user_id == user_id
    ).execution_options(populate_existing=True))
    if reward:
        return reward
    ids = await qualifying_referrals(db, user_id)
    if len(ids) < 3:
        return None
    reward = ReferralSoloReward(user_id=user_id, referral_ids=ids[:3])
    db.add(reward)
    await db.flush()
    user = await db.get(User, user_id)
    text = (
        "🎁 Ты пригласил трёх друзей с оплатой от 100 ₽! Месяц AERA SOLO в подарок. "
        "Если у тебя есть действующая платная подписка, бонус сохранён и будет выдан после её окончания. "
        "При отсутствии свободного ключа бонус остаётся в очереди. Прогресс — в разделе «Пригласить друга»."
        if user.language_code == "ru" else
        "🎁 Three friends paid at least 100 RUB each: you earned a free month of AERA SOLO! "
        "Your gift will be issued after existing paid access ends, when a verified stock key is available."
    )
    await enqueue(db, "referral-solo-earned:" + reward.id, user_id, "PORTAL", text)
    return reward


async def grant_reward(db, reward, settings):
    await db.execute(update(User).where(User.id == reward.user_id).values(is_active=User.is_active))
    await db.refresh(reward)
    if reward.status == "ISSUED":
        return True
    user = await db.get(User, reward.user_id)
    if not user or not user.is_active or user.is_blocked:
        return False
    from app.services.paid_pool import current, enabled, reserve
    if not await enabled(db):
        return False
    # Never hide or shorten a customer's existing paid or manually issued key.
    if await current(db, user.id):
        return False
    if await db.scalar(select(FamilyAccess.id).where(FamilyAccess.user_id == user.id, FamilyAccess.is_active.is_(True))):
        return False
    manual = await db.scalar(select(ManualAccess).where(ManualAccess.user_id == user.id))
    if manual and utc(manual.expires_at) > datetime.now(UTC):
        return False
    # Unknown panel state may still represent paid access; defer instead of replacing it.
    uncertain = await db.scalar(select(PaidLink.id).where(
        PaidLink.user_id == user.id, PaidLink.issued_at.is_not(None),
        PaidLink.status.not_in(["EXPIRED", "RETIRED"]),
        (PaidLink.expires_at.is_(None) | (PaidLink.expires_at > datetime.now(UTC))),
    ).limit(1))
    if uncertain:
        return False
    plan = await db.scalar(select(Plan).where(
        Plan.slug == "solo-1m", Plan.is_active.is_(True), Plan.device_limit == 1,
    ))
    if not plan:
        return False
    if reward.order_id:
        order = await db.get(SaleOrder, reward.order_id)
    else:
        ticket = SupportTicket(
            user_id=user.id, category="REFERRAL_REWARD", status="IN_PROGRESS",
            subject="🎁 AERA SOLO · 1 месяц за трёх друзей",
        )
        db.add(ticket)
        await db.flush()
        order = SaleOrder(ticket_id=ticket.id, plan_id=plan.id, amount_rub_minor=0)
        db.add(order)
        await db.flush()
        reward.order_id = order.id
    assert order and order.plan_id == plan.id and not order.paid_at and not order.payment_id
    record = await reserve(db, order)
    if record is None:
        for identity in settings.admin_ids:
            admin = await db.scalar(select(User).where(User.telegram_id == identity))
            if admin:
                await enqueue(
                    db, f"referral-solo-no-stock:{reward.id}:{identity}", admin.id, "PORTAL",
                    f"🎁 Месяц SOLO за трёх друзей ожидает выдачи. Telegram ID: {user.telegram_id}. "
                    "Пополни проверенный запас SOLO на месяц; бонус будет выдан автоматически.",
                )
        return False
    if not record.issued_at:
        record.issued_at = datetime.now(UTC)
        record.status = "WAITING" if record.started_at is None else record.status
    reward.status, reward.issued_at = "ISSUED", record.issued_at
    ticket = await db.get(SupportTicket, order.ticket_id)
    ticket.status, ticket.closed_at = "CONNECTED", record.issued_at
    await db.flush()
    await enqueue(db, "paid-link:" + order.id, user.id, "ACCESS_READY", record.id)
    return True


async def process_rewards(db, settings):
    candidates = list(await db.scalars(
        select(Referral.referrer_user_id).distinct().where(
            ~exists().where(ReferralSoloReward.user_id == Referral.referrer_user_id)
        )
    ))
    for user_id in candidates:
        await ensure_reward(db, user_id)
    pending = list(await db.scalars(select(ReferralSoloReward).where(
        ReferralSoloReward.status == "PENDING"
    ).order_by(ReferralSoloReward.created_at)))
    for reward in pending:
        await grant_reward(db, reward, settings)
