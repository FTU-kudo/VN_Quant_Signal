# check_data_freshness.py
import sys
sys.stdout.reconfigure(encoding='utf-8', errors='ignore')
import pandas as pd
sys.path.insert(0, str(Path(__file__).resolve().parent))
from pathlib import Path
from datetime import date
from config.settings import RAW_DIR as raw, PROC_DIR as proc

today = date.today()
print(f"Hôm nay: {today}")

# 1. Kiểm tra ngày cuối cùng của OCB raw data
ocb = pd.read_parquet(raw / "OCB.parquet")
print(f"\n=== OCB RAW DATA ===")
print(f"Last date : {ocb['time'].max().date()}")
print(f"Last close: {ocb.sort_values('time')['close'].iloc[-1]:,.0f} VND")
print(f"Thiếu bao nhiêu ngày: {(today - ocb['time'].max().date()).days} ngày")
print(ocb.tail(5)[['time','close','volume']].to_string())

# 2. Kiểm tra features parquet
feat = pd.read_parquet(proc / "universe_features.parquet")
ocb_feat = feat[feat['ticker'] == 'OCB']
print(f"\n=== OCB FEATURES ===")
print(f"Last date : {ocb_feat['time'].max().date()}")
print(ocb_feat.tail(3)[['time','close','RSI_14','MACD_12_26_9','ADX_14']].to_string())

# 3. Kiểm tra toàn bộ universe
print(f"\n=== UNIVERSE FRESHNESS ===")
last_dates = feat.groupby('ticker')['time'].max()
print(f"Tickers có data đến hôm nay ({today}): {(last_dates.dt.date == today).sum()}")
print(f"Tickers có data đến hôm qua         : {(last_dates.dt.date.astype(str) == str(today)).sum()}")
print(f"Ngày cuối phổ biến nhất: {last_dates.dt.date.mode()[0]}")
print(f"Tickers bị stale (>3 ngày)          : {(last_dates.dt.date < today).sum()}")
