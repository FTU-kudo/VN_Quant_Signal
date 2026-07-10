"""
Auto-updater cho live_signal_tracker.
Chạy hàng ngày sau run_daily.py để cập nhật
trạng thái WIN/LOSS/HOLDING dựa trên giá thực tế.
"""
import pandas as pd
import numpy as np
from datetime import date, timedelta
from pathlib import Path
from config.settings import PROC_DIR

TRACKER_PATH = PROC_DIR / 'live_signal_tracker.parquet'
TRACKER_CSV  = PROC_DIR / 'live_signal_tracker.csv'


def update_signal_status(df_tracker: pd.DataFrame,
                         df_prices: pd.DataFrame) -> pd.DataFrame:
    """
    Cập nhật trạng thái cho các signals đang HOLDING.
    
    Logic xác định WIN/LOSS:
    
    WIN  → giá hiện tại >= TARGET_1
           (đạt mục tiêu chốt lời +2×ATR)
    
    LOSS → giá hiện tại <= STOP_LOSS
           (chạm cắt lỗ -2×ATR)
    
    EXPIRED → đã giữ > 20 ngày giao dịch
              (hết holding period backtest)
              Tự động tính return tại giá hiện tại.
    
    HOLDING → chưa chạm ngưỡng nào, tiếp tục theo dõi.
    """
    df = df_tracker.copy()

    # Ensure required columns exist
    for col in ['result', 'current_price', 'return_pct', 'last_updated', 'days_held', 'exit_price', 'exit_date', 'note']:
        if col not in df.columns:
            df[col] = None

    # Chỉ update các signals chưa đóng (result is NA hoặc 'HOLDING')
    holding_mask = df['result'].isna() | (df['result'] == 'HOLDING')
    if not holding_mask.any():
        return df
    
    # Latest price cho mỗi ticker
    latest_prices = (
        df_prices.sort_values('time')
                 .groupby('ticker')['close']
                 .last()
                 .to_dict()
    )
    latest_date = df_prices['time'].max().date()
    
    for idx in df[holding_mask].index:
        row    = df.loc[idx]
        ticker = row['ticker']
        
        current_price = latest_prices.get(ticker)
        if current_price is None:
            continue
        
        # Cập nhật current price và return
        df.at[idx, 'current_price'] = current_price
        df.at[idx, 'return_pct'] = round(
            (current_price - row['entry_price'])
            / row['entry_price'] * 100, 2
        )
        df.at[idx, 'last_updated'] = latest_date
        
        # Tính số ngày giao dịch đã giữ
        signal_date = pd.Timestamp(row['signal_date']).date()
        trading_days_held = len(
            df_prices[
                (df_prices['ticker'] == ticker) &
                (df_prices['time'].dt.date > signal_date) &
                (df_prices['time'].dt.date <= latest_date)
            ]
        )
        df.at[idx, 'days_held'] = trading_days_held
        
        # Xác định trạng thái
        sl = row.get('STOP_LOSS', 0)
        t1 = row.get('TARGET_1', float('inf'))
        
        if current_price >= t1:
            df.at[idx, 'result']     = 'WIN'
            df.at[idx, 'status']     = 'WIN'
            df.at[idx, 'exit_price'] = current_price
            df.at[idx, 'exit_date']  = latest_date
            
        elif current_price <= sl and sl > 0:
            df.at[idx, 'result']     = 'LOSS'
            df.at[idx, 'status']     = 'LOSS'
            df.at[idx, 'exit_price'] = current_price
            df.at[idx, 'exit_date']  = latest_date
            
        elif trading_days_held >= 20:
            # Hết holding period → đóng lệnh theo giá hiện tại
            res = 'WIN' if current_price > row['entry_price'] else 'LOSS'
            df.at[idx, 'result']     = res
            df.at[idx, 'status']     = res
            df.at[idx, 'exit_price'] = current_price
            df.at[idx, 'exit_date']  = latest_date
            df.at[idx, 'note']       = 'EXPIRED_T+20'
        else:
            df.at[idx, 'result']     = 'HOLDING'
            df.at[idx, 'status']     = 'HOLDING'
    
    return df


def save_tracker(df: pd.DataFrame) -> None:
    """Lưu tracker ra cả parquet và CSV."""
    df.to_parquet(TRACKER_PATH, index=False)
    df.to_csv(TRACKER_CSV, index=False, sep=';',
              encoding='utf-8-sig')   # utf-8-sig cho Excel VN


def generate_weekly_report(df: pd.DataFrame) -> dict:
    """
    Tính toán metrics cho weekly Telegram report.
    
    Returns dict với các chỉ số cần thiết.
    """
    df['signal_date'] = pd.to_datetime(df['signal_date'])
    
    # Tổng quan
    total    = len(df)
    holding  = (df['result'] == 'HOLDING').sum()
    wins     = (df['result'] == 'WIN').sum()
    losses   = (df['result'] == 'LOSS').sum()
    closed   = wins + losses
    win_rate = (wins / closed * 100) if closed > 0 else None
    
    # Return trung bình các lệnh đã đóng
    closed_df  = df[df['result'].isin(['WIN', 'LOSS'])].copy()
    avg_return = None
    if len(closed_df) > 0 and 'return_pct' in closed_df.columns:
        avg_return = closed_df['return_pct'].mean()
    
    # Signals trong 7 ngày qua
    week_ago     = pd.Timestamp(date.today() - timedelta(days=7))
    new_signals  = (df['signal_date'] >= week_ago).sum()
    
    # Regime distribution gần đây
    regime_path = PROC_DIR / 'market_regime.parquet'
    regime_dist = {}
    if regime_path.exists():
        r = pd.read_parquet(regime_path).tail(20)
        regime_dist = r['regime'].value_counts().to_dict()
    
    return {
        'total'      : total,
        'holding'    : holding,
        'wins'       : wins,
        'losses'     : losses,
        'closed'     : closed,
        'win_rate'   : win_rate,
        'avg_return' : avg_return,
        'new_signals': new_signals,
        'regime_dist': regime_dist,
    }


def format_weekly_telegram(metrics: dict) -> str:
    """Format Telegram message cho weekly review."""
    today    = date.today().strftime('%d/%m/%Y')
    wr       = metrics['win_rate']
    avg_ret  = metrics['avg_return']
    
    # Win rate status
    if wr is None:
        wr_line = "📊 Win Rate: Chưa có lệnh đóng"
    elif wr >= 52:
        wr_line = f"📊 Win Rate: {wr:.1f}% ✅ (mục tiêu ≥52%)"
    else:
        wr_line = f"📊 Win Rate: {wr:.1f}% ⚠️ (dưới mục tiêu)"
    
    # Avg return
    if avg_ret is not None:
        ret_emoji = "✅" if avg_ret > 0 else "❌"
        ret_line  = f"💰 Avg Return: {avg_ret:+.2f}% {ret_emoji}"
    else:
        ret_line  = "💰 Avg Return: Chưa có dữ liệu"
    
    # Regime summary
    regime_lines = []
    for regime, count in metrics['regime_dist'].items():
        emoji = {'BULL':'🟢','SIDEWAY':'🟡','BEAR':'🔴'}.get(regime,'⚪')
        regime_lines.append(f"  {emoji} {regime}: {count} ngày")
    
    return "\n".join([
        f"📅 <b>WEEKLY REVIEW — {today}</b>",
        "━━━━━━━━━━━━━━━━━━━━",
        f"📈 Signals tuần này : {metrics['new_signals']}",
        f"⏳ Đang HOLDING     : {metrics['holding']}",
        f"✅ WIN              : {metrics['wins']}",
        f"❌ LOSS             : {metrics['losses']}",
        "━━━━━━━━━━━━━━━━━━━━",
        wr_line,
        ret_line,
        "━━━━━━━━━━━━━━━━━━━━",
        "🌐 Regime 20 ngày gần nhất:",
        *regime_lines,
        "━━━━━━━━━━━━━━━━━━━━",
        "📝 <i>Mở live_signal_tracker.csv",
        "để xem chi tiết từng lệnh</i>",
    ])
