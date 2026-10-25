-- 01_roles.sql
-- Роли кластера PostgreSQL для системы обслуживания клиентов PosmShop.
-- Роли общие для всего кластера, поэтому скрипт выполняется один раз
-- от имени суперпользователя postgres (scripts/setup_db.py делает это сам,
-- если ролей ещё нет). Пароли здесь не задаются: их выставляет setup_db.py
-- из файла .env, чтобы пароли не попадали в репозиторий.

-- Владелец всех объектов базы. Входить под ним нельзя (NOLOGIN),
-- скрипты изменения структуры выполняются через SET ROLE posm_owner.
CREATE ROLE posm_owner NOLOGIN;

-- Роли приложений: у каждой своя учётная запись и лимит подключений.
CREATE ROLE bot_app LOGIN CONNECTION LIMIT 20;
CREATE ROLE manager_app LOGIN CONNECTION LIMIT 10;
CREATE ROLE report_reader LOGIN CONNECTION LIMIT 3;

-- Роль резервного копирования: только чтение всех данных.
-- BYPASSRLS нужен pg_dump, иначе он не может прочитать таблицы
-- с включённой построчной защитой (см. раздел 2.2 отчёта).
CREATE ROLE backup_agent LOGIN BYPASSRLS CONNECTION LIMIT 2;
GRANT pg_read_all_data TO backup_agent;

-- Максимальное время выполнения одного запроса для каждой роли.
ALTER ROLE bot_app SET statement_timeout = '5s';
ALTER ROLE manager_app SET statement_timeout = '10s';
ALTER ROLE report_reader SET statement_timeout = '60s';
