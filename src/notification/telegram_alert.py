"""
src/notification/telegram_alert.py
Gửi Telegram message tóm tắt Top 10 tín hiệu hàng ngày.
"""
import os, sys, requests
try:
    sys.stdout.reconfigure(encoding='utf-8', errors='ignore')
except Exception:
    pass
from datetime import date
from typing import Optional
import pandas as pd
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

TELEGRAM_TOKEN   = os.getenv('TELEGRAM_TOKEN', '')
TELEGRAM_CHAT_ID = os.getenv('TELEGRAM_CHAT_ID', '')
TELEGRAM_API     = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}"


def send_telegram_message(text: str) -> bool:
    """Gửi text message qua Telegram Bot API."""
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        print("⚠️  Telegram chưa cấu hình trong .env")
        return False
    try:
        resp = requests.post(
            f"{TELEGRAM_API}/sendMessage",
            json={
                'chat_id'   : TELEGRAM_CHAT_ID,
                'text'      : text,
                'parse_mode': 'HTML',
            },
            timeout=10,
        )
        resp.raise_for_status()
        print("✅ Telegram gửi thành công")
        return True
    except Exception as e:
        print(f"❌ Telegram thất bại: {e}")
        # Ghi log
        Path('logs').mkdir(parents=True, exist_ok=True)
        Path('logs/telegram_log.txt').open('a', encoding='utf-8').write(
            f"{date.today()} | FAILED | {e}\n"
        )
        return False


def format_telegram_report(top10_df: pd.DataFrame) -> str:
    """
    Format message Telegram ngắn gọn, đọc được trên mobile.
    """
    today = date.today().strftime('%d/%m/%Y')

    # Header
    lines = [
        f"📊 <b>VN Quant Signal — {today}</b>",
        "━━━━━━━━━━━━━━━━━━━━",
    ]

    # Market regime nếu có
    try:
        from config.settings import PROC_DIR
        regime_df = pd.read_parquet(PROC_DIR / 'market_regime.parquet')
        regime    = regime_df.iloc[-1]['regime']
        emoji     = {'BULL':'🟢','SIDEWAY':'🟡','BEAR':'🔴'}.get(regime,'⚪')
        lines.append(f"{emoji} Thị trường: <b>{regime}</b>")
        lines.append("━━━━━━━━━━━━━━━━━━━━")
    except Exception:
        pass

    lines.append("🏆 <b>Top Tín Hiệu Hôm Nay:</b>\n")

    # Top 10 rows
    if len(top10_df) == 0:
        lines.append("⚠️ Không có tín hiệu hôm nay")
    else:
        for i, row in top10_df.iterrows():
            ticker   = row.get('ticker', '')
            score    = row.get('score', 0)
            close    = row.get('close', 0)
            signals  = row.get('signals_fired', '-')
            sl       = row.get('STOP_LOSS', 0)
            t1       = row.get('TARGET_1', 0)
            sector   = row.get('sector', '')
            risk_pct = row.get('RISK_PCT', 0)

            lines.append(
                f"{i+1}. <b>{ticker}</b> | Score: {score:.1f} | "
                f"{close:,.0f} VND"
            )
            if sector:
                lines.append(f"   🏢 {sector}")
            lines.append(f"   📈 {signals}")
            if sl and t1:
                lines.append(
                    f"   🛡 SL: {sl:,.0f} | "
                    f"T1: {t1:,.0f} | "
                    f"Risk: {risk_pct:.1f}%"
                )
            lines.append("")

    lines += [
        "━━━━━━━━━━━━━━━━━━━━",
        "⚠️ <i>Tham khảo — không phải khuyến nghị đầu tư</i>",
    ]

    return "\n".join(lines)


def send_daily_alert(
    top10_df: pd.DataFrame,
    dry_run: bool = False,
) -> bool:
    """Entry point: format và gửi Telegram alert."""
    message = format_telegram_report(top10_df)

    if dry_run:
        print("=== TELEGRAM DRY RUN ===")
        print(message)
        return True

    return send_telegram_message(message)
