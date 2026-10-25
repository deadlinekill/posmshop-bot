-- 07_maintenance.sql
-- Регламентное обслуживание (раздел 2.2 отчёта).

-- Статистика запросов. Работает, когда в postgresql.conf указано
-- shared_preload_libraries = 'pg_stat_statements' (config/postgresql.conf.sample).
CREATE EXTENSION IF NOT EXISTS pg_stat_statements;

SET ROLE posm_owner;

-- В таблице заявок часто меняются статусы: автоочистка при 5 %
-- изменённых строк вместо 20 % по умолчанию.
ALTER TABLE shop.orders SET (autovacuum_vacuum_scale_factor = 0.05,
                             autovacuum_analyze_scale_factor = 0.05);

-- Журнал диалогов хранится 90 дней. Вызывается еженедельно из cron:
--   psql -d posmshop -c "SELECT shop.cleanup_dialog_log();"
CREATE FUNCTION shop.cleanup_dialog_log(keep interval DEFAULT interval '90 days')
RETURNS bigint LANGUAGE sql AS $$
    WITH deleted AS (
        DELETE FROM shop.dialog_log
        WHERE created_at < now() - keep
        RETURNING 1
    )
    SELECT count(*) FROM deleted
$$;
REVOKE ALL ON FUNCTION shop.cleanup_dialog_log(interval) FROM PUBLIC;

RESET ROLE;
