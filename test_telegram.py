# test_telegram.py
import sys
sys.stdout.reconfigure(encoding='utf-8', errors='ignore')

import pandas as pd
sys.path.insert(0, '.')
from config.settings import PROC_DIR
from src.notification.telegram_alert import (
    format_telegram_report, send_daily_alert
)

top10 = pd.read_parquet(PROC_DIR / 'top10_today.parquet')

if top10.empty:
    print("Top 10 hien tai rong. Them sample row de kiem tra format Telegram:")
    top10 = pd.DataFrame([
        {'ticker': 'OCB', 'sector': 'Ngân hàng', 'score': 2.0, 'signals_fired': 'ICHIMOKU_TREND', 'close': 11350, 'STOP_LOSS': 10790, 'TARGET_1': 11910, 'RISK_PCT': 4.9},
        {'ticker': 'CTS', 'sector': 'Dịch vụ tài chính', 'score': 2.0, 'signals_fired': 'ICHIMOKU_TREND', 'close': 28650, 'STOP_LOSS': 27100, 'TARGET_1': 30200, 'RISK_PCT': 5.4},
    ])

# Dry run — xem format trước
send_daily_alert(top10, dry_run=True)

# Gửi thật
send_daily_alert(top10, dry_run=False)
