"""Ozon ETL — Ozon data ingestion"""
import os, time, logging
from datetime import datetime, timedelta, date
import requests, psycopg2
from psycopg2.extras import execute_values
from dotenv import load_dotenv

load_dotenv()
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger(__name__)

OZON_CLIENT_ID = os.getenv("OZON_CLIENT_ID")
OZON_API_KEY   = os.getenv("OZON_API_KEY")
DB_URL         = os.getenv("DATABASE_URL")
OZON_BASE      = "https://api-seller.ozon.ru"


def get_headers():
    return {"Client-Id": OZON_CLIENT_ID, "Api-Key": OZON_API_KEY,
            "Content-Type": "application/json"}


def get_conn():
    conn = psycopg2.connect(DB_URL)
    conn.autocommit = False
    return conn


def _post(path, payload):
    for attempt in range(3):
        try:
            r = requests.post(OZON_BASE + path, headers=get_headers(),
                              json=payload, timeout=30)
            if r.status_code == 429:
                log.warning("Rate limit Ozon, ждём 30 сек...")
                time.sleep(30); continue
            if not r.ok:
                log.error(f"{r.status_code} {path}: {r.text[:200]}")
                return {}
            return r.json()
        except requests.RequestException as e:
            log.error(f"POST {path} попытка {attempt+1}/3: {e}")
            time.sleep(5 * (attempt + 1))
    return {}


def load_ozon_products(conn):
    """Загружает товары Ozon из постингов (FBS) — получаем sku + название."""
    log.info("Ozon: загружаем каталог товаров из постингов...")
    names = {}  # sku -> name
    end   = datetime.now()
    start = end - timedelta(days=90)
    chunk = timedelta(days=28)
    cur_start = start
    while cur_start < end:
        cur_end   = min(cur_start + chunk, end)
        date_from = cur_start.strftime("%Y-%m-%dT00:00:00.000Z")
        date_to   = cur_end.strftime("%Y-%m-%dT23:59:59.000Z")
        offset = 0
        while True:
            data = _post("/v3/posting/fbs/list", {
                "dir": "asc",
                "filter": {"since": date_from, "to": date_to},
                "limit": 100, "offset": offset,
                "with": {"analytics_data": False, "financial_data": False}
            })
            postings = data.get("result", {}).get("postings", [])
            if not postings:
                break
            for p in postings:
                for product in p.get("products", []):
                    sku  = product.get("sku")
                    name = product.get("name", "")
                    if sku and name and sku not in names:
                        names[sku] = name
            offset += len(postings)
            if len(postings) < 100:
                break
            time.sleep(0.2)
        cur_start = cur_end + timedelta(seconds=1)

    if not names:
        log.warning("Ozon: не удалось получить названия товаров")
        return

    with conn.cursor() as cur:
        for sku, name in names.items():
            cur.execute("""
                INSERT INTO products(source, sku, name, cost_price)
                VALUES ('ozon', %s, %s, 0)
                ON CONFLICT(source, sku) DO UPDATE SET
                    name = CASE
                        WHEN products.name LIKE 'ozon SKU%%' OR products.name = ''
                        THEN EXCLUDED.name
                        ELSE products.name
                    END
            """, (int(sku), name))
    conn.commit()
    log.info(f"Ozon каталог: {len(names)} товаров обновлено")


def load_ozon_stocks(conn):
    log.info("Ozon: остатки...")
    today, rows, offset, limit = date.today(), [], 0, 1000
    while True:
        data  = _post("/v2/analytics/stock_on_warehouses",
                      {"warehouse_type": "ALL", "limit": limit, "offset": offset})
        items = data.get("result", {}).get("rows", [])
        if not items:
            break
        for item in items:
            sku = item.get("sku")
            if sku:
                rows.append((int(sku), "ozon", item.get("warehouse_name", "Ozon"),
                             int(item.get("free_to_sell_amount", 0)), today))
                _ensure_ozon_product(conn, int(sku))
        offset += limit
        if len(items) < limit:
            break
        time.sleep(0.3)
    if rows:
        with conn.cursor() as cur:
            execute_values(cur, """
                INSERT INTO stocks(sku, source, warehouse_name, quantity, snapshot_date)
                VALUES %s ON CONFLICT(sku, source, warehouse_name, snapshot_date)
                DO UPDATE SET quantity = EXCLUDED.quantity
            """, rows)
        conn.commit()
        log.info(f"Ozon остатки: {len(rows)} строк")


def _ensure_ozon_product(conn, sku: int):
    """Добавляет заглушку для Ozon-товара если ещё нет в products."""
    with conn.cursor() as cur:
        cur.execute("""
            INSERT INTO products(source, sku, name, cost_price)
            VALUES ('ozon', %s, %s, 0)
            ON CONFLICT(source, sku) DO NOTHING
        """, (sku, f"ozon SKU {sku}"))
    conn.commit()


def load_ozon_orders(conn, days_back=7):
    log.info(f"Ozon: заказы за {days_back} дней...")
    rows = []
    end       = datetime.now()
    start     = end - timedelta(days=days_back)
    chunk     = timedelta(days=28)
    cur_start = start
    while cur_start < end:
        cur_end   = min(cur_start + chunk, end)
        date_from = cur_start.strftime("%Y-%m-%dT00:00:00.000Z")
        date_to   = cur_end.strftime("%Y-%m-%dT23:59:59.000Z")
        for schema in ("fbo", "fbs"):
            offset = 0
            while True:
                data = _post(f"/v3/posting/{schema}/list", {
                    "dir": "asc",
                    "filter": {"since": date_from, "to": date_to},
                    "limit": 100, "offset": offset
                })
                postings = data.get("result", {}).get("postings", [])
                if not postings:
                    break
                for p in postings:
                    for product in p.get("products", []):
                        sku = product.get("sku")
                        name = product.get("name", "")
                        if sku and name:
                            with conn.cursor() as cur:
                                cur.execute("""
                                    INSERT INTO products(source, sku, name, cost_price)
                                    VALUES ('ozon', %s, %s, 0)
                                    ON CONFLICT(source, sku) DO UPDATE SET
                                        name = CASE
                                            WHEN products.name LIKE 'ozon SKU%%' OR products.name = ''
                                            THEN EXCLUDED.name
                                            ELSE products.name
                                        END
                                """, (int(sku), name))
                        rows.append(("ozon",
                            f"{p['posting_number']}_{product.get('sku', '')}",
                            product.get("sku"), p.get("created_at"),
                            product.get("quantity", 1),
                            float(product.get("price", 0)),
                            p.get("status", "")))
                conn.commit()
                offset += len(postings)
                if len(postings) < 100:
                    break
                time.sleep(0.2)
        cur_start = cur_end + timedelta(seconds=1)
    if rows:
        with conn.cursor() as cur:
            execute_values(cur, """
                INSERT INTO orders(source, order_id, sku, order_date, quantity, price, status)
                VALUES %s ON CONFLICT(source, order_id) DO NOTHING
            """, rows)
        conn.commit()
        log.info(f"Ozon заказы: {len(rows)} строк")


def load_ozon_sales(conn, days_back=7):
    log.info(f"Ozon: продажи за {days_back} дней...")
    rows      = []
    end       = datetime.now()
    start     = end - timedelta(days=days_back)
    chunk     = timedelta(days=28)
    cur_start = start
    while cur_start < end:
        cur_end   = min(cur_start + chunk, end)
        date_from = cur_start.strftime("%Y-%m-%dT00:00:00.000Z")
        date_to   = cur_end.strftime("%Y-%m-%dT23:59:59.000Z")
        page = 1
        while True:
            data = _post("/v3/finance/transaction/list", {
                "filter": {"date": {"from": date_from, "to": date_to},
                           "operation_type": [], "posting_number": "",
                           "transaction_type": "all"},
                "page": page, "page_size": 1000
            })
            transactions = data.get("result", {}).get("operations", [])
            if not transactions:
                break
            for t in transactions:
                services   = t.get("services", [])
                commission = sum(abs(float(s.get("price", 0))) for s in services
                                 if "комиссия" in s.get("name", "").lower())
                logistics  = sum(abs(float(s.get("price", 0))) for s in services
                                 if any(k in s.get("name", "").lower()
                                        for k in ("логист", "доставк")))
                for item in t.get("items", []):
                    rows.append(("ozon", str(t.get("operation_id", "")),
                                 item.get("sku"), t.get("operation_date"),
                                 item.get("quantity", 1),
                                 float(t.get("amount", 0)),
                                 commission, logistics))
            page += 1
            if len(transactions) < 1000:
                break
            time.sleep(0.3)
        cur_start = cur_end + timedelta(seconds=1)
    if rows:
        with conn.cursor() as cur:
            execute_values(cur, """
                INSERT INTO sales(source, sale_id, sku, sale_date, quantity, revenue, commission, logistics)
                VALUES %s ON CONFLICT(source, sale_id) DO NOTHING
            """, rows)
        conn.commit()
        log.info(f"Ozon продажи: {len(rows)} строк")


def load_ozon_card_stats(conn, days_back=7):
    log.info(f"Ozon: карточки за {days_back} дней...")
    date_from = (datetime.now() - timedelta(days=days_back)).strftime("%Y-%m-%d")
    date_to   = datetime.now().strftime("%Y-%m-%d")
    rows, offset = [], 0
    while True:
        data = _post("/v1/analytics/data", {
            "date_from": date_from,
            "date_to": date_to,
            "dimension": ["sku", "day"],
            "metrics": ["hits_view", "hits_tocart", "session_view",
                        "conv_tocart", "ordered_units"],
            "limit": 1000,
            "offset": offset
        })
        items = data.get("result", {}).get("data", [])
        if not items:
            break
        for item in items:
            dims = {}
            for d in item.get("dimensions", []):
                if isinstance(d, dict):
                    dims[d.get("id", "")] = d.get("value", "")
            metrics = item.get("metrics", [0] * 5)
            sku_val = dims.get("sku", "")
            day_val = dims.get("day", "")
            if sku_val and day_val:
                try:
                    rows.append(("ozon", int(sku_val), day_val[:10],
                                 int(metrics[0]) if len(metrics) > 0 else 0,
                                 int(metrics[2]) if len(metrics) > 2 else 0,
                                 int(metrics[1]) if len(metrics) > 1 else 0,
                                 int(metrics[4]) if len(metrics) > 4 else 0))
                except (ValueError, TypeError):
                    pass
        offset += len(items)
        if len(items) < 1000:
            break
        time.sleep(0.3)
    if rows:
        with conn.cursor() as cur:
            execute_values(cur, """
                INSERT INTO card_stats(source, sku, stat_date, views, clicks, add_to_cart, orders_count)
                VALUES %s ON CONFLICT(source, sku, stat_date) DO UPDATE SET
                    views=EXCLUDED.views, clicks=EXCLUDED.clicks,
                    add_to_cart=EXCLUDED.add_to_cart, orders_count=EXCLUDED.orders_count
            """, rows)
        conn.commit()
        log.info(f"Ozon карточки: {len(rows)} строк")


def run(days_back=7):
    log.info("=== OZON ETL START ===")
    conn = get_conn()
    try:
        load_ozon_products(conn)
        load_ozon_stocks(conn)
        load_ozon_orders(conn, days_back)
        load_ozon_sales(conn, days_back)
        load_ozon_card_stats(conn, days_back)
        log.info("=== OZON ETL DONE ===")
    except Exception as e:
        log.error(f"OZON ETL ОШИБКА: {e}")
        conn.rollback()
        raise
    finally:
        conn.close()


if __name__ == "__main__":
    run()
