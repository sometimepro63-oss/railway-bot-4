from __future__ import annotations

import asyncio
import logging
from datetime import timedelta
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, urlsplit

from aiogram import Dispatcher
from aiogram.exceptions import TelegramForbiddenError
from aiogram.types import FSInputFile
from fastapi import FastAPI
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from app.api.routes.health import router as health_router
from app.api.routes.pay import router as pay_router
from app.api.routes.webhooks import router as webhooks_router
from app.bot.keyboards.start_inline import start_inline_keyboard
from app.bot.messages import REMINDER_TEXT
from app.config import Settings, load_settings
from app.db.models import Subscription, SubscriptionStatus, User
from app.db.session import create_engine, create_sessionmaker
from app.logging_setup import setup_logging
from app.main_bot import create_bot, create_dispatcher
from app.services.subscriptions import utcnow
from app.services.telegram_access import kick_then_unban


log = logging.getLogger(__name__)

settings = load_settings()
setup_logging(settings.log_level)

app = FastAPI()
app.include_router(health_router)
app.include_router(pay_router)
app.include_router(webhooks_router)

def _get_reminder_photo() -> FSInputFile | None:
    file_path = Path(__file__).resolve()
    for base in (file_path.parent, *file_path.parents):
        assets_dir = base / "assets"
        if not assets_dir.is_dir():
            continue
        for name in ("reminder.jpg", "reminder.jpeg", "reminder.png", "reminder.webp"):
            path = assets_dir / name
            if path.exists():
                return FSInputFile(str(path))
    return None


async def _mark_reminder_sent(
    sessionmaker: async_sessionmaker[AsyncSession],
    user_id: int,
    now: Any,
) -> None:
    async with sessionmaker() as session:
        user = (
            await session.execute(
                select(User).where(User.telegram_id == user_id).with_for_update()
            )
        ).scalar_one_or_none()
        if user is None:
            return
        user.reminder_sent_at = now
        await session.commit()


async def _expire_once(
    sessionmaker: async_sessionmaker[AsyncSession],
    bot: Any,
    settings: Settings,
) -> None:
    now = utcnow()
    if settings.lifetime_access:
        return
    async with sessionmaker() as session:
        rows = (
            await session.execute(
                select(Subscription)
                .where(
                    Subscription.status == SubscriptionStatus.active,
                    Subscription.expires_at.is_not(None),
                    Subscription.expires_at < now,
                )
                .limit(500)
            )
        ).scalars().all()

    for sub in rows:
        async with sessionmaker() as session:
            async with session.begin():
                locked = (
                    await session.execute(
                        select(Subscription)
                        .where(Subscription.id == sub.id)
                        .with_for_update()
                    )
                ).scalar_one()
                if (
                    locked.status != SubscriptionStatus.active
                    or locked.expires_at is None
                    or locked.expires_at >= now
                ):
                    continue
                locked.status = SubscriptionStatus.expired
        try:
            await kick_then_unban(bot, settings.group_id, sub.telegram_id)
        except Exception:
            log.exception("expire_kick_failed telegram_id=%s", sub.telegram_id)
        try:
            await bot.send_message(
                sub.telegram_id,
                "Ваш доступ в закрытую группу закончился.\n\n"
                "Чтобы вернуться, оплатите продление доступа.",
            )
        except Exception:
            log.exception("expire_notify_failed telegram_id=%s", sub.telegram_id)


async def _expire_loop(
    sessionmaker: async_sessionmaker[AsyncSession],
    bot: Any,
    settings: Settings,
) -> None:
    while True:
        try:
            await _expire_once(sessionmaker, bot, settings)
        except Exception:
            log.exception("expire_loop_failed")
        await asyncio.sleep(3600)

async def _reminder_once(
    sessionmaker: async_sessionmaker[AsyncSession],
    bot: Any,
    settings: Settings,
) -> None:
    now = utcnow()
    cutoff = now - timedelta(minutes=settings.reminder_after_minutes)
    async with sessionmaker() as session:
        users = (
            await session.execute(
                select(User)
                .where(
                    User.last_start_at.is_not(None),
                    User.last_start_at < cutoff,
                    or_(User.reminder_sent_at.is_(None), User.reminder_sent_at < User.last_start_at),
                )
                .order_by(User.created_at.asc())
                .limit(200)
            )
        ).scalars().all()
    log.info("reminder_scan after_minutes=%s selected=%s", settings.reminder_after_minutes, len(users))

    photo = _get_reminder_photo()

    for u in users:
        telegram_id = u.telegram_id
        should_send = False
        async with sessionmaker() as session:
            async with session.begin():
                locked = (
                    await session.execute(
                        select(User)
                        .where(User.id == u.id)
                        .with_for_update()
                    )
                ).scalar_one()
                if locked.reminder_sent_at is not None:
                    continue

                sub = (
                    await session.execute(
                        select(Subscription).where(Subscription.telegram_id == telegram_id)
                    )
                ).scalar_one_or_none()

                has_active_access = bool(
                    sub
                    and sub.status == SubscriptionStatus.active
                    and (sub.expires_at is None or sub.expires_at > now)
                )
                if has_active_access:
                    should_send = False
                else:
                    locked.reminder_sent_at = now
                    should_send = True

        if not should_send:
            continue

        try:
            if photo:
                await bot.send_photo(telegram_id, photo=photo)
        except TelegramForbiddenError:
            log.warning("reminder_user_blocked telegram_id=%s", telegram_id)
            await _mark_reminder_sent(sessionmaker, telegram_id, now)
            continue
        except Exception:
            log.exception("reminder_photo_send_failed telegram_id=%s", telegram_id)

        try:
            await bot.send_message(
                telegram_id,
                REMINDER_TEXT,
                reply_markup=start_inline_keyboard(),
                disable_web_page_preview=True,
            )
        except TelegramForbiddenError:
            log.warning("reminder_user_blocked telegram_id=%s", telegram_id)
            await _mark_reminder_sent(sessionmaker, telegram_id, now)
            continue
        except Exception:
            log.exception("reminder_send_failed telegram_id=%s", telegram_id)


async def _reminder_loop(
    sessionmaker: async_sessionmaker[AsyncSession],
    bot: Any,
    settings: Settings,
) -> None:
    while True:
        try:
            await _reminder_once(sessionmaker, bot, settings)
        except Exception:
            log.exception("reminder_loop_failed")
        await asyncio.sleep(settings.reminder_loop_seconds)


@app.on_event("startup")
async def on_startup() -> None:
    engine: AsyncEngine = create_engine(settings.database_url)
    sessionmaker = create_sessionmaker(engine)
    bot = create_bot(settings)
    dp: Dispatcher = create_dispatcher(settings, sessionmaker)

    app.state.settings = settings
    app.state.engine = engine
    app.state.sessionmaker = sessionmaker
    app.state.bot = bot
    app.state.dp = dp

    base_order_id = next(
        (v for k, v in parse_qsl(urlsplit(settings.prodamus_payment_page_url).query, keep_blank_values=True) if k == "orderId"),
        "",
    )
    log.info("prodamus_payment_page_url_orderId=%s", base_order_id)
    log.info(
        "reminder_config after_minutes=%s loop_seconds=%s",
        settings.reminder_after_minutes,
        settings.reminder_loop_seconds,
    )

    async def run_polling() -> None:
        await dp.start_polling(bot)

    app.state.polling_task = asyncio.create_task(run_polling())
    app.state.expire_task = asyncio.create_task(_expire_loop(sessionmaker, bot, settings))
    app.state.reminder_task = asyncio.create_task(_reminder_loop(sessionmaker, bot, settings))

    log.info("startup_complete")


@app.on_event("shutdown")
async def on_shutdown() -> None:
    for task_name in ("expire_task", "reminder_task", "polling_task"):
        task = getattr(app.state, task_name, None)
        if task:
            task.cancel()
    for task_name in ("expire_task", "reminder_task", "polling_task"):
        task = getattr(app.state, task_name, None)
        if task:
            try:
                await task
            except asyncio.CancelledError:
                continue
            except Exception:
                log.exception("task_shutdown_error name=%s", task_name)

    bot = getattr(app.state, "bot", None)
    if bot:
        try:
            await bot.session.close()
        except Exception:
            log.exception("bot_session_close_failed")

    engine = getattr(app.state, "engine", None)
    if engine:
        try:
            await engine.dispose()
        except Exception:
            log.exception("engine_dispose_failed")

