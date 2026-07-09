# verify_filter_logic.py
import sys
sys.stdout.reconfigure(encoding='utf-8', errors='ignore')
import pandas as pd
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
from config.settings import PROC_DIR as proc

# 1. Confirm cả 2 điều kiện AND đang hoạt động
df = pd.read_parquet(proc / "universe_filtered.parquet")
print(f"Tickers passed: {df['ticker'].nunique()}")

# 2. Verify threshold thực tế trong settings
from config.settings import MIN_AVG_VALUE_20D, MIN_CLOSE_PRICE
try:
    from config.settings import MIN_ADV_20
    print(f"MIN_ADV_20        : {MIN_ADV_20:,} shares")
except ImportError:
    print("MIN_ADV_20 : chưa có trong settings — cần kiểm tra")

print(f"MIN_AVG_VALUE_20D : {MIN_AVG_VALUE_20D:,} VND")
print(f"MIN_CLOSE_PRICE   : {MIN_CLOSE_PRICE:,} VND")

# 3. Spot-check: 5 tickers vừa pass filter — confirm cả 2 điều kiện đều đạt
from src.data.ingestion import load_universe_parquets
raw = load_universe_parquets(tickers=df['ticker'].unique()[:5].tolist())
raw['trading_value'] = raw['close'] * raw['volume']
check = (raw.groupby('ticker')
           .apply(lambda g: pd.Series({
               'ADTV_20' : g['trading_value'].tail(20).mean(),
               'ADV_20'  : g['volume'].tail(20).mean(),
               'last_close': g['close'].iloc[-1]
           }))
        )
check['ADTV_pass'] = check['ADTV_20'] >= 5_000_000_000
check['ADV_pass']  = check['ADV_20']  >= 100_000
print("\nSpot-check 5 tickers:")
print(check.round(0).to_string())
