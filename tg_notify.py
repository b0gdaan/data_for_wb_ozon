"""
Telegram bot — daily stock reorder alerts
Cron: 0 9 * * * cd /opt/app && venv/bin/python tg_notify.py >> /var/log/analytics_tg.log 2>&1
"""
import os
import psycopg2
import requests
from dotenv import load_dotenv

load_dotenv()


def notify():
    conn = psycopg2.connect(os.getenv("DATABASE_URL"))
    cur  = conn.cursor()
    cur.execute("""
        SELECT name, source, stock_qty,
               COALESCE(days_of_stock::TEXT, '?') AS days,
               status
        FROM v_reorder_list
        WHERE status IN ('СРОЧНО', 'ДОКУПИТЬ')
        ORDER BY
            CASE status WHEN 'СРОЧНО' THEN 0 ELSE 1 END,
            days_of_stock NULLS LAST
    """)
    rows = cur.fetchall()
    conn.close()

    if not rows:
        return  # все в норме, не спамим

    lines = ["📦 <b>Аналитика — Сводка по остаткам</b>\n"]
    for name, src, qty, days, status in rows:
        emoji = "🔴" if status == "СРОЧНО" else "⚠️"
        lines.append(f"{emoji} <b>{name}</b> ({src.upper()})")
        lines.append(f"   Остаток: {qty} шт · {days} дней")
        lines.append("")

    text = "\n".join(lines)
    requests.post(
        f"https://api.telegram.org/bot{os.getenv('TG_BOT_TOKEN')}/sendMessage",
        json={"chat_id": os.getenv("TG_CHAT_ID"),
              "text": text, "parse_mode": "HTML"},
        timeout=10
    )
    print(f"Отправлено {len(rows)} товаров в Telegram")


if __name__ == "__main__":
    notify()
