"""Сборка диспетчера aiogram: роутеры, middlewares, обработчик ошибок."""
from __future__ import annotations

import logging

from aiogram import Dispatcher
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import ErrorEvent

from . import texts
from .config import Config
from .db import Database
from .handlers import cart, catalog, common, manager, registration
from .middlewares import DialogLogMiddleware

log = logging.getLogger(__name__)


async def on_error(event: ErrorEvent) -> bool:
    """Любая ошибка (например, база временно недоступна): пишем в лог,
    пользователю показываем понятное сообщение, бот продолжает работу."""
    log.exception("ошибка при обработке обновления", exc_info=event.exception)
    upd = event.update
    try:
        if upd.callback_query:
            await upd.callback_query.answer(texts.ERROR, show_alert=True)
        elif upd.message:
            await upd.message.answer(texts.ERROR)
    except Exception:
        log.exception("не удалось отправить сообщение об ошибке")
    return True


def create_dispatcher(db: Database, cfg: Config) -> Dispatcher:
    dp = Dispatcher(storage=MemoryStorage())
    dp["db"] = db
    dp["cfg"] = cfg
    dp.message.outer_middleware(DialogLogMiddleware())
    dp.include_routers(
        common.router,
        registration.router,
        manager.router,
        catalog.router,
        cart.router,
        common.fallback_router,  # последним: непонятные сообщения
    )
    dp.errors.register(on_error)
    return dp
