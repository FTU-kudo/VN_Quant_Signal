"""
src/data/filters.py
────────────────────
Step 2: Liquidity & Quality Filtering

Reduces the raw ~1,700-ticker universe to an investable set by applying
four quantitative screens. All heavy computations are vectorized via
groupby().transform() and groupby().agg() — zero row-level Python loops
on the time-series data.

Screen summary
──────────────
  1. Penny stock     → last_close  ≥ 5,000 VND
  2. Liquidity       → avg_value_20d ≥ 5B VND     (close × volume, 20-day MA)
  3. Trading density → recent_active_ratio ≥ 0.75  (active days / market days)
  4. History length  → n_bars ≥ 252

Outputs
───────
  data/processed/universe_filtered.parquet  — filtered OHLCV, long-format
  data/processed/rejected_tickers.parquet  — rejection audit log

Typical result (Vietnamese market, 2024):
  ~900–1,100 tickers pass out of ~1,700 listed.
  Rejection breakdown: ~300 low-liquidity, ~150 penny, ~100 thin-history,
  ~100 low-continuity.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional, Tuple

import numpy as np
import pandas as pd

from config.settings import (
    LIQUIDITY_LOOKBACK,
    LOG_DIR,
    MIN_AVG_VALUE_20D,
    MIN_AVG_VOLUME_20D,
    MIN_CLOSE_PRICE,
    MIN_HISTORY_BARS,
    MIN_TRADING_DAYS_RATIO,
    PROC_DIR,
)

log = logging.getLogger("filters")

# ─────────────────────────────────────────────────────────────────────────────
# 1. Compute liquidity metrics (vectorized)
# ─────────────────────────────────────────────────────────────────────────────

def compute_liquidity_metrics(df: pd.DataFrame) -> pd.DataFrame:
    """
    Append per-bar and per-ticker liquidity metrics to the long-format DataFrame.

    New columns added
    ─────────────────
    trading_value : float
        Daily turnover in VND = close × volume.
        More informative than raw volume because it equalizes stocks at
        very different price levels (e.g. BID at 50k vs FLC at 8k).

    avg_value_20d : float
        20-session rolling mean of trading_value, per ticker.

    avg_volume_20d : float
        20-session rolling mean of volume, per ticker.

    last_close : float  (per-ticker scalar, broadcast to all rows)
        Most recent observed close price.

    n_bars : int  (per-ticker scalar)
        Total number of daily bars available for this ticker.

    recent_active_days : int  (per-ticker scalar)
        Number of bars the ticker has in the last LIQUIDITY_LOOKBACK
        market days (the window over which we assess trading continuity).
    """
    if df.empty or df["time"].isna().all():
        return df.copy()

    df = df.sort_values(["ticker", "time"]).copy().reset_index(drop=True)

    # ── Daily turnover ────────────────────────────────────────────────────────
    df["trading_value"] = df["close"] * df["volume"]

    # ── 20-session rolling average value (per ticker) ─────────────────────────
    # Using transform so the result aligns with the original DataFrame index.
    df["avg_value_20d"] = (
        df.groupby("ticker", observed=True)["trading_value"]
          .transform(lambda s: s.rolling(20, min_periods=10).mean())
    )

    # ── 20-session rolling average volume (per ticker) ────────────────────────
    df["avg_volume_20d"] = (
        df.groupby("ticker", observed=True)["volume"]
          .transform(lambda s: s.rolling(20, min_periods=10).mean())
    )

    # ── Per-ticker scalar stats ───────────────────────────────────────────────
    # Compute via agg (fast), then merge back once.
    ticker_agg = (
        df.groupby("ticker", observed=True)
          .agg(
              last_close=("close", "last"),    # latest available close
              n_bars    =("time",  "count"),   # total bar count
          )
          .reset_index()
    )

    # Bars in the recent window (LIQUIDITY_LOOKBACK market days back)
    # We use calendar days × 1.5 as a buffer for weekends/holidays.
    max_date    = df["time"].max()
    cutoff_date = max_date - pd.Timedelta(days=int(LIQUIDITY_LOOKBACK * 1.5))

    recent_agg = (
        df[df["time"] >= cutoff_date]
          .groupby("ticker", observed=True)["time"]
          .count()
          .rename("recent_active_days")
          .reset_index()
    )

    ticker_stats = ticker_agg.merge(recent_agg, on="ticker", how="left")
    ticker_stats["recent_active_days"] = (
        ticker_stats["recent_active_days"].fillna(0).astype(int)
    )

    # Merge scalar stats back onto the row-level DataFrame
    df = df.merge(ticker_stats, on="ticker", how="left")

    return df


# ─────────────────────────────────────────────────────────────────────────────
# 2. Market-calendar expected days (denominator for continuity ratio)
# ─────────────────────────────────────────────────────────────────────────────

def _expected_market_days(df: pd.DataFrame) -> int:
    """
    Count the number of distinct trading dates in the universe that fall
    within the last LIQUIDITY_LOOKBACK calendar window.

    We use the union of all dates across all tickers as a proxy for the
    market calendar (avoids dependency on an external holiday schedule).
    A date present in ANY ticker's data is treated as a valid trading day.

    This count becomes the denominator for MIN_TRADING_DAYS_RATIO.
    """
    if df.empty or df["time"].isna().all():
        return 0
    max_date    = df["time"].max()
    cutoff_date = max_date - pd.Timedelta(days=int(LIQUIDITY_LOOKBACK * 1.5))
    n_days      = df.loc[df["time"] >= cutoff_date, "time"].nunique()
    return int(n_days)


# ─────────────────────────────────────────────────────────────────────────────
# 3. Apply filters → split passed / rejected
# ─────────────────────────────────────────────────────────────────────────────

def apply_quality_filters(
    df: pd.DataFrame,
) -> Tuple[pd.DataFrame, pd.DataFrame]:
    """
    Apply all four quality/liquidity screens to the enriched DataFrame.

    Parameters
    ----------
    df : pd.DataFrame
        Long-format OHLCV DataFrame (output of compute_liquidity_metrics
        or the raw load). If metrics columns are absent they are computed here.

    Returns
    -------
    passed_df : pd.DataFrame
        Full OHLCV + metrics rows for tickers that pass all four screens.
        Ready for feature engineering (Step 3).

    rejected_summary : pd.DataFrame
        One row per rejected ticker with columns:
        [ticker, last_close, avg_value_20d, n_bars,
         recent_active_days, rejection_reason]
        Persisted to processed/rejected_tickers.parquet.

    Logic notes
    ───────────
    • We build a per-ticker snapshot (one row per ticker) for the filter
      comparisons. Masks are applied on that small DataFrame (~1,700 rows),
      not on the 6M-row time-series — avoids duplicated computation.
    • Rejection reason assignment uses a priority waterfall:
        price → liquidity → continuity → history
      The first failing screen is recorded. This matches common quant
      practice of stopping at the first disqualifying factor.
    """
    # Compute metrics if not already present
    if "avg_value_20d" not in df.columns:
        df = compute_liquidity_metrics(df)

    # ── Per-ticker snapshot ───────────────────────────────────────────────────
    # One row per ticker with the latest scalar values.
    snapshot = (
        df.sort_values("time")
          .groupby("ticker", observed=True)
          .agg(
              last_close          =("last_close",          "last"),
              avg_value_20d       =("avg_value_20d",       "last"),
              n_bars              =("n_bars",               "last"),
              recent_active_days  =("recent_active_days",  "last"),
          )
          .reset_index()
    )

    # ── Expected trading days in the lookback window ──────────────────────────
    expected_days     = _expected_market_days(df)
    min_active_days   = int(expected_days * MIN_TRADING_DAYS_RATIO)

    log.info(
        f"Filter thresholds: price≥{MIN_CLOSE_PRICE:,.0f} VND | "
        f"avg_value≥{MIN_AVG_VALUE_20D:,.0f} VND | "
        f"active_days≥{min_active_days}/{expected_days} | bars≥{MIN_HISTORY_BARS}"
    )

    # ── Boolean masks (vectorized on snapshot) ────────────────────────────────
    mask_price      = snapshot["last_close"]         >= MIN_CLOSE_PRICE
    mask_liquidity  = snapshot["avg_value_20d"]      >= MIN_AVG_VALUE_20D
    mask_continuity = snapshot["recent_active_days"] >= min_active_days
    mask_history    = snapshot["n_bars"]             >= MIN_HISTORY_BARS

    mask_all = mask_price & mask_liquidity & mask_continuity & mask_history

    # ── Rejection reason — priority waterfall ─────────────────────────────────
    # Vectorized via np.select (faster than apply on 1700 rows, and avoids
    # the hidden Python loop overhead of pandas .apply())
    conditions = [
        ~mask_price,
        ~mask_liquidity,
        ~mask_continuity,
        ~mask_history,
    ]
    choices = [
        "penny_stock",
        "low_liquidity",
        "low_trading_continuity",
        "insufficient_history",
    ]
    snapshot["rejection_reason"] = np.select(conditions, choices, default="passed")

    # ── Split universe ────────────────────────────────────────────────────────
    passed_tickers = snapshot.loc[mask_all, "ticker"].tolist()

    rejected_summary = (
        snapshot.loc[~mask_all]
        [[
            "ticker", "last_close", "avg_value_20d",
            "n_bars", "recent_active_days", "rejection_reason",
        ]]
        .copy()
        .sort_values("rejection_reason")
        .reset_index(drop=True)
    )

    passed_df = df[df["ticker"].isin(passed_tickers)].copy()

    # ── Summary logging ───────────────────────────────────────────────────────
    total     = len(snapshot)
    n_passed  = len(passed_tickers)
    n_rejected = total - n_passed

    log.info(
        "Quality filter result: %d / %d passed | %d rejected (%.1f%%)",
        n_passed, total, n_rejected, 100.0 * n_rejected / total if total else 0,
    )
    log.info(
        "Rejection breakdown:\n%s",
        rejected_summary["rejection_reason"].value_counts().to_string(),
    )

    return passed_df, rejected_summary


def optimize_dtypes(df: pd.DataFrame) -> pd.DataFrame:
    """
    Downcast dtypes để giảm RAM ~50%.
    float64 -> float32
    int64 -> int32
    ticker -> category
    """
    if 'ticker' in df.columns:
        df['ticker'] = df['ticker'].astype('category')

    float_cols = df.select_dtypes(include='float64').columns
    if len(float_cols) > 0:
        df[float_cols] = df[float_cols].astype('float32')

    int_cols = df.select_dtypes(include='int64').columns
    if len(int_cols) > 0:
        df[int_cols] = df[int_cols].astype('int32')

    return df


# ─────────────────────────────────────────────────────────────────────────────
# 4. Persist outputs
# ─────────────────────────────────────────────────────────────────────────────

def save_filtered_universe(
    passed_df:        pd.DataFrame,
    rejected_summary: pd.DataFrame,
) -> Path:
    """
    Write the filtered OHLCV universe and rejection report to parquet.

    Files written
    ─────────────
    processed/universe_filtered.parquet
        Full OHLCV + metrics time-series for tickers that passed.
        Downstream steps (Feature Engineering, Backtesting) read this file.

    processed/rejected_tickers.parquet
        Rejection summary for audit / debugging.

    Returns
    -------
    Path  to universe_filtered.parquet.
    """
    filtered_path  = PROC_DIR / "universe_filtered.parquet"
    rejected_path  = PROC_DIR / "rejected_tickers.parquet"

    # Drop intermediate metric columns before storage to keep file lean.
    # Downstream steps will recompute what they need.
    drop_cols = [
        "trading_value", "avg_value_20d",
        "last_close", "n_bars", "recent_active_days",
    ]
    ohlcv_cols = ["ticker", "time", "open", "high", "low", "close", "volume"]
    cols_to_keep = [c for c in ohlcv_cols if c in passed_df.columns]

    tmp_filtered = filtered_path.with_suffix(".parquet.tmp")
    tmp_rejected = rejected_path.with_suffix(".parquet.tmp")

    passed_df = optimize_dtypes(passed_df)
    passed_df[cols_to_keep].to_parquet(
        tmp_filtered, index=False, compression="snappy"
    )
    tmp_filtered.replace(filtered_path)

    rejected_summary.to_parquet(
        tmp_rejected, index=False, compression="snappy"
    )
    tmp_rejected.replace(rejected_path)

    log.info(
        "Saved: universe_filtered.parquet (%d tickers, %d rows)",
        passed_df["ticker"].nunique(), len(passed_df),
    )
    log.info(
        "Saved: rejected_tickers.parquet  (%d tickers)",
        len(rejected_summary),
    )
    return filtered_path


# ─────────────────────────────────────────────────────────────────────────────
# 5. Pipeline entry point
# ─────────────────────────────────────────────────────────────────────────────

def run_filter_pipeline(
    df_raw: Optional[pd.DataFrame] = None,
) -> pd.DataFrame:
    """
    Full Step-2 pipeline entry point.

    Parameters
    ----------
    df_raw : pd.DataFrame, optional
        Pre-loaded long-format OHLCV DataFrame.
        If None, loads automatically from per-ticker parquets via
        src.data.ingestion.load_universe_parquets().

    Returns
    -------
    pd.DataFrame
        Filtered OHLCV DataFrame (same schema as input, subset of tickers).

    Typical runtime: ~15–30 seconds for 1,700 tickers × 10 years on a
    standard laptop (dominated by parquet I/O, not computation).
    """
    if df_raw is None:
        from src.data.ingestion import load_universe_parquets
        log.info("Loading universe from per-ticker parquets …")
        df_raw = load_universe_parquets()

    log.info(
        "Raw universe: %d tickers, %d rows, date range [%s → %s]",
        df_raw["ticker"].nunique(),
        len(df_raw),
        df_raw["time"].min().date(),
        df_raw["time"].max().date(),
    )

    # Step 1: enrich with metrics
    df_enriched = compute_liquidity_metrics(df_raw)

    # Step 2: apply screens
    passed_df, rejected_summary = apply_quality_filters(df_enriched)

    # Step 3: persist
    save_filtered_universe(passed_df, rejected_summary)

    return passed_df


# ─────────────────────────────────────────────────────────────────────────────
# 6. Utility: load filtered universe for downstream steps
# ─────────────────────────────────────────────────────────────────────────────

def load_filtered_universe(
    start_date: Optional[str] = None,
    end_date:   Optional[str] = None,
) -> pd.DataFrame:
    """
    Load the already-filtered universe tickers from processed/universe_filtered.parquet,
    and fetch their latest OHLCV data from raw parquets.
    Used by Steps 3–5 (Feature Engineering, Strategy, Backtesting).

    Raises FileNotFoundError if run_filter_pipeline() has not been called yet.
    """
    path = PROC_DIR / "universe_filtered.parquet"
    if not path.exists():
        raise FileNotFoundError(
            f"Filtered universe not found at {path}. "
            "Run run_filter_pipeline() (Step 2) first."
        )

    filtered_tickers = pd.read_parquet(path, columns=["ticker"])["ticker"].unique().tolist()
    from src.data.ingestion import load_universe_parquets
    df = load_universe_parquets(tickers=filtered_tickers, start_date=start_date, end_date=end_date)
    return df.reset_index(drop=True)
