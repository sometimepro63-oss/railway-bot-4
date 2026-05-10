from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.config import Settings
from app.db.models import Payment, PaymentStatus


router = APIRouter()


def _now_like(dt: datetime) -> datetime:
    tz = dt.tzinfo or timezone.utc
    return datetime.now(tz)


@router.get("/pay/{order_id}")
async def pay_redirect(order_id: str, request: Request):
    settings: Settings = request.app.state.settings
    sessionmaker: async_sessionmaker[AsyncSession] = request.app.state.sessionmaker
    async with sessionmaker() as session:
        async with session.begin():
            payment = (
                await session.execute(
                    select(Payment).where(Payment.order_id == order_id).with_for_update().limit(1)
                )
            ).scalar_one_or_none()

            if payment is None or not payment.payment_url:
                raise HTTPException(status_code=404, detail="not found")

            if payment.status == PaymentStatus.paid:
                return HTMLResponse("Платёж уже оплачен. Вернитесь в Telegram.")

            if payment.status in {PaymentStatus.cancelled, PaymentStatus.failed}:
                return HTMLResponse("Этот заказ больше не активен. Вернитесь в Telegram и создайте новый заказ.")

            if settings.payment_link_ttl_minutes > 0 and payment.created_at:
                if payment.created_at + timedelta(minutes=settings.payment_link_ttl_minutes) < _now_like(payment.created_at):
                    payment.status = PaymentStatus.cancelled
                    await session.flush()
                    return HTMLResponse(
                        "Ссылка на оплату устарела. Вернитесь в Telegram и нажмите ‘Купить доступ’ заново."
                    )

            url = payment.payment_url

    return RedirectResponse(url=url, status_code=302)

