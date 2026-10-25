"""Вспомогательные функции для сообщений."""
from __future__ import annotations

from aiogram.exceptions import TelegramBadRequest
from aiogram.types import InlineKeyboardMarkup, Message


async def safe_edit(message: Message, text: str, markup: InlineKeyboardMarkup | None = None) -> None:
    """Редактирует сообщение, а если это невозможно (старое сообщение),
    отправляет новое. Ошибку «message is not modified» игнорирует."""
    try:
        await message.edit_text(text, reply_markup=markup)
    except TelegramBadRequest as exc:
        if "not modified" in str(exc):
            return
        await message.answer(text, reply_markup=markup)
