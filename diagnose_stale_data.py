import pandas as pd
from pathlib import Path
from config.settings import PROC_DIR, RAW_DIR
from datetime import date
import sys
sys.stdout.reconfigure(encoding='utf-8', errors='ignore')

print(f"Hôm nay: {date.today()}")

# 1. Kiểm tra raw data có fresh không
for ticker in ['CTS', 'OCB', 'ORS', 'KLB']:
    path = RAW_DIR / f'{ticker}.parquet'
    if path.exists():
        df = pd.read_parquet(path)
        last_date = df['time'].max()
        last_date_str = last_date.date() if hasattr(last_date, 'date') else last_date
        last_close = df.sort_values('time')['close'].iloc[-1]
        print(f"{ticker}: last_date={last_date_str} | close={last_close:,.0f}")
    else:
        print(f"{ticker}: FILE NOT FOUND")

# 2. Kiểm tra daily_snapshot & universe_features
snap_path = PROC_DIR / 'daily_snapshot.parquet'
if snap_path.exists():
    snap = pd.read_parquet(snap_path)
    val = snap['time'].max()
    dt_str = val.date() if hasattr(val, 'date') else val
    print(f"\nSnapshot last date: {dt_str}")
    print(f"Snapshot tickers: {snap['ticker'].nunique()}")
else:
    print("\nSnapshot file NOT FOUND")

feat_path = PROC_DIR / 'universe_features.parquet'
if feat_path.exists():
    fdf = pd.read_parquet(feat_path, columns=['time'])
    val = fdf['time'].max()
    dt_str = val.date() if hasattr(val, 'date') else val
    print(f"universe_features.parquet last date: {dt_str}")

# 3. Kiểm tra log ingestion
log_path = Path('logs/daily_run.log')
if log_path.exists():
    lines = log_path.read_text(encoding='utf-8', errors='ignore').splitlines()
    print("\n=== 20 dòng log cuối (13/07) ===")
    today_lines = [l for l in lines if '2026-07-13' in l]
    for l in today_lines[-20:]:
        print(l)
