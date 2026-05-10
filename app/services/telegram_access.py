from __future__ import annotations

from datetime import datetime

from aiogram import Bot
from aiogram.enums import ChatMemberStatus
from aiogram.types import ChatInviteLink


async def is_member(bot: Bot, group_id: int, telegram_id: int) -> bool:
    member = await bot.get_chat_member(chat_id=group_id, user_id=telegram_id)
    return member.status in {
        ChatMemberStatus.MEMBER,
        ChatMemberStatus.ADMINISTRATOR,
        ChatMemberStatus.OWNER,
    }


async def create_one_time_invite(
    bot: Bot,
    group_id: int,
    expire_date: datetime,
) -> ChatInviteLink:
    return await bot.create_chat_invite_link(
        chat_id=group_id,
        member_limit=1,
        expire_date=expire_date,
    )


async def kick_then_unban(bot: Bot, group_id: int, telegram_id: int) -> None:
    await bot.ban_chat_member(chat_id=group_id, user_id=telegram_id)
    await bot.unban_chat_member(chat_id=group_id, user_id=telegram_id)

