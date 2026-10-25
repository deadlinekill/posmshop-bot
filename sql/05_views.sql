-- 05_views.sql
-- Представления для отчётов. В них нет телефонов и имён контактных лиц.
-- Представления принадлежат posm_owner, поэтому report_reader получает
-- данные через них, не имея ни одного права на таблицы схемы shop.
SET ROLE posm_owner;

CREATE VIEW reports.orders_by_day AS
SELECT (o.created_at AT TIME ZONE 'Europe/Moscow')::date AS day,
       o.status,
       count(*)     AS orders_count,
       sum(o.total) AS orders_total
FROM shop.orders o
GROUP BY 1, 2;

CREATE VIEW reports.top_products AS
SELECT p.sku,
       p.name,
       sum(oi.quantity)                 AS quantity,
       sum(oi.quantity * oi.unit_price) AS revenue
FROM shop.order_items oi
JOIN shop.products p ON p.id = oi.product_id
JOIN shop.orders o ON o.id = oi.order_id
WHERE o.status <> 'cancelled'
GROUP BY p.sku, p.name;

CREATE VIEW reports.orders_summary AS
SELECT o.id AS order_id,
       o.created_at,
       o.status,
       o.total,
       u.organization
FROM shop.orders o
JOIN shop.users u ON u.id = o.user_id;

RESET ROLE;

GRANT USAGE ON SCHEMA reports TO report_reader;
GRANT SELECT ON ALL TABLES IN SCHEMA reports TO report_reader;
