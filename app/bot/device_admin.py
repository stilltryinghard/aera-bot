"""Owner-only device revocation; a slot frees after every node is disabled."""
from html import escape
import logging
from uuid import UUID

from aiogram import Router
from aiogram.filters import Command
from sqlalchemy import select, update

from app.config import get_settings
from app.db.models import DeviceBinding, User
from app.db.session import sessions
from app.services import device_access

router = Router(name="device_admin")
logger = logging.getLogger(__name__)


def authorized(message, settings):
    return bool(message.from_user and message.from_user.id in settings.admin_ids
                and message.chat.type == "private")


@router.message(Command("device_bindings"))
async def device_bindings(message):
    settings = get_settings()
    if not authorized(message, settings):
        return
    parts = (message.text or "").split()
    if len(parts) != 2 or not parts[1].isascii() or not parts[1].isdigit() or len(parts[1]) > 18:
        await message.answer("Использование: /device_bindings TELEGRAM_ID")
        return
    async with sessions() as db:
        user = await db.scalar(select(User).where(User.telegram_id == int(parts[1])))
        if not user:
            await message.answer("Клиент не найден.")
            return
        rows = list(await db.scalars(select(DeviceBinding).where(DeviceBinding.user_id == user.id)
                                    .order_by(DeviceBinding.created_at, DeviceBinding.id)))
    if not rows:
        await message.answer("У клиента нет зарегистрированных HWID-устройств.")
        return
    # Keep Telegram messages bounded. Subscription database IDs identify the
    # entitlement without disclosing parent VPN UUIDs, credentials or HWIDs.
    for offset in range(0, len(rows), 10):
        text = f"<b>Устройства клиента {user.telegram_id}</b>"
        for row in rows[offset:offset + 10]:
            subscription = row.paid_link_id or row.trial_link_id or "не определена"
            kind = "Пробная" if row.trial_link_id else "Подписка"
            text += (f"\n\n{kind}: <code>{escape(subscription)}</code>"
                     f"\nУстройство: <code>{row.id}</code>"
                     f"\nПлатформа: {escape(row.platform or 'не указана')}"
                     f"\nСтатус: {escape(row.status)}")
        text += "\n\nОтключить устройство: /device_revoke ID_устройства"
        await message.answer(text, parse_mode="HTML")


@router.message(Command("device_revoke"))
async def device_revoke(message):
    settings = get_settings()
    if not authorized(message, settings):
        return
    parts = (message.text or "").split()
    try:
        if len(parts) != 2 or len(parts[1]) > 36:
            raise ValueError("Invalid device ID")
        identity = str(UUID(parts[1]))
    except (ValueError, AttributeError):
        await message.answer("Использование: /device_revoke ID_устройства")
        return
    async with sessions.begin() as db:
        row = await db.get(DeviceBinding, identity)
        if not row:
            await message.answer("Устройство не найдено.")
            return
        await db.execute(update(User).where(User.id == row.user_id).values(is_active=User.is_active))
        await db.refresh(row)
        if row.status == "REVOKED":
            await message.answer("Устройство уже отключено, место освобождено.")
            return
        row.status = "REVOKING"
        row.bundle_encrypted = None
    try:
        await device_access.provision(sessions, settings, identity)
        async with sessions() as db:
            row = await db.get(DeviceBinding, identity)
            verified = bool(row and row.status == "REVOKED")
    except Exception as error:
        # Exception contents may contain node configuration; never send them.
        logger.warning("device_admin_revocation_pending type=%s", type(error).__name__)
        verified = False
    await message.answer(
        "✅ Устройство отключено на всех серверах. Место освобождено."
        if verified else
        "⏳ Отключение ещё проверяется. Место занято до подтверждения всех серверов. "
        "Фоновая проверка продолжится; повторная команда безопасна.")
