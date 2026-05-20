"""WB + Ozon Analytics API (FastAPI + psycopg2)"""
from datetime import datetime, timedelta
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import Response
import psycopg2, os, threading, subprocess, csv, io, time
from psycopg2.extras import RealDictCursor
from dotenv import load_dotenv

load_dotenv()

app = FastAPI(title="Marketplace Analytics API", docs_url=None, redoc_url=None)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET", "POST", "PATCH"],
    allow_headers=["*"],
)

_sync  = {"running": False, "status": "idle", "started": None, "log": []}
_cache = {}
CACHE_TTL = 300  # 5 minutes


def q(sql: str, params=()) -> list:
    with psycopg2.connect(os.getenv("DATABASE_URL")) as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(sql, params)
            return [dict(r) for r in cur.fetchall()]


def since(days: int) -> datetime:
    return datetime.now() - timedelta(days=days)


def to_csv(rows: list, filename: str) -> Response:
    if not rows:
        rows = [{"info": "no data"}]
    output = io.StringIO()
    writer = csv.DictWriter(output, fieldnames=rows[0].keys())
    writer.writeheader()
    for r in rows:
        writer.writerow({k: str(v) if v is not None else "" for k, v in r.items()})
    content = "﻿" + output.getvalue()
    return Response(
        content=content.encode("utf-8"),
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{filename}"}
    )


# ─── KPI ────────────────────────────────────────────────────────────────────

@app.get("/api/kpis")
def kpis():
    d30 = since(30)
    d60 = since(60)
    main = q("""
        WITH cur AS (
            SELECT s.quantity, s.revenue, s.net_revenue,
                   COALESCE(p.cost_price, 0) AS cp
            FROM sales s
            LEFT JOIN products p ON p.source = s.source AND p.sku = s.sku
            WHERE s.sale_date >= %s
        ),
        prev AS (
            SELECT s.quantity, s.revenue, s.net_revenue,
                   COALESCE(p.cost_price, 0) AS cp
            FROM sales s
            LEFT JOIN products p ON p.source = s.source AND p.sku = s.sku
            WHERE s.sale_date >= %s AND s.sale_date < %s
        )
        SELECT
            ROUND(SUM(cur.revenue)::numeric, 0)                                          AS revenue_30d,
            ROUND((SUM(cur.net_revenue) - SUM(cur.quantity * cur.cp))::numeric, 0)       AS profit_30d,
            SUM(cur.quantity)                                                             AS units_30d,
            ROUND(SUM(cur.net_revenue) / NULLIF(SUM(cur.revenue), 0) * 100, 1)           AS margin_pct,
            ROUND(SUM(prev.revenue)::numeric, 0)                                         AS revenue_prev,
            ROUND((SUM(prev.net_revenue) - SUM(prev.quantity * prev.cp))::numeric, 0)    AS profit_prev,
            SUM(prev.quantity)                                                            AS units_prev
        FROM cur, prev
    """, [d30, d60, d30])

    extra = q("""
        SELECT
            (SELECT COUNT(*) FROM v_reorder_list WHERE status = 'СРОЧНО')   AS urgent,
            (SELECT COUNT(*) FROM v_reorder_list WHERE status = 'ДОКУПИТЬ') AS reorder,
            (SELECT COUNT(*) FROM products)                                  AS total_skus
    """)

    result = dict(main[0]) if main else {}
    if extra:
        result.update(extra[0])

    def delta(cur, prev):
        if prev and prev != 0:
            return round((cur - prev) / abs(prev) * 100, 1)
        return None

    r = result
    r['revenue_delta'] = delta(r.get('revenue_30d') or 0, r.get('revenue_prev') or 0)
    r['profit_delta']  = delta(r.get('profit_30d')  or 0, r.get('profit_prev')  or 0)
    r['units_delta']   = delta(r.get('units_30d')   or 0, r.get('units_prev')   or 0)
    return r


# ─── ПРИБЫЛЬ ────────────────────────────────────────────────────────────────

@app.get("/api/profit/daily")
def profit_daily(days: int = 30, source: str = "all"):
    d = since(days)
    if source in ("wb", "ozon"):
        return q("SELECT day, source, revenue, net_revenue, gross_profit, units_sold "
                 "FROM v_profit_daily WHERE day >= %s AND source = %s ORDER BY day", [d, source])
    return q("SELECT day, source, revenue, net_revenue, gross_profit, units_sold "
             "FROM v_profit_daily WHERE day >= %s ORDER BY day", [d])


@app.get("/api/profit/weekly")
def profit_weekly(weeks: int = 12, source: str = "all"):
    d = since(weeks * 7)
    if source in ("wb", "ozon"):
        return q("SELECT week_start, source, revenue, net_revenue, gross_profit, units_sold "
                 "FROM v_profit_weekly WHERE week_start >= %s AND source = %s ORDER BY week_start", [d, source])
    return q("SELECT week_start, source, revenue, net_revenue, gross_profit, units_sold "
             "FROM v_profit_weekly WHERE week_start >= %s ORDER BY week_start", [d])


@app.get("/api/profit/monthly")
def profit_monthly():
    return q("SELECT month, source, revenue, net_revenue, gross_profit, units_sold, total_cost "
             "FROM v_profit_monthly ORDER BY month ASC LIMIT 24")


@app.get("/api/profit/quarterly")
def profit_quarterly():
    return q("SELECT year, quarter, source, revenue, net_revenue, gross_profit "
             "FROM v_profit_quarterly ORDER BY year ASC, quarter ASC LIMIT 20")


@app.get("/api/profit/alltime")
def profit_alltime(source: str = "all"):
    if source in ("wb", "ozon"):
        return q("SELECT day, source, revenue, net_revenue, gross_profit, units_sold "
                 "FROM v_profit_daily WHERE source = %s ORDER BY day", [source])
    return q("SELECT day, source, revenue, net_revenue, gross_profit, units_sold "
             "FROM v_profit_daily ORDER BY day")


# ─── ТРЕНДЫ (WoW) ───────────────────────────────────────────────────────────

@app.get("/api/trends")
def trends(weeks: int = 16):
    d = since(weeks * 7)
    return q("""
        WITH weekly AS (
            SELECT
                DATE_TRUNC('week', sale_date)::date AS week,
                source,
                ROUND(SUM(revenue)::numeric, 0)     AS revenue,
                ROUND(SUM(net_revenue)::numeric, 0) AS net_revenue,
                SUM(quantity)                        AS units
            FROM sales
            WHERE sale_date >= %s
            GROUP BY DATE_TRUNC('week', sale_date), source
        ),
        lagged AS (
            SELECT *,
                LAG(revenue)     OVER (PARTITION BY source ORDER BY week) AS prev_rev,
                LAG(net_revenue) OVER (PARTITION BY source ORDER BY week) AS prev_net,
                LAG(units)       OVER (PARTITION BY source ORDER BY week) AS prev_units
            FROM weekly
        )
        SELECT week, source, revenue, net_revenue, units,
            CASE WHEN prev_rev > 0
                THEN ROUND(((revenue - prev_rev)::numeric / prev_rev * 100), 1)
                ELSE NULL END AS revenue_wow,
            CASE WHEN prev_units > 0
                THEN ROUND(((units - prev_units)::numeric / prev_units * 100), 1)
                ELSE NULL END AS units_wow
        FROM lagged
        ORDER BY week ASC
    """, [d])


# ─── СРАВНЕНИЕ ПЕРИОДОВ ─────────────────────────────────────────────────────

@app.get("/api/comparison")
def comparison(days: int = 30):
    d_cur  = since(days)
    d_prev = since(days * 2)
    return q("""
        WITH cur AS (
            SELECT source,
                ROUND(SUM(revenue)::numeric, 0)     AS revenue,
                ROUND(SUM(net_revenue)::numeric, 0) AS net_revenue,
                SUM(quantity)                        AS units,
                ROUND(SUM(commission)::numeric, 0)  AS commission,
                ROUND(SUM(logistics)::numeric, 0)   AS logistics
            FROM sales WHERE sale_date >= %s
            GROUP BY source
        ),
        prev AS (
            SELECT source,
                ROUND(SUM(revenue)::numeric, 0)     AS revenue,
                ROUND(SUM(net_revenue)::numeric, 0) AS net_revenue,
                SUM(quantity)                        AS units
            FROM sales WHERE sale_date >= %s AND sale_date < %s
            GROUP BY source
        )
        SELECT
            c.source,
            c.revenue, p.revenue AS revenue_prev,
            CASE WHEN p.revenue > 0
                THEN ROUND(((c.revenue - p.revenue)::numeric / p.revenue * 100), 1)
                ELSE NULL END AS revenue_delta,
            c.net_revenue, p.net_revenue AS net_revenue_prev,
            CASE WHEN p.net_revenue > 0
                THEN ROUND(((c.net_revenue - p.net_revenue)::numeric / p.net_revenue * 100), 1)
                ELSE NULL END AS net_delta,
            c.units, p.units AS units_prev,
            CASE WHEN p.units > 0
                THEN ROUND(((c.units - p.units)::numeric / p.units * 100), 1)
                ELSE NULL END AS units_delta,
            c.commission, c.logistics
        FROM cur c
        LEFT JOIN prev p ON p.source = c.source
        ORDER BY c.source
    """, [d_cur, d_prev, d_cur])


# ─── ВОРОНКА КОНВЕРСИИ ──────────────────────────────────────────────────────

@app.get("/api/funnel")
def funnel(days: int = 30, source: str = "all"):
    d = since(days)
    if source in ("wb", "ozon"):
        src_filter = "AND cs.source = %s"
        params = [d, source]
    else:
        src_filter = ""
        params = [d]
    return q(f"""
        SELECT
            cs.sku, cs.source,
            COALESCE(p.name, cs.source || ' SKU ' || cs.sku) AS name,
            SUM(cs.views)        AS views,
            SUM(cs.clicks)       AS clicks,
            SUM(cs.add_to_cart)  AS add_to_cart,
            SUM(cs.orders_count) AS orders,
            CASE WHEN SUM(cs.views) > 0
                THEN ROUND((SUM(cs.clicks)::numeric / SUM(cs.views) * 100), 2)
                ELSE 0 END AS ctr,
            CASE WHEN SUM(cs.clicks) > 0
                THEN ROUND((SUM(cs.add_to_cart)::numeric / SUM(cs.clicks) * 100), 2)
                ELSE 0 END AS click_to_cart,
            CASE WHEN SUM(cs.add_to_cart) > 0
                THEN ROUND((SUM(cs.orders_count)::numeric / SUM(cs.add_to_cart) * 100), 2)
                ELSE 0 END AS cart_to_order,
            CASE WHEN SUM(cs.views) > 0
                THEN ROUND((SUM(cs.orders_count)::numeric / SUM(cs.views) * 100), 3)
                ELSE 0 END AS total_conv
        FROM card_stats cs
        LEFT JOIN products p ON p.source = cs.source AND p.sku = cs.sku
        WHERE cs.stat_date >= %s {src_filter}
        GROUP BY cs.sku, cs.source, COALESCE(p.name, cs.source || ' SKU ' || cs.sku)
        HAVING SUM(cs.views) > 100
        ORDER BY views DESC
        LIMIT 100
    """, params)


# ─── ТОП / ФЛОП ТОВАРЫ ──────────────────────────────────────────────────────

@app.get("/api/top")
def top_products(metric: str = "profit", days: int = 30, limit: int = 20, source: str = "all"):
    valid = {"profit": "gross_profit", "revenue": "revenue", "units": "units_sold"}
    col = valid.get(metric, "gross_profit")
    direction = "DESC" if metric != "flop" else "ASC"
    d = since(days)
    src_filter = "AND s.source = %(src)s" if source in ("wb", "ozon") else ""
    return q(f"""
        SELECT
            s.sku, s.source,
            COALESCE(p.name, s.source || ' SKU ' || s.sku) AS name,
            p.category,
            SUM(s.quantity)                                                              AS units_sold,
            ROUND(SUM(s.revenue)::numeric, 0)                                           AS revenue,
            ROUND(SUM(s.commission)::numeric, 0)                                        AS commission,
            ROUND(SUM(s.logistics)::numeric, 0)                                         AS logistics,
            ROUND((SUM(s.net_revenue) - SUM(s.quantity * COALESCE(p.cost_price,0)))::numeric, 0) AS gross_profit,
            CASE WHEN SUM(s.quantity * COALESCE(p.cost_price,0)) > 0
                THEN ROUND(((SUM(s.net_revenue) - SUM(s.quantity * COALESCE(p.cost_price,0)))
                           / SUM(s.quantity * COALESCE(p.cost_price,0)) * 100)::numeric, 1)
                ELSE NULL END AS roi_pct
        FROM sales s
        LEFT JOIN products p ON p.source = s.source AND p.sku = s.sku
        WHERE s.sale_date >= %(d)s {src_filter}
        GROUP BY s.sku, s.source,
                 COALESCE(p.name, s.source || ' SKU ' || s.sku), p.category
        ORDER BY {col} {direction} NULLS LAST
        LIMIT %(limit)s
    """, {"d": d, "src": source, "limit": limit})


# ─── СЕЗОННОСТЬ ─────────────────────────────────────────────────────────────

@app.get("/api/seasonality")
def seasonality():
    return q("""
        SELECT
            EXTRACT(DOW FROM sale_date)::int  AS dow,
            TO_CHAR(sale_date, 'Dy')          AS dow_name,
            ROUND(AVG(daily_rev)::numeric, 0) AS avg_revenue,
            ROUND(AVG(daily_units)::numeric, 1) AS avg_units
        FROM (
            SELECT DATE(sale_date) AS sale_date,
                   SUM(revenue)    AS daily_rev,
                   SUM(quantity)   AS daily_units
            FROM sales
            WHERE sale_date >= NOW() - INTERVAL '90 days'
            GROUP BY DATE(sale_date)
        ) t
        GROUP BY EXTRACT(DOW FROM sale_date), TO_CHAR(sale_date, 'Dy')
        ORDER BY dow
    """)


# ─── КАТЕГОРИИ ──────────────────────────────────────────────────────────────

@app.get("/api/categories")
def categories(days: int = 30):
    d = since(days)
    return q("""
        SELECT
            COALESCE(p.category, 'Без категории') AS category,
            s.source,
            SUM(s.quantity)                        AS units_sold,
            ROUND(SUM(s.revenue)::numeric, 0)     AS revenue,
            ROUND((SUM(s.net_revenue) - SUM(s.quantity * COALESCE(p.cost_price,0)))::numeric, 0) AS profit,
            COUNT(DISTINCT s.sku)                  AS skus
        FROM sales s
        LEFT JOIN products p ON p.source = s.source AND p.sku = s.sku
        WHERE s.sale_date >= %s
        GROUP BY COALESCE(p.category, 'Без категории'), s.source
        ORDER BY profit DESC NULLS LAST
    """, [d])


# ─── ТОВАРЫ ─────────────────────────────────────────────────────────────────

@app.get("/api/profitability")
def profitability():
    return q("SELECT sku, source, name, category, cost_price, units_sold, revenue, "
             "commission, logistics, gross_profit, roi_pct "
             "FROM v_profitability ORDER BY gross_profit DESC NULLS LAST")


@app.get("/api/abc")
def abc():
    return q("SELECT sku, source, name, category, profit_30d, cumulative_pct, abc_class "
             "FROM v_abc_analysis")


# ─── СКЛАД ──────────────────────────────────────────────────────────────────

@app.get("/api/reorder")
def reorder():
    return q("SELECT name, sku, source, stock_qty, avg_daily_sales, days_of_stock, status "
             "FROM v_reorder_list "
             "ORDER BY CASE status WHEN 'СРОЧНО' THEN 0 WHEN 'ДОКУПИТЬ' THEN 1 ELSE 2 END, "
             "days_of_stock NULLS LAST")


# ─── КАРТОЧКИ ───────────────────────────────────────────────────────────────

@app.get("/api/cards")
def cards(days: int = 30):
    return q("SELECT name, sku, source, stat_date, views, clicks, add_to_cart, "
             "orders_count, ctr_pct, cr_cart_pct, ctr_grade "
             "FROM v_card_performance WHERE stat_date >= %s "
             "ORDER BY stat_date DESC, views DESC", [since(days)])


# ─── WB VS OZON ─────────────────────────────────────────────────────────────

@app.get("/api/platform")
def platform():
    return q("SELECT source, units, revenue, commission, logistics, net_revenue, "
             "commission_pct, logistics_pct FROM v_platform_comparison")


# ─── ЭКСПОРТ CSV ─────────────────────────────────────────────────────────────

@app.get("/api/export/finance")
def export_finance(days: int = 30):
    rows = q("SELECT day, source, revenue, net_revenue, gross_profit, units_sold, "
             "commission, logistics FROM v_profit_daily WHERE day >= %s ORDER BY day DESC", [since(days)])
    return to_csv(rows, f"finance_{days}d.csv")

@app.get("/api/export/profitability")
def export_profitability():
    rows = q("SELECT sku, source, name, category, cost_price, units_sold, revenue, "
             "commission, logistics, gross_profit, roi_pct FROM v_profitability ORDER BY gross_profit DESC")
    return to_csv(rows, "products.csv")

@app.get("/api/export/reorder")
def export_reorder():
    rows = q("SELECT name, sku, source, stock_qty, avg_daily_sales, days_of_stock, status "
             "FROM v_reorder_list ORDER BY days_of_stock NULLS LAST")
    return to_csv(rows, "reorder.csv")

@app.get("/api/export/cards")
def export_cards(days: int = 30):
    rows = q("SELECT name, sku, source, stat_date, views, clicks, add_to_cart, "
             "orders_count, ctr_pct, cr_cart_pct, ctr_grade FROM v_card_performance "
             "WHERE stat_date >= %s ORDER BY stat_date DESC", [since(days)])
    return to_csv(rows, f"cards_{days}d.csv")

@app.get("/api/export/platform")
def export_platform():
    rows = q("SELECT source, units, revenue, commission, logistics, net_revenue, "
             "commission_pct, logistics_pct FROM v_platform_comparison")
    return to_csv(rows, "platform.csv")


# ─── СИНХРОНИЗАЦИЯ ───────────────────────────────────────────────────────────

@app.post("/api/sync")
def sync_start(source: str = "all", days: int = 30):
    if _sync["running"]:
        return {"status": "already_running", "message": "Синхронизация уже идёт"}

    def _run():
        _sync["running"] = True
        _sync["started"] = datetime.now().isoformat()
        _sync["log"] = []
        try:
            cmd = ["/opt/analytics/venv/bin/python", "/opt/analytics/run_etl.py", f"--days={days}"]
            if source == "wb":     cmd.append("--wb")
            elif source == "ozon": cmd.append("--ozon")
            _sync["status"] = f"running ({source}, {days}д)"
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=900)
            _sync["log"] = (result.stdout + result.stderr).splitlines()[-20:]
            _sync["status"] = "done" if result.returncode == 0 else "error"
        except subprocess.TimeoutExpired:
            _sync["status"] = "timeout"
        except Exception as e:
            _sync["status"] = f"error: {e}"
        finally:
            _sync["running"] = False

    threading.Thread(target=_run, daemon=True).start()
    return {"status": "started"}


@app.get("/api/sync/status")
def sync_status():
    return _sync


# ─── ПРОДУКТЫ ────────────────────────────────────────────────────────────────

@app.get("/api/products/list")
def products_list():
    return q("SELECT source, sku, name, category, cost_price "
             "FROM products ORDER BY source, name")


@app.patch("/api/products/{source}/{sku}/cost")
def update_cost(source: str, sku: int, cost_price: float):
    if source not in ("wb", "ozon"):
        raise HTTPException(status_code=400, detail="source must be wb or ozon")
    with psycopg2.connect(os.getenv("DATABASE_URL")) as conn:
        with conn.cursor() as cur:
            cur.execute("UPDATE products SET cost_price = %s WHERE source = %s AND sku = %s",
                        (cost_price, source, sku))
        conn.commit()
    return {"ok": True}


# ─── СТАТИКА ─────────────────────────────────────────────────────────────────
app.mount("/", StaticFiles(directory="dashboard", html=True), name="static")
