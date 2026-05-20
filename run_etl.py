"""
ETL Runner — WB + Ozon data sync

Usage:
  python run_etl.py              # WB + Ozon, last 7 days
  python run_etl.py --days 30   # historical data
  python run_etl.py --wb        # WB only
  python run_etl.py --ozon      # Ozon only

Cron (every day at 06:00):
  0 6 * * * cd /opt/analytics && venv/bin/python run_etl.py >> /var/log/analytics_etl.log 2>&1
"""
import argparse, logging, sys
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--wb",   action="store_true")
    p.add_argument("--ozon", action="store_true")
    p.add_argument("--days", type=int, default=7)
    args = p.parse_args()
    run_wb   = args.wb   or (not args.wb and not args.ozon)
    run_ozon = args.ozon or (not args.wb and not args.ozon)
    errors   = []
    if run_wb:
        try:
            import wb_etl; wb_etl.run(args.days)
            print("✅ WB готово")
        except Exception as e:
            print(f"❌ WB ошибка: {e}"); errors.append("WB")
    if run_ozon:
        try:
            import ozon_etl; ozon_etl.run(args.days)
            print("✅ Ozon готово")
        except Exception as e:
            print(f"❌ Ozon ошибка: {e}"); errors.append("Ozon")
    if errors:
        sys.exit(1)


if __name__ == "__main__":
    main()
