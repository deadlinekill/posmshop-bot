-- 03_grants.sql
-- Выдача прав по принципу минимальных привилегий (листинг 3 отчёта).
-- Сначала у PUBLIC отзываются все права, затем каждой роли явно выдаётся
-- только то, что нужно для её работы.

REVOKE ALL ON DATABASE posmshop FROM PUBLIC;
REVOKE ALL ON SCHEMA public FROM PUBLIC;
GRANT CONNECT ON DATABASE posmshop
    TO bot_app, manager_app, report_reader, backup_agent;

GRANT USAGE ON SCHEMA shop TO bot_app, manager_app;
GRANT USAGE ON SCHEMA ext TO bot_app, manager_app;

-- ---------- bot_app: Telegram-бот, клиентская часть ----------
GRANT SELECT ON shop.categories, shop.products TO bot_app;
GRANT SELECT, INSERT ON shop.orders, shop.order_items
    TO bot_app;
GRANT INSERT ON shop.dialog_log TO bot_app;
-- Клиент видит свою запись; при регистрации бот может заполнить только
-- эти столбцы, поэтому выдать себе роль manager через бота нельзя.
GRANT SELECT ON shop.users TO bot_app;
GRANT INSERT (tg_id, organization, contact_name, phone_enc, consent_at)
    ON shop.users TO bot_app;
GRANT UPDATE (organization, contact_name, phone_enc)
    ON shop.users TO bot_app;

-- ---------- manager_app: менеджерская часть ----------
GRANT SELECT ON shop.users TO manager_app;
GRANT SELECT ON shop.orders, shop.order_items TO manager_app;
-- Менеджер меняет только статус, ответственного и время изменения.
-- Изменить сумму или состав уже оформленной заявки нельзя.
GRANT UPDATE (status, manager_id, updated_at)
    ON shop.orders TO manager_app;
-- Ведение каталога
GRANT SELECT, INSERT, UPDATE ON shop.categories, shop.products TO manager_app;

-- ---------- report_reader: только представления схемы reports ----------
-- Прав на схему shop у этой роли нет вообще (см. 05_views.sql).

-- ---------- audit_log ----------
-- Никому из приложений: журнал пишут только триггеры (04_security.sql).
REVOKE ALL ON shop.audit_log FROM PUBLIC;
