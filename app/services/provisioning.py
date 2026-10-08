from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.core.security import TokenVault
from app.db.models import ProvisioningJob, Subscription, User, VPNClient, uid
from app.integrations.xui.client import XUIClient
from app.integrations.xui.schemas import ClientSpec
from app.services.commerce import CommerceService, utc


class ProvisioningService:
    def __init__(self, sessions: async_sessionmaker, adapter: XUIClient, vault: TokenVault):
        self.sessions, self.adapter, self.vault = sessions, adapter, vault

    async def run(self, job_id: str, notify: bool = True) -> bool:
        # Reserve and persist UUID before external IO. Recovery reuses exactly the same identity.
        async with self.sessions.begin() as db:
            job = await db.get(ProvisioningJob, job_id)
            if not job or job.status == "DONE":
                return True
            sub = await db.scalar(
                select(Subscription).where(Subscription.id == job.subscription_id).with_for_update()
            )
            client = await db.scalar(select(VPNClient).where(VPNClient.subscription_id == sub.id))
            if not client:
                server = await CommerceService(db, self.vault).select_server()
                identity = uid()
                _, digest, encrypted = self.vault.create()
                client = VPNClient(
                    subscription_id=sub.id,
                    server_id=server.id,
                    inbound_id=server.inbound_id,
                    xui_client_id=identity,
                    uuid=identity,
                    email=f"aera-{identity}",
                    subscription_token_hash=digest,
                    token_encrypted=encrypted,
                    expires_at=sub.expires_at,
                    traffic_limit_bytes=sub.traffic_limit_bytes,
                )
                db.add(client)
        # Never hold row locks across the panel call: snapshot, push, then re-check and record.
        for _ in range(3):
            async with self.sessions() as db:
                job = await db.get(ProvisioningJob, job_id)
                if job.status == "DONE":
                    return True
                sub = await db.get(Subscription, job.subscription_id)
                spec = await self._spec(db, sub)
            try:
                await self.adapter.ensure_client(spec)
            except Exception:
                async with self.sessions.begin() as db:
                    job = await db.scalar(
                        select(ProvisioningJob)
                        .where(ProvisioningJob.id == job_id)
                        .with_for_update()
                    )
                    if job.status != "DONE":
                        job.attempts += 1
                        job.next_attempt_at = datetime.now(UTC) + timedelta(
                            seconds=min(5 * 3 ** min(job.attempts - 1, 6), 300)
                        )
                        job.status = "RETRY"
                return False
            async with self.sessions.begin() as db:
                job = await db.scalar(
                    select(ProvisioningJob).where(ProvisioningJob.id == job_id).with_for_update()
                )
                if job.status == "DONE":
                    return True
                sub = await db.scalar(
                    select(Subscription)
                    .where(Subscription.id == job.subscription_id)
                    .with_for_update()
                )
                if await self._spec(db, sub) != spec:
                    # Changed while the panel call was in flight; push the new state.
                    continue
                user = await db.get(User, sub.user_id)
                client = await db.scalar(
                    select(VPNClient).where(VPNClient.subscription_id == sub.id)
                )
                enabled = spec.enabled
                client.enabled = enabled
                client.expires_at = sub.expires_at
                client.traffic_limit_bytes = sub.traffic_limit_bytes
                sub.status = "ACTIVE" if enabled else "SUSPENDED"
                job.status = "DONE"
                from app.services.notifications import enqueue

                if enabled and job.payment_id and notify:
                    await enqueue(
                        db,
                        f"ready:{job.id}",
                        user.id,
                        "READY",
                        "AERA готова ✓ Откройте бота, чтобы подключить устройство.",
                        sub.id,
                    )
                elif enabled and user.trial_used and not job.payment_id:
                    from app.db.models import Payment

                    paid = await db.scalar(
                        select(Payment.id)
                        .where(Payment.user_id == user.id, Payment.status == "PAID")
                        .limit(1)
                    )
                    if not paid:
                        sub.status = "TRIAL"
                return True
        # The subscription kept changing; the worker retries the job on its next tick.
        return False

    @staticmethod
    async def _spec(db, sub) -> ClientSpec:
        user = await db.get(User, sub.user_id)
        client = await db.scalar(select(VPNClient).where(VPNClient.subscription_id == sub.id))
        enabled = (
            user.is_active
            and not user.is_blocked
            and sub.status not in {"SUSPENDED", "CANCELLED"}
            and utc(sub.expires_at) > datetime.now(UTC)
        )
        return ClientSpec(
            client.uuid,
            client.email,
            client.inbound_id,
            sub.expires_at,
            sub.traffic_limit_bytes,
            enabled,
            client.server_id,
        )

    @staticmethod
    def _client_spec(client) -> ClientSpec:
        return ClientSpec(
            client.uuid,
            client.email,
            client.inbound_id,
            client.expires_at,
            client.traffic_limit_bytes,
            client.enabled,
            client.server_id,
        )

    async def tick(self) -> None:
        async with self.sessions() as db:
            ids = list(
                await db.scalars(
                    select(ProvisioningJob.id).where(
                        ProvisioningJob.status != "DONE",
                        ProvisioningJob.next_attempt_at <= datetime.now(UTC),
                    )
                )
            )
        for job_id in ids:
            try:
                await self.run(job_id)
            except Exception:
                async with self.sessions.begin() as db:
                    job = await db.get(ProvisioningJob, job_id)
                    job.attempts += 1
                    job.status = "RETRY"
                    job.next_attempt_at = datetime.now(UTC) + timedelta(seconds=300)
        # Status changes are DB-only and short; panel I/O happens after commit.
        pending = []
        async with self.sessions.begin() as db:
            from app.services.referrals import reward_referrals

            await reward_referrals(db)
            subs = await db.scalars(
                select(Subscription)
                .where(
                    Subscription.status.in_(["ACTIVE", "EXPIRING", "EXPIRED", "SUSPENDED", "TRIAL"])
                )
                .with_for_update(skip_locked=True)
            )
            for sub in subs:
                client = await db.scalar(
                    select(VPNClient).where(VPNClient.subscription_id == sub.id)
                )
                if not client:
                    continue
                expired = utc(sub.expires_at) <= datetime.now(UTC)
                user = await db.get(User, sub.user_id)
                if expired:
                    sub.status = "EXPIRED"
                    client.enabled = False
                elif user.is_blocked or not user.is_active or sub.status == "SUSPENDED":
                    client.enabled = False
                elif sub.status == "ACTIVE" and utc(sub.expires_at) - datetime.now(
                    UTC
                ) <= timedelta(days=3):
                    sub.status = "EXPIRING"
                pending.append((client.id, self._client_spec(client)))
        for client_id, spec in pending:
            try:
                await self.adapter.ensure_client(spec)
                traffic = await self.adapter.get_client_traffic(spec.email)
            except Exception:
                # EXPIRED remains eligible for later disable retries.
                continue
            async with self.sessions.begin() as db:
                client = await db.scalar(
                    select(VPNClient).where(VPNClient.id == client_id).with_for_update()
                )
                if client is None or self._client_spec(client) != spec:
                    # Changed meanwhile; the next tick pushes and measures the new state.
                    continue
                client.traffic_used_bytes = traffic.uploaded + traffic.downloaded
                if (
                    client.traffic_limit_bytes
                    and client.traffic_used_bytes >= client.traffic_limit_bytes
                ):
                    client.enabled = False
