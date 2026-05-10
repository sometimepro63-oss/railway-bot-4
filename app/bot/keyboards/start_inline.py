from __future__ import annotations

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup


def start_inline_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Купить доступ в канал", callback_data="buy")],
        ]
    )

