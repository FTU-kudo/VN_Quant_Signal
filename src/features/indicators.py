"""
src/features/indicators.py
──────────────────────────
Step 3 — Feature Engineering (Technical Indicators & Smart Money Concepts).

This module ingests the filtered stock universe from Step 2 and enriches it with:
  - Group A: Standard Indicators (MA, EMA, RSI, MACD, Bollinger Bands) via pandas-ta.
  - Group B: Advanced Indicators (Ichimoku Cloud, ADX/DI with Wilder smoothing) via pure numpy/pandas.
  - Group C: Smart Money Concepts (Swing High/Low, BOS, CHoCH, Order Blocks).

Rules Adhered To:
  1. No full DataFrame for/while loops; groupby().transform() used for single-output indicators; explicit group loops only for multi-output indicators.
  2. Zero iterrows() / itertuples() on time-series data.
  3. Comprehensive Google-style docstrings with mathematical formulas.
  4. Parquet output with snappy compression.
  5. Cross-platform pathlib.Path usage.
  6. Absolute imports from project root.
  7. Naming convention: {INDICATOR}_{PARAM1}_{PARAM2}...
"""

from __future__ import annotations

import logging
from typing import Optional

import numpy as np
import pandas as pd
import pandas_ta as ta

from config.settings import PROC_DIR
from src.data.filters import load_filtered_universe

log = logging.getLogger("features")


def compute_standard_indicators(df: pd.DataFrame) -> pd.DataFrame:
    """
    Nhóm A: Tính toán các chỉ báo tiêu chuẩn bằng thư viện pandas-ta.

    Parameters
    ----------
    df : pd.DataFrame
        DataFrame chứa các cột OHLCV với index đã sắp xếp theo [ticker, time].

    Returns
    -------
    pd.DataFrame
        DataFrame gốc kèm các cột: MA_20, MA_50, MA_200, EMA_12, EMA_26, RSI_14,
        MACD_12_26_9, MACD_HIST, MACD_SIGNAL_9, BB_LOWER_20, BB_MID_20, BB_UPPER_20.

    Formulas
    --------
    - MA_n      : Simple Moving Average = (1/n) * sum_{i=0}^{n-1} close_{t-i}
    - EMA_n     : Exponential Moving Average = close_t * k + EMA_{t-1} * (1 - k), k = 2/(n+1)
    - RSI_14    : Relative Strength Index = 100 - (100 / (1 + RS)), RS = Average Gain / Average Loss
    - MACD      : Fast EMA_12 - Slow EMA_26; Signal = EMA_9(MACD); Hist = MACD - Signal
    - BBands    : Mid = MA_20; Upper/Lower = Mid ± 2 * StdDev_20
    """
    def _safe_ta(func, s, **kwargs):
        res = func(s, **kwargs)
        if res is None:
            return pd.Series(np.nan, index=s.index, dtype=float)
        return res

    # ── Single-output indicators via groupby().transform() ────────────────────
    df["MA_20"] = df.groupby("ticker", observed=True)["close"].transform(
        lambda s: _safe_ta(ta.sma, s, length=20)
    )
    df["MA_50"] = df.groupby("ticker", observed=True)["close"].transform(
        lambda s: _safe_ta(ta.sma, s, length=50)
    )
    df["MA_200"] = df.groupby("ticker", observed=True)["close"].transform(
        lambda s: _safe_ta(ta.sma, s, length=200)
    )
    df["EMA_12"] = df.groupby("ticker", observed=True)["close"].transform(
        lambda s: _safe_ta(ta.ema, s, length=12)
    )
    df["EMA_26"] = df.groupby("ticker", observed=True)["close"].transform(
        lambda s: _safe_ta(ta.ema, s, length=26)
    )
    df["RSI_14"] = df.groupby("ticker", observed=True)["close"].transform(
        lambda s: _safe_ta(ta.rsi, s, length=14)
    )

    # ── Multi-output indicators (MACD & Bollinger Bands) via explicit group loop ──
    macd_parts = []
    bb_parts = []

    for ticker, g in df.groupby("ticker", observed=True):
        # MACD (12, 26, 9) -> returns MACD_12_26_9, MACDh_12_26_9, MACDs_12_26_9
        macd_df = ta.macd(g["close"], fast=12, slow=26, signal=9)
        if macd_df is not None and not macd_df.empty and macd_df.shape[1] >= 3:
            macd_df = macd_df.iloc[:, [0, 1, 2]].copy()
            macd_df.columns = ["MACD_12_26_9", "MACD_HIST", "MACD_SIGNAL_9"]
        else:
            macd_df = pd.DataFrame(
                np.nan,
                index=g.index,
                columns=["MACD_12_26_9", "MACD_HIST", "MACD_SIGNAL_9"],
            )
        macd_parts.append(macd_df)

        # Bollinger Bands (length=20, std=2) -> returns BBL, BBM, BBU, BBB, BBP
        bb_df = ta.bbands(g["close"], length=20, std=2)
        if bb_df is not None and not bb_df.empty and bb_df.shape[1] >= 3:
            bb_sub = bb_df.iloc[:, [0, 1, 2]].copy()
            bb_sub.columns = ["BB_LOWER_20", "BB_MID_20", "BB_UPPER_20"]
        else:
            bb_sub = pd.DataFrame(
                np.nan,
                index=g.index,
                columns=["BB_LOWER_20", "BB_MID_20", "BB_UPPER_20"],
            )
        bb_parts.append(bb_sub)

    macd_concat = pd.concat(macd_parts)
    bb_concat = pd.concat(bb_parts)

    for col in ["MACD_12_26_9", "MACD_HIST", "MACD_SIGNAL_9"]:
        df[col] = macd_concat[col]
    for col in ["BB_UPPER_20", "BB_MID_20", "BB_LOWER_20"]:
        df[col] = bb_concat[col]

    return df


def compute_ichimoku(df: pd.DataFrame) -> pd.DataFrame:
    """
    Nhóm B1: Ichimoku Cloud (chuẩn Nhật Bản, thuần pandas/numpy).

    Parameters
    ----------
    df : pd.DataFrame
        DataFrame chứa các cột OHLCV với index đã sắp xếp theo [ticker, time].

    Returns
    -------
    pd.DataFrame
        DataFrame gốc kèm 5 cột: TENKAN_9, KIJUN_26, SENKOU_A, SENKOU_B, CHIKOU_VIZ.

    Formulas
    --------
    - Tenkan-sen (9) : (highest_high_9 + lowest_low_9) / 2
    - Kijun-sen (26) : (highest_high_26 + lowest_low_26) / 2
    - Senkou Span A  : ((Tenkan + Kijun) / 2).shift(26)  [Shifted forward 26 bars]
    - Senkou Span B  : ((highest_high_52 + lowest_low_52) / 2).shift(26)
    - Chikou Span    : close.shift(-26)  [Shifted backward 26 bars for visualization]
    """
    parts = []
    for ticker, g in df.groupby("ticker", observed=True):
        high = g["high"]
        low = g["low"]
        close = g["close"]

        tenkan = (high.rolling(9).max() + low.rolling(9).min()) / 2.0
        kijun = (high.rolling(26).max() + low.rolling(26).min()) / 2.0
        senkou_a = ((tenkan + kijun) / 2.0).shift(26)
        senkou_b = ((high.rolling(52).max() + low.rolling(52).min()) / 2.0).shift(26)
        chikou = close.shift(-26)

        ich_df = pd.DataFrame(
            {
                "TENKAN_9": tenkan,
                "KIJUN_26": kijun,
                "SENKOU_A": senkou_a,
                "SENKOU_B": senkou_b,
                "CHIKOU_VIZ": chikou,
            },
            index=g.index,
        )
        parts.append(ich_df)

    res = pd.concat(parts)
    for col in ["TENKAN_9", "KIJUN_26", "SENKOU_A", "SENKOU_B", "CHIKOU_VIZ"]:
        df[col] = res[col]
    return df


def compute_adx(df: pd.DataFrame) -> pd.DataFrame:
    """
    Nhóm B2: ADX/DI với Wilder smoothing (thuần pandas/numpy).

    Parameters
    ----------
    df : pd.DataFrame
        DataFrame chứa các cột OHLCV với index đã sắp xếp theo [ticker, time].

    Returns
    -------
    pd.DataFrame
        DataFrame gốc kèm 4 cột: ADX_14, DI_PLUS_14, DI_MINUS_14, ATR_14.

    Formulas
    --------
    - TR      = max(high - low, |high - prev_close|, |low - prev_close|)
    - +DM     = high - prev_high nếu > 0 và > (prev_low - low), else 0
    - -DM     = prev_low - low nếu > 0 và > (high - prev_high), else 0
    - ATR_14  = TR.ewm(alpha=1/14, adjust=False).mean()
    - +DI_14  = 100 * +DM.ewm(alpha=1/14, adjust=False).mean() / ATR_14
    - -DI_14  = 100 * -DM.ewm(alpha=1/14, adjust=False).mean() / ATR_14
    - DX      = 100 * |+DI - -DI| / (+DI + -DI)
    - ADX_14  = DX.ewm(alpha=1/14, adjust=False).mean()
    """
    parts = []
    for ticker, g in df.groupby("ticker", observed=True):
        high = g["high"]
        low = g["low"]
        close = g["close"]
        prev_close = close.shift(1)
        prev_high = high.shift(1)
        prev_low = low.shift(1)

        tr1 = high - low
        tr2 = (high - prev_close).abs()
        tr3 = (low - prev_close).abs()
        tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)

        up_move = high - prev_high
        down_move = prev_low - low

        plus_dm = np.where((up_move > 0) & (up_move > down_move), up_move, 0.0)
        minus_dm = np.where((down_move > 0) & (down_move > up_move), down_move, 0.0)

        plus_dm = pd.Series(plus_dm, index=g.index)
        minus_dm = pd.Series(minus_dm, index=g.index)

        atr_14 = tr.ewm(alpha=1.0 / 14.0, adjust=False).mean()
        smooth_plus_dm = plus_dm.ewm(alpha=1.0 / 14.0, adjust=False).mean()
        smooth_minus_dm = minus_dm.ewm(alpha=1.0 / 14.0, adjust=False).mean()

        atr_val = atr_14.to_numpy()
        di_plus_14 = np.divide(
            100.0 * smooth_plus_dm.to_numpy(),
            atr_val,
            out=np.zeros_like(atr_val),
            where=(atr_val != 0),
        )
        di_minus_14 = np.divide(
            100.0 * smooth_minus_dm.to_numpy(),
            atr_val,
            out=np.zeros_like(atr_val),
            where=(atr_val != 0),
        )

        di_sum = di_plus_14 + di_minus_14
        dx_val = np.divide(
            100.0 * np.abs(di_plus_14 - di_minus_14),
            di_sum,
            out=np.zeros_like(di_sum),
            where=(di_sum != 0),
        )
        dx = pd.Series(dx_val, index=g.index)

        adx_14 = dx.ewm(alpha=1.0 / 14.0, adjust=False).mean()

        adx_df = pd.DataFrame(
            {
                "ADX_14": adx_14,
                "DI_PLUS_14": di_plus_14,
                "DI_MINUS_14": di_minus_14,
                "ATR_14": atr_14,
            },
            index=g.index,
        )
        parts.append(adx_df)

    res = pd.concat(parts)
    for col in ["ADX_14", "DI_PLUS_14", "DI_MINUS_14", "ATR_14"]:
        df[col] = res[col]
    return df


def compute_smc(df: pd.DataFrame) -> pd.DataFrame:
    """
    Nhóm C: Smart Money Concepts (SMC) — Swing High/Low, BOS, CHoCH, Order Block.

    Parameters
    ----------
    df : pd.DataFrame
        DataFrame chứa các cột OHLCV với index đã sắp xếp theo [ticker, time].

    Returns
    -------
    pd.DataFrame
        DataFrame gốc kèm 10 cột SMC:
        SWING_HIGH, SWING_LOW, BOS_BULL, BOS_BEAR, CHOCH_BULL, CHOCH_BEAR,
        OB_BULL, OB_BEAR, OB_HIGH, OB_LOW.

    Formulas
    --------
    - Swing High : high[i] > high[i-1] AND high[i] > high[i+1]
    - Swing Low  : low[i] < low[i-1] AND low[i] < low[i+1]
    - BOS / CHoCH: Khi giá phá vỡ mức Swing High/Low gần nhất theo chiều xu hướng (BOS)
                   hoặc ngược chiều xu hướng (CHoCH).
    - Order Block: Bullish OB = nến giảm cuối cùng trước ít nhất 3 nến tăng liên tiếp.
                   Bearish OB = nến tăng cuối cùng trước ít nhất 3 nến giảm liên tiếp.
    """
    parts = []
    for ticker, g in df.groupby("ticker", observed=True):
        high_arr = g["high"].to_numpy()
        low_arr = g["low"].to_numpy()
        open_arr = g["open"].to_numpy()
        close_arr = g["close"].to_numpy()
        n = len(high_arr)

        # C1. Swing High / Swing Low (vectorized shift logic)
        swing_high = np.zeros(n, dtype=bool)
        swing_low = np.zeros(n, dtype=bool)
        if n >= 3:
            swing_high[1:-1] = (high_arr[1:-1] > high_arr[:-2]) & (
                high_arr[1:-1] > high_arr[2:]
            )
            swing_low[1:-1] = (low_arr[1:-1] < low_arr[:-2]) & (
                low_arr[1:-1] < low_arr[2:]
            )

        # C2. BOS (Break of Structure) & CHoCH (Change of Character)
        bos_bull = np.zeros(n, dtype=bool)
        bos_bear = np.zeros(n, dtype=bool)
        choch_bull = np.zeros(n, dtype=bool)
        choch_bear = np.zeros(n, dtype=bool)

        last_sh = np.nan
        prev_sh = np.nan
        last_sl = np.nan
        prev_sl = np.nan

        sh_broken = False
        sl_broken = False
        trend = 0  # 1: uptrend (HH+HL), -1: downtrend (LH+LL), 0: neutral

        for i in range(1, n):
            # Swing confirmed at bar i-1
            if swing_high[i - 1]:
                prev_sh = last_sh
                last_sh = high_arr[i - 1]
                sh_broken = False
                if (
                    not np.isnan(prev_sh)
                    and not np.isnan(last_sh)
                    and not np.isnan(prev_sl)
                    and not np.isnan(last_sl)
                ):
                    if last_sh > prev_sh and last_sl > prev_sl:
                        trend = 1
                    elif last_sh < prev_sh and last_sl < prev_sl:
                        trend = -1

            if swing_low[i - 1]:
                prev_sl = last_sl
                last_sl = low_arr[i - 1]
                sl_broken = False
                if (
                    not np.isnan(prev_sh)
                    and not np.isnan(last_sh)
                    and not np.isnan(prev_sl)
                    and not np.isnan(last_sl)
                ):
                    if last_sh > prev_sh and last_sl > prev_sl:
                        trend = 1
                    elif last_sh < prev_sh and last_sl < prev_sl:
                        trend = -1

            # Check structure breaks at bar i
            if not np.isnan(last_sh) and not sh_broken and high_arr[i] > last_sh:
                sh_broken = True
                if trend == 1:
                    bos_bull[i] = True
                elif trend == -1:
                    choch_bull[i] = True
                elif trend == 0:
                    bos_bull[i] = True

            if not np.isnan(last_sl) and not sl_broken and low_arr[i] < last_sl:
                sl_broken = True
                if trend == -1:
                    bos_bear[i] = True
                elif trend == 1:
                    choch_bear[i] = True
                elif trend == 0:
                    bos_bear[i] = True

        # C3. Order Block (OB) — vectorized slice matching
        ob_bull = np.zeros(n, dtype=bool)
        ob_bear = np.zeros(n, dtype=bool)
        ob_high = np.full(n, np.nan, dtype=float)
        ob_low = np.full(n, np.nan, dtype=float)

        if n >= 4:
            is_bear = close_arr < open_arr
            is_bull = close_arr > open_arr
            ob_bull[:-3] = (
                is_bear[:-3] & is_bull[1:-2] & is_bull[2:-1] & is_bull[3:]
            )
            ob_bear[:-3] = (
                is_bull[:-3] & is_bear[1:-2] & is_bear[2:-1] & is_bear[3:]
            )
            ob_high[ob_bull | ob_bear] = high_arr[ob_bull | ob_bear]
            ob_low[ob_bull | ob_bear] = low_arr[ob_bull | ob_bear]

        smc_df = pd.DataFrame(
            {
                "SWING_HIGH": swing_high,
                "SWING_LOW": swing_low,
                "BOS_BULL": bos_bull,
                "BOS_BEAR": bos_bear,
                "CHOCH_BULL": choch_bull,
                "CHOCH_BEAR": choch_bear,
                "OB_BULL": ob_bull,
                "OB_BEAR": ob_bear,
                "OB_HIGH": ob_high,
                "OB_LOW": ob_low,
            },
            index=g.index,
        )
        parts.append(smc_df)

    res = pd.concat(parts)
    for col in [
        "SWING_HIGH",
        "SWING_LOW",
        "BOS_BULL",
        "BOS_BEAR",
        "CHOCH_BULL",
        "CHOCH_BEAR",
        "OB_BULL",
        "OB_BEAR",
        "OB_HIGH",
        "OB_LOW",
    ]:
        df[col] = res[col]
    return df


def run_feature_engineering(
    df: Optional[pd.DataFrame] = None,
    mode: str = 'full',      # 'full' = toàn bộ history | 'incremental' = chỉ N bars cuối
    lookback_bars: int = 260 # đủ cho indicator dài nhất (MA_200 + buffer)
) -> pd.DataFrame:
    """
    mode='full'        : Chạy lần đầu hoặc khi rebuild toàn bộ dataset
    mode='incremental' : Chạy daily — chỉ load 260 bars cuối mỗi ticker,
                         tính indicators, lấy bar cuối cùng làm snapshot
    """
    if df is None:
        log.info("Loading filtered universe from Step 2...")
        df_full = load_filtered_universe()
        if mode == 'incremental':
            df = (df_full.groupby('ticker', observed=True)
                         .tail(lookback_bars)
                         .reset_index(drop=True))
            print(f"Incremental mode: {len(df):,} rows ({df['ticker'].nunique()} tickers × ~{lookback_bars} bars)")
            log.info("Incremental mode: %d rows (%d tickers × ~%d bars)", len(df), df['ticker'].nunique(), lookback_bars)
        else:
            df = df_full
            print(f"Full mode: {len(df):,} rows")
            log.info("Full mode: %d rows", len(df))

    if df.empty:
        log.warning("Filtered universe is empty. Cannot compute indicators.")
        return df

    if "avg_value_20d" not in df.columns and "close" in df.columns and "volume" in df.columns:
        trading_val = df["close"] * df["volume"]
        df["avg_value_20d"] = (
            trading_val.groupby(df["ticker"], observed=True)
                       .transform(lambda s: s.rolling(20, min_periods=10).mean())
        )

    log.info("Computing Standard Indicators (Group A)...")
    df = compute_standard_indicators(df)

    log.info("Computing Ichimoku Cloud (Group B1)...")
    df = compute_ichimoku(df)

    log.info("Computing ADX & DI (Group B2)...")
    df = compute_adx(df)

    log.info("Computing Smart Money Concepts (Group C)...")
    df = compute_smc(df)

    from src.data.filters import optimize_dtypes
    df = optimize_dtypes(df)

    out_path = PROC_DIR / "universe_features.parquet"
    if mode == 'incremental':
        snapshot = (df.sort_values('time')
                      .groupby('ticker', observed=True)
                      .last()
                      .reset_index())
        snapshot.to_parquet(
            PROC_DIR / "daily_snapshot.parquet",
            index=False, compression='snappy'
        )
        print(f"Snapshot saved: {len(snapshot)} tickers")
        log.info("Snapshot saved to daily_snapshot.parquet: %d tickers", len(snapshot))

        # Đồng bộ cập nhật universe_features.parquet để tránh lỗi stale data khi đọc lại
        if out_path.exists():
            try:
                df_old = pd.read_parquet(out_path)
                min_new_time = df['time'].min()
                df_combined = pd.concat(
                    [df_old[df_old['time'] < min_new_time], df],
                    ignore_index=True
                )
                df_combined.to_parquet(out_path, compression="snappy", index=False)
                log.info("Updated universe_features.parquet incrementally. Shape: %s, Max date: %s",
                         df_combined.shape, df_combined['time'].max())
            except Exception as e:
                log.warning("Could not merge universe_features.parquet (%s), saving incremental df directly", e)
                df.to_parquet(out_path, compression="snappy", index=False)
        else:
            df.to_parquet(out_path, compression="snappy", index=False)
        return df
    else:
        log.info("Saving feature dataset to %s...", out_path)
        df.to_parquet(out_path, compression="snappy", index=False)
        log.info("Feature engineering complete. Output shape: %s", df.shape)
        return df

