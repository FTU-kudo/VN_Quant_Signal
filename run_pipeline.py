"""
run_pipeline.py
────────────────
Entry point for running Steps 1 and 2 of the VN Quant Signal pipeline.

Usage (CLI)
───────────
  # Full run: ingest all tickers, then filter
  python run_pipeline.py

  # Ingest only (skip filter)
  python run_pipeline.py --step ingest

  # Filter only (assumes data already downloaded)
  python run_pipeline.py --step filter

  # Re-ingest specific tickers (debugging / manual patch)
  python run_pipeline.py --step ingest --tickers ACB VCB HPG

  # Dry-run: show universe stats without writing files
  python run_pipeline.py --dry-run

Environment
───────────
  Copy .env.example → .env and fill in:
    SMTP_HOST / SMTP_PORT / SMTP_USER / SMTP_PASS / REPORT_RECIPIENTS
  (Required only for Step 6 email delivery.)
"""

from __future__ import annotations

import argparse
import logging
import sys
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="ignore")
import time
from pathlib import Path

# Ensure project root is on the path when running from any working directory
sys.path.insert(0, str(Path(__file__).resolve().parent))

from src.data.ingestion import load_universe_parquets, run_ingestion
from src.data.filters import run_filter_pipeline
from src.features.indicators import run_feature_engineering

log = logging.getLogger("pipeline")


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="VN Quant Signal Pipeline — Steps 1, 2 & 3",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument(
        "--step",
        choices=["ingest", "filter", "features", "all"],
        default="all",
        help="Which step(s) to run (default: all).",
    )
    p.add_argument(
        "--tickers",
        nargs="*",
        help="Override: only process these tickers (space-separated). "
             "Applies to ingestion step only.",
    )
    p.add_argument(
        "--exchanges",
        nargs="*",
        default=["HOSE", "HNX", "UPCOM"],
        help="Exchanges to include (default: HOSE HNX UPCOM).",
    )
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="Print stats without writing any files.",
    )
    return p.parse_args()


def main() -> None:
    args = _parse_args()
    t0   = time.perf_counter()

    log.info("══════════════════════════════════════════════════")
    log.info("  VN Quant Signal Pipeline  |  step=%s", args.step)
    log.info("══════════════════════════════════════════════════")

    # ── Step 1: Data Ingestion ─────────────────────────────────────────────────
    if args.step in ("ingest", "all"):
        if args.dry_run:
            log.info("[DRY RUN] Would run ingestion for exchanges=%s", args.exchanges)
        else:
            summary = run_ingestion(
                exchanges=args.exchanges,
                tickers=args.tickers,
            )
            log.info(
                "Step 1 done | %d tickers processed | "
                "updated=%d | up_to_date=%d | failed=%d",
                len(summary),
                (summary["status"].isin(["full_load", "delta_updated"])).sum(),
                (summary["status"] == "up_to_date").sum(),
                (summary["status"].isin(["fetch_failed", "worker_exception"])).sum(),
            )

    # ── Step 2: Quality / Liquidity Filter ────────────────────────────────────
    if args.step in ("filter", "all"):
        if args.dry_run:
            # Load data without filtering, just show stats
            try:
                df = load_universe_parquets()
                log.info(
                    "[DRY RUN] Universe: %d tickers | %d rows | %s → %s",
                    df["ticker"].nunique(), len(df),
                    df["time"].min().date(), df["time"].max().date(),
                )
            except FileNotFoundError as e:
                log.warning("[DRY RUN] No data loaded yet: %s", e)
        else:
            try:
                passed_df = run_filter_pipeline()
                log.info(
                    "Step 2 done | %d tickers in filtered universe",
                    passed_df["ticker"].nunique(),
                )
            except FileNotFoundError as e:
                log.error("Cannot run filter step: %s", e)

    # ── Step 3: Feature Engineering (Technical Indicators & SMC) ──────────────
    if args.step in ("features", "all"):
        if args.dry_run:
            log.info("[DRY RUN] Would run feature engineering (Step 3).")
        else:
            try:
                features_df = run_feature_engineering()
                log.info(
                    "Step 3 done | %d tickers processed | %d columns generated",
                    features_df["ticker"].nunique() if not features_df.empty else 0,
                    features_df.shape[1] if not features_df.empty else 0,
                )
            except FileNotFoundError as e:
                log.error("Cannot run feature engineering step: %s", e)

    elapsed = time.perf_counter() - t0
    log.info("Pipeline finished in %.1f seconds (%.1f min)", elapsed, elapsed / 60)


if __name__ == "__main__":
    main()
