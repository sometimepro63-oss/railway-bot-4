from __future__ import annotations

import logging
from datetime import timezone, timedelta

from aiogram import Bot, Router
from aiogram.filters import Command
from aiogram.types import Message
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.db.models import Payment, PaymentStatus, Subscription, SubscriptionStatus, User
from app.services.subscriptions import ensure_subscription_paid, get_subscription, utcnow
from app.services.telegram_access import kick_then_unban


log = logging.getLogger(__name__)
router = Router()


def _is_admin(message: Message, settings: Settings) -> bool:
    return bool(message.from_user) and message.from_user.id in set(settings.admin_ids)


@router.message(Command("admin_stats"))
async def admin_stats(message: Message, session: AsyncSession, settings: Settings) -> None:
    if not _is_admin(message, settings):
        return

    users_count = (await session.execute(select(func.count()).select_from(User))).scalar_one()
    active_count = (
        await session.execute(
            select(func.count()).select_from(Subscription).where(Subscription.status == SubscriptionStatus.active)
        )
    ).scalar_one()

    rows = (
        await session.execute(
            select(Payment.status, func.count()).group_by(Payment.status).order_by(Payment.status)
        )
    ).all()

    parts = [f"Пользователей: {users_count}", f"Активных подписок: {active_count}", "Платежи:"]
    for status, cnt in rows:
        parts.append(f"- {status.value}: {cnt}")

    await message.answer("\n".join(parts))


@router.message(Command("admin_user"))
async def admin_user(message: Message, session: AsyncSession, settings: Settings) -> None:
    if not _is_admin(message, settings):
        return

    parts = (message.text or "").split()
    if len(parts) < 2:
        await message.answer("Использование: /admin_user <telegram_id>")
        return
    telegram_id = int(parts[1])

    user = (
        await session.execute(select(User).where(User.telegram_id == telegram_id))
    ).scalar_one_or_none()
    sub = await get_subscription(session, telegram_id)

    out = []
    if user is None:
        out.append("Пользователь не найден")
    else:
        out.append(f"telegram_id: {user.telegram_id}")
        out.append(f"username: {user.username or '-'}")
        out.append(f"name: {(user.first_name or '').strip()} {(user.last_name or '').strip()}".strip())
        out.append(f"created_at: {user.created_at.astimezone(timezone.utc).strftime('%d.%m.%Y %H:%M')}")
        out.append(
            "last_start_at: -"
            if user.last_start_at is None
            else f"last_start_at: {user.last_start_at.astimezone(timezone.utc).strftime('%d.%m.%Y %H:%M')}"
        )
        out.append(
            "reminder_sent_at: -"
            if user.reminder_sent_at is None
            else f"reminder_sent_at: {user.reminder_sent_at.astimezone(timezone.utc).strftime('%d.%m.%Y %H:%M')}"
        )

    if sub is None:
        out.append("Подписка: отсутствует")
    else:
        expires = sub.expires_at.astimezone(timezone.utc).strftime("%d.%m.%Y %H:%M")
        out.append(f"Подписка: {sub.status.value}")
        out.append(f"Доступ до: {expires}")

    await message.answer("\n".join(out))


@router.message(Command("admin_reset_reminder"))
async def admin_reset_reminder(message: Message, session: AsyncSession, settings: Settings) -> None:
    if not _is_admin(message, settings):
        return

    parts = (message.text or "").split()
    if len(parts) < 2:
        await message.answer("Использование: /admin_reset_reminder <telegram_id>")
        return
    telegram_id = int(parts[1])

    user = (
        await session.execute(select(User).where(User.telegram_id == telegram_id).with_for_update())
    ).scalar_one_or_none()
    if user is None:
        await message.answer("Пользователь не найден")
        return
    user.reminder_sent_at = None
    await session.flush()
    await message.answer("Готово. reminder_sent_at сброшен.")
    log.info("admin_reset_reminder telegram_id=%s", telegram_id)


@router.message(Command("admin_extend"))
async def admin_extend(message: Message, session: AsyncSession, settings: Settings) -> None:
    if not _is_admin(message, settings):
        return

    parts = (message.text or "").split()
    if len(parts) < 3:
        await message.answer("Использование: /admin_extend <telegram_id> <days>")
        return
    telegram_id = int(parts[1])
    days = int(parts[2])

    sub = await ensure_subscription_paid(session, telegram_id, days)
    expires = sub.expires_at.astimezone(timezone.utc).strftime("%d.%m.%Y %H:%M")
    await message.answer(f"Готово. Доступ до: {expires}")
    log.info("admin_extend telegram_id=%s days=%s", telegram_id, days)


@router.message(Command("admin_revoke"))
async def admin_revoke(message: Message, bot: Bot, session: AsyncSession, settings: Settings) -> None:
    if not _is_admin(message, settings):
        return

    parts = (message.text or "").split()
    if len(parts) < 2:
        await message.answer("Использование: /admin_revoke <telegram_id>")
        return
    telegram_id = int(parts[1])

    res = await session.execute(
        select(Subscription).where(Subscription.telegram_id == telegram_id).with_for_update()
    )
    sub = res.scalar_one_or_none()
    if sub is None:
        await message.answer("Подписка не найдена")
        return

    sub.status = SubscriptionStatus.cancelled
    sub.expires_at = min(sub.expires_at, utcnow())
    await session.flush()

    try:
        await kick_then_unban(bot, settings.group_id, telegram_id)
    except Exception:
        log.exception("admin_revoke_kick_failed telegram_id=%s", telegram_id)

    await message.answer("Готово. Доступ отозван.")
    log.info("admin_revoke telegram_id=%s", telegram_id)

