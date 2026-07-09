# verify_step3.py
import sys
sys.stdout.reconfigure(encoding='utf-8', errors='ignore')
import pandas as pd
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
from config.settings import PROC_DIR as proc
df   = pd.read_parquet(proc / "universe_features.parquet")

print("=== BASIC STATS ===")
print(f"Tickers : {df['ticker'].nunique()}")
print(f"Rows    : {len(df):,}")
print(f"Dates   : {df['time'].min().date()} -> {df['time'].max().date()}")

print("\n=== COLUMNS ===")
print(df.columns.tolist())

print("\n=== NaN % (kỳ vọng: RSI<5%, TENKAN<5%, BOS<1%) ===")
nan_pct = df.isnull().mean().mul(100).round(1)
print(nan_pct[nan_pct > 0].to_string())

print("\n=== LOOK-AHEAD BIAS CHECK (Ichimoku) ===")
# Senkou A/B phải có NaN ở CUỐI dataset (shifted forward 26 bars)
# Nếu không có NaN cuối = look-ahead bias
if 'SENKOU_A' in df.columns:
    last_ticker = df['ticker'].iloc[-1]
    tail = df[df['ticker']==last_ticker].tail(30)[['time','close','SENKOU_A','SENKOU_B']]
    print(tail.to_string())

print("\n=== SMC SIGNAL COUNTS ===")
for col in ['BOS_BULL','BOS_BEAR','CHOCH_BULL','CHOCH_BEAR','OB_BULL','OB_BEAR']:
    if col in df.columns:
        n = df[col].sum() if df[col].dtype == bool else (df[col] == True).sum()
        pct = n / len(df) * 100
        print(f"  {col:12s}: {n:6,} signals ({pct:.2f}%)")

print("\n=== SAMPLE ROW (1 ticker, latest 3 bars) ===")
sample = df[df['ticker'] == df['ticker'].iloc[100]]
print(sample[['ticker','time','close','RSI_14','MACD_12_26_9',
              'ADX_14','TENKAN_9','BOS_BULL','OB_BULL']].tail(3).to_string())
