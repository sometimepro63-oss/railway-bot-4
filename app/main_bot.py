from __future__ import annotations

import logging

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.bot.handlers.admin import router as admin_router
from app.bot.handlers.user import router as user_router
from app.bot.middlewares.db import DBSessionMiddleware
from app.bot.middlewares.settings import SettingsMiddleware
from app.config import Settings


log = logging.getLogger(__name__)


def create_bot(settings: Settings) -> Bot:
    return Bot(
        token=settings.bot_token,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )


def create_dispatcher(
    settings: Settings,
    sessionmaker: async_sessionmaker[AsyncSession],
) -> Dispatcher:
    dp = Dispatcher()

    dp.update.middleware(SettingsMiddleware(settings))
    dp.update.middleware(DBSessionMiddleware(sessionmaker))

    dp.include_router(user_router)
    dp.include_router(admin_router)

    return dp

