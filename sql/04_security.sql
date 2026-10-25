-- 04_security.sql
-- Построчная защита (RLS) и журнал аудита.
SET ROLE posm_owner;

-- Telegram ID пользователя, которого бот передаёт в начале каждой
-- транзакции: SELECT set_config('app.tg_id', '<id>', true)
CREATE FUNCTION shop.current_tg_id() RETURNS bigint
LANGUAGE sql STABLE AS $$
    SELECT NULLIF(current_setting('app.tg_id', true), '')::bigint
$$;

-- Внутренний номер клиента по Telegram ID (листинг 5 отчёта).
-- SECURITY DEFINER: выполняется с правами владельца, поэтому сама
-- функция не зависит от политик таблицы users.
CREATE FUNCTION shop.current_client_id() RETURNS bigint
LANGUAGE sql STABLE SECURITY DEFINER
SET search_path = shop, pg_temp AS $$
    SELECT id FROM shop.users
    WHERE tg_id = shop.current_tg_id()
$$;

REVOKE ALL ON FUNCTION shop.current_tg_id() FROM PUBLIC;
REVOKE ALL ON FUNCTION shop.current_client_id() FROM PUBLIC;
GRANT EXECUTE ON FUNCTION shop.current_tg_id() TO bot_app, manager_app;
GRANT EXECUTE ON FUNCTION shop.current_client_id() TO bot_app, manager_app;

-- ---------- users ----------
ALTER TABLE shop.users ENABLE ROW LEVEL SECURITY;

CREATE POLICY users_client ON shop.users
    FOR ALL TO bot_app
    USING (tg_id = shop.current_tg_id())
    WITH CHECK (tg_id = shop.current_tg_id());

CREATE POLICY users_manager ON shop.users
    FOR SELECT TO manager_app USING (true);

-- ---------- orders (листинг 5 отчёта) ----------
ALTER TABLE shop.orders ENABLE ROW LEVEL SECURITY;

CREATE POLICY orders_client ON shop.orders
    FOR ALL TO bot_app
    USING (user_id = shop.current_client_id())
    WITH CHECK (user_id = shop.current_client_id());

CREATE POLICY orders_manager ON shop.orders
    FOR ALL TO manager_app USING (true);

-- ---------- order_items ----------
-- Подзапрос к orders сам проходит политику orders_client,
-- поэтому клиент видит позиции только своих заявок.
ALTER TABLE shop.order_items ENABLE ROW LEVEL SECURITY;

CREATE POLICY order_items_client ON shop.order_items
    FOR ALL TO bot_app
    USING (order_id IN (SELECT id FROM shop.orders))
    WITH CHECK (order_id IN (SELECT id FROM shop.orders));

CREATE POLICY order_items_manager ON shop.order_items
    FOR SELECT TO manager_app USING (true);

-- ---------- журнал аудита ----------
-- Функция триггера выполняется с правами владельца (SECURITY DEFINER),
-- поэтому приложениям не нужен доступ к audit_log. В журнал попадает
-- роль, под которой подключилось приложение (session_user), и Telegram ID
-- пользователя. Зашифрованный телефон в журнал не пишем.
CREATE FUNCTION shop.audit_trigger() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER
SET search_path = shop, pg_temp AS $$
DECLARE
    old_row jsonb;
    new_row jsonb;
BEGIN
    IF TG_OP IN ('UPDATE', 'DELETE') THEN
        old_row := to_jsonb(OLD) - 'phone_enc';
    END IF;
    IF TG_OP IN ('INSERT', 'UPDATE') THEN
        new_row := to_jsonb(NEW) - 'phone_enc';
    END IF;
    INSERT INTO shop.audit_log
        (table_name, operation, row_id, old_data, new_data, db_role, app_tg_id)
    VALUES
        (TG_TABLE_NAME, TG_OP,
         COALESCE((new_row ->> 'id')::bigint, (old_row ->> 'id')::bigint),
         old_row, new_row, session_user, shop.current_tg_id());
    RETURN NULL;
END;
$$;

REVOKE ALL ON FUNCTION shop.audit_trigger() FROM PUBLIC;

CREATE TRIGGER users_audit AFTER INSERT OR UPDATE OR DELETE ON shop.users
    FOR EACH ROW EXECUTE FUNCTION shop.audit_trigger();
CREATE TRIGGER orders_audit AFTER INSERT OR UPDATE OR DELETE ON shop.orders
    FOR EACH ROW EXECUTE FUNCTION shop.audit_trigger();
CREATE TRIGGER products_audit AFTER INSERT OR UPDATE OR DELETE ON shop.products
    FOR EACH ROW EXECUTE FUNCTION shop.audit_trigger();

RESET ROLE;
