# diagnose_step4.py
import sys
sys.stdout.reconfigure(encoding='utf-8', errors='ignore')
import pandas as pd
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
from config.settings import PROC_DIR as proc
df   = pd.read_parquet(proc / "universe_signals.parquet")

print("=== STRATEGY FIRE RATES (kỳ vọng mỗi cái > 0%) ===")
for col in ['SIG_SMC','SIG_ICHIMOKU','SIG_MOMENTUM','SIG_GOLDEN','SIG_OB']:
    if col in df.columns:
        n   = df[col].sum()
        pct = n / len(df) * 100
        print(f"  {col:15s}: {n:6,} fires ({pct:.2f}%)")
    else:
        print(f"  {col:15s}: COLUMN NOT FOUND ← bug")

print("\n=== KIỂM TRA SMC_REVERSAL CONDITIONS RIÊNG LẺ ===")
print(f"  CHOCH_BULL == True : {df['CHOCH_BULL'].sum():,}")
print(f"  RSI_14 < 40        : {(df['RSI_14'] < 40).sum():,}")
print(f"  OB_BULL == True    : {df['OB_BULL'].sum():,}")
print(f"  BB_LOWER condition : {(df['close'] <= df['BB_LOWER_20']).sum():,}")
macd_rising = df.groupby('ticker')['MACD_HIST'].diff() > 0
print(f"  MACD_HIST rising   : {macd_rising.sum():,}")
# All 4 conditions together
all4 = (df['CHOCH_BULL'] & 
        (df['RSI_14'] < 40) & 
        (df['OB_BULL'] | (df['close'] <= df['BB_LOWER_20'])) &
        macd_rising)
print(f"  ALL 4 conditions   : {all4.sum():,}  ← nếu 0 thì bug logic")

print("\n=== KIỂM TRA GOLDEN_CROSS CONDITIONS ===")
df_sorted = df.sort_values(['ticker','time'])
ma50_cross = (
    (df_sorted['MA_50'] > df_sorted['MA_200']) & 
    (df_sorted.groupby('ticker')['MA_50'].shift(1) <= 
     df_sorted.groupby('ticker')['MA_200'].shift(1))
)
print(f"  MA50 crosses MA200 : {ma50_cross.sum():,}  ← nếu 0 thì không có Golden Cross nào")
print(f"  MA_50 > MA_200 (bất kỳ ngày): {(df['MA_50'] > df['MA_200']).sum():,}")
