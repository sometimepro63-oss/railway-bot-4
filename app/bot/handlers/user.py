from __future__ import annotations

import logging
from datetime import timezone
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from uuid import uuid4

from aiogram import Bot, Router
from aiogram.filters import Command
from aiogram.types import Message
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.bot.keyboards.start_inline import start_inline_keyboard
from app.config import Settings
from app.db.models import Payment, PaymentStatus, SubscriptionStatus, User
from app.services.prodamus import build_payment_url
from app.services.subscriptions import get_subscription, utcnow


log = logging.getLogger(__name__)
router = Router()

START_TEXT = """Присоединяйся к моему закрытому каналу с готовыми рационами питания 🥗✨

 🍱 Каждую неделю — новые полезные и сбалансированные меню на 3 дня, так же дополнительно публикуются рецепты закусок, перекусов и десертов

 💰Доступ: 1490 руб. (разовая оплата, материалы остаются у тебя навсегда)

 📋Что уже есть внутри:
 ▪️10+ готовых рационов из простых продуктов
 (подходят для снижения веса и поддержания формы)
 ▪️30+ видео-рецептов на каждый день
 ▪️Подробный КБЖУ для каждого приёма пищи
 ▪️Списки продуктов к каждому рациону
 ▪️Точные граммовки (на 1 и на 3 порции)
 ▪️Закуски на каждый день
 ▪️Десерты
 ▪️Удобная навигация по группе

 После оплаты бот пришлет временную ссылку, которая будет работать 3 минуты, за это время надо добавиться в закрытый канал и потом начать когда будет удобно 💫

 По любым вопросам пишите @irinasyic"""


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
async def start_cmd(message: Message, bot: Bot, session: AsyncSession, settings: Settings) -> None:
    await _upsert_user(session, message)
    if not message.from_user:
        return
    telegram_id = message.from_user.id
    short_url = await _create_payment(telegram_id, bot, session, settings)
    await message.answer(
        START_TEXT,
        reply_markup=start_inline_keyboard(short_url),
        disable_web_page_preview=True,
    )
    log.info("payment_link_sent telegram_id=%s order_id=%s", telegram_id, short_url.rsplit("/", 1)[-1])


async def _create_payment(telegram_id: int, bot: Bot, session: AsyncSession, settings: Settings) -> str:
    await session.execute(
        update(Payment)
        .where(
            Payment.telegram_id == telegram_id,
            Payment.status.in_([PaymentStatus.created, PaymentStatus.pending]),
        )
        .values(status=PaymentStatus.cancelled)
    )

    order_id = uuid4().hex
    payment = Payment(
        telegram_id=telegram_id,
        order_id=order_id,
        amount=settings.product_price,
        currency="rub",
        status=PaymentStatus.pending,
        created_at=utcnow(),
    )
    session.add(payment)
    await session.flush()

    me = await bot.get_me()
    back_url = f"https://t.me/{me.username}" if me.username else "https://t.me"

    data = {
        "do": "pay",
        "order_id": order_id,
        "order_num": order_id,
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
        "customer_extra": order_id,
        "customer_extra_telegram_id": str(telegram_id),
    }

    url = build_payment_url(settings.prodamus_payment_page_url, settings.prodamus_secret_key, data)
    payment.payment_url = url
    await session.flush()

    short_url = f"{settings.webhook_base_url}/pay/{order_id}"

    split = urlsplit(url)
    qs = parse_qsl(split.query, keep_blank_values=True)
    masked_qs = [(k, "***" if k == "signature" else v) for k, v in qs]
    masked_url = urlunsplit((split.scheme, split.netloc, split.path, urlencode(masked_qs), split.fragment))
    query_keys = sorted({k for k, _ in qs})
    base_order_id = next((v for k, v in qs if k == "orderId"), "")
    has_order_num = "order_num" in query_keys
    has_customer_extra = "customer_extra" in query_keys
    log.info(
        "payment_created base_orderId=%s internal_order_id=%s has_order_num=%s has_customer_extra=%s base_payment_page_url=%s query_keys=%s payment_url=%s",
        base_order_id,
        order_id,
        has_order_num,
        has_customer_extra,
        settings.prodamus_payment_page_url,
        ",".join(query_keys),
        masked_url,
    )
    return short_url


@router.message(Command("buy"))
async def buy_cmd(message: Message, bot: Bot, session: AsyncSession, settings: Settings) -> None:
    await _upsert_user(session, message)
    if not message.from_user:
        return
    short_url = await _create_payment(message.from_user.id, bot, session, settings)
    await message.answer(
        START_TEXT,
        reply_markup=start_inline_keyboard(short_url),
        disable_web_page_preview=True,
    )
    log.info("payment_link_sent telegram_id=%s order_id=%s", message.from_user.id, short_url.rsplit("/", 1)[-1])


@router.message(Command("profile"))
async def profile_cmd(message: Message, session: AsyncSession, settings: Settings) -> None:
    await _upsert_user(session, message)
    sub = await get_subscription(session, message.from_user.id)
    now = utcnow()
    if sub is None or sub.status != SubscriptionStatus.active:
        text = (
            "Ваш статус: неактивен\n\n"
            "У вас пока нет активного доступа.\n\n"
            "Нажмите “Купить доступ”, чтобы оплатить вход в группу."
        )
        await message.answer(text)
        return

    if sub.expires_at is None:
        await message.answer("Ваш доступ: навсегда")
        return

    if sub.expires_at <= now:
        text = (
            "Ваш статус: неактивен\n\n"
            "У вас пока нет активного доступа.\n\n"
            "Нажмите “Купить доступ”, чтобы оплатить вход в группу."
        )
        await message.answer(text)
        return

    expires = sub.expires_at.astimezone(timezone.utc).strftime("%d.%m.%Y %H:%M")
    await message.answer(f"Ваш статус: активен\nДоступ до: {expires}")


@router.message(Command("help"))
async def help_cmd(message: Message) -> None:
    await message.answer("Нажмите «Оплатить» в /start или используйте /buy для создания оплаты.")

