"""Общие фикстуры тестов.

Тесты работают с настоящим PostgreSQL: создают отдельную базу posmshop_test,
применяют все SQL-скрипты проекта и удаляют базу в конце.
Нужна переменная TEST_ADMIN_DSN (суперпользователь), например:
    TEST_ADMIN_DSN=postgresql://postgres:pass@127.0.0.1:5432/postgres
"""
from __future__ import annotations

import asyncio
import os
import secrets
import sys
from pathlib import Path
from urllib.parse import urlparse

import asyncpg
import pytest
import pytest_asyncio
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))
load_dotenv(ROOT / ".env")

import setup_db  # noqa: E402

TEST_DB = "posmshop_test"
PHONE_KEY = "test-phone-key-0123456789"


def _drop_db(admin_dsn: str) -> None:
    async def run() -> None:
        conn = await asyncpg.connect(admin_dsn)
        try:
            await conn.execute(f'DROP DATABASE IF EXISTS "{TEST_DB}" WITH (FORCE)')
        finally:
            await conn.close()
    asyncio.run(run())


@pytest.fixture(scope="session")
def pg():
    admin_dsn = os.getenv("TEST_ADMIN_DSN")
    if not admin_dsn:
        pytest.skip("не задана переменная TEST_ADMIN_DSN")
    # Роли общие для кластера: берём пароли из .env, если они там есть,
    # чтобы тесты не сломали подключение работающего бота.
    passwords = {role: os.getenv(env) or secrets.token_urlsafe(18)
                 for role, env in setup_db.PASSWORD_ENV.items()}
    _drop_db(admin_dsn)
    asyncio.run(setup_db.setup(admin_dsn, TEST_DB, passwords))
    url = urlparse(admin_dsn)
    info = {
        "admin_dsn": setup_db.with_database(admin_dsn, TEST_DB),
        "host": url.hostname or "127.0.0.1",
        "port": url.port or 5432,
        "db": TEST_DB,
        "passwords": passwords,
    }
    yield info
    _drop_db(admin_dsn)


@pytest_asyncio.fixture
async def connect(pg):
    """Фабрика подключений: conn = await connect('bot_app')."""
    opened: list[asyncpg.Connection] = []

    async def _connect(role: str) -> asyncpg.Connection:
        if role == "postgres":
            conn = await asyncpg.connect(pg["admin_dsn"])
        else:
            conn = await asyncpg.connect(host=pg["host"], port=pg["port"], database=pg["db"],
                                         user=role, password=pg["passwords"][role], ssl="prefer")
        opened.append(conn)
        return conn

    yield _connect
    for c in opened:
        if not c.is_closed():
            await c.close()


async def as_client(conn: asyncpg.Connection, tg_id: int) -> None:
    """Передаёт серверу Telegram ID клиента на время текущей транзакции."""
    await conn.execute("SELECT set_config('app.tg_id', $1, true)", str(tg_id))


async def register(connect, tg_id: int, org: str = "ООО Тест", name: str = "Тестов",
                   phone: str = "+79990000000") -> int:
    from bot import repo
    conn = await connect("bot_app")
    async with conn.transaction():
        await as_client(conn, tg_id)
        return await repo.register_user(conn, tg_id, org, name, phone, PHONE_KEY)
