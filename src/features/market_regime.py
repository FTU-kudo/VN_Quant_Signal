"""
Market Regime Classification Module
=====================================
Phân loại chế độ thị trường chung (VNINDEX) theo ngày:
  - BULL   : VNINDEX > MA200 AND ADX(VNINDEX) > 20
  - SIDEWAY: VNINDEX > MA200 AND ADX(VNINDEX) <= 20
  - BEAR   : VNINDEX <= MA200
"""

from __future__ import annotations

from datetime import date
import logging
from pathlib import Path
from typing import Optional

import pandas as pd

from config.settings import DATA_SOURCE, HISTORY_START, PROC_DIR
from src.data.ingestion import _fetch_history as fetch_ohlcv_history
from src.features.indicators import compute_adx

log = logging.getLogger(__name__)


def compute_market_regime(
    start: str = HISTORY_START,
    end: Optional[str] = None,
    save_parquet: bool = True,
    force_recompute: bool = True
) -> pd.DataFrame:
    """
    Tính toán và phân loại Market Regime (BULL / SIDEWAY / BEAR) từ VNINDEX.
    """
    if end is None:
        end = date.today().strftime("%Y-%m-%d")

    log.info("Fetching VNINDEX OHLCV from %s to %s...", start, end)
    df = fetch_ohlcv_history("VNINDEX", start=start, end=end, source="VCI")
    if df is None or df.empty:
        df = fetch_ohlcv_history("VNINDEX", start=start, end=end, source="KBS")

    if df is None or df.empty:
        raise RuntimeError("Unable to fetch VNINDEX history to compute Market Regime.")

    df["ticker"] = "VNINDEX"
    df = df.sort_values("time").reset_index(drop=True)

    # Tính MA200
    df["vnindex_ma200"] = df["close"].rolling(200, min_periods=50).mean()

    # Tính ADX_14
    df_adx = compute_adx(df)
    df["vnindex_adx"] = df_adx["ADX_14"]

    # Phân loại chế độ thị trường (Regime)
    def classify_regime(row: pd.Series) -> str:
        close = row["close"]
        ma200 = row["vnindex_ma200"]
        adx = row["vnindex_adx"]

        if pd.isna(ma200):
            return "UNKNOWN"
        if close <= ma200:
            return "BEAR"
        if pd.isna(adx) or adx <= 20.0:
            return "SIDEWAY"
        return "BULL"

    df["regime"] = df.apply(classify_regime, axis=1)

    regime_df = pd.DataFrame({
        "date": df["time"].dt.date,
        "vnindex_close": df["close"],
        "vnindex_ma200": df["vnindex_ma200"],
        "vnindex_adx": df["vnindex_adx"],
        "regime": df["regime"]
    })

    if save_parquet:
        PROC_DIR.mkdir(parents=True, exist_ok=True)
        out_path = PROC_DIR / "market_regime.parquet"
        regime_df.to_parquet(out_path, index=False, compression="snappy")
        log.info("Market regime saved to %s (%d rows)", out_path, len(regime_df))

    return regime_df
