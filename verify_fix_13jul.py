import pandas as pd
from config.settings import PROC_DIR
from datetime import date
import sys
sys.stdout.reconfigure(encoding='utf-8', errors='ignore')

TARGET_DATE = '2026-07-13'

# 1. universe_features.parquet
feat = pd.read_parquet(PROC_DIR / 'universe_features.parquet')
feat_last = feat['time'].max()
feat_last_str = str(feat_last.date()) if hasattr(feat_last, 'date') else str(feat_last)[:10]
print(f"universe_features last: {feat_last_str}")
assert feat_last_str == TARGET_DATE, f"STILL STALE: {feat_last_str}"

# 2. daily_snapshot.parquet
snap = pd.read_parquet(PROC_DIR / 'daily_snapshot.parquet')
snap_last = snap['time'].max()
snap_last_str = str(snap_last.date()) if hasattr(snap_last, 'date') else str(snap_last)[:10]
print(f"daily_snapshot last: {snap_last_str}")
assert snap_last_str == TARGET_DATE

# 3. Giá đóng cửa đúng ngày 13/07
for ticker, expected_close in [
    ('CTS', 26_900), ('OCB', 10_850),
    ('ORS', 13_600), ('KLB', 12_350)
]:
    row = snap[snap['ticker'] == ticker].iloc[0]
    actual = row['close']
    print(f"{ticker}: close={actual:,.0f} "
          f"(expected ~{expected_close:,.0f}) "
          f"{'✅' if abs(actual - expected_close) < 500 else '❌'}")

# 4. Signal engine dùng đúng ngày
from src.strategies.signal_engine import run_signal_generation
top10 = run_signal_generation()
if len(top10) > 0:
    print(f"\nTop 10 giá check:")
    print(top10[['ticker','close','signals_fired']].to_string())
    # CTS phải là 26,900 không phải 28,650
    if 'CTS' in top10['ticker'].values:
        cts_close = top10[top10['ticker']=='CTS']['close'].iloc[0]
        print(f"\nCTS close trong signal: {cts_close:,.0f}")
        print(f"Đúng (13/07): 26,900 | Sai (09/07): 28,650")
        if abs(cts_close - 26900) < 500:
            print("✅ Fix hoạt động đúng")
        else:
            print("❌ Vẫn còn stale data")
else:
    print("\nTop 10 = 0 (có thể do giá giảm → signals bị filter)")
    print("Đây có thể là kết quả đúng nếu VNINDEX đang giảm mạnh")
