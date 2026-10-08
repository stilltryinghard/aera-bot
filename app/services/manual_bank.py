"""Merchant checkout, authenticated polling and delivery from personal VPN key stock."""

import asyncio
import logging
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from urllib.parse import urlsplit

from sqlalchemy import select, update

from app.db.models import PaidLink, Payment, SaleOrder, SupportTicket, User
from app.services.commerce import utc
from app.services.portal import confirm_order

_create_lock = asyncio.Lock()
# WATA links expire after 3 days; allow a day for late settlement before giving up.
WATA_ABANDON_AFTER = timedelta(days=4)
# Well past the 20 s HTTP timeout: an older unanswered link request is no longer in flight.
WATA_REQUEST_ABANDON_AFTER = timedelta(minutes=2)


def merchant(settings, provider):
    if not settings.manual_sbp_enabled:
        return None
    if provider == "yookassa" and settings.yookassa_shop_id and settings.yookassa_secret_key:
        from app.integrations.payments.yookassa import YooKassaProvider

        return YooKassaProvider(settings.yookassa_shop_id, settings.yookassa_secret_key)
    if provider == "wata" and settings.wata_token and settings.wata_terminal_id:
        from app.integrations.payments.wata import WATAProvider

        return WATAProvider(settings.wata_token, settings.wata_terminal_id)
    return None


def yoo_matches(body, identity, amount):
    try:
        return (
            body.get("metadata", {}).get("payment_id") == identity
            and Decimal(body["amount"]["value"]) * 100 == amount
            and body["amount"]["currency"] == "RUB"
            and body.get("test") is False
        )
    except (KeyError, TypeError, ValueError, ArithmeticError):
        return False


async def checkout(sessions, settings, order_id, user_id, provider):
    adapter = merchant(settings, provider)
    if adapter is None:
        return None
    try:
        async with _create_lock:
            async with sessions.begin() as db:
                await db.execute(
                    update(User).where(User.id == user_id).values(is_active=User.is_active)
                )
                order = await db.get(SaleOrder, order_id, populate_existing=True)
                ticket = await db.get(SupportTicket, order.ticket_id) if order else None
                user = await db.get(User, user_id)
                if (
                    not ticket
                    or ticket.user_id != user_id
                    or order.paid_at
                    or ticket.status not in {"OPEN", "IN_PROGRESS"}
                    or not user.terms_accepted_at
                ):
                    raise ValueError("Order unavailable")
                from app.services.paid_pool import enabled, reserve

                if await enabled(db) and await reserve(db, order) is None:
                    return None
                payment = await db.get(Payment, order.payment_id) if order.payment_id else None
                if payment:
                    if payment.provider != "manual_" + provider:
                        raise ValueError("Another payment is pending")
                    if payment.details.get("checkout_url"):
                        return payment.details["checkout_url"]
                    if provider == "wata" and payment.details.get("request_started"):
                        # The link POST is not idempotent and its outcome is unknown. Never
                        # repeat it for the same payment; once it can no longer be in flight,
                        # abandon that attempt. Its link never reached the customer, so it
                        # cannot have been paid.
                        started = payment.details.get("request_started_at")
                        if (
                            started
                            and datetime.now(UTC) - datetime.fromtimestamp(started, UTC)
                            < WATA_REQUEST_ABANDON_AFTER
                        ):
                            raise ValueError("WATA link creation in progress")
                        payment.status = "CANCELLED"
                        order.payment_id = None
                        await db.flush()
                        payment = None
                if payment is None:
                    payment = Payment(
                        user_id=user_id,
                        plan_id=order.plan_id,
                        amount_minor=order.amount_rub_minor,
                        currency="RUB",
                        provider="manual_" + provider,
                        details={"manual_order": order.id},
                    )
                    db.add(payment)
                    await db.flush()
                    order.payment_id = payment.id
                payment.details = {
                    **payment.details,
                    "request_started": True,
                    "request_started_at": datetime.now(UTC).timestamp(),
                }
                identity, amount = payment.id, payment.amount_minor
            return_url = f"https://t.me/{settings.bot_username}"
            if provider == "yookassa":
                body = await adapter.create_payment(identity, amount, "RUB", return_url, sbp=True)
                if not yoo_matches(body, identity, amount):
                    raise ValueError("Merchant response mismatch")
                url = body["confirmation"]["confirmation_url"]
            else:
                body = await adapter.create_payment(identity, amount, return_url)
                if not adapter.matches(body, identity, amount):
                    raise ValueError("Merchant response mismatch")
                url = body["url"]
            if urlsplit(url).scheme != "https":
                raise ValueError("Unsafe checkout URL")
            async with sessions.begin() as db:
                await db.execute(
                    update(User).where(User.id == user_id).values(is_active=User.is_active)
                )
                payment = await db.get(Payment, identity, populate_existing=True)
                if payment.status != "PENDING":
                    # Abandoned by a later attempt; never hand out a link nobody will poll.
                    raise ValueError("Checkout attempt was abandoned")
                payment.provider_payment_id = body["id"]
                payment.details = {**payment.details, "checkout_url": url}
            return url
    finally:
        await adapter.close()


async def cancel(sessions, record):
    """Close a checkout the merchant can no longer settle so it stops occupying the poll batch.

    The order keeps its reserved key and can be paid again with a new checkout.
    """
    async with sessions.begin() as db:
        await db.execute(
            update(User).where(User.id == record.user_id).values(is_active=User.is_active)
        )
        payment = await db.get(Payment, record.id, populate_existing=True)
        if payment.status != "PENDING":
            return
        payment.status = "CANCELLED"
        order = await db.scalar(select(SaleOrder).where(SaleOrder.payment_id == payment.id))
        if order and not order.paid_at:
            order.payment_id = None


async def poll(sessions, settings):
    async with sessions() as db:
        records = list(
            await db.scalars(
                select(Payment)
                .where(
                    Payment.status == "PENDING",
                    Payment.provider.in_(["manual_yookassa", "manual_wata"]),
                    # Without a checkout link nobody can pay; such rows must not use up the batch.
                    Payment.details["checkout_url"].as_string().is_not(None),
                )
                .order_by(Payment.created_at)
                .limit(40)
            )
        )
    for record in records:
        provider = record.provider.removeprefix("manual_")
        adapter = merchant(settings, provider)
        if adapter is None:
            continue
        try:
            if provider == "yookassa":
                body = await adapter.verify_payment(record.provider_payment_id)
                paid = (
                    body.get("id") == record.provider_payment_id
                    and body.get("status") == "succeeded"
                    and body.get("paid") is True
                    and yoo_matches(body, record.id, record.amount_minor)
                )
                abandoned = (
                    body.get("id") == record.provider_payment_id
                    and body.get("status") == "canceled"
                )
            else:
                body = await adapter.paid_transaction(
                    record.id, record.amount_minor, record.provider_payment_id
                )
                paid = body is not None
                abandoned = (
                    not paid and datetime.now(UTC) - utc(record.created_at) > WATA_ABANDON_AFTER
                )
            if abandoned:
                await cancel(sessions, record)
            if paid:
                async with sessions.begin() as db:
                    await db.execute(
                        update(User)
                        .where(User.id == record.user_id)
                        .values(is_active=User.is_active)
                    )
                    payment = await db.get(Payment, record.id, populate_existing=True)
                    if payment.status == "PAID":
                        continue
                    order = await db.scalar(
                        select(SaleOrder).where(SaleOrder.payment_id == payment.id)
                    )
                    if not order:
                        raise ValueError("Paid order missing")
                    payment.status, payment.paid_at = "PAID", datetime.now(UTC)
                    payment.details = {**payment.details, "verified_transaction": body["id"]}
                    await confirm_order(db, order, settings)
                    issued = await db.scalar(
                        select(PaidLink.id).where(
                            PaidLink.order_id == order.id, PaidLink.issued_at.is_not(None)
                        )
                    )
                    user = await db.get(User, payment.user_id)
                    from app.bot.texts.portal import text
                    from app.services.notifications import enqueue

                    await enqueue(
                        db,
                        "bank-paid:" + payment.id,
                        user.id,
                        "PORTAL",
                        text("paid_ready" if issued else "paid", user.language_code),
                    )
        except Exception as error:
            logging.getLogger("aera").error("bank_poll_failed type=%s", type(error).__name__)
        finally:
            await adapter.close()
