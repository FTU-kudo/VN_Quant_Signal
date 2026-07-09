"""
Signal Engine Module — Step 4: Strategy Design & Signal Generation
===================================================================
Implements 5 core quantitative strategies on 159 institutional-liquidity stocks
and scores tickers for daily Top 10 rankings.

All logic strictly adheres to causality (no look-ahead bias: only bar[t] and <=t used).
"""

from __future__ import annotations

import logging
from pathlib import Path
import numpy as np
import pandas as pd

from config.settings import PROC_DIR

log = logging.getLogger("signal_engine")

MAX_TICKERS_PER_SECTOR = 2   # Giới hạn số lượng cổ phiếu tối đa cho mỗi ngành trong Top 10

TOTAL_CAPITAL      = 1_000_000_000   # 1 tỷ VND (thay đổi theo thực tế)
MAX_RISK_PER_TRADE = 0.01            # Tối đa 1% vốn/lệnh
MAX_POSITION_PCT   = 0.20            # Tối đa 20% vốn/1 cổ phiếu


# ══════════════════════════════════════════════════════════════════════════════
# 1. Individual Strategies (Per-Ticker Vectorized Series[bool])
# ══════════════════════════════════════════════════════════════════════════════

def strategy_smc_reversal(df: pd.DataFrame) -> pd.Series:
    """
    Strategy 1: SMC_REVERSAL
    Tín hiệu đảo chiều kết hợp SMC + Momentum.

    Điều kiện ĐỒNG THỜI (AND):
      - CHOCH_BULL == True (cấu trúc thị trường đảo chiều tăng)
      - OB_BULL == True HOẶC close <= BB_LOWER_20 (giá chạm vùng cầu)
      - RSI_14 < 40 (momentum chưa overbought)
      - MACD_HIST tăng so với bar trước (MACD_HIST > MACD_HIST.shift(1))
    """
    choch_bull = df["CHOCH_BULL"].fillna(False).astype(bool)
    ob_bull    = df["OB_BULL"].fillna(False).astype(bool)
    touch_bb   = df["close"] <= df["BB_LOWER_20"]

    cond1 = choch_bull
    cond2 = ob_bull | touch_bb
    cond3 = df["RSI_14"] < 40.0
    cond4 = df["MACD_HIST"] > df["MACD_HIST"].shift(1)

    return (cond1 & cond2 & cond3 & cond4).fillna(False)


def strategy_ichimoku_trend(df: pd.DataFrame) -> pd.Series:
    """
    Strategy 2: ICHIMOKU_TREND
    Tín hiệu xu hướng Ichimoku thuần túy.

    Điều kiện ĐỒNG THỜI:
      - close > SENKOU_A AND close > SENKOU_B (giá trên mây Kumo)
      - TENKAN_9 > KIJUN_26 (Tenkan nằm trên Kijun)
      - BOS_BULL == True (cấu trúc xác nhận uptrend)
      - ADX_14 > 25 (xu hướng đủ mạnh)
    """
    cond1 = (df["close"] > df["SENKOU_A"]) & (df["close"] > df["SENKOU_B"])
    cond2 = df["TENKAN_9"] > df["KIJUN_26"]
    cond3 = df["BOS_BULL"].fillna(False).astype(bool)
    cond4 = df["ADX_14"] > 25.0

    return (cond1 & cond2 & cond3 & cond4).fillna(False)


def strategy_momentum_breakout(df: pd.DataFrame) -> pd.Series:
    """
    Strategy 3: MOMENTUM_BREAKOUT
    Tín hiệu breakout kết hợp nhiều momentum.

    Điều kiện ĐỒNG THỜI:
      - close > MA_20 AND MA_20 > MA_50 (price above rising MAs)
      - RSI_14 > 50 AND RSI_14 < 70 (momentum tăng, chưa quá mua)
      - MACD_12_26_9 > MACD_SIGNAL_9 (MACD above signal)
      - MACD_HIST > 0 AND MACD_HIST > MACD_HIST.shift(1) (histogram expanding)
      - BOS_BULL == True (structure break confirms)
    """
    cond1 = (df["close"] > df["MA_20"]) & (df["MA_20"] > df["MA_50"])
    cond2 = (df["RSI_14"] > 50.0) & (df["RSI_14"] < 70.0)
    cond3 = df["MACD_12_26_9"] > df["MACD_SIGNAL_9"]
    cond4 = (df["MACD_HIST"] > 0.0) & (df["MACD_HIST"] > df["MACD_HIST"].shift(1))
    cond5 = df["BOS_BULL"].fillna(False).astype(bool)

    return (cond1 & cond2 & cond3 & cond4 & cond5).fillna(False)


def strategy_golden_cross_plus(df: pd.DataFrame) -> pd.Series:
    """
    Strategy 4: GOLDEN_CROSS_PLUS
    Tín hiệu MA Golden Cross có xác nhận.

    Điều kiện ĐỒNG THỜI:
      - MA_50 crosses above MA_200: (MA_50 > MA_200) & (MA_50.shift(1) <= MA_200.shift(1))
      - close > SENKOU_A AND close > SENKOU_B (Ichimoku confirm)
      - ADX_14 > 20 (có trend)
      - Volume tại bar cross > MA20 của volume (volume xác nhận)
    """
    ma_cross = (df["MA_50"] > df["MA_200"]) & (df["MA_50"].shift(1) <= df["MA_200"].shift(1))
    cond2 = (df["close"] > df["SENKOU_A"]) & (df["close"] > df["SENKOU_B"])
    cond3 = df["ADX_14"] > 20.0
    vol_ma20 = df["volume"].rolling(20, min_periods=10).mean()
    cond4 = df["volume"] > vol_ma20

    return (ma_cross & cond2 & cond3 & cond4).fillna(False)


def strategy_ob_bounce(df: pd.DataFrame) -> pd.Series:
    """
    Strategy 5: OB_BOUNCE
    Tín hiệu giá bounce từ Order Block với confluence.

    Điều kiện ĐỒNG THỜI:
      - OB_BULL == True HOẶC (low <= OB_LOW.shift(1) AND close > OB_LOW.shift(1))
      - RSI_14 < 45 (chưa overbought)
      - DI_PLUS_14 > DI_MINUS_14 (bullish directional bias)
      - close > KIJUN_26 (Kijun làm support)
    """
    ob_bull = df["OB_BULL"].fillna(False).astype(bool)
    ob_low_prev = df["OB_LOW"].shift(1)
    bounce_ob = (df["low"] <= ob_low_prev) & (df["close"] > ob_low_prev)

    cond1 = ob_bull | bounce_ob
    cond2 = df["RSI_14"] < 45.0
    cond3 = df["DI_PLUS_14"] > df["DI_MINUS_14"]
    cond4 = df["close"] > df["KIJUN_26"]

    return (cond1 & cond2 & cond3 & cond4).fillna(False)


# ══════════════════════════════════════════════════════════════════════════════
# 2. Apply All Strategies
# ══════════════════════════════════════════════════════════════════════════════

def apply_all_strategies(df: pd.DataFrame, regime_filter: bool = True) -> pd.DataFrame:
    """
    Áp dụng 5 strategies trên toàn bộ universe.
    Mỗi strategy chạy per-ticker trong groupby loop.
    Tích hợp bộ lọc Market Regime (VNINDEX) để kiểm soát tín hiệu.
    """
    df = df.sort_values(["ticker", "time"]).copy()

    # Ensure ADTV_20 column exists
    if "ADTV_20" not in df.columns:
        trading_val = df["close"] * df["volume"]
        df["ADTV_20"] = (
            df.assign(_tv=trading_val)
              .groupby("ticker", observed=True)["_tv"]
              .transform(lambda s: s.rolling(20, min_periods=10).mean())
        )

    parts = []
    for ticker, g in df.groupby("ticker", observed=True):
        g = g.copy()
        g["SIG_SMC"]      = strategy_smc_reversal(g)
        g["SIG_ICHIMOKU"] = strategy_ichimoku_trend(g)
        g["SIG_MOMENTUM"] = strategy_momentum_breakout(g)
        g["SIG_GOLDEN"]   = strategy_golden_cross_plus(g)
        g["SIG_OB"]       = strategy_ob_bounce(g)
        parts.append(g)

    out = pd.concat(parts).sort_values(["ticker", "time"]).reset_index(drop=True)

    if regime_filter:
        regime_path = PROC_DIR / "market_regime.parquet"
        if not regime_path.exists():
            from src.features.market_regime import compute_market_regime
            compute_market_regime()
        if regime_path.exists():
            regime_df = pd.read_parquet(regime_path)
            regime_map = dict(zip(pd.to_datetime(regime_df["date"]).dt.date, regime_df["regime"]))
            out["regime"] = out["time"].dt.date.map(lambda d: regime_map.get(d, "UNKNOWN"))

            bull_mask = out["regime"].isin(["BULL", "UNKNOWN"])
            bear_mask = out["regime"] == "BEAR"

            # SIG_GOLDEN chỉ fire trong BULL (cần xu hướng tăng mạnh của VNINDEX)
            out["SIG_GOLDEN"]   = out["SIG_GOLDEN"] & bull_mask
            # SIG_ICHIMOKU và SIG_SMC fire trong BULL và SIDEWAY (VNINDEX > MA200), không fire trong BEAR
            out["SIG_ICHIMOKU"] = out["SIG_ICHIMOKU"] & ~bear_mask
            out["SIG_SMC"]      = out["SIG_SMC"] & ~bear_mask
        else:
            out["regime"] = "UNKNOWN"

    return out


# ══════════════════════════════════════════════════════════════════════════════
# 3. Scoring Function
# ══════════════════════════════════════════════════════════════════════════════

def score_tickers(df_latest: pd.DataFrame) -> pd.DataFrame:
    """
    Scoring dựa trên backtest-validated strategies only.
    
    Weights đã được backtest approve:
      SIG_GOLDEN   → +2.0  (55.15% win rate, PF 2.02, stable 6/11 năm)
      SIG_ICHIMOKU → +1.5  (52.78% win rate, PF 1.61, stable 7/11 năm)
      SIG_SMC      → +0.5  (bonus only — sample quá nhỏ để rely on)
      SIG_MOMENTUM → 0     (rejected — win rate < 50%)
      SIG_OB       → 0     (rejected — avg return âm)
    
    Bonus:
      ADX_14 > 30  → +0.5  (trend mạnh xác nhận)
      RSI trong [40, 60] → +0.3  (momentum healthy, không extreme)
    
    Penalty:
      close < MA_200 → -1.0  (downtrend dài hạn)
      RSI > 75       → -0.5  (overbought risk)
    
    Chỉ giữ tickers có score > 0 trong Top 10.
    """
    df = df_latest.copy()
    df['score'] = 0.0
    
    # Validated signals
    if 'SIG_GOLDEN'   in df.columns: df['score'] += df['SIG_GOLDEN']   * 2.0
    if 'SIG_ICHIMOKU' in df.columns: df['score'] += df['SIG_ICHIMOKU'] * 1.5
    if 'SIG_SMC'      in df.columns: df['score'] += df['SIG_SMC']      * 0.5
    
    # Bonus
    df['score'] += (df['ADX_14'] > 30).astype(float) * 0.5
    df['score'] += ((df['RSI_14'] >= 40) & (df['RSI_14'] <= 60)).astype(float) * 0.3
    
    # Penalty
    if 'MA_200' in df.columns:
        df['score'] -= (df['close'] < df['MA_200']).astype(float) * 1.0
    df['score'] -= (df['RSI_14'] > 75).astype(float) * 0.5
    if 'regime' in df.columns:
        df['score'] -= (df['regime'] == 'BEAR').astype(float) * 2.0
    
    # Which signals fired
    sig_cols = {'SIG_GOLDEN': 'GOLDEN_CROSS', 
                'SIG_ICHIMOKU': 'ICHIMOKU_TREND',
                'SIG_SMC': 'SMC_REVERSAL'}
    df['signals_fired'] = df.apply(
        lambda r: ', '.join(v for k,v in sig_cols.items() 
                           if k in r.index and bool(r[k])), axis=1
    )
    df['signals_fired'] = df['signals_fired'].replace('', '-')
    
    # ADTV hiển thị theo tỷ VND (trung bình 20 phiên)
    if 'avg_value_20d' in df.columns:
        df['ADTV_tỷ'] = (df['avg_value_20d'] / 1e9).round(1)
    elif 'trading_value' in df.columns:
        df['ADTV_tỷ'] = (df['trading_value'] / 1e9).round(1)
    elif 'close' in df.columns and 'volume' in df.columns:
        df['ADTV_tỷ'] = ((df['close'] * df['volume']) / 1e9).round(1)
    
    # ATR-based levels (ATR_14 đã có trong features)
    if 'ATR_14' in df.columns:
        df['STOP_LOSS'] = (df['close'] - 2 * df['ATR_14']).round(0)
        df['TARGET_1']  = (df['close'] + 2 * df['ATR_14']).round(0)
        df['TARGET_2']  = (df['close'] + 3 * df['ATR_14']).round(0)
        df['RISK_PCT']  = ((df['close'] - df['STOP_LOSS']) / df['close'] * 100).round(1)

    # Load sector mapping
    sector_path = PROC_DIR / 'sector_mapping.parquet'
    if sector_path.exists():
        sectors = pd.read_parquet(sector_path)[['ticker', 'sector']]
        df = df.merge(sectors, on='ticker', how='left')
        df['sector'] = df['sector'].fillna('Unknown')
    else:
        df['sector'] = 'Unknown'

    has_signal = (
        df.get('SIG_GOLDEN',   False) |
        df.get('SIG_ICHIMOKU', False) |
        df.get('SIG_SMC',      False)
    )

    df_sorted = df[has_signal & (df['score'] > 0)].sort_values('score', ascending=False)

    selected = []
    sector_count = {}

    for _, row in df_sorted.iterrows():
        sector = row.get('sector', 'Unknown')
        count = sector_count.get(sector, 0)

        if count < MAX_TICKERS_PER_SECTOR:
            selected.append(row)
            sector_count[sector] = count + 1

        if len(selected) >= 10:
            break

    top10 = pd.DataFrame(selected).reset_index(drop=True) if selected else pd.DataFrame(columns=df.columns)
    return top10


def calc_position_size(
    capital:    float,
    close:      float,
    stop_loss:  float,
    atr:        float,
) -> dict:
    """
    Kelly-inspired position sizing dựa trên ATR risk.
    """
    risk_per_share = close - stop_loss
    if risk_per_share <= 0:
        return {'shares': 0, 'value_vnd': 0, 'pct_capital': 0.0}

    max_shares_by_risk  = (capital * MAX_RISK_PER_TRADE) / risk_per_share
    max_shares_by_value = (capital * MAX_POSITION_PCT)   / close
    final_shares        = int(min(max_shares_by_risk, max_shares_by_value))
    final_value         = final_shares * close

    return {
        'shares'      : final_shares,
        'value_vnd'   : int(final_value),
        'pct_capital' : round(final_value / capital * 100, 1),
    }


def add_position_sizing(
    top10: pd.DataFrame,
    capital: float = TOTAL_CAPITAL,
) -> pd.DataFrame:
    """Apply position sizing cho từng ticker trong Top 10."""
    if top10.empty or 'STOP_LOSS' not in top10.columns:
        return top10

    sizing = top10.apply(
        lambda r: pd.Series(calc_position_size(
            capital   = capital,
            close     = r['close'],
            stop_loss = r['STOP_LOSS'],
            atr       = r.get('ATR_14', r['close'] * 0.02),
        )),
        axis=1,
    )

    top10['SL_SHARES']   = sizing['shares']
    top10['SL_VALUE_TỶ'] = (sizing['value_vnd'] / 1e9).round(2)
    top10['SL_PCT_VỐN']  = sizing['pct_capital']

    return top10


# ══════════════════════════════════════════════════════════════════════════════
# 4. Pipeline Entry Point
# ══════════════════════════════════════════════════════════════════════════════

def run_signal_generation(
    df: pd.DataFrame | None = None,
    capital: float = TOTAL_CAPITAL,
) -> pd.DataFrame:
    """
    Entry point:
    1. Load universe_features.parquet nếu df=None
    2. apply_all_strategies() trên toàn bộ history
    3. Lấy bar cuối cùng mỗi ticker (latest snapshot)
    4. score_tickers() -> Top 10
    5. add_position_sizing() theo ATR stop loss
    6. Lưu files và return Top 10
    """
    if df is None:
        in_path = PROC_DIR / "universe_features.parquet"
        log.info("Loading features dataset from %s", in_path)
        df = pd.read_parquet(in_path)

    log.info("Applying 5 strategies on %d rows across %d tickers...", len(df), df["ticker"].nunique())
    df_signals = apply_all_strategies(df)

    # Save full history with signals
    out_signals_path = PROC_DIR / "universe_signals.parquet"
    df_signals.to_parquet(out_signals_path, index=False)
    log.info("Saved full signal history to %s", out_signals_path)

    # Latest bar per ticker
    latest_date = df_signals["time"].max()
    log.info("Extracting latest snapshot for date: %s", latest_date)
    df_latest = df_signals.sort_values("time").groupby("ticker", observed=True).last().reset_index()

    # Score and select Top 10
    ranked = score_tickers(df_latest)
    top10 = ranked.head(10).copy()
    top10 = add_position_sizing(top10, capital=capital)

    out_top10_path = PROC_DIR / "top10_today.parquet"
    top10.to_parquet(out_top10_path, index=False)
    log.info("Saved Top 10 signals to %s", out_top10_path)

    return top10
