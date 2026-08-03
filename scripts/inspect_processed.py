import pandas as pd
from pathlib import Path
import datetime


def main():
    p = Path(__file__).resolve().parent.parent / 'data' / 'processed'
    print('--- top10_today.parquet ---')
    top = p / 'top10_today.parquet'
    if top.exists():
        try:
            df = pd.read_parquet(top)
            if df.empty:
                print('Top10 is EMPTY')
            else:
                cols = [c for c in ['ticker', 'score', 'close', 'time', 'signals_fired'] if c in df.columns]
                print(df[cols].to_string(index=False))
        except Exception as e:
            print('Could not read top10:', e)
    else:
        print('MISSING')

    print('\n--- processed file mtimes ---')
    files = ['universe_features.parquet','daily_snapshot.parquet','top10_today.parquet','market_regime.parquet','universe_filtered.parquet']
    for f in files:
        fp = p / f
        if fp.exists():
            print(f, datetime.datetime.fromtimestamp(fp.stat().st_mtime))
        else:
            print(f, 'MISSING')


if __name__ == '__main__':
    main()
