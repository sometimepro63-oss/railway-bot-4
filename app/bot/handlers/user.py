from __future__ import annotations

import logging
from datetime import timedelta, timezone
from uuid import uuid4

from aiogram import Bot, F, Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, Message
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.bot.keyboards.payment import payment_keyboard
from app.bot.keyboards.start import start_keyboard
from app.config import Settings
from app.db.models import InviteLink, Payment, PaymentStatus, SubscriptionStatus, User
from app.services.prodamus import build_payment_url
from app.services.subscriptions import get_subscription, utcnow
from app.services.telegram_access import create_one_time_invite, is_member


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
    access_str = "навсегда" if settings.lifetime_access else f"{settings.access_days} дней"
    text = (
        "Привет! Здесь можно оплатить доступ в закрытую группу.\n\n"
        f"Стоимость: {settings.product_price} ₽\n"
        f"Срок доступа: {access_str}\n\n"
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
    payment.payment_url = url
    await session.flush()

    short_url = f"{settings.webhook_base_url}/pay/{order_id}"

    await message.answer(
        "Для оплаты доступа нажмите кнопку ниже 👇\n\n"
        "После успешной оплаты бот автоматически отправит ссылку для входа.",
        reply_markup=payment_keyboard(short_url),
    )
    log.info("payment_link_sent telegram_id=%s order_id=%s", message.from_user.id, order_id)


@router.message(F.text == "Купить доступ")
async def buy_btn(message: Message, bot: Bot, session: AsyncSession, settings: Settings) -> None:
    await buy_cmd(message, bot, session, settings)


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
        await message.answer(text, reply_markup=start_keyboard())
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


async def _get_existing_invite(session: AsyncSession, telegram_id: int) -> InviteLink | None:
    now = utcnow()
    res = await session.execute(
        select(InviteLink)
        .where(
            InviteLink.telegram_id == telegram_id,
            InviteLink.used.is_(False),
            InviteLink.expire_at > now,
        )
        .order_by(InviteLink.created_at.desc())
        .limit(1)
    )
    return res.scalar_one_or_none()


@router.callback_query(F.data == "check_payment")
async def check_payment(cb: CallbackQuery, bot: Bot, session: AsyncSession, settings: Settings) -> None:
    if not cb.from_user:
        await cb.answer()
        return

    telegram_id = cb.from_user.id
    await cb.answer()

    now = utcnow()
    sub = await get_subscription(session, telegram_id)
    active = (
        sub is not None
        and sub.status == SubscriptionStatus.active
        and (sub.expires_at is None or sub.expires_at > now)
    )

    if not active:
        paid = (
            await session.execute(
                select(Payment)
                .where(Payment.telegram_id == telegram_id, Payment.status == PaymentStatus.paid)
                .order_by(desc(Payment.paid_at))
                .limit(1)
            )
        ).scalar_one_or_none()
        if paid is None:
            await cb.message.answer(
                "Платёж пока не найден. Обычно подтверждение приходит в течение 1–2 минут. "
                "Если вы только что оплатили, попробуйте ещё раз чуть позже."
            )
            return
        await cb.message.answer(
            "Оплата подтверждена, но доступ ещё активируется. "
            "Обычно это занимает до 1–2 минут. Попробуйте ещё раз чуть позже."
        )
        return

    try:
        in_group = await is_member(bot, settings.group_id, telegram_id)
    except Exception:
        log.exception("tg_membership_check_failed telegram_id=%s", telegram_id)
        in_group = False

    if in_group:
        if sub and sub.expires_at is None:
            await cb.message.answer("Ваш доступ уже активен.\n\nВаш доступ: навсегда")
        else:
            await cb.message.answer("Ваш доступ уже активен.")
        return

    existing = await _get_existing_invite(session, telegram_id)
    invite_url = existing.invite_link if existing else None
    if not invite_url:
        expire_at = now + timedelta(minutes=settings.invite_link_expire_minutes)
        invite = await create_one_time_invite(bot, settings.group_id, expire_at)
        invite_url = invite.invite_link
        session.add(
            InviteLink(
                telegram_id=telegram_id,
                invite_link=invite_url,
                expire_at=expire_at,
                used=False,
            )
        )
        await session.flush()

    await cb.message.answer(
        "Ваша ссылка для входа в закрытую группу:\n"
        f"{invite_url}\n\n"
        f"Ссылка действует {settings.invite_link_expire_minutes} минут и только для одного вступления.",
        disable_web_page_preview=True,
    )

