-- monitoring.sql
-- Ежедневные запросы мониторинга (выполняются администратором в psql):
--   psql -d posmshop -f sql/monitoring.sql

\echo '=== 1. Активные подключения по ролям ==='
SELECT usename AS role, state, count(*) AS connections
FROM pg_stat_activity
WHERE datname = current_database()
GROUP BY usename, state
ORDER BY usename, state;

\echo '=== 2. Запросы дольше 30 секунд ==='
SELECT pid, usename, now() - query_start AS duration, left(query, 80) AS query
FROM pg_stat_activity
WHERE state = 'active' AND now() - query_start > interval '30 seconds';

\echo '=== 3. Десять самых затратных запросов ==='
SELECT calls,
       round(total_exec_time::numeric, 1) AS total_ms,
       round(mean_exec_time::numeric, 2)  AS mean_ms,
       left(query, 70) AS query
FROM pg_stat_statements
WHERE dbid = (SELECT oid FROM pg_database WHERE datname = current_database())
ORDER BY total_exec_time DESC
LIMIT 10;

\echo '=== 4. Устаревшие строки и автоочистка ==='
SELECT relname AS table_name, n_live_tup, n_dead_tup, last_autovacuum, last_autoanalyze
FROM pg_stat_user_tables
WHERE schemaname = 'shop'
ORDER BY n_dead_tup DESC;

\echo '=== 5. Размер базы данных ==='
SELECT pg_size_pretty(pg_database_size(current_database())) AS db_size;
