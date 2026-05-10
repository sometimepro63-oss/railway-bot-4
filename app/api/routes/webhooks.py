from __future__ import annotations

import json
import logging
from datetime import timedelta

from fastapi import APIRouter, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.config import Settings
from app.db.models import InviteLink, Payment, PaymentStatus
from app.services.prodamus import extract_webhook, parse_bracketed_params, verify_signature
from app.services.subscriptions import ensure_subscription_paid, get_subscription, utcnow
from app.services.telegram_access import create_one_time_invite, is_member


log = logging.getLogger(__name__)
router = APIRouter()


async def _read_payload(request: Request) -> dict:
    body = await request.body()
    content_type = (request.headers.get("content-type") or "").lower()

    if "application/json" in content_type:
        if not body:
            return {}
        data = json.loads(body.decode("utf-8"))
        if not isinstance(data, dict):
            raise HTTPException(status_code=400, detail="invalid json")
        return data

    form = await request.form()
    flat: dict[str, str] = {}
    for k, v in form.multi_items():
        if k not in flat:
            flat[k] = str(v)
    if any("[" in k for k in flat.keys()):
        try:
            return parse_bracketed_params(flat)
        except Exception:
            raise HTTPException(status_code=400, detail="invalid form data")
    return dict(flat)


async def _get_existing_invite(
    session: AsyncSession,
    telegram_id: int,
) -> InviteLink | None:
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


@router.post("/webhooks/prodamus")
async def prodamus_webhook(request: Request) -> dict:
    settings: Settings = request.app.state.settings
    sessionmaker: async_sessionmaker[AsyncSession] = request.app.state.sessionmaker
    bot = request.app.state.bot

    payload = await _read_payload(request)
    sign = request.headers.get("Sign") or request.headers.get("sign") or request.headers.get("SIGN")

    if not verify_signature(payload, settings.prodamus_secret_key, sign):
        log.warning("prodamus_signature_invalid ip=%s", request.client.host if request.client else None)
        raise HTTPException(status_code=403, detail="invalid signature")

    webhook = extract_webhook(payload)

    payment_telegram_id: int | None = None
    expires_at_str: str | None = None
    already_paid = False

    async with sessionmaker() as session:
        async with session.begin():
            res = await session.execute(
                select(Payment).where(Payment.order_id == webhook.order_id).with_for_update()
            )
            payment = res.scalar_one_or_none()
            if payment is None:
                raise HTTPException(status_code=400, detail="unknown order_id")

            payment.raw_payload = webhook.raw
            if webhook.prodamus_payment_id:
                payment.prodamus_payment_id = webhook.prodamus_payment_id

            payment_telegram_id = payment.telegram_id

            if payment.amount != webhook.amount:
                log.warning(
                    "prodamus_amount_mismatch order_id=%s expected=%s got=%s",
                    webhook.order_id,
                    payment.amount,
                    webhook.amount,
                )
                raise HTTPException(status_code=400, detail="amount mismatch")

            if webhook.currency and payment.currency and webhook.currency.lower() != payment.currency.lower():
                log.warning(
                    "prodamus_currency_mismatch order_id=%s expected=%s got=%s",
                    webhook.order_id,
                    payment.currency,
                    webhook.currency,
                )
                raise HTTPException(status_code=400, detail="currency mismatch")

            if payment.status == PaymentStatus.paid:
                already_paid = True
            else:
                payment.status = PaymentStatus.paid
                payment.paid_at = utcnow()
                sub = await ensure_subscription_paid(session, payment.telegram_id, settings.access_days)
                expires_at_str = sub.expires_at.strftime("%d.%m.%Y %H:%M")
            if already_paid:
                sub = await get_subscription(session, payment.telegram_id)
                if sub is not None:
                    expires_at_str = sub.expires_at.strftime("%d.%m.%Y %H:%M")

    if payment_telegram_id is None:
        raise HTTPException(status_code=500, detail="internal error")

    try:
        in_group = await is_member(bot, settings.group_id, payment_telegram_id)
    except Exception:
        log.exception("tg_membership_check_failed telegram_id=%s", payment_telegram_id)
        in_group = False

    if in_group:
        if not already_paid:
            try:
                await bot.send_message(
                    payment_telegram_id,
                    f"Оплата прошла успешно ✅\n\nВаш доступ активен до: {expires_at_str}",
                )
            except Exception:
                log.exception("tg_send_extend_failed telegram_id=%s", payment_telegram_id)
        return {"ok": True}

    async with sessionmaker() as session:
        async with session.begin():
            existing = await _get_existing_invite(session, payment_telegram_id)
            invite_url = existing.invite_link if existing else None

            if already_paid and invite_url:
                return {"ok": True}

            if not invite_url:
                expire_at = utcnow() + timedelta(minutes=settings.invite_link_expire_minutes)
                try:
                    invite = await create_one_time_invite(bot, settings.group_id, expire_at)
                    invite_url = invite.invite_link
                except Exception:
                    log.exception("tg_create_invite_failed telegram_id=%s", payment_telegram_id)
                    raise HTTPException(status_code=502, detail="telegram invite failed")
                session.add(
                    InviteLink(
                        telegram_id=payment_telegram_id,
                        invite_link=invite_url,
                        expire_at=expire_at,
                        used=False,
                    )
                )

    try:
        await bot.send_message(
            payment_telegram_id,
            "Оплата прошла успешно ✅\n\n"
            "Ваша ссылка для входа в закрытую группу:\n"
            f"{invite_url}\n\n"
            f"Ссылка действует {settings.invite_link_expire_minutes} минут и только для одного вступления.",
            disable_web_page_preview=True,
        )
    except Exception:
        log.exception("tg_send_invite_failed telegram_id=%s", payment_telegram_id)
        raise HTTPException(status_code=502, detail="telegram send failed")

    return {"ok": True}

