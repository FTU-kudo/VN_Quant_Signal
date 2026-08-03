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


def run(dry_run: bool = False, send_test: bool = False, full_scan: bool = False, analyze_only: bool = False, notify_only: bool = False):
    if not is_trading_day():
        log.info("Hôm nay không phải ngày giao dịch — skip.")
        return

    t0 = time.perf_counter()
    import pandas as _pd

    if notify_only:
        log.info("=== VN Quant Daily Pipeline: NOTIFY ONLY ===")
        top10_path = PROC_DIR / 'top10_today.parquet'
        regime_path = PROC_DIR / 'market_regime.parquet'
        
        regime = _pd.read_parquet(regime_path).iloc[-1]['regime'] if regime_path.exists() else "UNKNOWN"
        emoji_map = {"BULL": "🟢", "SIDEWAY": "🟡", "BEAR": "🔴"}
        emoji = emoji_map.get(regime, "⚪")

        from src.notification.telegram_alert import send_telegram_message, send_daily_alert
        if not top10_path.exists():
            no_signal_msg = (
                f"📊 VN Quant Signal — {date.today():%d/%m/%Y}\n"
                f"{emoji} Thị trường: {regime}\n"
                f"⏸ Không có tín hiệu hôm nay\n"
                f"Hệ thống đang bảo vệ vốn — chờ thị trường hồi phục."
            )
            if not dry_run:
                send_telegram_message(no_signal_msg)
            else:
                log.info("[DRY RUN] Telegram No-Signal:\n%s", no_signal_msg)
            log.info("Không có signal — đã gửi thông báo BEAR")
            return
            
        top10 = _pd.read_parquet(top10_path)
        if len(top10) > 0:
            if not dry_run:
                send_daily_alert(top10)
            else:
                send_daily_alert(top10, dry_run=True)
            log.info("Đã gửi Telegram Top %d", len(top10))
        return

    log.info("=== VN Quant Daily Pipeline START ===")

    import time as _time
    filtered_path = PROC_DIR / 'universe_filtered.parquet'
    days_since_filter = (
        (_time.time() - filtered_path.stat().st_mtime) / 86400
        if filtered_path.exists() else 999
    )

    from src.data.ingestion import run_ingestion

    # Quét toàn bộ ~1,700 mã vào Thứ Hai hàng tuần (weekday=0) hoặc khi truyền cờ --full-scan / chưa có bộ lọc
    is_monday_weekly = (date.today().weekday() == 0 and days_since_filter >= 5)
    should_full_scan = full_scan or is_monday_weekly or not filtered_path.exists()

    if should_full_scan:
        log.info("Step 1 & 2: Quét toàn bộ ~1,700 mã trên HOSE, HNX, UPCOM & cập nhật bộ lọc (Thứ Hai hàng tuần / Full scan)...")
        run_ingestion()
        from src.data.filters import run_filter_pipeline
        run_filter_pipeline()
    else:
        log.info("Step 1: Quét nhanh Incremental cho danh sách lọc (Tiết kiệm RAM & thời gian — cập nhật %.1f ngày trước)...", days_since_filter)
        filtered_tickers = _pd.read_parquet(filtered_path)['ticker'].unique().tolist()
        run_ingestion(tickers=filtered_tickers)
        log.info("Step 2: Skip filter")


    # Step 3: Feature Engineering — INCREMENTAL
    log.info("Step 3: Feature Engineering (incremental)...")
    from src.features.indicators import run_feature_engineering
    # Run feature engineering in incremental mode and keep the in-memory
    # features dataframe produced for the most-recent bars. Previously the
    # pipeline read `universe_features.parquet` (which is only updated on
    # full runs) causing stale prices to be used for scoring/signals.
    features_df = run_feature_engineering(mode='incremental', lookback_bars=260)

    # Step 4: Signal Generation từ snapshot
    log.info("Updating market regime...")
    from src.features.market_regime import compute_market_regime
    compute_market_regime(force_recompute=True)  # luôn cập nhật mới

    log.info("Step 4: Signal Generation...")
    from src.strategies.signal_engine import run_signal_generation
    # Prefer the freshly computed incremental features (in-memory) so that
    # today's bars are used. Fall back to the persisted `universe_features.parquet`
    # only if the incremental run returned None/empty for some reason.
    if features_df is not None and not features_df.empty:
        full_df = features_df
    else:
        full_df = _pd.read_parquet(PROC_DIR / 'universe_features.parquet')

    top10 = run_signal_generation(full_df)

    top10_path = PROC_DIR / 'top10_today.parquet'
    if len(top10) == 0:
        if top10_path.exists():
            top10_path.unlink()
        
        try:
            from track_signals import record_and_update_signals
            from src.tracking.auto_updater import sync_to_excel
            log.info("Updating live signal tracker...")
            record_and_update_signals()
            sync_to_excel(
                csv_path=PROC_DIR / 'live_signal_tracker.csv',
                xlsx_path=Path('journal/trade_log.xlsx')
            )
        except Exception as e:
            log.warning("Could not sync live signal tracker to Excel: %s", e)

        log.info("=== DONE ANALYSIS in %.1f giây ===", time.perf_counter() - t0)
        return

    log.info("Top 10 hôm nay:\n%s", top10[['ticker', 'score', 'signals_fired', 'close', 'volume', 'RSI_14', 'ADTV_tỷ']].head(5))
    top10.to_parquet(top10_path)
    log.info("Đã lưu kết quả vào %s", top10_path)

    try:
        from track_signals import record_and_update_signals
        from src.tracking.auto_updater import sync_to_excel
        log.info("Updating live signal tracker...")
        record_and_update_signals()
        sync_to_excel(
            csv_path=PROC_DIR / 'live_signal_tracker.csv',
            xlsx_path=Path('journal/trade_log.xlsx')
        )
    except Exception as e:
        log.warning("Could not sync live signal tracker to Excel: %s", e)

    log.info("=== DONE ANALYSIS in %.1f giây ===", time.perf_counter() - t0)



if __name__ == "__main__":
    p = argparse.ArgumentParser(description="VN Quant Signal Daily Pipeline")
    p.add_argument("--dry-run", action="store_true", help="Chạy pipeline nhưng không gửi thông báo")
    p.add_argument("--send-test", action="store_true", help="Gửi test thông báo")
    p.add_argument("--full-scan", action="store_true", help="Quét toàn bộ ~1,700 mã trên HOSE, HNX, UPCOM và tái sàng lọc bộ lọc")
    p.add_argument("--analyze-only", action="store_true", help="Chỉ phân tích, lưu kết quả, không thông báo")
    p.add_argument("--notify-only", action="store_true", help="Chỉ đọc kết quả và gửi thông báo Telegram")
    args = p.parse_args()
    run(dry_run=args.dry_run, send_test=args.send_test, full_scan=args.full_scan, analyze_only=args.analyze_only, notify_only=args.notify_only)
