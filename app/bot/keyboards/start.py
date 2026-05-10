from __future__ import annotations

from aiogram.types import KeyboardButton, ReplyKeyboardMarkup


def start_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="Купить доступ")],
            [KeyboardButton(text="Мой доступ"), KeyboardButton(text="Помощь")],
        ],
        resize_keyboard=True,
        is_persistent=True,
    )

