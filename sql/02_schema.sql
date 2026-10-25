-- 02_schema.sql
-- Схема shop, таблицы, ограничения целостности и индексы.
-- Выполняется суперпользователем; объекты создаются от имени posm_owner.

-- Расширение шифрования ставим в отдельную схему ext, потому что
-- права на схему public у всех ролей отозваны (03_grants.sql).
CREATE SCHEMA IF NOT EXISTS ext;
CREATE EXTENSION IF NOT EXISTS pgcrypto SCHEMA ext;

GRANT CREATE ON DATABASE posmshop TO posm_owner;
SET ROLE posm_owner;

CREATE SCHEMA shop;
CREATE SCHEMA reports;

-- Клиенты и менеджеры
CREATE TABLE shop.users (
    id           bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    tg_id        bigint NOT NULL UNIQUE,
    organization text NOT NULL CHECK (length(organization) BETWEEN 2 AND 200),
    contact_name text NOT NULL CHECK (length(contact_name) BETWEEN 2 AND 100),
    phone_enc    bytea NOT NULL,              -- телефон, зашифрованный pgcrypto
    role         text NOT NULL DEFAULT 'client'
                 CHECK (role IN ('client', 'manager', 'admin')),
    consent_at   timestamptz NOT NULL,        -- когда дано согласие на обработку ПДн
    created_at   timestamptz NOT NULL DEFAULT now()
);

-- Категории каталога (вложенные через parent_id)
CREATE TABLE shop.categories (
    id         bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    parent_id  bigint REFERENCES shop.categories (id),
    name       text NOT NULL CHECK (length(name) BETWEEN 2 AND 100),
    sort_order integer NOT NULL DEFAULT 0
);

-- Товары
CREATE TABLE shop.products (
    id          bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    category_id bigint NOT NULL REFERENCES shop.categories (id),
    sku         text NOT NULL UNIQUE,
    name        text NOT NULL CHECK (length(name) BETWEEN 2 AND 200),
    description text,
    price       numeric(10, 2) NOT NULL CHECK (price > 0),
    is_active   boolean NOT NULL DEFAULT true
);

-- Заявки (листинг 1 отчёта)
CREATE TABLE shop.orders (
    id         bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    user_id    bigint NOT NULL REFERENCES shop.users (id),
    status     text NOT NULL DEFAULT 'new'
        CHECK (status IN ('new', 'in_work', 'confirmed',
                          'shipped', 'done', 'cancelled')),
    total      numeric(12, 2) NOT NULL DEFAULT 0
        CHECK (total >= 0),
    comment    text CHECK (length(comment) <= 500),
    manager_id bigint REFERENCES shop.users (id),
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);

-- Позиции заявок: цена фиксируется на момент оформления
CREATE TABLE shop.order_items (
    id         bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    order_id   bigint NOT NULL REFERENCES shop.orders (id) ON DELETE CASCADE,
    product_id bigint NOT NULL REFERENCES shop.products (id),
    quantity   integer NOT NULL CHECK (quantity BETWEEN 1 AND 100000),
    unit_price numeric(10, 2) NOT NULL CHECK (unit_price > 0),
    UNIQUE (order_id, product_id)
);

-- Журнал сообщений с ботом (хранится 90 дней, см. 07_maintenance.sql)
CREATE TABLE shop.dialog_log (
    id         bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    tg_id      bigint NOT NULL,
    direction  text NOT NULL CHECK (direction IN ('in', 'out')),
    message    text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now()
);

-- Журнал аудита изменений (заполняется только триггерами)
CREATE TABLE shop.audit_log (
    id         bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    table_name text NOT NULL,
    operation  text NOT NULL,
    row_id     bigint,
    old_data   jsonb,
    new_data   jsonb,
    db_role    text NOT NULL,
    app_tg_id  bigint,
    changed_at timestamptz NOT NULL DEFAULT now()
);

-- Индексы по внешним ключам и полям отбора
CREATE INDEX orders_user_id_idx ON shop.orders (user_id);
CREATE INDEX orders_status_idx ON shop.orders (status);
CREATE INDEX orders_created_at_idx ON shop.orders (created_at);
-- Частичный индекс для самого частого запроса менеджеров: новые заявки
CREATE INDEX orders_new_idx ON shop.orders (created_at) WHERE status = 'new';
CREATE INDEX order_items_order_id_idx ON shop.order_items (order_id);
CREATE INDEX products_category_id_idx ON shop.products (category_id);
CREATE INDEX categories_parent_id_idx ON shop.categories (parent_id);
CREATE INDEX dialog_log_created_at_idx ON shop.dialog_log (created_at);
CREATE INDEX audit_log_changed_at_idx ON shop.audit_log (changed_at);

RESET ROLE;
