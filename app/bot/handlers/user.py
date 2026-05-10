from __future__ import annotations

import logging
from datetime import timezone
from uuid import uuid4

from aiogram import Bot, F, Router
from aiogram.filters import Command
from aiogram.types import Message
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.bot.keyboards.start import start_keyboard
from app.config import Settings
from app.db.models import Payment, PaymentStatus, SubscriptionStatus, User
from app.services.prodamus import build_payment_url
from app.services.subscriptions import get_subscription, utcnow


log = logging.getLogger(__name__)
router = Router()


async def _upsert_user(session: AsyncSession, message: Message) -> None:
    tg = message.from_user
    if tg is None:
        return
    res = await session.execute(select(User).where(User.telegram_id == tg.id).with_for_update())
    user = res.scalar_one_or_none()
    if user is None:
        user = User(
            telegram_id=tg.id,
            username=tg.username,
            first_name=tg.first_name,
            last_name=tg.last_name,
        )
        session.add(user)
        await session.flush()
        return
    user.username = tg.username
    user.first_name = tg.first_name
    user.last_name = tg.last_name
    await session.flush()


@router.message(Command("start"))
async def start_cmd(message: Message, session: AsyncSession, settings: Settings) -> None:
    await _upsert_user(session, message)
    text = (
        "Привет! Здесь можно оплатить доступ в закрытую группу.\n\n"
        f"Стоимость: {settings.product_price} ₽\n"
        f"Срок доступа: {settings.access_days} дней\n\n"
        "После оплаты бот автоматически выдаст временную ссылку для входа."
    )
    await message.answer(text, reply_markup=start_keyboard())


@router.message(Command("buy"))
async def buy_cmd(message: Message, bot: Bot, session: AsyncSession, settings: Settings) -> None:
    await _upsert_user(session, message)

    order_id = uuid4().hex
    payment = Payment(
        telegram_id=message.from_user.id,
        order_id=order_id,
        amount=settings.product_price,
        currency="rub",
        status=PaymentStatus.pending,
    )
    session.add(payment)
    await session.flush()

    me = await bot.get_me()
    back_url = f"https://t.me/{me.username}" if me.username else "https://t.me"

    data = {
        "do": "pay",
        "order_id": order_id,
        "products": [
            {
                "name": settings.product_name,
                "price": settings.product_price,
                "quantity": 1,
            }
        ],
        "currency": "rub",
        "callbackType": "json",
        "urlSuccess": back_url,
        "urlReturn": back_url,
        "urlNotification": f"{settings.webhook_base_url}/webhooks/prodamus",
        "customer_extra": str(message.from_user.id),
    }

    url = build_payment_url(settings.prodamus_payment_page_url, settings.prodamus_secret_key, data)

    await message.answer(f"Ссылка для оплаты:\n{url}", disable_web_page_preview=True)
    log.info("payment_link_sent telegram_id=%s order_id=%s", message.from_user.id, order_id)


@router.message(F.text == "Купить доступ")
async def buy_btn(message: Message, bot: Bot, session: AsyncSession, settings: Settings) -> None:
    await buy_cmd(message, bot, session, settings)


@router.message(Command("profile"))
async def profile_cmd(message: Message, session: AsyncSession, settings: Settings) -> None:
    await _upsert_user(session, message)
    sub = await get_subscription(session, message.from_user.id)
    now = utcnow()
    if sub is None or sub.status != SubscriptionStatus.active or sub.expires_at <= now:
        text = (
            "Ваш статус: неактивен\n\n"
            "У вас пока нет активного доступа.\n\n"
            "Нажмите “Купить доступ”, чтобы оплатить вход в группу."
        )
        await message.answer(text, reply_markup=start_keyboard())
        return

    expires = sub.expires_at.astimezone(timezone.utc).strftime("%d.%m.%Y %H:%M")
    await message.answer(f"Ваш статус: активен\nДоступ до: {expires}")


@router.message(F.text == "Мой доступ")
async def profile_btn(message: Message, session: AsyncSession, settings: Settings) -> None:
    await profile_cmd(message, session, settings)


@router.message(Command("help"))
async def help_cmd(message: Message) -> None:
    await message.answer(
        "/buy — купить доступ\n"
        "/profile — проверить доступ\n"
        "/help — помощь"
    )


@router.message(F.text == "Помощь")
async def help_btn(message: Message) -> None:
    await help_cmd(message)

