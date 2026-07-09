# diagnose_backtest_bug.py
import sys
sys.stdout.reconfigure(encoding='utf-8', errors='ignore')
import pandas as pd, numpy as np
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
from config.settings import PROC_DIR as proc
df   = pd.read_parquet(proc / "universe_signals.parquet")
df   = df.sort_values(['ticker','time']).reset_index(drop=True)

# Tìm tất cả SIG_ICHIMOKU signals và tính returns thủ công
signals = df[df['SIG_ICHIMOKU'] == True].copy()
print(f"Total SIG_ICHIMOKU signals: {len(signals)}")

# Với mỗi signal, lấy entry (open t+1) và exit (open t+6)
results = []
for _, row in signals.iterrows():
    ticker = row['ticker']
    t      = row.name
    ticker_df = df[df['ticker'] == ticker].reset_index(drop=True)
    pos = ticker_df[ticker_df.index == ticker_df[ticker_df['time'] == row['time']].index[0]].index[0] if len(ticker_df[ticker_df['time'] == row['time']]) > 0 else None
    if pos is None or pos + 6 >= len(ticker_df):
        results.append({'return': np.nan, 'reason': 'no_exit_bar'})
        continue
    entry = ticker_df.iloc[pos + 1]['open']
    exit_ = ticker_df.iloc[pos + 6]['open']
    if pd.isna(entry) or entry == 0:
        results.append({'return': np.nan, 'reason': 'nan_entry'})
    elif pd.isna(exit_) or exit_ == 0:
        results.append({'return': np.nan, 'reason': 'nan_exit'})
    else:
        ret = (exit_ - entry) / entry - 0.005
        results.append({'return': ret, 'reason': 'ok'})

res_df = pd.DataFrame(results)
print("\n=== RETURN DIAGNOSIS ===")
print(res_df['reason'].value_counts())
print(f"\nNaN returns  : {res_df['return'].isna().sum()}")
print(f"Returns < -50%: {(res_df['return'] < -0.5).sum()}")
print(f"Returns < -99%: {(res_df['return'] < -0.99).sum()}")
print(f"\nReturn distribution (valid trades):")
valid = res_df[res_df['reason'] == 'ok']['return']
print(valid.describe())
