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
from src.utils.security import redact_sensitive_text


import time


def send_telegram_message(text: str, max_retries: int = 3) -> bool:
    """
    Gửi text message qua Telegram Bot API.
    Hỗ trợ tự động chia nhỏ tin nhắn (>4000 ký tự) và retry khi gặp lỗi mạng/429.
    """
    load_dotenv()
    token   = os.getenv('TELEGRAM_TOKEN', '').strip()
    chat_id = os.getenv('TELEGRAM_CHAT_ID', '').strip()

    if not token or not chat_id:
        print("⚠️  Telegram chưa cấu hình trong .env")
        return False

    api_url = f"https://api.telegram.org/bot{token}/sendMessage"

    # Telegram giới hạn tối đa 4096 ký tự/tin nhắn -> cắt chunk 4000 ký tự
    chunks = []
    current_chunk = ""
    for line in text.splitlines():
        if len(current_chunk) + len(line) + 1 > 4000:
            chunks.append(current_chunk)
            current_chunk = line
        else:
            current_chunk = f"{current_chunk}\n{line}" if current_chunk else line
    if current_chunk:
        chunks.append(current_chunk)

    all_success = True
    for idx, chunk in enumerate(chunks):
        success = False
        for attempt in range(1, max_retries + 1):
            try:
                resp = requests.post(
                    api_url,
                    json={
                        'chat_id'   : chat_id,
                        'text'      : chunk,
                        'parse_mode': 'HTML',
                    },
                    timeout=10,
                )
                resp.raise_for_status()
                success = True
                break
            except Exception as e:
                safe_error = redact_sensitive_text(str(e))
                if attempt < max_retries:
                    time.sleep(2 * attempt)
                else:
                    print(f"❌ Telegram thất bại (chunk {idx+1}/{len(chunks)}): {safe_error}")
                    Path('logs').mkdir(parents=True, exist_ok=True)
                    with Path('logs/telegram_log.txt').open('a', encoding='utf-8') as f:
                        f.write(f"{date.today()} | FAILED | {safe_error}\n")
        if not success:
            all_success = False

    if all_success:
        print("✅ Telegram gửi thành công")
    return all_success




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
