"""Все запросы к базе данных.

Правило: только параметризованные запросы ($1, $2, ...). Значения, которые
ввёл пользователь, никогда не вклеиваются в текст SQL, поэтому SQL-инъекция
невозможна (раздел 2.3 отчёта).
"""
from __future__ import annotations

from decimal import Decimal

import asyncpg

MANAGER_ROLES = {"manager", "admin"}
ACTIVE_STATUSES = ["new", "in_work", "confirmed", "shipped"]


class EmptyOrderError(Exception):
    """В корзине нет ни одного доступного товара."""


# ---------------------------------------------------------------- клиенты

async def get_user(conn: asyncpg.Connection, tg_id: int) -> asyncpg.Record | None:
    return await conn.fetchrow(
        "SELECT id, tg_id, organization, contact_name, role FROM shop.users WHERE tg_id = $1",
        tg_id)


async def register_user(conn: asyncpg.Connection, tg_id: int, organization: str,
                        contact_name: str, phone: str, phone_key: str) -> int:
    """Регистрация с шифрованием телефона (pgcrypto). Ключ передаётся
    параметром и в базе не хранится."""
    return await conn.fetchval(
        "INSERT INTO shop.users (tg_id, organization, contact_name, phone_enc, consent_at) "
        "VALUES ($1, $2, $3, ext.pgp_sym_encrypt($4, $5), now()) RETURNING id",
        tg_id, organization, contact_name, phone, phone_key)


# ---------------------------------------------------------------- каталог

async def list_categories(conn: asyncpg.Connection, parent_id: int | None) -> list[asyncpg.Record]:
    return await conn.fetch(
        """SELECT c.id, c.name,
                  EXISTS (SELECT 1 FROM shop.categories s WHERE s.parent_id = c.id) AS has_children
           FROM shop.categories c
           WHERE c.parent_id IS NOT DISTINCT FROM $1::bigint
           ORDER BY c.sort_order, c.name""",
        parent_id)


async def get_category(conn: asyncpg.Connection, category_id: int) -> asyncpg.Record | None:
    return await conn.fetchrow(
        "SELECT id, parent_id, name FROM shop.categories WHERE id = $1", category_id)


async def list_products(conn: asyncpg.Connection, category_id: int,
                        limit: int, offset: int) -> tuple[list[asyncpg.Record], int]:
    rows = await conn.fetch(
        """SELECT id, sku, name, price, count(*) OVER () AS total_count
           FROM shop.products
           WHERE category_id = $1 AND is_active
           ORDER BY name LIMIT $2 OFFSET $3""",
        category_id, limit, offset)
    return rows, (rows[0]["total_count"] if rows else 0)


def like_pattern(text: str) -> str:
    """Экранирует служебные символы LIKE, чтобы % и _ искались как обычные."""
    escaped = text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


async def search_products(conn: asyncpg.Connection, query: str, limit: int = 20) -> list[asyncpg.Record]:
    return await conn.fetch(
        """SELECT id, sku, name, price FROM shop.products
           WHERE is_active AND (name ILIKE $1 OR sku ILIKE $1)
           ORDER BY name LIMIT $2""",
        like_pattern(query), limit)


async def get_product(conn: asyncpg.Connection, product_id: int) -> asyncpg.Record | None:
    return await conn.fetchrow(
        "SELECT id, sku, name, description, price, category_id FROM shop.products "
        "WHERE id = $1 AND is_active", product_id)


async def get_products(conn: asyncpg.Connection, ids: list[int]) -> list[asyncpg.Record]:
    return await conn.fetch(
        "SELECT id, sku, name, price FROM shop.products "
        "WHERE id = ANY($1::bigint[]) AND is_active ORDER BY name", ids)


# ---------------------------------------------------------------- заявки клиента

async def create_order(conn: asyncpg.Connection, cart: dict[int, int],
                       comment: str | None) -> tuple[int, Decimal]:
    """Создаёт заявку и её позиции в одной транзакции. Цены берутся из базы,
    а не из того, что пришло от пользователя."""
    products = await get_products(conn, list(cart))
    if not products:
        raise EmptyOrderError
    user_id = await conn.fetchval("SELECT shop.current_client_id()")
    if user_id is None:
        raise PermissionError("клиент не зарегистрирован")
    total = sum((p["price"] * cart[p["id"]] for p in products), Decimal("0"))
    order_id = await conn.fetchval(
        "INSERT INTO shop.orders (user_id, total, comment) VALUES ($1, $2, $3) RETURNING id",
        user_id, total, comment)
    await conn.executemany(
        "INSERT INTO shop.order_items (order_id, product_id, quantity, unit_price) "
        "VALUES ($1, $2, $3, $4)",
        [(order_id, p["id"], cart[p["id"]], p["price"]) for p in products])
    return order_id, total


async def list_my_orders(conn: asyncpg.Connection, limit: int = 10) -> list[asyncpg.Record]:
    # Условие по клиенту стоит и здесь, и в политике RLS (второй уровень защиты).
    return await conn.fetch(
        """SELECT id, status, total, created_at FROM shop.orders
           WHERE user_id = shop.current_client_id()
           ORDER BY created_at DESC LIMIT $1""",
        limit)


async def get_my_order(conn: asyncpg.Connection, order_id: int) -> asyncpg.Record | None:
    return await conn.fetchrow(
        """SELECT id, status, total, comment, created_at FROM shop.orders
           WHERE id = $1 AND user_id = shop.current_client_id()""",
        order_id)


async def get_order_items(conn: asyncpg.Connection, order_id: int) -> list[asyncpg.Record]:
    # Один запрос с JOIN вместо запроса на каждую позицию (без проблемы N+1).
    return await conn.fetch(
        """SELECT oi.product_id, p.sku, p.name, p.is_active, oi.quantity, oi.unit_price
           FROM shop.order_items oi
           JOIN shop.products p ON p.id = oi.product_id
           WHERE oi.order_id = $1
           ORDER BY p.name""",
        order_id)


async def log_message(conn: asyncpg.Connection, tg_id: int, text: str) -> None:
    await conn.execute(
        "INSERT INTO shop.dialog_log (tg_id, direction, message) VALUES ($1, 'in', $2)",
        tg_id, text[:1000])


# ---------------------------------------------------------------- менеджерская часть

async def list_active_orders(conn: asyncpg.Connection, limit: int = 20) -> list[asyncpg.Record]:
    return await conn.fetch(
        """SELECT o.id, o.status, o.total, o.created_at, u.organization
           FROM shop.orders o
           JOIN shop.users u ON u.id = o.user_id
           WHERE o.status = ANY($1::text[])
           ORDER BY o.created_at
           LIMIT $2""",
        ACTIVE_STATUSES, limit)


async def get_order_for_manager(conn: asyncpg.Connection, order_id: int,
                                phone_key: str) -> asyncpg.Record | None:
    """Карточка заявки для менеджера. Телефон расшифровывается только здесь."""
    return await conn.fetchrow(
        """SELECT o.id, o.status, o.total, o.comment, o.created_at,
                  u.organization, u.contact_name, u.tg_id AS client_tg_id,
                  ext.pgp_sym_decrypt(u.phone_enc, $2) AS phone,
                  m.contact_name AS manager_name
           FROM shop.orders o
           JOIN shop.users u ON u.id = o.user_id
           LEFT JOIN shop.users m ON m.id = o.manager_id
           WHERE o.id = $1""",
        order_id, phone_key)


async def set_order_status(conn: asyncpg.Connection, order_id: int, old_status: str,
                           new_status: str, manager_tg_id: int) -> int | None:
    """Меняет статус, если заявка всё ещё в старом статусе (защита от того,
    что два менеджера одновременно нажали разные кнопки).
    Возвращает Telegram ID клиента или None, если статус уже изменён."""
    return await conn.fetchval(
        """UPDATE shop.orders o
           SET status = $3,
               manager_id = (SELECT id FROM shop.users WHERE tg_id = $4),
               updated_at = now()
           WHERE o.id = $1 AND o.status = $2
           RETURNING (SELECT u.tg_id FROM shop.users u WHERE u.id = o.user_id)""",
        order_id, old_status, new_status, manager_tg_id)
