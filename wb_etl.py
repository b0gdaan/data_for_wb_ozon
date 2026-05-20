"""WB ETL — Wildberries data ingestion"""
import os, time, logging
from datetime import datetime, timedelta, date
import requests, psycopg2
from psycopg2.extras import execute_values
from dotenv import load_dotenv

load_dotenv()
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger(__name__)

WB_TOKEN     = os.getenv("WB_TOKEN")
DB_URL       = os.getenv("DATABASE_URL")
WB_MARKET    = "https://marketplace-api.wildberries.ru"
WB_STAT      = "https://statistics-api.wildberries.ru"
WB_CONTENT   = "https://content-api.wildberries.ru"
WB_ANALYTICS = "https://seller-analytics-api.wildberries.ru"


def get_conn():
    conn = psycopg2.connect(DB_URL)
    conn.autocommit = False
    return conn


def _get(base, path, params=None):
    headers = {"Authorization": WB_TOKEN, "Content-Type": "application/json"}
    for attempt in range(3):
        try:
            r = requests.get(base + path, headers=headers, params=params, timeout=30)
            if r.status_code == 429:
                log.warning("Rate limit WB, ждём 60 сек...")
                time.sleep(60); continue
            if not r.ok:
                log.error(f"GET {path} -> {r.status_code}: {r.text[:200]}")
                return None
            return r.json()
        except requests.RequestException as e:
            log.error(f"GET {path} попытка {attempt+1}/3: {e}")
            time.sleep(5 * (attempt + 1))
    return None


def _post(base, path, payload):
    headers = {"Authorization": WB_TOKEN, "Content-Type": "application/json"}
    for attempt in range(3):
        try:
            r = requests.post(base + path, headers=headers, json=payload, timeout=30)
            if r.status_code == 429:
                log.warning("Rate limit WB, ждём 60 сек...")
                time.sleep(60); continue
            if not r.ok:
                log.error(f"POST {path} -> {r.status_code}: {r.text[:200]}")
                return {}
            return r.json()
        except requests.RequestException as e:
            log.error(f"POST {path} попытка {attempt+1}/3: {e}")
            time.sleep(5 * (attempt + 1))
    return {}


def load_wb_products(conn):
    """Загружает каталог WB товаров (nmId + название) через Content API."""
    log.info("WB: загружаем каталог товаров...")
    products = []
    cursor = {}
    while True:
        payload = {
            "settings": {
                "cursor": {**cursor, "limit": 100},
                "filter": {"withPhoto": -1}
            }
        }
        data = _post(WB_CONTENT, "/content/v2/get/cards/list", payload)
        if not data:
            break
        cards = data.get("cards", [])
        if not cards:
            break
        for card in cards:
            nm_id = card.get("nmID")
            title = card.get("title", "") or card.get("subjectName", "")
            if nm_id and title:
                products.append((int(nm_id), title))
        cur_info = data.get("cursor", {})
        total = cur_info.get("total", 0)
        if total < 100:
            break
        cursor = {
            "updatedAt": cur_info.get("updatedAt", ""),
            "nmID": cur_info.get("nmID", 0)
        }
        time.sleep(0.3)

    if not products:
        log.warning("WB: каталог пуст или API недоступен")
        return

    with conn.cursor() as cur:
        for nm_id, name in products:
            cur.execute("""
                INSERT INTO products(source, sku, name, cost_price)
                VALUES ('wb', %s, %s, 0)
                ON CONFLICT(source, sku) DO UPDATE SET
                    name = CASE
                        WHEN products.name LIKE 'wb SKU%%' OR products.name = ''
                        THEN EXCLUDED.name
                        ELSE products.name
                    END
            """, (nm_id, name))
    conn.commit()
    log.info(f"WB каталог: {len(products)} товаров обновлено")


def load_stocks(conn):
    log.info("WB: загружаем остатки...")
    today = date.today()
    warehouses = _get(WB_MARKET, "/api/v3/warehouses") or []
    with conn.cursor() as cur:
        cur.execute("SELECT sku FROM products WHERE source = 'wb'")
        wb_skus = [str(row[0]) for row in cur.fetchall()]
    if not wb_skus:
        log.warning("WB: нет WB-товаров в products. Сначала запустите load_wb_products.")
        return

    rows = []
    batch_size = 1000
    for wh in warehouses:
        for i in range(0, len(wb_skus), batch_size):
            batch = wb_skus[i:i + batch_size]
            try:
                result = _post(WB_MARKET, f"/api/v3/stocks/{wh['id']}", {"skus": batch})
                stocks = result.get("stocks", [])
                for s in stocks:
                    qty = int(s.get("amount", 0))
                    if qty > 0:
                        rows.append((int(s["sku"]), "wb", wh["name"], qty, today))
            except Exception as e:
                log.error(f"WB склад {wh.get('name', '?')} batch {i}: {e}")
            time.sleep(0.2)

    if rows:
        with conn.cursor() as cur:
            execute_values(cur, """
                INSERT INTO stocks(sku, source, warehouse_name, quantity, snapshot_date)
                VALUES %s ON CONFLICT(sku, source, warehouse_name, snapshot_date)
                DO UPDATE SET quantity = EXCLUDED.quantity
            """, rows)
        conn.commit()
        log.info(f"WB остатки: {len(rows)} строк")
    else:
        log.info("WB остатки: 0 строк (нет товаров на складах)")


def load_orders(conn, days_back=7):
    log.info(f"WB: заказы за {days_back} дней...")
    date_from = (datetime.now() - timedelta(days=days_back)).strftime("%Y-%m-%dT00:00:00")
    data = _get(WB_STAT, "/api/v1/supplier/orders", {"dateFrom": date_from, "flag": 0})
    if not data:
        return
    rows = [("wb", str(o.get("orderId", o.get("srid", ""))),
             o.get("nmId"), o.get("date"),
             o.get("quantity", 1), o.get("totalPrice"), o.get("orderType", "new"))
            for o in data]
    if rows:
        with conn.cursor() as cur:
            execute_values(cur, """
                INSERT INTO orders(source, order_id, sku, order_date, quantity, price, status)
                VALUES %s ON CONFLICT(source, order_id) DO NOTHING
            """, rows)
        conn.commit()
        log.info(f"WB заказы: {len(rows)} строк")


def load_sales(conn, days_back=7):
    log.info(f"WB: продажи за {days_back} дней...")
    date_from = (datetime.now() - timedelta(days=days_back)).strftime("%Y-%m-%dT00:00:00")
    data = _get(WB_STAT, "/api/v1/supplier/sales", {"dateFrom": date_from, "flag": 0})
    if not data:
        return
    rows = []
    for s in data:
        if not str(s.get("saleID", "")).startswith("S"):
            continue  # только реальные продажи, не возвраты
        revenue    = float(s.get("priceWithDisc", 0) or 0)
        for_pay    = float(s.get("forPay", 0) or 0)
        logistics  = float(s.get("deliveryRub", 0) or 0)
        commission = max(0, revenue - for_pay - logistics)
        rows.append(("wb", str(s.get("saleID", "")), s.get("nmId"),
                     s.get("date"), s.get("quantity", 1), revenue, commission, logistics))
    if rows:
        with conn.cursor() as cur:
            execute_values(cur, """
                INSERT INTO sales(source, sale_id, sku, sale_date, quantity, revenue, commission, logistics)
                VALUES %s ON CONFLICT(source, sale_id) DO NOTHING
            """, rows)
        conn.commit()
        log.info(f"WB продажи: {len(rows)} строк")


def load_card_stats(conn, days_back=7):
    """WB аналитика карточек."""
    log.info(f"WB: статистика карточек за {days_back} дней...")
    date_from = (datetime.now() - timedelta(days=min(days_back, 90))).strftime("%Y-%m-%d 00:00:00")
    date_to   = datetime.now().strftime("%Y-%m-%d 23:59:59")
    with conn.cursor() as cur:
        cur.execute("SELECT sku FROM products WHERE source = 'wb' LIMIT 200")
        nm_ids = [row[0] for row in cur.fetchall()]
    if not nm_ids:
        log.info("WB карточки: нет WB товаров, пропуск")
        return
    rows = []
    for i in range(0, len(nm_ids), 20):
        batch = nm_ids[i:i + 20]
        try:
            data = _post(WB_ANALYTICS, "/api/v2/nm-report/detail",
                         {"nmIDs": batch,
                          "period": {"begin": date_from, "end": date_to},
                          "page": 1})
            if not data:
                data = _post(WB_ANALYTICS, "/api/v1/nm-report/detail",
                             {"nmIDs": batch,
                              "period": {"begin": date_from, "end": date_to},
                              "page": 1})
            for card in (data or {}).get("data", {}).get("cards", []):
                nm = card.get("nmID")
                for day in card.get("history", []):
                    rows.append(("wb", nm, day.get("dt", "")[:10],
                                 day.get("openCardCount", 0),
                                 day.get("addToCartCount", 0),
                                 day.get("addToCartCount", 0),
                                 day.get("ordersCount", 0)))
        except Exception as e:
            log.error(f"WB nm-report batch {i}: {e}")
        time.sleep(1)
    if rows:
        with conn.cursor() as cur:
            execute_values(cur, """
                INSERT INTO card_stats(source, sku, stat_date, views, clicks, add_to_cart, orders_count)
                VALUES %s ON CONFLICT(source, sku, stat_date) DO UPDATE SET
                    views=EXCLUDED.views, clicks=EXCLUDED.clicks,
                    add_to_cart=EXCLUDED.add_to_cart, orders_count=EXCLUDED.orders_count
            """, rows)
        conn.commit()
        log.info(f"WB карточки: {len(rows)} строк")
    else:
        log.info("WB карточки: нет данных (API может быть недоступен)")


def run(days_back=7):
    log.info("=== WB ETL START ===")
    conn = get_conn()
    try:
        load_wb_products(conn)
        load_stocks(conn)
        load_orders(conn, days_back)
        load_sales(conn, days_back)
        load_card_stats(conn, days_back)
        log.info("=== WB ETL DONE ===")
    except Exception as e:
        log.error(f"WB ETL ОШИБКА: {e}")
        conn.rollback()
        raise
    finally:
        conn.close()


if __name__ == "__main__":
    run()
