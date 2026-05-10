from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Subscription, SubscriptionStatus


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


async def get_subscription(session: AsyncSession, telegram_id: int) -> Subscription | None:
    res = await session.execute(select(Subscription).where(Subscription.telegram_id == telegram_id))
    return res.scalar_one_or_none()


async def ensure_subscription_paid(
    session: AsyncSession,
    telegram_id: int,
    access_days: int | None,
    lifetime_access: bool,
) -> Subscription:
    now = utcnow()
    res = await session.execute(
        select(Subscription).where(Subscription.telegram_id == telegram_id).with_for_update()
    )
    sub = res.scalar_one_or_none()
    delta = timedelta(days=access_days) if (access_days is not None) else None

    if sub is None:
        sub = Subscription(
            telegram_id=telegram_id,
            starts_at=now,
            expires_at=None if lifetime_access else (now + delta),
            status=SubscriptionStatus.active,
        )
        session.add(sub)
        await session.flush()
        return sub

    if lifetime_access:
        sub.starts_at = sub.starts_at or now
        sub.expires_at = None
        sub.status = SubscriptionStatus.active
        await session.flush()
        return sub

    if delta is None:
        raise RuntimeError("ACCESS_DAYS is required when LIFETIME_ACCESS=false")

    if sub.status == SubscriptionStatus.active and sub.expires_at > now:
        sub.expires_at = sub.expires_at + delta
    else:
        sub.starts_at = now
        sub.expires_at = now + delta

    sub.status = SubscriptionStatus.active
    await session.flush()
    return sub


async def expire_subscription(session: AsyncSession, telegram_id: int) -> Subscription | None:
    now = utcnow()
    res = await session.execute(
        select(Subscription).where(Subscription.telegram_id == telegram_id).with_for_update()
    )
    sub = res.scalar_one_or_none()
    if sub is None:
        return None
    if sub.status != SubscriptionStatus.active:
        return sub
    if sub.expires_at is None:
        return sub
    if sub.expires_at >= now:
        return sub
    sub.status = SubscriptionStatus.expired
    await session.flush()
    return sub

