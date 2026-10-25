"""Подключение к PostgreSQL.

Бот держит два пула соединений под разными ролями:
  * bot_app      - клиентская часть (каталог, корзина, свои заявки);
  * manager_app  - менеджерская часть (все заявки, смена статуса).

В начале каждой транзакции серверу передаётся Telegram ID пользователя
(set_config('app.tg_id', ..., true)). По нему работают политики построчной
защиты (RLS) и журнал аудита. Параметр действует только до конца
транзакции, поэтому соединения из пула можно безопасно переиспользовать.
"""
from __future__ import annotations

from contextlib import asynccontextmanager
from typing import AsyncIterator

import asyncpg

from .config import Config


class Database:
    def __init__(self, client_pool: asyncpg.Pool, manager_pool: asyncpg.Pool) -> None:
        self._client_pool = client_pool
        self._manager_pool = manager_pool

    @classmethod
    async def connect(cls, cfg: Config) -> "Database":
        common = dict(host=cfg.db_host, port=cfg.db_port, database=cfg.db_name,
                      ssl=cfg.db_sslmode, command_timeout=10,
                      server_settings={"application_name": "posmshop_bot"})
        client_pool = await asyncpg.create_pool(user="bot_app", password=cfg.bot_db_password,
                                                min_size=1, max_size=10, **common)
        manager_pool = await asyncpg.create_pool(user="manager_app", password=cfg.manager_db_password,
                                                 min_size=1, max_size=3, **common)
        return cls(client_pool, manager_pool)

    @staticmethod
    @asynccontextmanager
    async def _tx(pool: asyncpg.Pool, tg_id: int) -> AsyncIterator[asyncpg.Connection]:
        async with pool.acquire() as conn:
            async with conn.transaction():
                await conn.execute("SELECT set_config('app.tg_id', $1, true)", str(int(tg_id)))
                yield conn

    def client(self, tg_id: int):
        """Транзакция от имени клиента (роль bot_app, действует RLS)."""
        return self._tx(self._client_pool, tg_id)

    def manager(self, tg_id: int):
        """Транзакция менеджерской части (роль manager_app)."""
        return self._tx(self._manager_pool, tg_id)

    async def close(self) -> None:
        await self._client_pool.close()
        await self._manager_pool.close()
