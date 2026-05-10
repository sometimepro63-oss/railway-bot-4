from __future__ import annotations

import os
from dataclasses import dataclass
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from dotenv import load_dotenv

from app.db.utils import normalize_db_url


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


def _parse_bool(value: str | None) -> bool:
    if value is None:
        return False
    v = value.strip().lower()
    return v in {"1", "true", "yes", "y", "on"}


def _sanitize_payment_page_url(url: str) -> str:
    raw = url.strip()
    split = urlsplit(raw)
    if not split.scheme or not split.netloc:
        raise RuntimeError("PRODAMUS_PAYMENT_PAGE_URL is invalid")
    banned = {
        "order_id",
        "signature",
        "do",
        "customer_extra",
        "urlSuccess",
        "urlReturn",
        "urlNotification",
        "callbackType",
        "currency",
    }
    kept: list[tuple[str, str]] = []
    for k, v in parse_qsl(split.query, keep_blank_values=True):
        if k in banned or k.startswith("products"):
            continue
        kept.append((k, v))

    query = urlencode(kept, doseq=True) if kept else ""
    return urlunsplit((split.scheme, split.netloc, split.path or "/", query, ""))


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
    return normalize_db_url(url)


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
    lifetime_access: bool
    access_days: int | None
    invite_link_expire_minutes: int
    payment_link_ttl_minutes: int

    log_level: str = "INFO"


def load_settings() -> Settings:
    load_dotenv()

    bot_token = os.getenv("BOT_TOKEN", "").strip()
    prodamus_secret_key = os.getenv("PRODAMUS_SECRET_KEY", "").strip()
    raw_prodamus_payment_page_url = os.getenv("PRODAMUS_PAYMENT_PAGE_URL")
    if raw_prodamus_payment_page_url is None or not raw_prodamus_payment_page_url.strip():
        raise RuntimeError("PRODAMUS_PAYMENT_PAGE_URL is required")
    prodamus_payment_page_url = _sanitize_payment_page_url(raw_prodamus_payment_page_url)
    webhook_base_url = os.getenv("WEBHOOK_BASE_URL", "").strip().rstrip("/")

    group_id_raw = os.getenv("GROUP_ID", "").strip()
    admin_ids_raw = os.getenv("ADMIN_IDS", "").strip()

    database_url = _get_database_url()

    product_name = os.getenv("PRODUCT_NAME", "Доступ в закрытую группу").strip()
    product_price = int(os.getenv("PRODUCT_PRICE", "990").strip())
    lifetime_access = _parse_bool(os.getenv("LIFETIME_ACCESS"))
    access_days = None if lifetime_access else int(os.getenv("ACCESS_DAYS", "30").strip())
    invite_link_expire_minutes = int(os.getenv("INVITE_LINK_EXPIRE_MINUTES", "3").strip())
    payment_link_ttl_minutes = int(os.getenv("PAYMENT_LINK_TTL_MINUTES", "30").strip())

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
        lifetime_access=lifetime_access,
        access_days=access_days,
        invite_link_expire_minutes=invite_link_expire_minutes,
        payment_link_ttl_minutes=payment_link_ttl_minutes,
        log_level=log_level,
    )

