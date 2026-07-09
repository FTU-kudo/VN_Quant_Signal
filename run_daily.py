"""
run_daily.py — Daily pipeline runner
Chạy sau 15:15 ICT (sau khi tất cả 3 sàn đóng cửa)
"""
import argparse
from datetime import date, datetime
import logging
from pathlib import Path
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parent))
from config.settings import LOG_DIR, PROC_DIR

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(LOG_DIR / "daily_run.log", encoding="utf-8"),
    ],
)
log = logging.getLogger("daily")


def is_trading_day() -> bool:
    """Trả về False nếu là T7, CN."""
    return date.today().weekday() < 5  # 0=Mon, 4=Fri


def run(dry_run: bool = False, send_test: bool = False):
    if not is_trading_day():
        log.info("Hôm nay không phải ngày giao dịch — skip.")
        return

    t0 = time.perf_counter()
    log.info("=== VN Quant Daily Pipeline START ===")

    import time as _time
    import pandas as _pd
    filtered_path = PROC_DIR / 'universe_filtered.parquet'
    days_since_filter = (
        (_time.time() - filtered_path.stat().st_mtime) / 86400
        if filtered_path.exists() else 999
    )

    from src.data.ingestion import run_ingestion
    if days_since_filter > 7 or not filtered_path.exists():
        log.info("Step 1 & 2: Full ingestion & Rebuild filter (>7 ngày)...")
        run_ingestion()
        from src.data.filters import run_filter_pipeline
        run_filter_pipeline()
    else:
        log.info("Step 1: Targeted delta ingestion cho universe đã lọc (chạy %.1f ngày trước)...", days_since_filter)
        filtered_tickers = _pd.read_parquet(filtered_path)['ticker'].unique().tolist()
        run_ingestion(tickers=filtered_tickers)
        log.info("Step 2: Skip filter")

    # Step 3: Feature Engineering — INCREMENTAL (chỉ 260 bars × 159 tickers)
    log.info("Step 3: Feature Engineering (incremental)...")
    from src.features.indicators import run_feature_engineering
    snapshot = run_feature_engineering(mode='incremental', lookback_bars=260)

    # Step 4: Signal Generation từ snapshot
    log.info("Step 4: Signal Generation...")
    from src.strategies.signal_engine import score_tickers, apply_all_strategies, add_position_sizing
    snapshot_with_signals = apply_all_strategies(snapshot)
    top10 = score_tickers(
        snapshot_with_signals.groupby('ticker', observed=True).last().reset_index()
    )
    top10 = add_position_sizing(top10)
    top10.to_parquet(PROC_DIR / "top10_today.parquet", index=False)

    log.info("Top 10 hôm nay:\n%s",
             top10[['ticker','score','signals_fired','close','volume','RSI_14','ADTV_tỷ']].to_string()
             if all(c in top10.columns for c in ['volume','ADTV_tỷ']) else top10.to_string())

    # Step 5: Email & HTML Report
    from src.notification.email_report import generate_html_report, send_email_report
    html = generate_html_report(top10)
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    latest_file = LOG_DIR / "latest_report.html"
    dated_file  = LOG_DIR / f"report_{datetime.now().strftime('%Y%m%d')}.html"
    latest_file.write_text(html, encoding="utf-8")
    dated_file.write_text(html, encoding="utf-8")
    log.info("Đã tạo báo cáo HTML tại %s và %s", latest_file, dated_file)

    if not dry_run:
        send_email_report(html)
    else:
        log.info("[DRY RUN] Email skipped — xem logs/latest_report.html")

    from src.notification.telegram_alert import send_daily_alert
    if not dry_run:
        send_daily_alert(top10)
    else:
        send_daily_alert(top10, dry_run=True)

    log.info("=== DONE in %.1f giây ===", time.perf_counter() - t0)


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--send-test", action="store_true")
    args = p.parse_args()
    run(dry_run=args.dry_run, send_test=args.send_test)
