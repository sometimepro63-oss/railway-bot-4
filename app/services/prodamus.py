from __future__ import annotations

import hashlib
import hmac
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit


def _to_str(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "1" if value else ""
    return str(value)


def _normalize(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {k: _normalize(value[k]) for k in sorted(value.keys())}
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [_normalize(v) for v in value]
    return _to_str(value)


def _json_for_sign(data: Mapping[str, Any]) -> str:
    normalized = _normalize(data)
    raw = json.dumps(normalized, ensure_ascii=False, separators=(",", ":"))
    return raw


def create_signature(data: Mapping[str, Any], secret_key: str) -> str:
    payload = _json_for_sign(data).encode("utf-8")
    return hmac.new(secret_key.encode("utf-8"), payload, hashlib.sha256).hexdigest()


def verify_signature(data: Mapping[str, Any], secret_key: str, signature: str | None) -> bool:
    if not signature:
        return False
    expected = create_signature(data, secret_key)
    return hmac.compare_digest(expected.lower(), signature.strip().lower())


def _split_brackets(key: str) -> list[str]:
    parts: list[str] = []
    buf = []
    i = 0
    while i < len(key):
        ch = key[i]
        if ch == "[":
            if buf:
                parts.append("".join(buf))
                buf = []
            i += 1
            inner = []
            while i < len(key) and key[i] != "]":
                inner.append(key[i])
                i += 1
            parts.append("".join(inner))
        else:
            buf.append(ch)
        i += 1
    if buf:
        parts.append("".join(buf))
    return [p for p in parts if p != ""]


def parse_bracketed_params(flat: Mapping[str, str]) -> dict[str, Any]:
    root: dict[str, Any] = {}

    for raw_key, raw_value in flat.items():
        tokens = _split_brackets(raw_key)
        if not tokens:
            continue

        cur: Any = root
        for idx, token in enumerate(tokens):
            is_last = idx == len(tokens) - 1
            next_token = tokens[idx + 1] if not is_last else None

            if token.isdigit():
                index = int(token)
                if not isinstance(cur, list):
                    raise ValueError(f"Invalid bracket structure for key={raw_key}")
                while len(cur) <= index:
                    cur.append(None)
                if is_last:
                    cur[index] = raw_value
                else:
                    if cur[index] is None:
                        cur[index] = [] if (next_token and next_token.isdigit()) else {}
                    cur = cur[index]
            else:
                if not isinstance(cur, dict):
                    raise ValueError(f"Invalid bracket structure for key={raw_key}")
                if is_last:
                    cur[token] = raw_value
                else:
                    if token not in cur or cur[token] is None:
                        cur[token] = [] if (next_token and next_token.isdigit()) else {}
                    cur = cur[token]

    return root


def _flatten(prefix: str, value: Any, out: list[tuple[str, str]]) -> None:
    if isinstance(value, Mapping):
        for k in value.keys():
            next_prefix = f"{prefix}[{k}]" if prefix else str(k)
            _flatten(next_prefix, value[k], out)
        return
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        for i, v in enumerate(value):
            next_prefix = f"{prefix}[{i}]"
            _flatten(next_prefix, v, out)
        return
    out.append((prefix, _to_str(value)))


def build_payment_url(payment_page_url: str, secret_key: str, data: dict[str, Any]) -> str:
    split = urlsplit(payment_page_url)
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
    base_pairs: list[tuple[str, str]] = []
    for k, v in parse_qsl(split.query, keep_blank_values=True):
        if k in banned or k.startswith("products"):
            continue
        base_pairs.append((k, v))

    signature = create_signature(data, secret_key)
    signed = dict(data)
    signed["signature"] = signature
    pairs: list[tuple[str, str]] = []
    pairs.extend(base_pairs)
    _flatten("", signed, pairs)
    query = urlencode(pairs, doseq=True)
    return urlunsplit((split.scheme, split.netloc, split.path or "/", query, ""))


@dataclass(frozen=True, slots=True)
class ProdamusWebhook:
    order_id: str
    amount: int
    currency: str
    prodamus_payment_id: str | None
    raw: dict[str, Any]


def extract_webhook(payload: Mapping[str, Any]) -> ProdamusWebhook:
    order_id = _to_str(payload.get("order_id") or payload.get("orderid") or payload.get("order"))
    amount_raw = payload.get("order_sum") or payload.get("amount") or payload.get("sum")
    currency = _to_str(payload.get("currency") or payload.get("cur") or "rub").lower()
    prodamus_payment_id = _to_str(payload.get("payment_id") or payload.get("paymentid") or payload.get("id") or "") or None

    if not order_id:
        raise ValueError("order_id is required")
    if amount_raw is None or _to_str(amount_raw) == "":
        raise ValueError("amount is required")

    try:
        amount = int(float(_to_str(amount_raw).replace(",", ".")))
    except ValueError as e:
        raise ValueError("amount is invalid") from e

    return ProdamusWebhook(
        order_id=order_id,
        amount=amount,
        currency=currency,
        prodamus_payment_id=prodamus_payment_id,
        raw=dict(payload),
    )

