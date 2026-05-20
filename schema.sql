-- ============================================================
--  WB + Ozon Marketplace Analytics
--  PostgreSQL 14+
--  WB (Wildberries) + Ozon unified schema
-- ============================================================

-- ТОВАРЫ (source + sku = composite unique key)
CREATE TABLE IF NOT EXISTS products (
    id         BIGSERIAL PRIMARY KEY,
    source     TEXT NOT NULL CHECK (source IN ('wb', 'ozon')),
    sku        BIGINT NOT NULL,
    name       TEXT NOT NULL DEFAULT '',
    category   TEXT,
    cost_price NUMERIC(12,2) DEFAULT 0,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE (source, sku)
);

-- ОСТАТКИ
CREATE TABLE IF NOT EXISTS stocks (
    id             BIGSERIAL PRIMARY KEY,
    sku            BIGINT NOT NULL,
    source         TEXT NOT NULL CHECK (source IN ('wb','ozon')),
    warehouse_name TEXT,
    quantity       INT NOT NULL DEFAULT 0,
    snapshot_date  DATE NOT NULL DEFAULT CURRENT_DATE,
    UNIQUE (sku, source, warehouse_name, snapshot_date)
);

-- ЗАКАЗЫ
CREATE TABLE IF NOT EXISTS orders (
    id          BIGSERIAL PRIMARY KEY,
    source      TEXT NOT NULL CHECK (source IN ('wb','ozon')),
    order_id    TEXT NOT NULL,
    sku         BIGINT,
    order_date  TIMESTAMPTZ,
    quantity    INT DEFAULT 1,
    price       NUMERIC(12,2),
    status      TEXT,
    UNIQUE (source, order_id)
);

-- ПРОДАЖИ
CREATE TABLE IF NOT EXISTS sales (
    id          BIGSERIAL PRIMARY KEY,
    source      TEXT NOT NULL CHECK (source IN ('wb','ozon')),
    sale_id     TEXT NOT NULL,
    sku         BIGINT,
    sale_date   TIMESTAMPTZ,
    quantity    INT DEFAULT 1,
    revenue     NUMERIC(12,2) DEFAULT 0,
    commission  NUMERIC(12,2) DEFAULT 0,
    logistics   NUMERIC(12,2) DEFAULT 0,
    net_revenue NUMERIC(12,2) GENERATED ALWAYS AS
                (revenue - COALESCE(commission,0) - COALESCE(logistics,0)) STORED,
    UNIQUE (source, sale_id)
);

-- СТАТИСТИКА КАРТОЧЕК
CREATE TABLE IF NOT EXISTS card_stats (
    id           BIGSERIAL PRIMARY KEY,
    source       TEXT NOT NULL CHECK (source IN ('wb','ozon')),
    sku          BIGINT NOT NULL,
    stat_date    DATE NOT NULL,
    views        INT DEFAULT 0,
    clicks       INT DEFAULT 0,
    add_to_cart  INT DEFAULT 0,
    orders_count INT DEFAULT 0,
    ctr          NUMERIC(8,4) GENERATED ALWAYS AS
                 (CASE WHEN views>0 THEN ROUND(clicks::NUMERIC/views,4) ELSE 0 END) STORED,
    cr_cart      NUMERIC(8,4) GENERATED ALWAYS AS
                 (CASE WHEN clicks>0 THEN ROUND(add_to_cart::NUMERIC/clicks,4) ELSE 0 END) STORED,
    UNIQUE (source, sku, stat_date)
);

-- ИНДЕКСЫ
CREATE INDEX IF NOT EXISTS idx_sales_date    ON sales(sale_date);
CREATE INDEX IF NOT EXISTS idx_sales_src_sku ON sales(source, sku);
CREATE INDEX IF NOT EXISTS idx_orders_date   ON orders(order_date);
CREATE INDEX IF NOT EXISTS idx_stocks_date   ON stocks(snapshot_date);
CREATE INDEX IF NOT EXISTS idx_card_date     ON card_stats(stat_date);
CREATE INDEX IF NOT EXISTS idx_card_src_sku  ON card_stats(source, sku);

-- ============================================================
--  АНАЛИТИЧЕСКИЕ ВИТРИНЫ
-- ============================================================

-- 1. Прибыль по дням
DROP VIEW IF EXISTS v_profit_daily CASCADE;
CREATE VIEW v_profit_daily AS
SELECT
    DATE(s.sale_date)                       AS day,
    s.source,
    COUNT(*)                                AS transactions,
    SUM(s.quantity)                         AS units_sold,
    ROUND(SUM(s.revenue), 2)               AS revenue,
    ROUND(SUM(s.commission), 2)            AS commission,
    ROUND(SUM(s.logistics), 2)             AS logistics,
    ROUND(SUM(s.net_revenue), 2)           AS net_revenue,
    ROUND(SUM(s.net_revenue)
        - SUM(s.quantity * COALESCE(p.cost_price, 0)), 2) AS gross_profit
FROM sales s
LEFT JOIN products p ON p.source = s.source AND p.sku = s.sku
GROUP BY DATE(s.sale_date), s.source;

-- 2. Прибыль по неделям
DROP VIEW IF EXISTS v_profit_weekly CASCADE;
CREATE VIEW v_profit_weekly AS
SELECT
    DATE_TRUNC('week', s.sale_date)::DATE   AS week_start,
    s.source,
    SUM(s.quantity)                         AS units_sold,
    ROUND(SUM(s.revenue), 2)               AS revenue,
    ROUND(SUM(s.net_revenue), 2)           AS net_revenue,
    ROUND(SUM(s.net_revenue)
        - SUM(s.quantity * COALESCE(p.cost_price, 0)), 2) AS gross_profit
FROM sales s
LEFT JOIN products p ON p.source = s.source AND p.sku = s.sku
GROUP BY DATE_TRUNC('week', s.sale_date), s.source;

-- 3. Прибыль по месяцам
DROP VIEW IF EXISTS v_profit_monthly CASCADE;
CREATE VIEW v_profit_monthly AS
SELECT
    TO_CHAR(s.sale_date, 'YYYY-MM')        AS month,
    s.source,
    SUM(s.quantity)                         AS units_sold,
    ROUND(SUM(s.revenue), 2)               AS revenue,
    ROUND(SUM(s.commission), 2)            AS commission,
    ROUND(SUM(s.logistics), 2)             AS logistics,
    ROUND(SUM(s.net_revenue), 2)           AS net_revenue,
    ROUND(SUM(s.net_revenue)
        - SUM(s.quantity * COALESCE(p.cost_price, 0)), 2) AS gross_profit,
    ROUND(SUM(s.quantity * COALESCE(p.cost_price, 0)), 2) AS total_cost
FROM sales s
LEFT JOIN products p ON p.source = s.source AND p.sku = s.sku
GROUP BY TO_CHAR(s.sale_date, 'YYYY-MM'), s.source;

-- 4. Прибыль по кварталам
DROP VIEW IF EXISTS v_profit_quarterly CASCADE;
CREATE VIEW v_profit_quarterly AS
SELECT
    EXTRACT(YEAR FROM s.sale_date)::INT     AS year,
    EXTRACT(QUARTER FROM s.sale_date)::INT  AS quarter,
    s.source,
    SUM(s.quantity)                         AS units_sold,
    ROUND(SUM(s.revenue), 2)               AS revenue,
    ROUND(SUM(s.net_revenue), 2)           AS net_revenue,
    ROUND(SUM(s.net_revenue)
        - SUM(s.quantity * COALESCE(p.cost_price, 0)), 2) AS gross_profit
FROM sales s
LEFT JOIN products p ON p.source = s.source AND p.sku = s.sku
GROUP BY EXTRACT(YEAR FROM s.sale_date), EXTRACT(QUARTER FROM s.sale_date), s.source;

-- 5. Рентабельность по товарам (30 дней)
DROP VIEW IF EXISTS v_profitability CASCADE;
CREATE VIEW v_profitability AS
SELECT
    p.sku, p.source, p.name, p.category, p.cost_price,
    COALESCE(SUM(s.quantity), 0)                                                           AS units_sold,
    COALESCE(ROUND(SUM(s.revenue), 2), 0)                                                 AS revenue,
    COALESCE(ROUND(SUM(s.commission), 2), 0)                                              AS commission,
    COALESCE(ROUND(SUM(s.logistics), 2), 0)                                               AS logistics,
    COALESCE(ROUND(SUM(s.net_revenue) - SUM(s.quantity) * p.cost_price, 2), 0)           AS gross_profit,
    CASE
        WHEN SUM(s.quantity) * p.cost_price > 0
        THEN ROUND((SUM(s.net_revenue) - SUM(s.quantity) * p.cost_price)
                   / (SUM(s.quantity) * p.cost_price) * 100, 1)
        ELSE NULL
    END AS roi_pct
FROM products p
LEFT JOIN sales s ON s.source = p.source AND s.sku = p.sku
    AND s.sale_date >= NOW() - INTERVAL '30 days'
GROUP BY p.sku, p.source, p.name, p.category, p.cost_price;

-- 6. ABC-анализ
DROP VIEW IF EXISTS v_abc_analysis CASCADE;
CREATE VIEW v_abc_analysis AS
WITH ranked AS (
    SELECT
        p.sku, p.source, p.name, p.category,
        COALESCE(SUM(s.net_revenue - s.quantity * COALESCE(p.cost_price, 0)), 0) AS profit_30d,
        SUM(SUM(s.net_revenue - s.quantity * COALESCE(p.cost_price, 0)))
            OVER () AS total_profit,
        SUM(SUM(s.net_revenue - s.quantity * COALESCE(p.cost_price, 0)))
            OVER (ORDER BY SUM(s.net_revenue - s.quantity * COALESCE(p.cost_price, 0)) DESC
                  ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW) AS running_profit
    FROM products p
    LEFT JOIN sales s ON s.source = p.source AND s.sku = p.sku
        AND s.sale_date >= NOW() - INTERVAL '30 days'
    GROUP BY p.sku, p.source, p.name, p.category
)
SELECT
    sku, source, name, category,
    ROUND(profit_30d, 2) AS profit_30d,
    ROUND(running_profit / NULLIF(total_profit, 0) * 100, 1) AS cumulative_pct,
    CASE
        WHEN running_profit / NULLIF(total_profit, 0) <= 0.80 THEN 'A'
        WHEN running_profit / NULLIF(total_profit, 0) <= 0.95 THEN 'B'
        ELSE 'C'
    END AS abc_class
FROM ranked
ORDER BY profit_30d DESC;

-- 7. Дозакупка
DROP VIEW IF EXISTS v_reorder_list CASCADE;
CREATE VIEW v_reorder_list AS
WITH daily_sales AS (
    SELECT source, sku,
           ROUND(SUM(quantity) / NULLIF(COUNT(DISTINCT DATE(sale_date)), 0)::NUMERIC, 2) AS avg_daily
    FROM sales
    WHERE sale_date >= NOW() - INTERVAL '30 days'
    GROUP BY source, sku
),
latest_stocks AS (
    SELECT DISTINCT ON (sku, source) sku, source, quantity
    FROM stocks
    ORDER BY sku, source, snapshot_date DESC
)
SELECT
    COALESCE(p.name, ls.source || ' SKU ' || ls.sku)    AS name,
    ls.sku,
    ls.source,
    ls.quantity                                           AS stock_qty,
    COALESCE(ds.avg_daily, 0)                            AS avg_daily_sales,
    CASE WHEN ds.avg_daily > 0
         THEN ROUND(ls.quantity / ds.avg_daily)
         ELSE NULL END                                    AS days_of_stock,
    CASE WHEN ds.avg_daily > 0 AND ls.quantity / ds.avg_daily < 7  THEN 'СРОЧНО'
         WHEN ds.avg_daily > 0 AND ls.quantity / ds.avg_daily < 14 THEN 'ДОКУПИТЬ'
         ELSE 'OK' END                                    AS status
FROM latest_stocks ls
LEFT JOIN products p   ON p.source = ls.source AND p.sku = ls.sku
LEFT JOIN daily_sales ds ON ds.source = ls.source AND ds.sku = ls.sku;

-- 8. Эффективность карточек
DROP VIEW IF EXISTS v_card_performance CASCADE;
CREATE VIEW v_card_performance AS
SELECT
    COALESCE(p.name, cs.source || ' SKU ' || cs.sku) AS name,
    cs.sku, cs.source, cs.stat_date,
    cs.views, cs.clicks, cs.add_to_cart, cs.orders_count,
    ROUND(cs.ctr * 100, 2)     AS ctr_pct,
    ROUND(cs.cr_cart * 100, 2) AS cr_cart_pct,
    CASE WHEN cs.ctr * 100 >= 9  THEN 'ОТЛИЧНО'
         WHEN cs.ctr * 100 >= 6  THEN 'НОРМА'
         ELSE 'ПЛОХО' END        AS ctr_grade
FROM card_stats cs
LEFT JOIN products p ON p.source = cs.source AND p.sku = cs.sku;

-- 9. WB vs Ozon (последние 30 дней)
DROP VIEW IF EXISTS v_platform_comparison CASCADE;
CREATE VIEW v_platform_comparison AS
SELECT
    source,
    SUM(quantity)                                                         AS units,
    ROUND(SUM(revenue), 2)                                               AS revenue,
    ROUND(SUM(commission), 2)                                            AS commission,
    ROUND(SUM(logistics), 2)                                             AS logistics,
    ROUND(SUM(net_revenue), 2)                                           AS net_revenue,
    ROUND(SUM(commission) / NULLIF(SUM(revenue), 0) * 100, 1)           AS commission_pct,
    ROUND(SUM(logistics)  / NULLIF(SUM(revenue), 0) * 100, 1)           AS logistics_pct
FROM sales
WHERE sale_date >= NOW() - INTERVAL '30 days'
GROUP BY source;

-- ============================================================
--  ПРАВА ДОСТУПА (замените analytics_user на вашего пользователя БД)
-- ============================================================
-- GRANT ALL PRIVILEGES ON ALL TABLES IN SCHEMA public TO analytics_user;
-- GRANT ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA public TO analytics_user;
