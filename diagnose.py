import sys
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="ignore")
import pandas as pd
from pathlib import Path
import psutil, os
sys.path.insert(0, str(Path(__file__).resolve().parent))
from config.settings import RAW_DIR as raw, ROOT_DIR
src = ROOT_DIR / "src" / "utils" / "rate_limiter.py"

print("=== RATE LIMITER ===")
print(src.read_text(encoding="utf-8"))

print("\n=== RAW FILES ===")
files = sorted(raw.glob("[A-Z]*.parquet"))
print(f"Total: {len(files)} files")
for f in files[:5] + files[-5:]:
    try:
        df = pd.read_parquet(f)
        lo = df["time"].min().date() if len(df) > 0 else "EMPTY"
        hi = df["time"].max().date() if len(df) > 0 else "EMPTY"
        print(f"  {f.stem:8s}: {len(df):5,} rows | {lo} -> {hi}")
    except Exception as e:
        print(f"  {f.stem:8s}: ERROR {e}")
