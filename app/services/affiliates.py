"""First verified purchase commissions; reserved crypto-wallet withdrawal requests."""
import json
import re
from datetime import UTC, datetime

from sqlalchemy import func, select, update
from app.db.models import (AffiliateCommission, AffiliateWithdrawal, Payment,
                           Referral, SaleOrder, SupportTicket, User, AccessRenewal)
from app.core.security import TokenVault
from app.services.notifications import enqueue

MIN_WITHDRAWAL = 100000


async def accrue_first(db, order, settings):
    ticket = await db.get(SupportTicket, order.ticket_id)
    payment = await db.get(Payment, order.payment_id) if order.payment_id else None
    if not order.paid_at or not payment or payment.status != "PAID" or not payment.paid_at:
        return False
    if await db.scalar(select(AccessRenewal.id).where(AccessRenewal.order_id == order.id)):
        return False
    user = await db.get(User, ticket.user_id)
    if not user.referred_by_id or user.referred_by_id == user.id:
        return False
    # Serialize on referrer as well as the purchaser held by confirm_order.
    await db.execute(update(User).where(User.id == user.referred_by_id).values(is_active=User.is_active))
    first = await db.scalar(select(SaleOrder.id).join(SupportTicket)
                            .where(SupportTicket.user_id == user.id, SaleOrder.paid_at.is_not(None))
                            .order_by(SaleOrder.paid_at, SaleOrder.created_at, SaleOrder.id).limit(1))
    if first != order.id or await db.scalar(select(AffiliateCommission.id).where(
            AffiliateCommission.referred_user_id == user.id)):
        return False
    referrer = await db.get(User, user.referred_by_id)
    if not referrer or referrer.is_blocked or not referrer.is_active:
        return False
    amount = order.amount_rub_minor * 35 // 100
    db.add(AffiliateCommission(referred_user_id=user.id, referrer_user_id=referrer.id,
                              order_id=order.id, purchase_minor=order.amount_rub_minor,
                              amount_minor=amount, status="CONFIRMED"))
    referral = await db.scalar(select(Referral).where(Referral.referred_user_id == user.id))
    if referral:
        referral.status, referral.reward_type, referral.rewarded_at = "REWARDED", "COMMISSION_35", datetime.now(UTC)
    await enqueue(db, "affiliate-first:" + user.id, referrer.id, "PORTAL",
                  f"🤝 Друг совершил первую покупку. Начислено {amount / 100:g} ₽ партнёрского вознаграждения.")
    await db.flush()
    return True


async def reconcile_reversals(db, settings=None):
    rows = await db.scalars(select(AffiliateCommission).join(SaleOrder).join(Payment, SaleOrder.payment_id == Payment.id)
                            .where(AffiliateCommission.status == "CONFIRMED",
                                   Payment.status.in_(["CHARGEBACKED", "REFUNDED", "REVERSED"])))
    for row in rows:
        row.status = "REVERSED"
        # Pending withdrawals are held for review; never mark external money paid.
        pending = await db.scalars(select(AffiliateWithdrawal).where(
            AffiliateWithdrawal.user_id == row.referrer_user_id,
            AffiliateWithdrawal.status == "PENDING"))
        for withdrawal in pending:
            withdrawal.status = "REVIEW"


async def statistics(db, user_id):
    await reconcile_reversals(db)
    earned = await db.scalar(select(func.coalesce(func.sum(AffiliateCommission.amount_minor), 0)).where(
        AffiliateCommission.referrer_user_id == user_id, AffiliateCommission.status == "CONFIRMED"))
    reserved = await db.scalar(select(func.coalesce(func.sum(AffiliateWithdrawal.amount_minor), 0)).where(
        AffiliateWithdrawal.user_id == user_id, AffiliateWithdrawal.status.in_(["PENDING", "REVIEW", "PAID"])))
    invited = await db.scalar(select(func.count()).select_from(Referral).where(Referral.referrer_user_id == user_id))
    buyers = await db.scalar(select(func.count()).select_from(AffiliateCommission).where(
        AffiliateCommission.referrer_user_id == user_id, AffiliateCommission.status == "CONFIRMED"))
    return dict(earned=earned, available=earned - reserved, invited=invited, buyers=buyers)


def crypto_wallet_details(details):
    lines = [line.strip() for line in details.strip().splitlines() if line.strip()]
    if len(lines) != 3:
        raise ValueError("Send coin, network and address on three lines")
    coin, network, address = lines
    if (not re.fullmatch(r"[A-Za-z0-9 ._-]{2,32}", coin)
            or not re.fullmatch(r"[A-Za-z0-9 ._-]{2,64}", network)
            or not re.fullmatch(r"[A-Za-z0-9:_-]{10,180}", address)):
        raise ValueError("Invalid crypto wallet details")
    return json.dumps(dict(method="crypto", coin=coin, network=network, address=address), ensure_ascii=False)


def withdrawal_details_text(details):
    data = json.loads(details)
    if data.get("method") != "crypto":
        raise ValueError("Unsupported withdrawal method")
    return f"Монета: {data['coin']}\nСеть: {data['network']}\nКошелёк: {data['address']}"


async def request_withdrawal(db, user, details, settings):
    if user.is_blocked or not user.is_active or not details.strip() or len(details) > 400:
        raise ValueError("Invalid withdrawal details")
    details = crypto_wallet_details(details)
    await db.execute(update(User).where(User.id == user.id).values(is_active=User.is_active))
    previous = await db.scalar(select(AffiliateWithdrawal).where(
        AffiliateWithdrawal.user_id == user.id, AffiliateWithdrawal.status.in_(["PENDING", "REVIEW"])))
    if previous:
        return previous
    stats = await statistics(db, user.id)
    if stats["available"] < MIN_WITHDRAWAL:
        raise ValueError("Withdrawal requires 1000 RUB")
    vault = TokenVault(settings.app_secret)
    row = AffiliateWithdrawal(user_id=user.id, amount_minor=stats["available"],
                              details_encrypted=vault.cipher.encrypt(details.encode()).decode())
    db.add(row)
    await db.flush()
    for admin_id in settings.admin_ids:
        admin = await db.scalar(select(User).where(User.telegram_id == admin_id))
        if admin:
            await enqueue(db, "withdrawal:" + row.id + ":" + str(admin_id), admin.id, "PORTAL",
                          f"💸 Заявка на вывод {row.amount_minor / 100:g} ₽ на криптокошелёк. ID: {row.id}\n"
                          f"Клиент: {user.telegram_id}. Проверка: /withdrawals. Перевод не выполнен автоматически.")
    return row


async def decide_withdrawal(db, identity, admin_id, outcome, reference, settings):
    if admin_id not in settings.admin_ids or outcome not in {"PAID", "REJECTED"}:
        raise ValueError("Unauthorized withdrawal decision")
    await reconcile_reversals(db)
    row = await db.get(AffiliateWithdrawal, identity)
    if not row:
        raise ValueError("Withdrawal unavailable")
    await db.execute(update(User).where(User.id == row.user_id).values(is_active=User.is_active))
    await db.refresh(row)
    if row.status in {"PAID", "REJECTED"}:
        return row
    if row.status == "REVIEW" and outcome == "PAID":
        raise ValueError("Reversed payment requires review before withdrawal")
    if outcome == "PAID" and (not reference.strip() or len(reference) > 128):
        raise ValueError("Transfer receipt reference is required")
    row.status, row.decided_by, row.decided_at = outcome, admin_id, datetime.now(UTC)
    row.receipt_reference = reference.strip() if outcome == "PAID" else None
    await enqueue(db, "withdrawal-decision:" + row.id, row.user_id, "PORTAL",
                  f"💸 Вывод {row.amount_minor / 100:g} ₽ на криптокошелёк отмечен выполненным оператором."
                  if outcome == "PAID" else "Заявка на вывод отклонена. Сумма снова доступна в партнёрском балансе.")
    return row

