"""
track_signals.py
────────────────
Script theo dõi và ghi nhận hiệu suất thực tế (Live Forward Tracking) của các tín hiệu giao dịch
từ VN Quant Signal Engine.

Chức năng:
  1. Đọc tín hiệu Top 10 mới nhất từ `data/processed/top10_today.parquet`.
  2. Lưu/cập nhật vào sổ theo dõi lịch sử `data/processed/live_signal_tracker.parquet` (và `.csv`).
  3. Cập nhật giá hiện tại và tính toán hiệu suất thực tế (Return %, Status: HOLDING / T1_REACHED / T2_REACHED / SL_HIT).
  4. In báo cáo tổng hợp hiệu suất ra màn hình.
"""

from __future__ import annotations

import logging
from datetime import date, datetime
from pathlib import Path
import sys

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="ignore")

import numpy as np
import pandas as pd

# Thêm root path vào sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent))
from config.settings import PROC_DIR, LOG_DIR

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
    ],
)
log = logging.getLogger("track_signals")

TRACKER_PARQUET = PROC_DIR / "live_signal_tracker.parquet"
TRACKER_CSV     = PROC_DIR / "live_signal_tracker.csv"


def load_latest_prices() -> dict[str, float]:
    """Lấy giá đóng cửa mới nhất của các mã từ daily_snapshot hoặc universe_features."""
    prices = {}
    snapshot_path = PROC_DIR / "daily_snapshot.parquet"
    features_path = PROC_DIR / "universe_features.parquet"

    if snapshot_path.exists():
        try:
            df = pd.read_parquet(snapshot_path)
            if "ticker" in df.columns and "close" in df.columns:
                prices = dict(zip(df["ticker"], df["close"]))
                return prices
        except Exception as e:
            log.warning("Không thể đọc daily_snapshot.parquet: %s", e)

    if features_path.exists():
        try:
            df = pd.read_parquet(features_path, columns=["ticker", "time", "close"])
            df_latest = df.sort_values("time").groupby("ticker", observed=True).last().reset_index()
            prices = dict(zip(df_latest["ticker"], df_latest["close"]))
        except Exception as e:
            log.warning("Không thể đọc giá từ universe_features.parquet: %s", e)

    return prices


def record_and_update_signals() -> pd.DataFrame:
    """
    Ghi nhận tín hiệu mới từ top10_today.parquet vào sổ theo dõi và cập nhật trạng thái/hiệu suất.
    """
    top10_path = PROC_DIR / "top10_today.parquet"
    if not top10_path.exists():
        log.error("Không tìm thấy file %s — vui lòng chạy run_daily.py hoặc run_pipeline.py trước.", top10_path)
        return pd.DataFrame()

    top10_df = pd.read_parquet(top10_path)
    if top10_df.empty:
        log.info("Top 10 hôm nay rỗng — không có tín hiệu mới để ghi nhận.")
    else:
        log.info("Đọc được %d tín hiệu từ Top 10 hôm nay.", len(top10_df))

    # Đọc sổ theo dõi hiện tại nếu có
    if TRACKER_PARQUET.exists():
        tracker_df = pd.read_parquet(TRACKER_PARQUET)
    else:
        tracker_df = pd.DataFrame()

    # Chuẩn bị dữ liệu tín hiệu mới
    new_records = []
    today_str = date.today().strftime("%Y-%m-%d")

    for idx, row in top10_df.iterrows():
        ticker = str(row.get("ticker", "")).strip()
        if not ticker:
            continue

        sig_date = str(row.get("time", today_str))[:10]
        entry_price = float(row.get("close", 0))
        stop_loss = float(row.get("STOP_LOSS", entry_price * 0.95))
        target_1 = float(row.get("TARGET_1", entry_price * 1.05))
        target_2 = float(row.get("TARGET_2", entry_price * 1.10))
        signals_fired = str(row.get("signals_fired", "-"))
        score = float(row.get("score", 0))
        sector = str(row.get("sector", ""))

        new_records.append({
            "signal_date": sig_date,
            "ticker": ticker,
            "sector": sector,
            "signals_fired": signals_fired,
            "score": score,
            "entry_price": entry_price,
            "STOP_LOSS": stop_loss,
            "TARGET_1": target_1,
            "TARGET_2": target_2,
            "current_price": entry_price,
            "return_pct": 0.0,
            "status": "HOLDING",
            "last_updated": today_str,
        })

    new_df = pd.DataFrame(new_records)

    # Gộp tín hiệu mới vào tracker (tránh trùng lặp theo signal_date + ticker + signals_fired)
    if not tracker_df.empty and not new_df.empty:
        combined_df = pd.concat([tracker_df, new_df], ignore_index=True)
        combined_df = combined_df.drop_duplicates(
            subset=["signal_date", "ticker", "signals_fired"],
            keep="first"
        )
    elif tracker_df.empty:
        combined_df = new_df
    else:
        combined_df = tracker_df

    if combined_df.empty:
        log.info("Sổ theo dõi tín hiệu hiện đang rỗng.")
        return combined_df

    # Cập nhật giá mới nhất cho toàn bộ danh sách tín hiệu
    latest_prices = load_latest_prices()

    def update_row(row):
        t = row["ticker"]
        entry_p = row["entry_price"]
        sl = row["STOP_LOSS"]
        t1 = row["TARGET_1"]
        t2 = row["TARGET_2"]

        cur_p = latest_prices.get(t, row["current_price"])
        if cur_p <= 0:
            cur_p = entry_p

        ret_pct = ((cur_p - entry_p) / entry_p) * 100.0 if entry_p > 0 else 0.0

        # Xác định trạng thái
        if cur_p <= sl:
            status = "SL_HIT"
        elif cur_p >= t2:
            status = "T2_REACHED"
        elif cur_p >= t1:
            status = "T1_REACHED"
        else:
            status = "HOLDING"

        row["current_price"] = cur_p
        row["return_pct"] = round(ret_pct, 2)
        row["status"] = status
        row["last_updated"] = today_str
        return row

    combined_df = combined_df.apply(update_row, axis=1)

    # Lưu ra file Parquet & CSV
    PROC_DIR.mkdir(parents=True, exist_ok=True)
    combined_df.to_parquet(TRACKER_PARQUET, index=False)
    combined_df.to_csv(TRACKER_CSV, index=False, encoding="utf-8-sig")

    log.info("Đã cập nhật sổ theo dõi tín hiệu tại:\n  - %s\n  - %s", TRACKER_PARQUET, TRACKER_CSV)
    return combined_df


def print_performance_report(df: pd.DataFrame):
    """In báo cáo hiệu suất thực tế ra màn hình."""
    print("\n" + "=" * 90)
    print(" 📊  BÁO CÁO THEO DÕI HIỆU SUẤT TÍN HIỆU THỰC TẾ (LIVE FORWARD TRACKING)")
    print("=" * 90)

    if df.empty:
        print("Chưa có tín hiệu nào trong sổ theo dõi.")
        print("=" * 90 + "\n")
        return

    # In bảng chi tiết
    print(f"{'STT':<4} {'Ngày tín hiệu':<12} {'Ticker':<8} {'Chiến lược':<20} {'Entry':<10} {'Current':<10} {'Return %':<10} {'Trạng thái':<12}")
    print("-" * 90)

    for i, row in df.iterrows():
        date_str = str(row["signal_date"])
        ticker = str(row["ticker"])
        sig = str(row["signals_fired"])
        entry_p = f"{row['entry_price']:,.0f}"
        cur_p = f"{row['current_price']:,.0f}"
        ret_pct = f"{row['return_pct']:+.2f}%"
        status = str(row["status"])

        status_icon = {
            "HOLDING": "🟡 HOLDING",
            "T1_REACHED": "🟢 T1 REACHED",
            "T2_REACHED": "🟢 T2 REACHED",
            "SL_HIT": "🔴 SL HIT",
        }.get(status, status)

        print(f"{i+1:<4} {date_str:<12} {ticker:<8} {sig:<20} {entry_p:<10} {cur_p:<10} {ret_pct:<10} {status_icon:<12}")

    print("-" * 90)

    # Thống kê tổng hợp
    total_signals = len(df)
    avg_return = df["return_pct"].mean()
    t1_count = len(df[df["status"].isin(["T1_REACHED", "T2_REACHED"])])
    sl_count = len(df[df["status"] == "SL_HIT"])
    holding_count = len(df[df["status"] == "HOLDING"])

    win_rate = (t1_count / (t1_count + sl_count) * 100) if (t1_count + sl_count) > 0 else 0.0

    print(f"📈  TỔNG KẾT HIỆU SUẤT:")
    print(f"  • Tổng số tín hiệu đang theo dõi : {total_signals}")
    print(f"  • Đang nắm giữ (HOLDING)         : {holding_count}")
    print(f"  • Chốt lời (T1/T2 REACHED)       : {t1_count}")
    print(f"  • Chạm dừng lỗ (SL HIT)          : {sl_count}")
    print(f"  • Tỷ lệ thắng (Win Rate đã đóng) : {win_rate:.1f}%")
    print(f"  • Lợi nhuận trung bình (Avg Return): {avg_return:+.2f}%")
    print("=" * 90 + "\n")


def main():
    log.info("Bắt đầu cập nhật và ghi nhận hiệu suất tín hiệu...")
    tracker_df = record_and_update_signals()
    print_performance_report(tracker_df)


if __name__ == "__main__":
    main()
