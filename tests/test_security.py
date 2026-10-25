"""Проверка защищённости базы данных (таблица 6 отчёта) и дополнительные проверки."""
from __future__ import annotations

import itertools
import random
from pathlib import Path

import asyncpg
import pytest

from bot import repo
from conftest import PHONE_KEY, as_client, register

ROOT = Path(__file__).resolve().parent.parent
_ids = itertools.count(random.randint(10_000_000, 90_000_000))


def uid() -> int:
    return next(_ids)


async def make_order(connect, tg_id: int, cart: dict[int, int] | None = None) -> int:
    conn = await connect("bot_app")
    async with conn.transaction():
        await as_client(conn, tg_id)
        if cart is None:
            pid = await conn.fetchval("SELECT id FROM shop.products WHERE sku = 'PC-10'")
            cart = {pid: 3}
        order_id, _ = await repo.create_order(conn, cart, "тест")
    return order_id


# ---------------------------------------------------------------- таблица 6

async def test_01_bot_cannot_read_audit_log(connect):
    conn = await connect("bot_app")
    with pytest.raises(asyncpg.InsufficientPrivilegeError):
        await conn.fetch("SELECT * FROM shop.audit_log")


async def test_02_bot_cannot_delete_orders(connect):
    a = uid()
    await register(connect, a)
    await make_order(connect, a)
    conn = await connect("bot_app")
    with pytest.raises(asyncpg.InsufficientPrivilegeError):
        async with conn.transaction():
            await as_client(conn, a)
            await conn.execute("DELETE FROM shop.orders")


async def test_03_client_sees_only_own_orders(connect):
    a, b = uid(), uid()
    await register(connect, a)
    await register(connect, b)
    order_a = await make_order(connect, a)
    order_b = await make_order(connect, b)
    conn = await connect("bot_app")
    async with conn.transaction():
        await as_client(conn, a)
        # запрос БЕЗ условия по клиенту: фильтрует сама база (RLS)
        ids = {r["id"] for r in await conn.fetch("SELECT id FROM shop.orders")}
        items = await conn.fetch("SELECT order_id FROM shop.order_items")
        users = await conn.fetch("SELECT tg_id FROM shop.users")
    assert order_a in ids and order_b not in ids
    assert {r["order_id"] for r in items} <= ids
    assert [r["tg_id"] for r in users] == [a]


async def test_04_cannot_create_order_for_other_client(connect):
    a, b = uid(), uid()
    await register(connect, a)
    b_id = await register(connect, b)
    conn = await connect("bot_app")
    with pytest.raises(asyncpg.InsufficientPrivilegeError, match="row-level security"):
        async with conn.transaction():
            await as_client(conn, a)
            await conn.execute("INSERT INTO shop.orders (user_id, total) VALUES ($1, 10)", b_id)


async def test_05_manager_cannot_change_total(connect):
    a = uid()
    await register(connect, a)
    order_id = await make_order(connect, a)
    conn = await connect("manager_app")
    with pytest.raises(asyncpg.InsufficientPrivilegeError):
        await conn.execute("UPDATE shop.orders SET total = 1 WHERE id = $1", order_id)
    with pytest.raises(asyncpg.InsufficientPrivilegeError):
        await conn.execute("DELETE FROM shop.orders WHERE id = $1", order_id)


async def test_06_report_reader_has_no_access_to_personal_data(connect):
    conn = await connect("report_reader")
    with pytest.raises(asyncpg.InsufficientPrivilegeError):
        await conn.fetch("SELECT * FROM shop.users")
    # а представления для отчётов доступны, и персональных данных в них нет
    rows = await conn.fetch("SELECT * FROM reports.orders_summary LIMIT 1")
    cols = await conn.fetch(
        "SELECT column_name FROM information_schema.columns WHERE table_schema = 'reports'")
    names = {r["column_name"] for r in cols}
    assert not names & {"phone", "phone_enc", "contact_name", "tg_id"}
    assert isinstance(rows, list)


@pytest.mark.parametrize("attack", [
    "'; DROP TABLE shop.orders; --",
    "' OR '1'='1",
    "%",
    "_",
])
async def test_07_sql_injection_in_search(connect, attack):
    conn = await connect("bot_app")
    rows = await repo.search_products(conn, attack)
    assert rows == []  # строка ищется как обычный текст
    assert await conn.fetchval("SELECT to_regclass('shop.orders') IS NOT NULL")


def test_08_pg_hba_sample_rules():
    """Сетевые правила проверяются по конфигурации (на тестовом сервере
    pg_hba.conf свой). Проверяем, что образец для рабочего сервера верный."""
    lines = [l.split() for l in (ROOT / "config/pg_hba.conf.sample").read_text(encoding="utf-8").splitlines()
             if l.strip() and not l.lstrip().startswith("#")]
    postgres_rules = [l for l in lines if l[2] == "postgres"]
    assert postgres_rules == [["local", "all", "postgres", "peer"]]
    for role in ("bot_app", "manager_app", "report_reader"):
        rule = next(l for l in lines if l[2] == role)
        assert rule[0] == "hostssl" and rule[-1] == "scram-sha-256"
    assert lines[-1] == ["host", "all", "all", "0.0.0.0/0", "reject"]
    assert not any("trust" in l or "md5" in l for l in lines)


async def test_09_status_change_is_audited(connect):
    a, m = uid(), uid()
    await register(connect, a)
    await register(connect, m, org="PosmShop", name="Менеджер")
    order_id = await make_order(connect, a)
    mgr = await connect("manager_app")
    async with mgr.transaction():
        await as_client(mgr, m)
        client_tg = await repo.set_order_status(mgr, order_id, "new", "in_work", m)
    assert client_tg == a
    admin = await connect("postgres")
    row = await admin.fetchrow(
        """SELECT db_role, app_tg_id, old_data ->> 'status' AS old, new_data ->> 'status' AS new
           FROM shop.audit_log
           WHERE table_name = 'orders' AND operation = 'UPDATE' AND row_id = $1
           ORDER BY id DESC LIMIT 1""", order_id)
    assert dict(row) == {"db_role": "manager_app", "app_tg_id": m, "old": "new", "new": "in_work"}


# ---------------------------------------------------------------- дополнительные проверки

async def test_phone_is_encrypted(connect):
    a = uid()
    await register(connect, a, phone="+79991234567")
    admin = await connect("postgres")
    raw = await admin.fetchval("SELECT phone_enc FROM shop.users WHERE tg_id = $1", a)
    assert b"79991234567" not in raw
    assert await admin.fetchval("SELECT ext.pgp_sym_decrypt($1, $2)", raw, PHONE_KEY) == "+79991234567"
    with pytest.raises(asyncpg.PostgresError):
        await admin.fetchval("SELECT ext.pgp_sym_decrypt($1, 'wrong-key-wrong-key')", raw)
    # в журнал аудита зашифрованный телефон тоже не попадает
    data = await admin.fetchval(
        "SELECT new_data FROM shop.audit_log WHERE table_name = 'users' AND new_data ->> 'tg_id' = $1",
        str(a))
    assert "phone_enc" not in data


async def test_bot_cannot_make_itself_manager(connect):
    a = uid()
    conn = await connect("bot_app")
    with pytest.raises(asyncpg.InsufficientPrivilegeError):
        async with conn.transaction():
            await as_client(conn, a)
            await conn.execute(
                "INSERT INTO shop.users (tg_id, organization, contact_name, phone_enc, consent_at, role) "
                "VALUES ($1, 'ООО Хакер', 'Хакер', 'x', now(), 'manager')", a)
    await register(connect, a)
    with pytest.raises(asyncpg.InsufficientPrivilegeError):
        async with conn.transaction():
            await as_client(conn, a)
            await conn.execute("UPDATE shop.users SET role = 'manager' WHERE tg_id = $1", a)


async def test_dialog_log_is_write_only_for_bot(connect):
    conn = await connect("bot_app")
    await repo.log_message(conn, uid(), "/start")
    with pytest.raises(asyncpg.InsufficientPrivilegeError):
        await conn.fetch("SELECT * FROM shop.dialog_log")


async def test_without_tg_id_client_sees_nothing(connect):
    a = uid()
    await register(connect, a)
    await make_order(connect, a)
    conn = await connect("bot_app")
    assert await conn.fetch("SELECT id FROM shop.orders") == []
    assert await conn.fetch("SELECT id FROM shop.users") == []


async def test_role_limits(connect):
    admin = await connect("postgres")
    rows = await admin.fetch(
        """SELECT r.rolname, r.rolconnlimit, r.rolbypassrls, r.rolcanlogin, r.rolsuper
           FROM pg_roles r WHERE r.rolname IN
             ('posm_owner', 'bot_app', 'manager_app', 'report_reader', 'backup_agent')""")
    roles = {r["rolname"]: r for r in rows}
    assert not roles["posm_owner"]["rolcanlogin"]
    assert roles["bot_app"]["rolconnlimit"] == 20
    assert roles["backup_agent"]["rolbypassrls"]
    assert not any(r["rolsuper"] for r in rows)
    assert not any(roles[r]["rolbypassrls"] for r in ("bot_app", "manager_app", "report_reader"))
    bot = await connect("bot_app")
    assert await bot.fetchval("SHOW statement_timeout") == "5s"


async def test_public_has_no_access(connect, pg):
    admin = await connect("postgres")
    can_connect = await admin.fetchval(
        "SELECT has_database_privilege('public', $1, 'CONNECT')", pg["db"])
    assert can_connect is False


async def test_role_passwords_not_stored_in_plain_text(connect, pg):
    """setup_db.py передаёт серверу только хеш SCRAM: пароль в открытом виде
    не должен оказаться ни в pg_authid, ни в статистике pg_stat_statements."""
    admin = await connect("postgres")
    hashes = await admin.fetch(
        "SELECT rolpassword FROM pg_authid WHERE rolname = ANY($1::text[])", list(pg["passwords"]))
    assert hashes and all(h["rolpassword"].startswith("SCRAM-SHA-256$") for h in hashes)
    try:
        queries = [r["query"] for r in await admin.fetch("SELECT query FROM pg_stat_statements")]
    except asyncpg.PostgresError:
        pytest.skip("pg_stat_statements не подключён в shared_preload_libraries")
    leaked = [p for p in pg["passwords"].values() if any(p in q for q in queries)]
    assert leaked == []
