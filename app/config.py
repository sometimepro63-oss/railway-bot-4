from __future__ import annotations

import os
from dataclasses import dataclass

from dotenv import load_dotenv


def _parse_int_list(value: str | None) -> list[int]:
    if not value:
        return []
    parts = []
    for raw in value.replace(";", ",").split(","):
        s = raw.strip()
        if not s:
            continue
        parts.append(int(s))
    return parts


def _normalize_db_url(url: str) -> str:
    url = url.strip()
    if url.startswith("postgresql://"):
        url = "postgresql+asyncpg://" + url[len("postgresql://") :]
    return url


def _get_database_url() -> str:
    candidates = (
        os.getenv("DATABASE_URL"),
        os.getenv("DATABASE_PUBLIC_URL"),
        os.getenv("POSTGRES_URL"),
        os.getenv("POSTGRES_PUBLIC_URL"),
    )
    url = next((v.strip() for v in candidates if v and v.strip()), "")
    if not url:
        keys = sorted(
            {
                k
                for k in os.environ.keys()
                if ("DATABASE" in k) or ("POSTGRES" in k) or (k.startswith("PG"))
            }
        )
        raise RuntimeError(f"Database URL is missing. Available database env keys: {', '.join(keys)}")
    return _normalize_db_url(url)


@dataclass(frozen=True, slots=True)
class Settings:
    bot_token: str
    prodamus_secret_key: str
    prodamus_payment_page_url: str
    webhook_base_url: str

    group_id: int
    admin_ids: list[int]

    database_url: str

    product_name: str
    product_price: int
    access_days: int
    invite_link_expire_minutes: int

    log_level: str = "INFO"


def load_settings() -> Settings:
    load_dotenv()

    bot_token = os.getenv("BOT_TOKEN", "").strip()
    prodamus_secret_key = os.getenv("PRODAMUS_SECRET_KEY", "").strip()
    prodamus_payment_page_url = os.getenv("PRODAMUS_PAYMENT_PAGE_URL", "").strip().rstrip("/") + "/"
    webhook_base_url = os.getenv("WEBHOOK_BASE_URL", "").strip().rstrip("/")

    group_id_raw = os.getenv("GROUP_ID", "").strip()
    admin_ids_raw = os.getenv("ADMIN_IDS", "").strip()

    database_url = _get_database_url()

    product_name = os.getenv("PRODUCT_NAME", "Доступ в закрытую группу").strip()
    product_price = int(os.getenv("PRODUCT_PRICE", "990").strip())
    access_days = int(os.getenv("ACCESS_DAYS", "30").strip())
    invite_link_expire_minutes = int(os.getenv("INVITE_LINK_EXPIRE_MINUTES", "20").strip())

    log_level = os.getenv("LOG_LEVEL", "INFO").strip().upper()

    if not bot_token:
        raise RuntimeError("BOT_TOKEN is required")
    if not prodamus_secret_key:
        raise RuntimeError("PRODAMUS_SECRET_KEY is required")
    if not prodamus_payment_page_url or prodamus_payment_page_url == "/":
        raise RuntimeError("PRODAMUS_PAYMENT_PAGE_URL is required")
    if not webhook_base_url:
        raise RuntimeError("WEBHOOK_BASE_URL is required")
    if not group_id_raw:
        raise RuntimeError("GROUP_ID is required")
    return Settings(
        bot_token=bot_token,
        prodamus_secret_key=prodamus_secret_key,
        prodamus_payment_page_url=prodamus_payment_page_url,
        webhook_base_url=webhook_base_url,
        group_id=int(group_id_raw),
        admin_ids=_parse_int_list(admin_ids_raw),
        database_url=database_url,
        product_name=product_name,
        product_price=product_price,
        access_days=access_days,
        invite_link_expire_minutes=invite_link_expire_minutes,
        log_level=log_level,
    )

