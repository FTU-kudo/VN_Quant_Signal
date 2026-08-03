"""
src/data/ingestion.py
─────────────────────
Step 1: OHLCV data ingestion for the full Vietnamese equity universe
(HOSE + HNX + UPCOM, ~1,700 tickers, daily bars, 2016 → today).

Storage layout
──────────────
  data/raw/ohlcv/{TICKER}.parquet       ← one Snappy-compressed file per ticker
  data/raw/ohlcv/_ingestion_log.parquet ← run-level audit trail

Delta-load logic
────────────────
  First run  → fetch full history from HISTORY_START to today
  Later runs → read existing parquet → find max(time) → fetch only
               [max_date + 1 day, today] → concat → dedup → write back

  This means the second+ runs are O(days since last run × tickers),
  not O(10 years × tickers) — critical for a daily GitHub Actions job.

vnstock v3 API contract
───────────────────────
  from vnstock import Vnstock
  stock = Vnstock().stock(symbol='ACB', source='VCI')
  df    = stock.quote.history(start='YYYY-MM-DD', end='YYYY-MM-DD', interval='1D')
  # Returns DataFrame with columns: time, open, high, low, close, volume
  # 'time' column dtype: datetime64[ns] or object (we normalize on read)

  listing_api = Vnstock().stock(source='VCI').listing
  listing_df  = listing_api.all_symbols()
  # Returns DataFrame with at least: ticker (str), exchange (str)
  # Column 'symbol' may appear instead of 'ticker' — handled below.

  If the above raises AttributeError, try listing_api.symbols_by_exchange().
  Pin vnstock>=0.3.0 to ensure v3 API is active.
"""

from __future__ import annotations

import logging
import socket
socket.setdefaulttimeout(20.0)  # Prevent hanging TCP connections (e.g. 11-minute VCI freeze)
import sys
import time
import traceback

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="ignore")
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, timedelta
from pathlib import Path
from typing import Optional

import pandas as pd

from config.settings import (
    DATA_SOURCE,
    HISTORY_END,
    HISTORY_START,
    INTERVAL,
    LOG_DIR,
    MAX_RETRIES,
    MAX_WORKERS,
    RAW_DIR,
    RATE_LIMIT_CALLS,
    RATE_LIMIT_WINDOW,
    RETRY_BASE_DELAY,
    RETRY_MAX_DELAY,
    TARGET_EXCHANGES,
)
from src.utils.rate_limiter import SlidingWindowRateLimiter

# ── Logger ─────────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)-8s] %(name)s — %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(LOG_DIR / "ingestion.log", encoding="utf-8"),
    ],
)
log = logging.getLogger("ingestion")

# ── Module-level rate limiter singleton ───────────────────────────────────────
# Shared across all ThreadPoolExecutor worker threads — the Lock inside
# SlidingWindowRateLimiter ensures correct serialization.
_rate_limiter = SlidingWindowRateLimiter(RATE_LIMIT_CALLS, RATE_LIMIT_WINDOW)

# ─────────────────────────────────────────────────────────────────────────────
# 1. Universe listing
# ─────────────────────────────────────────────────────────────────────────────

def get_all_symbols(
    source: str = DATA_SOURCE,
    exchanges: list[str] = TARGET_EXCHANGES,
) -> pd.DataFrame:
    """
    Fetch the full Vietnamese equity listing via vnstock.

    Returns
    -------
    pd.DataFrame
        Columns: ['ticker', 'exchange']
        One row per unique ticker, filtered to the requested exchanges.

    Raises
    ------
    ValueError
        If the listing DataFrame is missing expected columns after
        all normalization attempts — indicates an API version mismatch.
    """
    from vnstock import Vnstock  # deferred: keeps module importable without vnstock

    listing_api = Vnstock(show_log=False).stock(symbol="VN30F1M", source=source).listing

    # Try the primary API method; fall back if 'exchange' column is missing or method not present
    df = None
    try:
        df = listing_api.all_symbols()
        if df is not None and isinstance(df, pd.DataFrame) and not df.empty:
            cols = [c.lower().strip() for c in df.columns]
            if "exchange" not in cols:
                df = None  # Force fallback to symbols_by_exchange()
    except Exception:
        df = None

    if df is None or not isinstance(df, pd.DataFrame) or df.empty:
        log.info("all_symbols() missing 'exchange' or failed; trying symbols_by_exchange()")
        try:
            df = listing_api.symbols_by_exchange()
        except Exception as e:
            raise RuntimeError(f"Failed to fetch stock listing from vnstock (source='{source}'): {e}") from e

    if df is None or not isinstance(df, pd.DataFrame) or df.empty:
        raise RuntimeError(f"Failed to fetch stock listing from vnstock (source='{source}'). Response is empty or invalid.")

    # ── Normalize column names ────────────────────────────────────────────────
    df.columns = [c.lower().strip() for c in df.columns]

    # vnstock sometimes returns 'symbol' instead of 'ticker'
    if "symbol" in df.columns and "ticker" not in df.columns:
        df = df.rename(columns={"symbol": "ticker"})

    # Verify required columns exist
    missing = {"ticker", "exchange"} - set(df.columns)
    if missing:
        raise ValueError(
            f"Listing API returned unexpected schema.\n"
            f"  Missing columns: {missing}\n"
            f"  Available:       {list(df.columns)}\n"
            f"  Check vnstock version (require >=0.3.0) and DATA_SOURCE='{source}'."
        )

    df["ticker"]   = df["ticker"].str.upper().str.strip()
    df["exchange"] = df["exchange"].str.upper().str.strip()

    # Filter out Corporate Bonds (len=9, e.g. LPB126018) and Covered Warrants (len=8, e.g. CFPT2529).
    # Normal equity stocks on HOSE/HNX/UPCOM have 3 (rarely 4) character symbols.
    df = df[df["ticker"].str.len() <= 4]

    if exchanges:
        df = df[df["exchange"].isin([e.upper() for e in exchanges])]

    df = df[["ticker", "exchange"]].drop_duplicates("ticker").reset_index(drop=True)
    log.info("Equity universe loaded: %d stocks across %s (filtered out bonds/warrants)", len(df), exchanges)
    return df


# ─────────────────────────────────────────────────────────────────────────────
# 2. Single-ticker fetch with retry + rate limiting
# ─────────────────────────────────────────────────────────────────────────────

_EXPECTED_OHLCV_COLS = {"open", "high", "low", "close", "volume"}

def _estimate_trading_days(start: str, end: str) -> int:
    """Ước tính số phiên giao dịch giữa 2 ngày (252/năm)."""
    days = (pd.Timestamp(end) - pd.Timestamp(start)).days
    return max(1, int(days * 252 / 365))


def _fetch_from_source(
    ticker: str,
    start:  str,
    end:    str,
    source: str,
) -> Optional[pd.DataFrame]:
    """Helper: fetch từ 1 source cụ thể với retry cơ bản, sử dụng vnstock.api v4."""
    from vnstock.api.quote import Quote

    if source == "TCBS":
        source = "KBS"

    delay = RETRY_BASE_DELAY
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            _rate_limiter.acquire()
            q = Quote(symbol=ticker, source=source)
            df = q.history(start=start, end=end, interval=INTERVAL)

            if df is None or not isinstance(df, pd.DataFrame) or df.empty:
                return None

            df.columns = [c.lower().strip() for c in df.columns]
            for date_col in ["date", "tradingdate", "trading_date", "datetime"]:
                if date_col in df.columns and "time" not in df.columns:
                    df = df.rename(columns={date_col: "time"})
                    break
            for vol_col in ["vol", "matchvolume", "match_volume"]:
                if vol_col in df.columns and "volume" not in df.columns:
                    df = df.rename(columns={vol_col: "volume"})
                    break

            missing_cols = _EXPECTED_OHLCV_COLS - set(df.columns)
            if missing_cols:
                return None

            df["time"]   = pd.to_datetime(df["time"], errors="coerce")
            df["open"]   = pd.to_numeric(df["open"],   errors="coerce")
            df["high"]   = pd.to_numeric(df["high"],   errors="coerce")
            df["low"]    = pd.to_numeric(df["low"],    errors="coerce")
            df["close"]  = pd.to_numeric(df["close"],  errors="coerce")
            df["volume"] = pd.to_numeric(df["volume"], errors="coerce").fillna(0).astype("int64")

            df = df.dropna(subset=["time", "close"])
            if not df.empty and df["close"].median() < 1000:
                for col in ["open", "high", "low", "close"]:
                    df[col] = df[col] * 1000.0

            df = df.sort_values("time").drop_duplicates("time").reset_index(drop=True)
            df.insert(0, "ticker", ticker)
            return df[["ticker", "time", "open", "high", "low", "close", "volume"]]
        except Exception as exc:
            if attempt == MAX_RETRIES:
                log.debug("[%s] _fetch_from_source(%s) error after %d retries: %s", ticker, source, MAX_RETRIES, exc)
                return None
            time.sleep(min(delay, RETRY_MAX_DELAY))
            delay *= 2.0
    return None


def _fetch_history(
    ticker: str,
    start:  str,
    end:    str,
    source: str = DATA_SOURCE,
) -> Optional[pd.DataFrame]:
    """
    Primary: KBS/TCBS (adjusted price)
    Fallback: VCI nếu TCBS/KBS không đủ history
    """
    df = _fetch_from_source(ticker, start, end, source=source)

    if df is not None and not df.empty:
        expected_bars = _estimate_trading_days(start, end)
        actual_bars   = len(df)
        coverage      = actual_bars / expected_bars

        if coverage >= 0.8:
            return df

        overlap_date = df["time"].min()
        tcbs_start   = overlap_date.strftime("%Y-%m-%d")
        vci_df       = _fetch_from_source(ticker, start, tcbs_start, source="VCI")

        if vci_df is not None and not vci_df.empty:
            tcbs_at_overlap = df[df["time"] == overlap_date]["close"].values
            vci_at_overlap  = vci_df[vci_df["time"] == overlap_date]["close"].values

            if len(tcbs_at_overlap) > 0 and len(vci_at_overlap) > 0 and vci_at_overlap[0] > 0:
                adj_factor = tcbs_at_overlap[0] / vci_at_overlap[0]
                vci_df["close"] = vci_df["close"] * adj_factor
                vci_df["open"]  = vci_df["open"]  * adj_factor
                vci_df["high"]  = vci_df["high"]  * adj_factor
                vci_df["low"]   = vci_df["low"]   * adj_factor

            combined = pd.concat(
                [vci_df[vci_df["time"] < overlap_date], df],
                ignore_index=True
            )
            return combined.sort_values("time").reset_index(drop=True)
        return df

    return _fetch_from_source(ticker, start, end, source="VCI")


# ─────────────────────────────────────────────────────────────────────────────
# 3. Parquet I/O helpers
# ─────────────────────────────────────────────────────────────────────────────

def _parquet_path(ticker: str) -> Path:
    """Return the canonical parquet path for a ticker."""
    return RAW_DIR / f"{ticker}.parquet"


def _read_existing(ticker: str) -> Optional[pd.DataFrame]:
    """
    Read existing per-ticker parquet.
    Returns None if the file is absent or unreadable (triggers full reload).
    """
    path = _parquet_path(ticker)
    if not path.exists():
        return None
    try:
        df = pd.read_parquet(path)
        df["time"] = pd.to_datetime(df["time"])
        return df
    except Exception as exc:
        log.warning("[%s] Unreadable parquet (%s) — will perform full reload.", ticker, exc)
        return None


def _write_parquet(ticker: str, df: pd.DataFrame) -> None:
    """
    Sort by time, drop exact duplicates, write to Snappy-compressed parquet.
    Overwrites the existing file atomically (write to temp → rename).
    """
    df = (
        df.sort_values("time")
          .drop_duplicates(subset=["ticker", "time"])
          .reset_index(drop=True)
    )

    tmp_path = _parquet_path(ticker).with_suffix(".parquet.tmp")
    df.to_parquet(tmp_path, index=False, compression="snappy")
    tmp_path.replace(_parquet_path(ticker))   # atomic overwrite on POSIX & Windows


# ─────────────────────────────────────────────────────────────────────────────
# 4. Delta-load logic for a single ticker
# ─────────────────────────────────────────────────────────────────────────────

def update_ticker(ticker: str) -> dict:
    """
    Perform a delta load for one ticker.

    Decision tree
    ─────────────
    ┌───────────────────────────────┬──────────────────────────────────────────┐
    │ Condition                     │ Action                                   │
    ├───────────────────────────────┼──────────────────────────────────────────┤
    │ Parquet absent                │ Fetch full history (HISTORY_START→today) │
    │ Parquet exists, up to date    │ Skip (return 'up_to_date')               │
    │ Parquet exists, stale         │ Fetch [max_date+1D, today] only          │
    └───────────────────────────────┴──────────────────────────────────────────┘

    Returns
    -------
    dict with keys: ticker, status, rows_added, total_rows, fetch_start, fetch_end
    """
    today     = date.today()
    today_str = today.strftime("%Y-%m-%d")
    existing  = _read_existing(ticker)

    if existing is not None and not existing.empty and not existing["time"].isna().all():
        max_date = existing["time"].max().date()

        if max_date >= today:
            # Luôn cập nhật lại nến của ngày hôm nay (today) để đảm bảo giá đóng cửa cuối phiên là chính xác nhất
            fetch_start = today_str
            existing    = existing[existing["time"].dt.date < today]
            is_full     = False
        else:
            fetch_start = (max_date + timedelta(days=1)).strftime("%Y-%m-%d")
            is_full     = False
    else:
        fetch_start = HISTORY_START
        is_full     = True

    fetch_end = today_str
    new_df    = _fetch_history(ticker, start=fetch_start, end=fetch_end)

    if new_df is None or new_df.empty:
        return {
            "ticker":    ticker,
            "status":    "fetch_failed" if is_full else "no_new_data",
            "rows_added": 0,
            "total_rows": len(existing) if existing is not None else 0,
            "fetch_start": fetch_start, "fetch_end": fetch_end,
        }

    combined = (
        pd.concat([existing, new_df], ignore_index=True)
        if existing is not None
        else new_df
    )

    _write_parquet(ticker, combined)

    return {
        "ticker":     ticker,
        "status":     "full_load" if is_full else "delta_updated",
        "rows_added": len(new_df),
        "total_rows": len(combined),
        "fetch_start": fetch_start,
        "fetch_end":   fetch_end,
    }


# ─────────────────────────────────────────────────────────────────────────────
# 5. Batch orchestrator — all tickers, parallel
# ─────────────────────────────────────────────────────────────────────────────

def run_ingestion(
    source:    str        = DATA_SOURCE,
    exchanges: list[str]  = TARGET_EXCHANGES,
    tickers:   Optional[list[str]] = None,
) -> pd.DataFrame:
    """
    Orchestrate a complete delta-load pass over the Vietnamese equity universe.

    Parameters
    ----------
    source : str
        vnstock data source (default: 'VCI').
    exchanges : list[str]
        Exchanges to include. Default: HOSE + HNX + UPCOM.
    tickers : list[str], optional
        Override: fetch only these tickers (useful for targeted reruns).

    Returns
    -------
    pd.DataFrame
        Run-level summary with columns:
        [ticker, exchange, status, rows_added, total_rows, fetch_start, fetch_end]
        Written to data/raw/ohlcv/_ingestion_log.parquet for audit.
    """
    log.info("═══ VN Quant Signal — Ingestion START (source=%s) ═══", source)

    # ── Determine universe ────────────────────────────────────────────────────
    listing = get_all_symbols(source=source, exchanges=exchanges)
    if tickers is not None:
        tickers_upper = [t.upper() for t in tickers]
        listing = listing[listing["ticker"].isin(tickers_upper)]

    ticker_list = listing["ticker"].tolist()
    log.info(
        "Processing %d tickers | exchanges=%s | workers=%d",
        len(ticker_list), exchanges, MAX_WORKERS,
    )

    # ── Parallel delta loads ──────────────────────────────────────────────────
    results      = []
    failed_list  = []

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
        future_map = {pool.submit(update_ticker, t): t for t in ticker_list}

        for i, future in enumerate(as_completed(future_map), 1):
            ticker = future_map[future]
            try:
                result = future.result()
                results.append(result)
            except Exception as exc:
                log.error("[%s] Unhandled worker exception: %s", ticker, exc)
                failed_list.append(ticker)
                results.append({
                    "ticker": ticker, "status": "worker_exception",
                    "rows_added": 0, "total_rows": 0,
                    "fetch_start": None, "fetch_end": None,
                })

            # Progress heartbeat every 50 tickers
            if i % 50 == 0 or i == len(ticker_list):
                n_done = sum(1 for r in results if r["status"] in
                             {"full_load", "delta_updated", "up_to_date"})
                log.info(
                    "  Progress: %d / %d | ok=%d | failed=%d",
                    i, len(ticker_list), n_done, len(failed_list),
                )

    # ── Build summary DataFrame ───────────────────────────────────────────────
    results_df = pd.DataFrame(results)
    summary    = listing.merge(results_df, on="ticker", how="left").fillna(
        {"status": "not_attempted", "rows_added": 0, "total_rows": 0}
    )

    # Status breakdown
    status_counts = summary["status"].value_counts()
    log.info("═══ Ingestion COMPLETE ═══\n%s", status_counts.to_string())

    # Persist audit log
    log_path = RAW_DIR / "_ingestion_log.parquet"
    try:
        existing_log = pd.read_parquet(log_path) if log_path.exists() else pd.DataFrame()
        summary["run_date"] = pd.Timestamp.today().normalize()
        audit_log = pd.concat([existing_log, summary], ignore_index=True)
        tmp_log = log_path.with_suffix(".parquet.tmp")
        audit_log.to_parquet(tmp_log, index=False, compression="snappy")
        tmp_log.replace(log_path)
        log.info("Audit log updated → %s", log_path)
    except Exception as exc:
        log.warning("Could not write audit log: %s", exc)

    return summary


# ─────────────────────────────────────────────────────────────────────────────
# 6. Utility: assemble multi-ticker long-format DataFrame from parquets
# ─────────────────────────────────────────────────────────────────────────────

def load_universe_parquets(
    tickers: Optional[list[str]] = None,
    start_date: Optional[str]    = None,
    end_date:   Optional[str]    = None,
) -> pd.DataFrame:
    """
    Load per-ticker parquets into a single long-format DataFrame.

    Parameters
    ----------
    tickers : list[str], optional
        Subset of tickers to load. None → load all parquets in RAW_DIR.
    start_date : str, optional
        Filter rows to time >= start_date ('YYYY-MM-DD').
    end_date : str, optional
        Filter rows to time <= end_date ('YYYY-MM-DD').

    Returns
    -------
    pd.DataFrame
        Columns: [ticker, time, open, high, low, close, volume]
        Sorted by [ticker, time]. Index is reset (0, 1, 2, …).

    Notes
    -----
    For 1,700 tickers × 10 years × 5 OHLCV columns ≈ 6–8M rows.
    Memory footprint with float32 ≈ 250–350 MB — acceptable for a standard
    machine. If RAM is a constraint, load in ticker batches using the
    `tickers` parameter.
    """
    if tickers is not None:
        paths = [_parquet_path(t) for t in tickers if _parquet_path(t).exists()]
    else:
        paths = [
            p for p in sorted(RAW_DIR.glob("*.parquet"))
            if p.name != "_ingestion_log.parquet" and not p.name.startswith("_")
        ]

    if not paths:
        raise FileNotFoundError(
            f"No OHLCV parquet files found in {RAW_DIR}. "
            "Run run_ingestion() first."
        )

    frames = []
    for p in paths:
        try:
            df = pd.read_parquet(p)
            if not df.empty and df["close"].median() < 1000:
                for col in ["open", "high", "low", "close"]:
                    df[col] = df[col] * 1000.0
            frames.append(df)
        except Exception as exc:
            log.warning("Skipping unreadable parquet %s: %s", p.name, exc)

    if not frames:
        raise RuntimeError("All parquet files failed to load.")

    combined = pd.concat(frames, ignore_index=True)
    combined["time"] = pd.to_datetime(combined["time"])

    # Optional date range filter
    if start_date:
        combined = combined[combined["time"] >= pd.Timestamp(start_date)]
    if end_date:
        combined = combined[combined["time"] <= pd.Timestamp(end_date)]

    combined = (
        combined
        .sort_values(["ticker", "time"])
        .reset_index(drop=True)
    )
    from src.data.filters import optimize_dtypes
    combined = optimize_dtypes(combined)
    return combined


def get_sector_mapping(source: str = 'VCI') -> pd.DataFrame:
    """
    Lấy mapping ticker -> ngành từ vnstock (hỗ trợ vnstock v4 Listing API).

    Returns DataFrame:
      ticker | sector | industry
    """
    from config.settings import PROC_DIR
    try:
        from vnstock.api.listing import Listing
        lst = Listing(source=source)
        df = lst.symbols_by_industries()

        if df is not None and not df.empty and 'icb_level' in df.columns:
            lvl2 = df[df['icb_level'] == 2].copy()
            rename_map = {'symbol': 'ticker', 'icb_name': 'sector'}
            lvl2 = lvl2.rename(columns=rename_map)
            lvl2['ticker'] = lvl2['ticker'].str.upper().str.strip()
            lvl2['industry'] = lvl2['sector']
            result = lvl2[['ticker', 'sector', 'industry']].drop_duplicates('ticker')
            PROC_DIR.mkdir(parents=True, exist_ok=True)
            result.to_parquet(PROC_DIR / 'sector_mapping.parquet', index=False)
            print(f"Sector mapping: {len(result)} tickers")
            print(result['sector'].value_counts().head(10))
            return result
    except Exception as e:
        log.warning("Could not load sector mapping via Listing API: %s", e)

    # Fallback rỗng nếu lỗi
    return pd.DataFrame(columns=['ticker', 'sector', 'industry'])

