from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.models import Payment, PaymentStatus


router = APIRouter()


@router.get("/pay/{order_id}")
async def pay_redirect(order_id: str, request: Request):
    sessionmaker: async_sessionmaker[AsyncSession] = request.app.state.sessionmaker
    async with sessionmaker() as session:
        payment = (
            await session.execute(select(Payment).where(Payment.order_id == order_id).limit(1))
        ).scalar_one_or_none()

    if payment is None or not payment.payment_url:
        raise HTTPException(status_code=404, detail="not found")

    if payment.status == PaymentStatus.paid:
        return HTMLResponse("Платёж уже оплачен. Вернитесь в Telegram.")

    return RedirectResponse(url=payment.payment_url, status_code=302)

