import pandas as pd
from pathlib import Path

def main():
    p = Path(__file__).resolve().parent.parent / 'data' / 'processed' / 'market_regime.parquet'
    if not p.exists():
        print('market_regime.parquet MISSING at', p)
        return
    df = pd.read_parquet(p)
    pd.set_option('display.max_columns', None)
    print('--- last 10 rows of market_regime.parquet ---')
    print(df.tail(10).to_string(index=False))

if __name__ == '__main__':
    main()
