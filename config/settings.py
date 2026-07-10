"""
config/settings.py
──────────────────
Single source of truth for every threshold, path, and API parameter
used across the pipeline. Downstream modules never contain magic numbers.

Usage:
    from config.settings import RAW_DIR, MIN_AVG_VALUE_20D, ...
"""

from __future__ import annotations

import os
from datetime import date
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()  # loads .env for SMTP credentials

# ══════════════════════════════════════════════════════════════════════════════
# 1. Directory Layout
# ══════════════════════════════════════════════════════════════════════════════

ROOT_DIR  = Path(__file__).resolve().parent.parent
DATA_DIR  = ROOT_DIR / "data"
RAW_DIR   = DATA_DIR / "raw" / "ohlcv"      # one .parquet per ticker
PROC_DIR  = DATA_DIR / "processed"          # post-filter outputs
LOG_DIR   = ROOT_DIR / "logs"

# Auto-create at import time (idempotent)
for _d in [RAW_DIR, PROC_DIR, LOG_DIR]:
    _d.mkdir(parents=True, exist_ok=True)

# ══════════════════════════════════════════════════════════════════════════════
# 2. Data Ingestion
# ══════════════════════════════════════════════════════════════════════════════

HISTORY_START = "2016-01-01"
HISTORY_END   = date.today().strftime("%Y-%m-%d")

# vnstock data source: 'VCI' is the most stable for bulk historical pulls.
# Alternatives: 'TCBS' (faster for recent data), 'MSN' (limited history).
DATA_SOURCE = "VCI"
INTERVAL    = "1D"     # daily OHLCV

TARGET_EXCHANGES = ["HOSE", "HNX", "UPCOM"]   # all three Vietnamese exchanges

# ══════════════════════════════════════════════════════════════════════════════
# 3. Rate Limiter  (vnstock Sponsor Tier)
# ══════════════════════════════════════════════════════════════════════════════
# Sponsor Tier allows ~3 req/s sustained.  We stay conservative at 2/s to
# avoid occasional 429s during burst peaks on the VCI gateway.

# Auto-adjust rate limits and concurrency based on API Key tier
_api_key = (
    os.getenv("VNSTOCK_API_KEY")
    or os.getenv("VNSTOCK_TOKEN")
    or os.getenv("VNSTOCK_VCI_API_KEY")
    or os.getenv("FMP_API_KEY")
)

_tier = "free"
try:
    import vnstock
    import contextlib
    import io
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        if hasattr(vnstock, "change_api_key") and _api_key and _api_key != "your_api_key_here":
            vnstock.change_api_key(_api_key)
        if hasattr(vnstock, "check_status"):
            _status = vnstock.check_status()
            if isinstance(_status, dict):
                _tier = _status.get("tier", "free").lower()
except Exception:
    pass

if _tier in ("sponsor", "pro", "insiders", "paid") or (_api_key and "sponsor" in str(_api_key)):
    RATE_LIMIT_CALLS = 10  # 10 calls per second for Sponsor/Pro tier
    RATE_LIMIT_WINDOW = 1.0
    MAX_WORKERS = 8
else:
    # Free tier limit is max 60 calls/minute. We use strictly 1 worker and 1.2s window (~50 calls/min)
    # so that concurrency spikes can NEVER exceed 60 req/min.
    RATE_LIMIT_CALLS = 1
    RATE_LIMIT_WINDOW = 1.2
    MAX_WORKERS = 1

# ══════════════════════════════════════════════════════════════════════════════
# 4. Retry / Backoff
# ══════════════════════════════════════════════════════════════════════════════

MAX_RETRIES      = 5
RETRY_BASE_DELAY = 2.0    # seconds; doubles each attempt (exponential backoff)
RETRY_MAX_DELAY  = 60.0   # hard ceiling on sleep between retries

# ══════════════════════════════════════════════════════════════════════════════
# 5. Step-2 Liquidity & Quality Thresholds
# ══════════════════════════════════════════════════════════════════════════════
#
# ┌─────────────────────────────┬──────────────────────────┬────────────────────────────────────────────────────────────┐
# │ Screen                      │ Threshold                │ Rationale                                                  │
# ├─────────────────────────────┼──────────────────────────┼────────────────────────────────────────────────────────────┤
# │ Penny stock (price floor)   │ last_close ≥ 5,000 VND  │ Sub-5k names carry VSD delisting-warning risk (3 cons.     │
# │                             │                          │ loss years + low price). Also, the 10-pip tick size        │
# │                             │                          │ (500 VND on HOSE) means bid-ask spread is ≥10% of price.  │
# ├─────────────────────────────┼──────────────────────────┼────────────────────────────────────────────────────────────┤
# │ Liquidity (avg daily value) │ avg_value_20d            │ 5B VND daily turnover is the strict institutional          │
# │                             │ ≥ 5,000,000,000 VND      │ threshold where orders can be filled at minimal            │
# │                             │                          │ market impact. Below this, slippage destroys alpha.        │
# │                             │                          │ Value-based (close×volume) is better than raw volume:      │
# │                             │                          │ a 10,000-VND stock trading 1M shares ≠ a 100,000-VND      │
# │                             │                          │ stock trading 100k shares in liquidity terms.              │
# ├─────────────────────────────┼──────────────────────────┼────────────────────────────────────────────────────────────┤
# │ Trading continuity          │ ≥ 75% of market days in  │ Gaps in trading indicate: (a) trading suspension under    │
# │                             │ last 60 market days       │ SSC investigation, (b) a stock so illiquid it often has  │
# │                             │                          │ zero bids. Either way, our signals cannot be acted on.    │
# │                             │                          │ 75% is lenient enough to survive the ~5-day Tet closure.  │
# ├─────────────────────────────┼──────────────────────────┼────────────────────────────────────────────────────────────┤
# │ Sufficient history          │ ≥ 252 bars               │ Minimum bars for statistically valid indicator computation: │
# │                             │                          │ Ichimoku Senkou B = 52-bar period, MACD slowest = 26 bars. │
# │                             │                          │ Backtesting requires at minimum 1 year of out-of-sample    │
# │                             │                          │ data (Steps 4–5). Tickers IPO'd after 2025 are excluded.  │
# └─────────────────────────────┴──────────────────────────┴────────────────────────────────────────────────────────────┘

MIN_CLOSE_PRICE         = 5_000           # VND
MIN_AVG_VALUE_20D       = 5_000_000_000   # VND (5 Billion VND daily trading value)
MIN_AVG_VOLUME_20D      = 100_000         # shares (100k shares daily trading volume)
MIN_ADV_20              = MIN_AVG_VOLUME_20D
MIN_TRADING_DAYS_RATIO  = 0.75            # fraction of market days
MIN_HISTORY_BARS        = 252             # calendar days ≈ 1 year daily
LIQUIDITY_LOOKBACK      = 60              # market days for continuity check

# ══════════════════════════════════════════════════════════════════════════════
# 6. Email Notification (Step 6)
# ══════════════════════════════════════════════════════════════════════════════
# Store credentials in .env — never commit to git.
# Example .env:
#   SMTP_HOST=smtp.gmail.com
#   SMTP_PORT=587
#   SMTP_USER=your@gmail.com
#   SMTP_PASS=app_password_here
#   REPORT_RECIPIENTS=analyst@yuanta.com,risk@yuanta.com

SMTP_HOST          = os.getenv("SMTP_HOST", "smtp.gmail.com")
SMTP_PORT          = int(os.getenv("SMTP_PORT", "587"))
SMTP_USER          = os.getenv("SMTP_USER", "")
SMTP_PASS          = os.getenv("SMTP_PASS", "")
REPORT_RECIPIENTS  = [
    r.strip()
    for r in os.getenv("REPORT_RECIPIENTS", "").split(",")
    if r.strip()
]
