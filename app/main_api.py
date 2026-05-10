from __future__ import annotations

import asyncio
import logging
from typing import Any

from aiogram import Dispatcher
from fastapi import FastAPI
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from app.api.routes.health import router as health_router
from app.api.routes.pay import router as pay_router
from app.api.routes.webhooks import router as webhooks_router
from app.config import Settings, load_settings
from app.db.models import Subscription, SubscriptionStatus
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

    async def run_polling() -> None:
        await dp.start_polling(bot)

    app.state.polling_task = asyncio.create_task(run_polling())
    app.state.expire_task = asyncio.create_task(_expire_loop(sessionmaker, bot, settings))

    log.info("startup_complete")


@app.on_event("shutdown")
async def on_shutdown() -> None:
    for task_name in ("expire_task", "polling_task"):
        task = getattr(app.state, task_name, None)
        if task:
            task.cancel()
    for task_name in ("expire_task", "polling_task"):
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

