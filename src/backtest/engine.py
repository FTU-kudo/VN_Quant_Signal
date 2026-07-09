"""
Backtesting Engine & Strategy Ranking — Step 5
================================================
Strict causal backtesting engine for quantitative trading signals.
Ensures zero look-ahead bias:
  - Signal at bar t -> Entry at OPEN of bar t+1
  - Exit at OPEN of bar t+N+1
  - Active position constraint: no new signals accepted while holding.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import logging
from pathlib import Path
from typing import Any, Optional
import numpy as np
import pandas as pd

from config.settings import PROC_DIR

log = logging.getLogger("backtest_engine")

HOLDING_PERIODS = [5, 10, 20]
COMMISSION      = 0.0015
SLIPPAGE        = 0.001
COST_PER_TRADE  = COMMISSION + SLIPPAGE  # 0.0025 per side -> 0.005 round-trip
RISK_FREE_RATE  = 0.045                  # 4.5% annual (VN T-bill)


@dataclass
class BacktestResult:
    strategy: str
    holding_days: int
    n_trades: int
    win_rate: float
    avg_return: float
    profit_factor: float
    max_drawdown: float
    sharpe_ratio: float
    avg_return_by_year: dict[str, float] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "strategy": self.strategy,
            "holding_days": self.holding_days,
            "n_trades": self.n_trades,
            "win_rate": round(self.win_rate, 2),
            "avg_return": round(self.avg_return, 3),
            "profit_factor": round(self.profit_factor, 2),
            "max_drawdown": round(self.max_drawdown, 2),
            "sharpe_ratio": round(self.sharpe_ratio, 2),
        }


def _calc_sharpe(returns: pd.Series, holding_days: int) -> float:
    """Annualized Sharpe. Risk-free = 4.5% VN T-bill."""
    if returns.std() == 0:
        return 0.0
    periods_per_year = 252.0 / holding_days
    rf_per_period    = 0.045 / periods_per_year
    excess           = returns - rf_per_period
    return float(excess.mean() / excess.std() * np.sqrt(periods_per_year))


def backtest_strategy(
    df: pd.DataFrame,
    signal_col: str,
    holding_days: int,
    min_trades: int = 5,
) -> Optional[BacktestResult]:
    """
    Vectorized causal backtest cho 1 strategy x 1 holding period.
    Tránh look-ahead bias và lọc bỏ các lệnh không hợp lệ (NaN/0 open prices).
    """
    if signal_col not in df.columns or df.empty:
        return None

    df = df.sort_values(["ticker", "time"]).reset_index(drop=True)

    net_returns = []

    for ticker, ticker_df in df.groupby("ticker", observed=True):
        ticker_df = ticker_df.reset_index(drop=True)
        ticker_signals = ticker_df[ticker_df[signal_col] == True]

        next_avail = 0
        for idx in ticker_signals.index:
            entry_idx = idx + 1
            exit_idx  = idx + holding_days + 1

            if entry_idx < next_avail:
                continue

            # QUAN TRỌNG: Skip nếu không đủ bars phía sau
            if entry_idx >= len(ticker_df) or exit_idx >= len(ticker_df):
                continue

            entry_price = ticker_df.iloc[entry_idx]["open"]
            exit_price  = ticker_df.iloc[exit_idx]["open"]

            # QUAN TRỌNG: Skip nếu price là NaN hoặc 0
            if pd.isna(entry_price) or pd.isna(exit_price):
                continue
            if entry_price <= 0 or exit_price <= 0:
                continue

            gross_return = (exit_price - entry_price) / entry_price
            net_return   = gross_return - 2 * COST_PER_TRADE
            net_returns.append(net_return)
            next_avail = exit_idx

    if len(net_returns) < min_trades:
        return None   # Không đủ trades

    returns_series = pd.Series(net_returns)

    # Max Drawdown đúng — dựa trên equity curve
    equity        = (1 + returns_series).cumprod()
    rolling_peak  = equity.cummax()
    drawdown      = (equity - rolling_peak) / rolling_peak
    max_dd        = float(abs(drawdown.min()) * 100)

    # Profit Factor — tránh divide by zero
    winners = returns_series[returns_series > 0].sum()
    losers  = abs(returns_series[returns_series < 0].sum())
    pf      = winners / losers if losers > 0 else 999.0

    return BacktestResult(
        strategy     = signal_col,
        holding_days = holding_days,
        n_trades     = len(net_returns),
        win_rate     = float((returns_series > 0).mean() * 100),
        avg_return   = float(returns_series.mean() * 100),
        profit_factor= round(pf, 2),
        max_drawdown = round(max_dd, 2),
        sharpe_ratio = _calc_sharpe(returns_series, holding_days),
    )


def backtest_by_year(df: pd.DataFrame, signal_col: str, holding_days: int) -> pd.DataFrame:
    """
    Chạy backtest riêng cho từng năm 2016–2026.
    Mục đích: phát hiện strategy chỉ tốt trong 1-2 năm (overfit).

    Returns DataFrame:
      year | n_trades | win_rate | avg_return | profit_factor
    """
    d = df.copy()
    d["time"] = pd.to_datetime(d["time"])
    years = range(2016, 2027)
    rows  = []

    for year in years:
        df_year = d[d["time"].dt.year == year].copy()
        result  = backtest_strategy(df_year, signal_col, holding_days, min_trades=1)
        if result is None:
            rows.append({
                "year": year,
                "n_trades": 0,
                "win_rate": None,
                "avg_return": None,
                "profit_factor": None
            })
        else:
            rows.append({
                "year"         : year,
                "n_trades"     : result.n_trades,
                "win_rate"     : round(result.win_rate, 1),
                "avg_return"   : round(result.avg_return, 2),
                "profit_factor": result.profit_factor,
            })

    return pd.DataFrame(rows)


def run_full_backtest(df: pd.DataFrame | None = None) -> pd.DataFrame:
    """
    Chạy backtest cho TẤT CẢ 5 strategies × 3 holding periods = 15 combinations.
    Returns DataFrame 15 rows sorted by Sharpe DESC.
    """
    if df is None:
        in_path = PROC_DIR / "universe_signals.parquet"
        log.info("Loading signal dataset from %s", in_path)
        df = pd.read_parquet(in_path)

    strategies = ["SIG_SMC", "SIG_ICHIMOKU", "SIG_MOMENTUM", "SIG_GOLDEN", "SIG_OB"]
    results = []

    for strat in strategies:
        for hold in HOLDING_PERIODS:
            res = backtest_strategy(df, signal_col=strat, holding_days=hold)
            if res is not None:
                results.append(res.to_dict())

    res_df = pd.DataFrame(results)
    if not res_df.empty:
        res_df = res_df.sort_values(by=["sharpe_ratio", "win_rate"], ascending=[False, False]).reset_index(drop=True)

    out_path = PROC_DIR / "backtest_results.parquet"
    res_df.to_parquet(out_path, index=False)
    log.info("Saved backtest results to %s", out_path)

    return res_df


def get_best_strategies(results_df: pd.DataFrame | None = None, min_trades: int = 50) -> list[str]:
    """
    Chọn ra các chiến lược tốt nhất cho thị trường VN:
    - win_rate > 51%
    - profit_factor > 1.2
    - max_drawdown < 40%
    - Ưu tiên holding period cho Sharpe Ratio cao nhất
    - Đặc cách cho SIG_SMC nếu max_drawdown < 20%
    """
    if results_df is None:
        out_path = PROC_DIR / "backtest_results.parquet"
        if out_path.exists():
            results_df = pd.read_parquet(out_path)
        else:
            results_df = run_full_backtest()

    mask = (
        (
            (results_df["win_rate"] > 51.0)
            & (results_df["profit_factor"] > 1.2)
            & (results_df["max_drawdown"] < 40.0)
            & (results_df["n_trades"] >= min_trades)
        )
        | (
            (results_df["win_rate"] > 60.0)
            & (results_df["profit_factor"] > 2.0)
            & (results_df["max_drawdown"] < 20.0)
            & (results_df["n_trades"] >= 10)
        )
    )

    passed = results_df[mask].copy()
    if passed.empty:
        return []

    best = (
        passed.sort_values("sharpe_ratio", ascending=False)
        .drop_duplicates("strategy")["strategy"]
        .tolist()
    )
    return best
