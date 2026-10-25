"""Развёртывание базы данных PosmShop на PostgreSQL.

Что делает:
  1. создаёт базу данных (если её нет);
  2. создаёт роли кластера (sql/01_roles.sql), если их ещё нет;
  3. применяет скрипты sql/02 ... sql/07 по порядку;
  4. задаёт пароли ролей из .env (в SQL-файлах паролей нет).

Запуск (от имени суперпользователя PostgreSQL):
    python scripts/setup_db.py --admin-dsn postgresql://postgres:ПАРОЛЬ@localhost:5432/postgres

Работает одинаково в Windows и Linux, psql не нужен.
"""
from __future__ import annotations

import argparse
import asyncio
import base64
import hashlib
import hmac
import os
import re
import sys
from pathlib import Path

import asyncpg

ROOT = Path(__file__).resolve().parent.parent
SQL_DIR = ROOT / "sql"
SCHEMA_FILES = [
    "02_schema.sql",
    "03_grants.sql",
    "04_security.sql",
    "05_views.sql",
    "06_seed.sql",
    "07_maintenance.sql",
]
PASSWORD_ENV = {
    "bot_app": "BOT_DB_PASSWORD",
    "manager_app": "MANAGER_DB_PASSWORD",
    "report_reader": "REPORT_DB_PASSWORD",
    "backup_agent": "BACKUP_DB_PASSWORD",
}
DEFAULT_DB = "posmshop"


def scram_verifier(password: str, iterations: int = 4096) -> str:
    """Хеш пароля в формате, в котором PostgreSQL хранит пароли
    при password_encryption = scram-sha-256 (RFC 5802 / RFC 7677)."""
    salt = os.urandom(16)
    salted = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
    client_key = hmac.new(salted, b"Client Key", hashlib.sha256).digest()
    stored_key = hashlib.sha256(client_key).digest()
    server_key = hmac.new(salted, b"Server Key", hashlib.sha256).digest()
    b64 = lambda b: base64.b64encode(b).decode("ascii")  # noqa: E731
    return f"SCRAM-SHA-256${iterations}:{b64(salt)}${b64(stored_key)}:{b64(server_key)}"


def read_sql(name: str, dbname: str) -> str:
    """Читает SQL-файл. Имя базы в файлах записано как posmshop;
    если разворачиваем под другим именем (например, для тестов), подменяем его
    в командах вида ... ON DATABASE posmshop ..."""
    text = (SQL_DIR / name).read_text(encoding="utf-8")
    if dbname != DEFAULT_DB:
        text = re.sub(r"\bDATABASE\s+posmshop\b", f'DATABASE "{dbname}"', text)
    return text


def with_database(dsn: str, dbname: str) -> str:
    """Заменяет имя базы в DSN вида postgresql://user:pass@host:port/db."""
    base, _, query = dsn.partition("?")
    head, _, _ = base.rpartition("/")
    return f"{head}/{dbname}" + (f"?{query}" if query else "")


async def setup(admin_dsn: str, dbname: str, passwords: dict[str, str]) -> None:
    if not re.fullmatch(r"[a-z_][a-z0-9_]*", dbname):
        raise ValueError("имя базы: только строчные латинские буквы, цифры и _")

    admin = await asyncpg.connect(admin_dsn)
    try:
        exists = await admin.fetchval("SELECT 1 FROM pg_database WHERE datname = $1", dbname)
        if not exists:
            await admin.execute(f'CREATE DATABASE "{dbname}"')
            print(f"[+] создана база {dbname}")
        has_roles = await admin.fetchval("SELECT 1 FROM pg_roles WHERE rolname = 'posm_owner'")
        if not has_roles:
            await admin.execute(read_sql("01_roles.sql", dbname))
            print("[+] созданы роли кластера (01_roles.sql)")
        else:
            print("[=] роли уже есть, 01_roles.sql пропущен")
    finally:
        await admin.close()

    conn = await asyncpg.connect(with_database(admin_dsn, dbname))
    try:
        already = await conn.fetchval("SELECT 1 FROM pg_namespace WHERE nspname = 'shop'")
        if already:
            print("[=] схема shop уже создана, скрипты структуры пропущены")
        else:
            for name in SCHEMA_FILES:
                await conn.execute(read_sql(name, dbname))
                print(f"[+] применён {name}")
        for role, password in passwords.items():
            if not password:
                print(f"[!] пароль для {role} не задан, вход под этой ролью будет невозможен")
                continue
            # Серверу передаётся не пароль, а готовый хеш SCRAM-SHA-256, поэтому
            # пароль в открытом виде не попадает ни в журнал сервера,
            # ни в статистику pg_stat_statements. ALTER ROLE не принимает
            # параметры, поэтому команда собирается через format(%I, %L).
            sql = await conn.fetchval("SELECT format('ALTER ROLE %I PASSWORD %L', $1::text, $2::text)",
                                      role, scram_verifier(password))
            await conn.execute(sql)
        print("[+] пароли ролей заданы (в виде хеша SCRAM-SHA-256)")
    finally:
        await conn.close()


def main() -> None:
    try:
        from dotenv import load_dotenv
        load_dotenv(ROOT / ".env")
    except ImportError:
        pass
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--admin-dsn", default=os.getenv("ADMIN_DSN"),
                        help="DSN суперпользователя, например postgresql://postgres:pass@localhost:5432/postgres")
    parser.add_argument("--dbname", default=os.getenv("DB_NAME", DEFAULT_DB))
    args = parser.parse_args()
    if not args.admin_dsn:
        sys.exit("Укажите --admin-dsn или переменную ADMIN_DSN в .env")
    passwords = {role: os.getenv(env, "") for role, env in PASSWORD_ENV.items()}
    asyncio.run(setup(args.admin_dsn, args.dbname, passwords))
    print("Готово.")


if __name__ == "__main__":
    main()
