"""Промежуточные обработчики (middlewares)."""
from __future__ import annotations

import logging
from typing import Any, Awaitable, Callable

from aiogram import BaseMiddleware
from aiogram.types import CallbackQuery, Message, TelegramObject

from . import repo
from .db import Database
from .states import PERSONAL_DATA_STATES

log = logging.getLogger(__name__)
Handler = Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]]


class DialogLogMiddleware(BaseMiddleware):
    """Пишет входящие сообщения в shop.dialog_log (роль bot_app имеет на эту
    таблицу только право INSERT). Персональные данные не записываются."""

    async def __call__(self, handler: Handler, event: TelegramObject, data: dict[str, Any]) -> Any:
        if isinstance(event, Message) and event.from_user:
            raw_state = data.get("raw_state")
            if raw_state in PERSONAL_DATA_STATES or event.contact:
                text = "[скрыто: персональные данные]"
            elif event.text:
                text = event.text
            else:
                text = f"[{event.content_type}]"
            db: Database = data["db"]
            try:
                async with db.client(event.from_user.id) as conn:
                    await repo.log_message(conn, event.from_user.id, text)
            except Exception:  # журнал не должен ломать работу бота
                log.exception("не удалось записать сообщение в dialog_log")
        return await handler(event, data)


class ProfileMiddleware(BaseMiddleware):
    """Загружает профиль клиента. Незарегистрированных отправляет на /start."""

    async def __call__(self, handler: Handler, event: TelegramObject, data: dict[str, Any]) -> Any:
        user = data.get("event_from_user")
        if user is None:
            return None
        db: Database = data["db"]
        async with db.client(user.id) as conn:
            profile = await repo.get_user(conn, user.id)
        if profile is None:
            text = "Сначала нужно зарегистрироваться: нажмите /start"
            if isinstance(event, CallbackQuery):
                await event.answer(text, show_alert=True)
            elif isinstance(event, Message):
                await event.answer(text)
            return None
        data["profile"] = profile
        return await handler(event, data)


class ManagerOnlyMiddleware(BaseMiddleware):
    """Пропускает только пользователей с ролью manager или admin."""

    async def __call__(self, handler: Handler, event: TelegramObject, data: dict[str, Any]) -> Any:
        profile = data.get("profile")
        if profile is None or profile["role"] not in repo.MANAGER_ROLES:
            text = "Раздел доступен только менеджерам"
            if isinstance(event, CallbackQuery):
                await event.answer(text, show_alert=True)
            elif isinstance(event, Message):
                await event.answer(text)
            return None
        return await handler(event, data)
