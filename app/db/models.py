import secrets
import uuid
from datetime import UTC, datetime

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    false,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def now() -> datetime:
    return datetime.now(UTC)


def uid() -> str:
    return str(uuid.uuid4())


class Base(DeclarativeBase):
    pass


class Record:
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, onupdate=now)


class User(Record, Base):
    __tablename__ = "users"
    telegram_id: Mapped[int] = mapped_column(BigInteger, unique=True)
    username: Mapped[str | None] = mapped_column(String(128))
    first_name: Mapped[str | None] = mapped_column(String(128))
    last_name: Mapped[str | None] = mapped_column(String(128))
    language_code: Mapped[str] = mapped_column(String(16), default="ru")
    referral_code: Mapped[str] = mapped_column(
        String(32), unique=True, default=lambda: secrets.token_urlsafe(12)
    )
    referred_by_id: Mapped[str | None] = mapped_column(ForeignKey("users.id"))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    is_blocked: Mapped[bool] = mapped_column(Boolean, default=False)
    trial_used: Mapped[bool] = mapped_column(Boolean, default=False)
    terms_accepted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    terms_version: Mapped[str | None] = mapped_column(String(64))
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class Plan(Record, Base):
    __tablename__ = "plans"
    __table_args__ = (
        CheckConstraint("price_minor > 0 AND duration_days > 0 AND device_limit > 0"),
        CheckConstraint("traffic_limit_bytes >= 0"),
    )
    name: Mapped[str] = mapped_column(String(128))
    slug: Mapped[str] = mapped_column(String(64), unique=True)
    description: Mapped[str] = mapped_column(Text, default="")
    price_minor: Mapped[int] = mapped_column(Integer)
    currency: Mapped[str] = mapped_column(String(8), default="RUB")
    stars_price: Mapped[int | None] = mapped_column(Integer)
    crypto_asset: Mapped[str | None] = mapped_column(String(16))
    crypto_amount: Mapped[str | None] = mapped_column(String(80))
    duration_days: Mapped[int] = mapped_column(Integer, default=30)
    duration_months: Mapped[int | None] = mapped_column(Integer)
    traffic_limit_bytes: Mapped[int] = mapped_column(BigInteger)
    device_limit: Mapped[int] = mapped_column(Integer)
    unlimited_devices: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false())
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    is_featured: Mapped[bool] = mapped_column(Boolean, default=False)
    sort_order: Mapped[int] = mapped_column(Integer, default=0)


class Server(Record, Base):
    __tablename__ = "servers"
    __table_args__ = (CheckConstraint("capacity >= 0"),)
    name: Mapped[str] = mapped_column(String(128))
    code: Mapped[str] = mapped_column(String(64), unique=True)
    country: Mapped[str] = mapped_column(String(64), default="Mock")
    city: Mapped[str] = mapped_column(String(64), default="Local")
    xui_base_url: Mapped[str] = mapped_column(String(512), default="mock://local")
    inbound_id: Mapped[int] = mapped_column(Integer, default=1)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    capacity: Mapped[int] = mapped_column(Integer, default=500)
    priority: Mapped[int] = mapped_column(Integer, default=0)
    public_config: Mapped[dict] = mapped_column(JSON, default=dict)


class Subscription(Record, Base):
    __tablename__ = "subscriptions"
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), unique=True)
    plan_id: Mapped[str] = mapped_column(ForeignKey("plans.id"))
    status: Mapped[str] = mapped_column(String(32), default="PENDING_PROVISIONING")
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    traffic_limit_bytes: Mapped[int] = mapped_column(BigInteger)
    device_limit: Mapped[int] = mapped_column(Integer)
    unlimited_devices: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false())
    auto_renew: Mapped[bool] = mapped_column(Boolean, default=False)


class Payment(Record, Base):
    __tablename__ = "payments"
    __table_args__ = (CheckConstraint("amount_minor > 0"),)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
    plan_id: Mapped[str] = mapped_column(ForeignKey("plans.id"))
    provider: Mapped[str] = mapped_column(String(32), default="mock")
    provider_payment_id: Mapped[str] = mapped_column(String(128), unique=True, default=uid)
    amount_minor: Mapped[int] = mapped_column(Integer)
    currency: Mapped[str] = mapped_column(String(8))
    status: Mapped[str] = mapped_column(String(32), default="PENDING")
    paid_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    details: Mapped[dict] = mapped_column("metadata", JSON, default=dict)
    applied_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class VPNClient(Record, Base):
    __tablename__ = "vpn_clients"
    subscription_id: Mapped[str] = mapped_column(ForeignKey("subscriptions.id"), unique=True)
    server_id: Mapped[str] = mapped_column(ForeignKey("servers.id"))
    inbound_id: Mapped[int] = mapped_column(Integer)
    xui_client_id: Mapped[str] = mapped_column(String(128), unique=True)
    uuid: Mapped[str] = mapped_column(String(36), unique=True, default=uid)
    email: Mapped[str] = mapped_column(String(128), unique=True)
    subscription_token_hash: Mapped[str | None] = mapped_column(String(64), unique=True)
    token_encrypted: Mapped[str | None] = mapped_column(Text)
    traffic_used_bytes: Mapped[int] = mapped_column(BigInteger, default=0)
    traffic_limit_bytes: Mapped[int] = mapped_column(BigInteger)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    enabled: Mapped[bool] = mapped_column(Boolean, default=False)


class ProvisioningJob(Record, Base):
    __tablename__ = "provisioning_jobs"
    payment_id: Mapped[str | None] = mapped_column(ForeignKey("payments.id"), unique=True)
    subscription_id: Mapped[str] = mapped_column(ForeignKey("subscriptions.id"))
    status: Mapped[str] = mapped_column(String(32), default="PENDING")
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    next_attempt_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class Referral(Record, Base):
    __tablename__ = "referrals"
    referrer_user_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
    referred_user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), unique=True)
    code: Mapped[str] = mapped_column(String(32))
    status: Mapped[str] = mapped_column(String(32), default="PENDING")
    reward_type: Mapped[str] = mapped_column(String(32), default="EXTRA_DAYS")
    reward_value: Mapped[int] = mapped_column(Integer, default=7)
    rewarded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class PromoCode(Record, Base):
    __tablename__ = "promo_codes"
    code: Mapped[str] = mapped_column(String(64), unique=True)
    discount_type: Mapped[str] = mapped_column(String(32))
    discount_value: Mapped[int] = mapped_column(Integer)
    max_uses: Mapped[int] = mapped_column(Integer, default=100)
    uses: Mapped[int] = mapped_column(Integer, default=0)
    valid_from: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    valid_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    plan_id: Mapped[str | None] = mapped_column(ForeignKey("plans.id"))
    one_use_per_user: Mapped[bool] = mapped_column(Boolean, default=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)


class PromoUse(Record, Base):
    __tablename__ = "promo_uses"
    promo_id: Mapped[str] = mapped_column(ForeignKey("promo_codes.id"))
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
    payment_id: Mapped[str] = mapped_column(ForeignKey("payments.id"), unique=True)


class Notification(Record, Base):
    __tablename__ = "notifications"
    dedupe_key: Mapped[str] = mapped_column(String(200), unique=True)
    notification_type: Mapped[str] = mapped_column(String(64))
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
    subscription_id: Mapped[str | None] = mapped_column(ForeignKey("subscriptions.id"))
    text: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(32), default="PENDING")
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    attempts: Mapped[int] = mapped_column(Integer, default=0)


class Broadcast(Record, Base):
    __tablename__ = "broadcasts"
    admin_user_id: Mapped[int] = mapped_column(BigInteger)
    text: Mapped[str] = mapped_column(Text)
    image_file_id: Mapped[str | None] = mapped_column(String(256))
    button_text: Mapped[str | None] = mapped_column(String(128))
    button_url: Mapped[str | None] = mapped_column(String(512))
    status: Mapped[str] = mapped_column(String(32), default="PENDING")
    total: Mapped[int] = mapped_column(Integer, default=0)
    sent: Mapped[int] = mapped_column(Integer, default=0)
    failed: Mapped[int] = mapped_column(Integer, default=0)
    blocked: Mapped[int] = mapped_column(Integer, default=0)


class BroadcastDelivery(Record, Base):
    __tablename__ = "broadcast_deliveries"
    __table_args__ = (UniqueConstraint("broadcast_id", "user_id"),)
    broadcast_id: Mapped[str] = mapped_column(ForeignKey("broadcasts.id"))
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
    status: Mapped[str] = mapped_column(String(32), default="PENDING")


class CryptoInvoice(Record, Base):
    __tablename__ = "crypto_invoices"
    payment_id: Mapped[str] = mapped_column(ForeignKey("payments.id"), unique=True)
    provider_invoice_id: Mapped[str] = mapped_column(String(128), unique=True)
    provider: Mapped[str] = mapped_column(String(32), default="crypto_mock")
    asset: Mapped[str] = mapped_column(String(32))
    network: Mapped[str] = mapped_column(String(32))
    expected_amount: Mapped[str] = mapped_column(String(80))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(32), default="PENDING")


class CryptoMockReceipt(Record, Base):
    __tablename__ = "crypto_mock_receipts"
    provider_invoice_id: Mapped[str] = mapped_column(String(128), unique=True)
    asset: Mapped[str] = mapped_column(String(32))
    network: Mapped[str] = mapped_column(String(32))
    received_amount: Mapped[str] = mapped_column(String(80), default="0")
    status: Mapped[str] = mapped_column(String(32), default="PENDING")
    paid_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class AppSetting(Base):
    __tablename__ = "app_settings"
    key: Mapped[str] = mapped_column(String(128), primary_key=True)
    value: Mapped[str] = mapped_column(Text)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, onupdate=now)


class AuditLog(Record, Base):
    __tablename__ = "audit_logs"
    admin_user_id: Mapped[int] = mapped_column(BigInteger)
    action: Mapped[str] = mapped_column(String(128))
    entity_type: Mapped[str] = mapped_column(String(64))
    entity_id: Mapped[str] = mapped_column(String(36))
    details: Mapped[dict] = mapped_column("metadata", JSON, default=dict)


class SupportTicket(Record, Base):
    __tablename__ = "support_tickets"
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
    category: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(32), default="OPEN")
    subject: Mapped[str] = mapped_column(String(256))
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class SupportMessage(Record, Base):
    __tablename__ = "support_messages"
    ticket_id: Mapped[str] = mapped_column(ForeignKey("support_tickets.id"))
    sender_type: Mapped[str] = mapped_column(String(32))
    sender_id: Mapped[str] = mapped_column(String(36))
    text: Mapped[str] = mapped_column(Text)


class TrialLink(Record, Base):
    __tablename__ = "trial_links"
    label: Mapped[str] = mapped_column(String(128))
    client_uuid: Mapped[str] = mapped_column(String(36), unique=True)
    link_encrypted: Mapped[str] = mapped_column(Text)
    user_id: Mapped[str | None] = mapped_column(ForeignKey("users.id"), unique=True)
    status: Mapped[str] = mapped_column(String(32), default="UNVERIFIED")
    panel_email: Mapped[str | None] = mapped_column(String(128))
    issued_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    source_uuid: Mapped[str | None] = mapped_column(String(36), unique=True)
    pending_uuid: Mapped[str | None] = mapped_column(String(36))
    recycle_reason: Mapped[str | None] = mapped_column(String(32))
    baseline_bytes: Mapped[int] = mapped_column(BigInteger, default=0)
    activation_deadline_ms: Mapped[int | None] = mapped_column(BigInteger)
    ready_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class TrialUsage(Base):
    __tablename__ = "trial_usage"
    fingerprint: Mapped[str] = mapped_column(String(64), primary_key=True)
    activated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class FamilyAccess(Record, Base):
    __tablename__ = "family_access"
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), unique=True)
    device_limit: Mapped[int] = mapped_column(Integer, default=0)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    link_encrypted: Mapped[str | None] = mapped_column(Text)


class ManualAccess(Record, Base):
    __tablename__ = "manual_access"
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), unique=True)
    plan_id: Mapped[str] = mapped_column(ForeignKey("plans.id"))
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    link_encrypted: Mapped[str | None] = mapped_column(Text)


class ReferralCoupon(Record, Base):
    __tablename__ = "referral_coupons"
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
    referral_id: Mapped[str] = mapped_column(ForeignKey("referrals.id"), unique=True)
    status: Mapped[str] = mapped_column(String(32), default="AVAILABLE")
    reserved_ticket_id: Mapped[str | None] = mapped_column(ForeignKey("support_tickets.id"))


class SaleOrder(Record, Base):
    __tablename__ = "sale_orders"
    ticket_id: Mapped[str] = mapped_column(ForeignKey("support_tickets.id"), unique=True)
    plan_id: Mapped[str] = mapped_column(ForeignKey("plans.id"))
    coupon_id: Mapped[str | None] = mapped_column(ForeignKey("referral_coupons.id"))
    amount_rub_minor: Mapped[int] = mapped_column(Integer)
    discount_percent: Mapped[int] = mapped_column(Integer, default=0)
    paid_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    payment_id: Mapped[str | None] = mapped_column(ForeignKey("payments.id"), unique=True)


class PaidLink(Record, Base):
    __tablename__ = "paid_links"
    label: Mapped[str] = mapped_column(String(128))
    client_uuid: Mapped[str] = mapped_column(String(36), unique=True)
    link_encrypted: Mapped[str] = mapped_column(Text)
    plan_id: Mapped[str] = mapped_column(ForeignKey("plans.id"))
    order_id: Mapped[str | None] = mapped_column(ForeignKey("sale_orders.id"), unique=True)
    user_id: Mapped[str | None] = mapped_column(ForeignKey("users.id"))
    status: Mapped[str] = mapped_column(String(32), default="UNVERIFIED")
    duration_ms: Mapped[int | None] = mapped_column(BigInteger)
    panel_email: Mapped[str | None] = mapped_column(String(128))
    issued_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ReferralSoloReward(Record, Base):
    __tablename__ = "referral_solo_rewards"
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), unique=True)
    referral_ids: Mapped[list] = mapped_column(JSON)
    order_id: Mapped[str | None] = mapped_column(ForeignKey("sale_orders.id"), unique=True)
    status: Mapped[str] = mapped_column(String(32), default="PENDING")
    issued_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class AccessRenewal(Record, Base):
    __tablename__ = "access_renewals"
    order_id: Mapped[str] = mapped_column(ForeignKey("sale_orders.id"), unique=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
    access_kind: Mapped[str] = mapped_column(String(16))
    access_id: Mapped[str] = mapped_column(String(36))
    duration_days: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(24), default="CHECKOUT")
    client_uuid: Mapped[str | None] = mapped_column(String(36))
    panel_email: Mapped[str | None] = mapped_column(String(128))
    original_expiry_ms: Mapped[int | None] = mapped_column(BigInteger)
    target_expiry_ms: Mapped[int | None] = mapped_column(BigInteger)
    fingerprint: Mapped[dict | None] = mapped_column(JSON)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class AffiliateCommission(Record, Base):
    __tablename__ = "affiliate_commissions"
    __table_args__ = (CheckConstraint("amount_minor >= 0 AND purchase_minor > 0"),)
    referred_user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), unique=True)
    referrer_user_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
    order_id: Mapped[str] = mapped_column(ForeignKey("sale_orders.id"), unique=True)
    purchase_minor: Mapped[int] = mapped_column(Integer)
    amount_minor: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(32), default="CONFIRMED")


class AffiliateWithdrawal(Record, Base):
    __tablename__ = "affiliate_withdrawals"
    __table_args__ = (CheckConstraint("amount_minor >= 100000"),)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
    amount_minor: Mapped[int] = mapped_column(Integer)
    details_encrypted: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(32), default="PENDING")
    decided_by: Mapped[int | None] = mapped_column(BigInteger)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    receipt_reference: Mapped[str | None] = mapped_column(String(128))


class DeviceBinding(Record, Base):
    __tablename__ = "device_bindings"
    __table_args__ = (UniqueConstraint("parent_uuid", "device_hash"),)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
    paid_link_id: Mapped[str | None] = mapped_column(ForeignKey("paid_links.id"))
    trial_link_id: Mapped[str | None] = mapped_column(ForeignKey("trial_links.id"))
    parent_uuid: Mapped[str] = mapped_column(String(36))
    device_hash: Mapped[str] = mapped_column(String(64))
    client_uuid: Mapped[str] = mapped_column(String(36), unique=True)
    label: Mapped[str] = mapped_column(String(128), default="AERA device")
    platform: Mapped[str] = mapped_column(String(32), default="")
    device_model: Mapped[str] = mapped_column(String(64), default="")
    link_encrypted: Mapped[str] = mapped_column(Text)
    bundle_encrypted: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(32), default="PENDING")
    duration_ms: Mapped[int] = mapped_column(BigInteger)
    issued_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class DeviceSubscription(Base):
    __tablename__ = "device_subscriptions"
    parent_uuid: Mapped[str] = mapped_column(String(36), primary_key=True)
    public_uuid: Mapped[str] = mapped_column(String(36), unique=True)
