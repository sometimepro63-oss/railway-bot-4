from __future__ import annotations

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup


def payment_keyboard(payment_url: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Оплатить доступ", url=payment_url)],
        ]
    )

