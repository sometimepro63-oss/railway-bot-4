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
from app.services.subscriptions import ensure_subscription_paid, utcnow
from app.services.telegram_access import create_one_time_invite


log = logging.getLogger(__name__)
router = APIRouter()


def _to_str(value: object) -> str:
    if value is None:
        return ""
    return str(value)


def _parse_bool(value: object) -> bool:
    v = _to_str(value).strip().lower()
    return v in {"1", "true", "yes", "y", "on"}


def _detect_paid(payload: dict) -> tuple[bool | None, str]:
    status = _to_str(payload.get("status") or payload.get("payment_status")).strip().lower()
    if status:
        return (status in {"paid", "success"}, status)
    if "paid" in payload:
        return (_parse_bool(payload.get("paid")), _to_str(payload.get("paid")))
    return (None, "")


def _extract_internal_order_id_from_email(value: object) -> str:
    email = _to_str(value).strip().lower()
    if not email:
        return ""
    prefix = "order_"
    suffix = "@bot.local"
    if email.startswith(prefix) and email.endswith(suffix) and len(email) > len(prefix) + len(suffix):
        return email[len(prefix) : -len(suffix)]
    return ""


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


@router.get("/webhooks/prodamus")
async def prodamus_webhook_alive() -> dict:
    return {
        "ok": True,
        "message": "Prodamus webhook endpoint is alive. Use POST for payment notifications.",
    }


@router.post("/webhooks/prodamus")
async def prodamus_webhook(request: Request) -> dict:
    settings: Settings = request.app.state.settings
    sessionmaker: async_sessionmaker[AsyncSession] = request.app.state.sessionmaker
    bot = request.app.state.bot

    payload = await _read_payload(request)
    sign = request.headers.get("Sign") or request.headers.get("sign") or request.headers.get("SIGN")
    query_internal_order_id = _to_str(request.query_params.get("internal_order_id"))
    query_telegram_id = _to_str(request.query_params.get("telegram_id"))

    payload_keys = sorted(payload.keys()) if isinstance(payload, dict) else []
    payload_order_num = _to_str(payload.get("order_num") if isinstance(payload, dict) else None)
    payload_customer_extra = _to_str(payload.get("customer_extra") if isinstance(payload, dict) else None)
    payload_customer_email = _to_str(payload.get("customer_email") if isinstance(payload, dict) else None)
    internal_order_id_from_email = _extract_internal_order_id_from_email(payload_customer_email)
    paid_detected, detected_status = _detect_paid(payload if isinstance(payload, dict) else {})
    payload_order_id = _to_str(
        payload.get("order_id") or payload.get("orderid") or payload.get("order") if isinstance(payload, dict) else None
    )
    internal_order_id_guess = payload_order_num or payload_customer_extra or payload_order_id

    log.info(
        "prodamus_webhook_received method=%s payload_keys=%s query_internal_order_id=%s query_telegram_id=%s payload_order_id=%s payload_order_num=%s payload_customer_extra=%s payload_customer_email=%s internal_order_id_from_email=%s internal_order_id_guess=%s detected_status=%s paid=%s sign_present=%s",
        request.method,
        ",".join(payload_keys),
        query_internal_order_id,
        query_telegram_id,
        payload_order_id,
        payload_order_num,
        payload_customer_extra,
        payload_customer_email,
        internal_order_id_from_email,
        internal_order_id_guess,
        detected_status,
        paid_detected,
        bool(sign),
    )

    signature_valid = verify_signature(payload, settings.prodamus_secret_key, sign)
    log.info(
        "prodamus_webhook_signature_checked query_internal_order_id=%s query_telegram_id=%s internal_order_id_guess=%s signature_valid=%s",
        query_internal_order_id,
        query_telegram_id,
        internal_order_id_guess,
        signature_valid,
    )
    if not signature_valid:
        log.warning("prodamus_signature_invalid ip=%s", request.client.host if request.client else None)
        raise HTTPException(status_code=400, detail="invalid signature")

    if paid_detected is not True:
        log.info(
            "prodamus_webhook_not_paid query_internal_order_id=%s payload_order_id=%s payload_order_num=%s payload_customer_extra=%s payload_customer_email=%s internal_order_id_from_email=%s detected_status=%s paid=%s",
            query_internal_order_id,
            payload_order_id,
            payload_order_num,
            payload_customer_extra,
            payload_customer_email,
            internal_order_id_from_email,
            detected_status,
            paid_detected,
        )
        return {"ok": True}

    webhook = extract_webhook(payload)
    internal_order_id = (
        query_internal_order_id
        or internal_order_id_from_email
        or payload_order_num
        or payload_customer_extra
        or webhook.order_id
    )

    payment_telegram_id: int | None = None
    telegram_id_from_db: int | None = None
    already_paid = False
    payment_found = False

    async with sessionmaker() as session:
        async with session.begin():
            res = await session.execute(
                select(Payment).where(Payment.order_id == internal_order_id).with_for_update()
            )
            payment = res.scalar_one_or_none()
            if payment is None:
                recent = (
                    await session.execute(
                        select(Payment.order_id).order_by(Payment.created_at.desc()).limit(5)
                    )
                ).scalars().all()
                log.warning(
                    "prodamus_payment_not_found query_internal_order_id=%s query_telegram_id=%s payload_order_id=%s payload_order_num=%s payload_customer_extra=%s payload_customer_email=%s internal_order_id_from_email=%s internal_order_id=%s recent_order_ids=%s",
                    query_internal_order_id,
                    query_telegram_id,
                    payload_order_id,
                    payload_order_num,
                    payload_customer_extra,
                    payload_customer_email,
                    internal_order_id_from_email,
                    internal_order_id,
                    ",".join(recent),
                )
                raise HTTPException(status_code=400, detail="unknown order_id")
            payment_found = True

            payment.raw_payload = webhook.raw
            if webhook.prodamus_payment_id:
                payment.prodamus_payment_id = webhook.prodamus_payment_id

            payment_telegram_id = payment.telegram_id
            telegram_id_from_db = payment.telegram_id

            if webhook.currency and payment.currency and webhook.currency.lower() != payment.currency.lower():
                log.warning(
                    "prodamus_currency_mismatch internal_order_id=%s expected=%s got=%s",
                    internal_order_id,
                    payment.currency,
                    webhook.currency,
                )
                raise HTTPException(status_code=400, detail="currency mismatch")

            if payment.status == PaymentStatus.paid:
                already_paid = True
            else:
                payment.status = PaymentStatus.paid
                payment.paid_at = utcnow()
                await ensure_subscription_paid(
                    session,
                    payment.telegram_id,
                    settings.access_days,
                    settings.lifetime_access,
                )

    if payment_telegram_id is None:
        raise HTTPException(status_code=500, detail="internal error")

    log.info(
        "prodamus_webhook_payment_loaded internal_order_id=%s query_internal_order_id=%s query_telegram_id=%s payload_order_id=%s payload_order_num=%s payload_customer_extra=%s signature_valid=%s payment_found=%s telegram_id_from_db=%s already_paid=%s",
        internal_order_id,
        query_internal_order_id,
        query_telegram_id,
        payload_order_id,
        payload_order_num,
        payload_customer_extra,
        signature_valid,
        payment_found,
        telegram_id_from_db,
        already_paid,
    )

    async with sessionmaker() as session:
        async with session.begin():
            existing = await _get_existing_invite(session, payment_telegram_id)
            invite_url = existing.invite_link if existing else None

            if already_paid and invite_url:
                log.info(
                    "prodamus_invite_existing internal_order_id=%s telegram_id=%s",
                    internal_order_id,
                    payment_telegram_id,
                )
                return {"ok": True}

            invite_created = False
            if not invite_url:
                expire_at = utcnow() + timedelta(minutes=settings.invite_link_expire_minutes)
                try:
                    invite = await create_one_time_invite(bot, settings.group_id, expire_at)
                    invite_url = invite.invite_link
                    invite_created = True
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
            "Вот ссылка для входа в закрытый канал:\n"
            f"{invite_url}\n\n"
            "Ссылка действует 3 минуты.",
            disable_web_page_preview=True,
        )
        log.info(
            "prodamus_message_sent internal_order_id=%s telegram_id=%s invite_created=%s",
            internal_order_id,
            payment_telegram_id,
            invite_created,
        )
    except Exception:
        log.exception("tg_send_invite_failed telegram_id=%s", payment_telegram_id)
        log.info(
            "prodamus_message_failed internal_order_id=%s telegram_id=%s invite_created=%s",
            internal_order_id,
            payment_telegram_id,
            invite_created,
        )
        raise HTTPException(status_code=502, detail="telegram send failed")

    return {"ok": True}

