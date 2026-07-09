"""
Script re-ingest toàn bộ dữ liệu OHLCV với nguồn giá đã điều chỉnh (adjusted prices).
Có backup thư mục raw cũ trước khi tải lại.
"""
import shutil
from pathlib import Path
from config.settings import RAW_DIR
from src.data.ingestion import run_ingestion

if __name__ == "__main__":
    # Backup raw data cũ trước khi xóa
    backup_dir = RAW_DIR.parent / 'ohlcv_backup'
    if not backup_dir.exists():
        shutil.copytree(RAW_DIR, backup_dir)
        print(f"Backup -> {backup_dir}")

    # Xóa raw data cũ
    for f in RAW_DIR.glob('[A-Z]*.parquet'):
        f.unlink()
    print(f"Cleared {RAW_DIR}")

    # Re-ingest với adjusted source (KBS/TCBS)
    run_ingestion()
