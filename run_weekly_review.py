"""
run_weekly_review.py
Chạy mỗi thứ 6 lúc 16:30 (sau run_daily.py).
Tự động: update WIN/LOSS → tính metrics → gửi Telegram.
"""
import logging, sys
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="ignore")
from pathlib import Path
from datetime import date

sys.path.insert(0, str(Path(__file__).parent))
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler('logs/weekly_review.log',
                           encoding='utf-8'),
    ]
)
log = logging.getLogger('weekly')


def run():
    log.info("=== WEEKLY REVIEW START ===")
    
    from config.settings import PROC_DIR
    import pandas as pd
    from src.tracking.auto_updater import (
        update_signal_status,
        save_tracker,
        generate_weekly_report,
        format_weekly_telegram,
        TRACKER_PATH,
    )
    
    # Load tracker
    if not TRACKER_PATH.exists():
        log.info("Chưa có tracker — chưa có signals nào")
        return
    
    df_tracker = pd.read_parquet(TRACKER_PATH)
    log.info(f"Tracker: {len(df_tracker)} signals")
    
    # Load giá mới nhất từ daily snapshot
    snap_path = PROC_DIR / 'daily_snapshot.parquet'
    if not snap_path.exists():
        log.warning("Chưa có daily_snapshot — chạy run_daily.py trước")
        return
    
    df_prices = pd.read_parquet(snap_path)
    
    # Cập nhật WIN/LOSS tự động
    df_updated = update_signal_status(df_tracker, df_prices)
    save_tracker(df_updated)
    log.info("Tracker đã cập nhật")
    
    # Tính metrics và gửi Telegram
    metrics = generate_weekly_report(df_updated)
    message = format_weekly_telegram(metrics)
    
    # In ra terminal
    print("\n" + "="*50)
    print(message.replace('<b>','').replace('</b>','')
                 .replace('<i>','').replace('</i>',''))
    print("="*50)
    
    # Gửi Telegram
    from src.notification.telegram_alert import send_telegram_message
    send_telegram_message(message)
    
    log.info("=== WEEKLY REVIEW DONE ===")


if __name__ == '__main__':
    run()
