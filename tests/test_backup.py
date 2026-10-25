"""Резервное копирование и восстановление (раздел 2.2 отчёта)."""
from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent

pytestmark = pytest.mark.skipif(shutil.which("pg_dump") is None, reason="нет pg_dump в PATH")


def env_for(pg, role: str, **extra) -> dict:
    env = dict(os.environ, PGHOST=pg["host"], PGPORT=str(pg["port"]),
               PGPASSWORD=pg["passwords"].get(role, ""), **extra)
    return env


def test_backup_script_creates_readable_dump(pg, tmp_path):
    """Запуск scripts/backup.sh от имени backup_agent: копия создаётся
    и читается утилитой pg_restore."""
    if shutil.which("bash") is None:
        pytest.skip("нет bash")
    env = env_for(pg, "backup_agent", BACKUP_DIR=str(tmp_path), DB_NAME=pg["db"], DB_HOST=pg["host"])
    res = subprocess.run(["bash", str(ROOT / "scripts/backup.sh")], env=env,
                         capture_output=True, text=True, timeout=120)
    assert res.returncode == 0, res.stderr
    assert " OK " in res.stdout
    dumps = list(tmp_path.glob(f"{pg['db']}_*.dump"))
    assert len(dumps) == 1
    listing = subprocess.run(["pg_restore", "--list", str(dumps[0])], capture_output=True, text=True)
    assert "TABLE DATA shop orders" in listing.stdout
    assert "TABLE DATA shop users" in listing.stdout


async def test_dump_without_bypassrls_fails(pg, connect, tmp_path):
    """Подтверждает ошибку, найденную при первой проверке восстановления:
    роль только с pg_read_all_data, но без BYPASSRLS не может сделать копию."""
    admin = await connect("postgres")
    await admin.execute("DROP ROLE IF EXISTS tmp_backup_no_rls")
    await admin.execute("CREATE ROLE tmp_backup_no_rls LOGIN PASSWORD 'tmp_pw_123456'")
    await admin.execute("GRANT pg_read_all_data TO tmp_backup_no_rls")
    await admin.execute(f'GRANT CONNECT ON DATABASE "{pg["db"]}" TO tmp_backup_no_rls')
    try:
        env = dict(os.environ, PGHOST=pg["host"], PGPORT=str(pg["port"]), PGPASSWORD="tmp_pw_123456")
        res = subprocess.run(["pg_dump", "-U", "tmp_backup_no_rls", "-d", pg["db"], "-Fc",
                              "-f", str(tmp_path / "x.dump")], env=env, capture_output=True, text=True)
        assert res.returncode != 0
        assert "row-level security" in res.stderr
    finally:
        await admin.execute(f'REVOKE CONNECT ON DATABASE "{pg["db"]}" FROM tmp_backup_no_rls')
        await admin.execute("DROP ROLE tmp_backup_no_rls")


def test_restore_into_new_database(pg, tmp_path):
    """Восстановление копии в отдельную базу и сравнение числа строк."""
    env = env_for(pg, "backup_agent")
    dump = tmp_path / "check.dump"
    res = subprocess.run(["pg_dump", "-U", "backup_agent", "-d", pg["db"], "-Fc", "-f", str(dump)],
                         env=env, capture_output=True, text=True)
    assert res.returncode == 0, res.stderr

    import asyncio
    import asyncpg
    restore_db = pg["db"] + "_restore"

    async def run() -> tuple[int, int]:
        admin = await asyncpg.connect(pg["admin_dsn"])
        await admin.execute(f'DROP DATABASE IF EXISTS "{restore_db}" WITH (FORCE)')
        await admin.execute(f'CREATE DATABASE "{restore_db}"')
        try:
            admin_env = dict(os.environ)
            from urllib.parse import urlparse
            u = urlparse(pg["admin_dsn"])
            admin_env.update(PGHOST=pg["host"], PGPORT=str(pg["port"]), PGPASSWORD=u.password or "")
            r = subprocess.run(["pg_restore", "--exit-on-error", "-U", u.username or "postgres",
                                "-d", restore_db, str(dump)], env=admin_env, capture_output=True, text=True)
            assert r.returncode == 0, r.stderr
            a = await asyncpg.connect(pg["admin_dsn"])
            b = await asyncpg.connect(pg["admin_dsn"].rsplit("/", 1)[0] + "/" + restore_db)
            n1 = await a.fetchval("SELECT count(*) FROM shop.products")
            n2 = await b.fetchval("SELECT count(*) FROM shop.products")
            await a.close(); await b.close()
            return n1, n2
        finally:
            await admin.execute(f'DROP DATABASE IF EXISTS "{restore_db}" WITH (FORCE)')
            await admin.close()

    n1, n2 = asyncio.run(run())
    assert n1 == n2 > 0
