"""Platega checkout with durable attempts and verified, idempotent fulfillment."""

import json
import logging
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from urllib.parse import urlsplit

import httpx
from sqlalchemy import and_, or_, select, update
from app.db.models import PaidLink, Payment, Plan, SaleOrder, SupportTicket, User

ORIGIN = "https://aera-reserve.duckdns.org"
METHODS = {"sbp": 2, "crypto": 13}


def configuration():
    return json.loads(Path("/etc/aera-payment/platega.json").read_text())


def credentials(channel):
    if channel not in {"bot", "site"}:
        raise ValueError("Unknown payment channel")
    return configuration()[channel]


def headers(channel):
    c = credentials(channel)
    return {"X-MerchantId": c["merchant_id"], "X-Secret": c["secret"]}


async def api(channel, method, path, body=None):
    async with httpx.AsyncClient(timeout=25, follow_redirects=False) as client:
        response = await client.request(
            method, "https://app.platega.io" + path, headers=headers(channel), json=body
        )
        if response.status_code not in {200, 201}:
            raise ValueError("Payment provider rejected request")
        return response.json()


def minor(value):
    try:
        number = Decimal(str(value)) * 100
        if not number.is_finite() or number != number.to_integral_value():
            raise ValueError("Invalid amount")
        return int(number)
    except (InvalidOperation, TypeError):
        raise ValueError("Invalid amount") from None


async def create_payment(
    sessions, settings, user_id, method, channel, *, plan_id=None, order_id=None
):
    if method not in METHODS:
        raise ValueError("Payment method unavailable")
    credentials(channel)
    async with sessions.begin() as db:
        await db.execute(update(User).where(User.id == user_id).values(is_active=User.is_active))
        user = await db.get(User, user_id)
        if not user or not user.is_active or user.is_blocked:
            raise ValueError("Account unavailable")
        if order_id:
            order = await db.get(SaleOrder, order_id)
            ticket = await db.get(SupportTicket, order.ticket_id) if order else None
            if (
                not ticket
                or ticket.user_id != user.id
            ):
                raise ValueError("Order unavailable")
            from app.services.payment_timeout import require_open
            await require_open(db, order)
            if ticket.status not in {"OPEN", "IN_PROGRESS"}:
                raise ValueError("Order unavailable")
            plan = await db.get(Plan, order.plan_id)
        else:
            from app.services.requests import select_tariff
            from app.services.commerce import CommerceService
            from app.core.security import TokenVault
            from app.services.portal import ensure_order

            ticket, plan = await select_tariff(
                db,
                CommerceService(db, TokenVault(settings.app_secret)),
                user,
                plan_id,
                settings.admin_ids,
                notify_owner=False,
            )
            order = await ensure_order(db, ticket, plan)
        if (
            not plan
            or not plan.is_active
            or order.paid_at
            or not 0 < order.amount_rub_minor <= 1000000
        ):
            raise ValueError("Order unavailable")
        from app.services.payment_timeout import require_open
        await require_open(db, order)
        if order.payment_id:
            previous = await db.get(Payment, order.payment_id)
            if (
                previous.provider == "platega"
                and previous.status == "PENDING"
                and previous.details.get("channel") == channel
                and previous.details.get("method") == method
                and previous.details.get("checkout_url")
            ):
                return {"url": previous.details["checkout_url"], "payment_id": previous.id}
            raise ValueError(
                "This order already has a payment. Check its status before paying again."
            )
        from app.services.paid_pool import enabled, reserve

        if await enabled(db) and not await reserve(db, order):
            raise ValueError("No available access for this tariff. Please contact support.")
        payment = Payment(
            user_id=user.id,
            plan_id=plan.id,
            provider="platega",
            currency="RUB",
            amount_minor=order.amount_rub_minor,
            status="CREATING",
            details={"manual_order": order.id, "channel": channel, "method": method},
        )
        db.add(payment)
        await db.flush()
        order.payment_id = payment.id
        user.terms_accepted_at = datetime.now(UTC)
        user.terms_version = "portal-2"
        pid, amount = payment.id, payment.amount_minor
        metadata = {"userId": str(user.telegram_id)}
        if user.username:
            metadata["userName"] = "@" + user.username
        description = f"AERA {plan.name} / {plan.duration_days} days"
    # Persist the attempt before external I/O. A timeout must never silently create a second invoice.
    try:
        body = await api(
            channel,
            "POST",
            "/transaction/process",
            {
                "paymentMethod": METHODS[method],
                "paymentDetails": {"amount": amount / 100, "currency": "RUB"},
                "description": description,
                "return": ORIGIN + "/payments/success",
                "failedUrl": ORIGIN + "/payments/failure",
                "payload": pid,
                "metadata": metadata,
            },
        )
        transaction = str(body["transactionId"])
        from uuid import UUID

        UUID(transaction)
        url = str(body["redirect"])
        parsed = urlsplit(url)
        if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
            raise ValueError("Invalid checkout URL")
        if body.get("merchantId") and body["merchantId"] != credentials(channel)["merchant_id"]:
            raise ValueError("Merchant mismatch")
        async with sessions.begin() as db:
            payment = await db.get(Payment, pid)
            payment.provider_payment_id = transaction
            payment.status = "PENDING"
            payment.details = {**payment.details, "checkout_url": url}
        return {"url": url, "payment_id": pid}
    except Exception:
        async with sessions.begin() as db:
            payment = await db.get(Payment, pid)
            if payment and payment.status == "CREATING":
                payment.status = "UNKNOWN"
        raise ValueError(
            "Не удалось получить ссылку оплаты. Не оплачивайте повторно; обратитесь в поддержку."
        ) from None


async def reconcile(sessions, settings, channel, transaction, callback=None):
    async with sessions() as db:
        record = await db.scalar(
            select(Payment).where(
                Payment.provider == "platega", Payment.provider_payment_id == transaction
            )
        )
        if not record or record.details.get("channel") != channel:
            raise ValueError("Unknown transaction")
        pid, expected = record.id, record.amount_minor
    if callback:
        if minor(callback.get("amount")) != expected or callback.get("currency") != "RUB":
            raise ValueError("Callback amount mismatch")
        if callback.get("paymentMethod") != METHODS[record.details["method"]]:
            raise ValueError("Callback method mismatch")
    body = await api(channel, "GET", "/transaction/" + transaction)
    detail = body.get("paymentDetails", {})
    if (
        body.get("id") != transaction
        or not isinstance(detail, dict)
        or detail.get("currency") != "RUB"
        or minor(detail.get("amount")) != expected
        or body.get("payload") != pid
    ):
        raise ValueError("Verified transaction mismatch")
    merchant = body.get("merchantId") or body.get("mechantId")
    if merchant and merchant != credentials(channel)["merchant_id"]:
        raise ValueError("Merchant mismatch")
    status = body.get("status")
    async with sessions.begin() as db:
        await db.execute(
            update(User).where(User.id == record.user_id).values(is_active=User.is_active)
        )
        payment = await db.get(Payment, pid, populate_existing=True)
        order = await db.scalar(select(SaleOrder).where(SaleOrder.payment_id == pid))
        if not order:
            raise ValueError("Missing order")
        payment.details = {
            **payment.details,
            "provider_status": status,
            "provider_checked_at": datetime.now(UTC).isoformat(),
        }
        if status == "CONFIRMED":
            if payment.status in {"PAID", "CHARGEBACKED"}:
                return payment.status
            payment.status, payment.paid_at = "PAID", datetime.now(UTC)
            from app.services.portal import confirm_order

            await confirm_order(db, order, settings)
        elif status == "CHARGEBACKED":
            payment.status = "CHARGEBACKED"
            from app.services.notifications import enqueue

            for aid in settings.admin_ids:
                admin = await db.scalar(select(User).where(User.telegram_id == aid))
                if admin:
                    await enqueue(
                        db,
                        f"platega-chargeback:{pid}:{aid}",
                        admin.id,
                        "PORTAL",
                        f"⚠️ Platega: возврат платежа {pid}. Проверьте доступ клиента.",
                    )
        elif status in {"CANCELED", "CANCELLED"} and payment.status not in {"PAID", "CHARGEBACKED"}:
            payment.status = "CANCELLED"
            from app.services.orders import close_cancelled_order

            await close_cancelled_order(db, order)
        return payment.status


async def poll(sessions, settings):
    async with sessions() as db:
        records = list(
            await db.scalars(
                select(Payment)
                .join(SaleOrder, SaleOrder.payment_id == Payment.id)
                .join(SupportTicket, SupportTicket.id == SaleOrder.ticket_id)
                .outerjoin(PaidLink, PaidLink.order_id == SaleOrder.id)
                .where(
                    Payment.provider == "platega",
                    Payment.provider_payment_id.is_not(None),
                    or_(
                        Payment.status == "PENDING",
                        and_(
                            Payment.status == "CANCELLED",
                            or_(
                                SupportTicket.status.in_(["OPEN", "IN_PROGRESS"]),
                                and_(PaidLink.id.is_not(None), PaidLink.issued_at.is_(None)),
                            ),
                        ),
                    ),
                )
                .order_by(Payment.updated_at, Payment.created_at)
                .limit(40)
            )
        )
    for record in records:
        try:
            await reconcile(
                sessions, settings, record.details["channel"], record.provider_payment_id
            )
        except Exception as error:
            logging.getLogger("aera").error("platega_poll_failed type=%s", type(error).__name__)

        finally:
            async with sessions.begin() as db:
                current = await db.get(Payment, record.id)
                if current and current.status in {"PENDING", "CANCELLED"}:
                    current.updated_at = datetime.now(UTC)
