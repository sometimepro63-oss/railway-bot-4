from __future__ import annotations

import asyncio
import logging
from datetime import timezone, timedelta
from pathlib import Path

from aiogram import Bot, Router
from aiogram.exceptions import TelegramForbiddenError
from aiogram.filters import Command
from aiogram.types import FSInputFile, Message
from sqlalchemy import and_, func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.db.models import Payment, PaymentStatus, Subscription, SubscriptionStatus, User
from app.services.subscriptions import ensure_subscription_paid, get_subscription, utcnow
from app.services.telegram_access import kick_then_unban
from app.bot.keyboards.start_inline import start_inline_keyboard
from app.bot.messages import BROADCAST_TEXT


log = logging.getLogger(__name__)
router = Router()

_BROADCAST_CAPTION_MAX = 1024
_BROADCAST_BATCH_LIMIT = 200
_BROADCAST_SLEEP_SECONDS = 0.05


def _is_admin(message: Message, settings: Settings) -> bool:
    return bool(message.from_user) and message.from_user.id in set(settings.admin_ids)

def _get_broadcast_photo() -> FSInputFile | None:
    file_path = Path(__file__).resolve()
    for base in (file_path.parent, *file_path.parents):
        assets_dir = base / "assets"
        if not assets_dir.is_dir():
            continue
        for name in ("broadcast.jpg", "broadcast.jpeg", "broadcast.png", "broadcast.webp"):
            path = assets_dir / name
            if path.exists():
                return FSInputFile(str(path))
    return None

def _parse_key(message: Message) -> str | None:
    parts = (message.text or "").split()
    if len(parts) < 2:
        return None
    key = parts[1].strip()
    return key or None

def _has_active_access(sub: Subscription | None, now: object) -> bool:
    return bool(
        sub
        and sub.status == SubscriptionStatus.active
        and (sub.expires_at is None or sub.expires_at > now)
    )


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


@router.message(Command("admin_broadcast_test"))
async def admin_broadcast_test(message: Message, bot: Bot, settings: Settings) -> None:
    if not _is_admin(message, settings):
        return
    if not message.from_user:
        return
    key = _parse_key(message)
    if not key:
        await message.answer("Использование: /admin_broadcast_test <key>")
        return

    photo = _get_broadcast_photo()
    telegram_id = message.from_user.id

    try:
        if photo and len(BROADCAST_TEXT) <= _BROADCAST_CAPTION_MAX:
            await bot.send_photo(
                telegram_id,
                photo=photo,
                caption=BROADCAST_TEXT,
                reply_markup=start_inline_keyboard(),
            )
        else:
            if photo:
                await bot.send_photo(telegram_id, photo=photo)
            await bot.send_message(
                telegram_id,
                BROADCAST_TEXT,
                reply_markup=start_inline_keyboard(),
                disable_web_page_preview=True,
            )
    except Exception:
        log.exception("admin_broadcast_test_failed telegram_id=%s key=%s", telegram_id, key)
        await message.answer("Не получилось отправить тест. Посмотрите логи.")
        return

    await message.answer(f"Тест отправлен. key={key}")


@router.message(Command("admin_broadcast_status"))
async def admin_broadcast_status(message: Message, session: AsyncSession, settings: Settings) -> None:
    if not _is_admin(message, settings):
        return
    key = _parse_key(message)
    if not key:
        await message.answer("Использование: /admin_broadcast_status <key>")
        return

    now = utcnow()
    active_sub = and_(
        Subscription.status == SubscriptionStatus.active,
        or_(Subscription.expires_at.is_(None), Subscription.expires_at > now),
    )
    res = await session.execute(
        select(func.count())
        .select_from(User)
        .outerjoin(Subscription, Subscription.telegram_id == User.telegram_id)
        .where(
            # Требование из бизнеса: рассылать тем, кто запускал бота, но не покупал доступ.
            # "Запускал" = есть last_start_at.
            # "Не покупал" = ни разу не было подписки (таблица subscriptions).
            User.last_start_at.is_not(None),
            Subscription.id.is_(None),
            or_(User.broadcast_key.is_(None), User.broadcast_key != key, User.broadcast_sent_at.is_(None)),
        )
    )
    cnt = res.scalar_one()
    await message.answer(f"Готово. Получателей (запускали бота, но не покупали доступ) для key={key}: {cnt}")


@router.message(Command("admin_broadcast_run"))
async def admin_broadcast_run(message: Message, bot: Bot, session: AsyncSession, settings: Settings) -> None:
    if not _is_admin(message, settings):
        return
    key = _parse_key(message)
    if not key:
        await message.answer("Использование: /admin_broadcast_run <key>")
        return

    await message.answer(f"Запускаю рассылку. key={key}")
    log.info("admin_broadcast_run_started key=%s", key)

    now = utcnow()
    active_sub = and_(
        Subscription.status == SubscriptionStatus.active,
        or_(Subscription.expires_at.is_(None), Subscription.expires_at > now),
    )
    photo = _get_broadcast_photo()
    sent = 0
    skipped = 0
    blocked = 0

    while True:
        rows = (
            await session.execute(
                select(User.id, User.telegram_id)
                .select_from(User)
                .outerjoin(Subscription, Subscription.telegram_id == User.telegram_id)
                .where(
                    # Рассылка только тем, кто запускал /start (last_start_at),
                    # и ни разу не покупал доступ (нет записи в subscriptions).
                    User.last_start_at.is_not(None),
                    Subscription.id.is_(None),
                    or_(User.broadcast_key.is_(None), User.broadcast_key != key, User.broadcast_sent_at.is_(None)),
                )
                .order_by(User.created_at.asc())
                .limit(_BROADCAST_BATCH_LIMIT)
            )
        ).all()
        if not rows:
            break

        for user_id, telegram_id in rows:
            should_send = False
            locked = (
                await session.execute(select(User).where(User.id == user_id).with_for_update())
            ).scalar_one()
            if locked.broadcast_key == key and locked.broadcast_sent_at is not None:
                skipped += 1
                await session.commit()
                continue

            sub = (
                await session.execute(select(Subscription).where(Subscription.telegram_id == telegram_id))
            ).scalar_one_or_none()
            if _has_active_access(sub, now):
                skipped += 1
                await session.commit()
                continue

            locked.broadcast_key = key
            locked.broadcast_sent_at = None
            await session.commit()
            should_send = True

            if not should_send:
                continue

            try:
                if photo and len(BROADCAST_TEXT) <= _BROADCAST_CAPTION_MAX:
                    await bot.send_photo(
                        telegram_id,
                        photo=photo,
                        caption=BROADCAST_TEXT,
                        reply_markup=start_inline_keyboard(),
                    )
                else:
                    if photo:
                        await bot.send_photo(telegram_id, photo=photo)
                    await bot.send_message(
                        telegram_id,
                        BROADCAST_TEXT,
                        reply_markup=start_inline_keyboard(),
                        disable_web_page_preview=True,
                    )
                sent += 1
                sent_at = utcnow()
                locked2 = (
                    await session.execute(select(User).where(User.id == user_id).with_for_update())
                ).scalar_one()
                if locked2.broadcast_key == key and locked2.broadcast_sent_at is None:
                    locked2.broadcast_sent_at = sent_at
                await session.commit()
            except TelegramForbiddenError:
                blocked += 1
                log.warning("broadcast_user_blocked telegram_id=%s key=%s", telegram_id, key)
                blocked_at = utcnow()
                locked2 = (
                    await session.execute(select(User).where(User.id == user_id).with_for_update())
                ).scalar_one()
                if locked2.broadcast_key == key and locked2.broadcast_sent_at is None:
                    locked2.broadcast_sent_at = blocked_at
                await session.commit()
            except Exception:
                log.exception("broadcast_send_failed telegram_id=%s key=%s", telegram_id, key)

            await asyncio.sleep(_BROADCAST_SLEEP_SECONDS)

    if sent == 0 and skipped == 0 and blocked == 0:
        await message.answer(f"Некому отправлять. key={key}")
        return

    await message.answer(
        f"Готово. key={key}\n"
        f"Отправлено: {sent}\n"
        f"Пропущено: {skipped}\n"
        f"Заблокировали бота: {blocked}"
    )
    log.info(
        "admin_broadcast_run_finished key=%s sent=%s skipped=%s blocked=%s",
        key,
        sent,
        skipped,
        blocked,
    )


@router.message(Command("admin_reset_broadcast"))
async def admin_reset_broadcast(message: Message, session: AsyncSession, settings: Settings) -> None:
    if not _is_admin(message, settings):
        return
    parts = (message.text or "").split()
    if len(parts) < 2:
        await message.answer("Использование: /admin_reset_broadcast <telegram_id>")
        return
    telegram_id = int(parts[1])

    user = (
        await session.execute(select(User).where(User.telegram_id == telegram_id).with_for_update())
    ).scalar_one_or_none()
    if user is None:
        await message.answer("Пользователь не найден")
        return
    user.broadcast_key = None
    user.broadcast_sent_at = None
    await session.flush()
    await message.answer("Готово. broadcast_* сброшены.")
    log.info("admin_reset_broadcast telegram_id=%s", telegram_id)


@router.message(Command("admin_reset_broadcast_key"))
async def admin_reset_broadcast_key(message: Message, session: AsyncSession, settings: Settings) -> None:
    if not _is_admin(message, settings):
        return
    key = _parse_key(message)
    if not key:
        await message.answer("Использование: /admin_reset_broadcast_key <key>")
        return

    res = await session.execute(
        update(User)
        .where(User.broadcast_key == key)
        .values(broadcast_key=None, broadcast_sent_at=None)
    )
    await session.flush()
    await message.answer(f"Готово. Сброшено пользователей для key={key}: {res.rowcount or 0}")
    log.info("admin_reset_broadcast_key key=%s rows=%s", key, res.rowcount)
