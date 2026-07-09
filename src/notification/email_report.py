"""
Automated Daily Reporting & Email Notification — Step 6
=========================================================
Generates institutional-quality HTML email reports summarizing Top 10 signals
and strategy backtest statistics, and dispatches via SMTP.
"""

from __future__ import annotations

from datetime import date, datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
import logging
from pathlib import Path
import smtplib
import sys
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="ignore")
from typing import Optional
import pandas as pd

from config.settings import LOG_DIR

log = logging.getLogger("email_report")


def generate_html_report(top10_df: pd.DataFrame) -> str:
    """Tạo HTML email report chuẩn institutional."""
    today_str = date.today().strftime("%d/%m/%Y")
    run_time  = datetime.now().strftime("%H:%M")

    # Row colors theo score
    def row_color(score):
        if score >= 3.0:
            return "#d4edda"   # xanh lá
        if score >= 2.0:
            return "#fff3cd"   # vàng
        return "#ffffff"

    # Build table rows
    rows_html = ""
    display_cols = {
        "ticker":        "Ticker",
        "sector":        "Ngành",
        "score":         "Score",
        "signals_fired": "Signals",
        "close":         "Giá",
        "volume":        "Khối lượng",
        "RSI_14":        "RSI(14)",
        "ADX_14":        "ADX(14)",
        "ADTV_tỷ":       "ADTV 20d (tỷ)",
    }

    if top10_df.empty:
        rows_html = '<tr><td colspan="10" style="padding:12px;text-align:center;color:#666">Không có cổ phiếu thỏa mãn điều kiện mua hôm nay</td></tr>'
    else:
        for i, row in top10_df.iterrows():
            bg = row_color(row.get("score", 0))
            sig_val = str(row.get("signals_fired", "-")).strip()
            is_active_signal = sig_val not in ["-", "", "None"]
            cells = f'<td style="padding:8px;text-align:center">{i+1}</td>'
            for col in display_cols:
                val = row.get(col, "")
                if col in ["close", "volume"] and pd.notna(val) and val != "":
                    val = f"{float(val):,.0f}"
                elif col in ["RSI_14", "ADX_14", "score"]:
                    val = f"{val:.2f}" if pd.notna(val) and val != "" else "-"
                elif col == "ADTV_tỷ":
                    val = f"{val:.1f}" if pd.notna(val) and val != "" else "-"
                cell_style = "padding:8px;text-align:center"
                if col == "ticker" and is_active_signal:
                    cell_style += ";font-weight:bold"
                cells += f'<td style="{cell_style}">{val}</td>'
            rows_html += f'<tr style="background:{bg}">{cells}</tr>'

    headers_html = "".join(
        f'<th style="padding:10px">{h}</th>'
        for h in display_cols.values()
    )

    from src.strategies.signal_engine import TOTAL_CAPITAL
    risk_rows = ''
    for i, row in top10_df.iterrows():
        if 'STOP_LOSS' not in row or pd.isna(row.get('STOP_LOSS')):
            continue
        risk_rows += f"""
        <tr>
          <td style="padding:8px;text-align:center">{i+1}</td>
          <td style="padding:8px;font-weight:bold">{row['ticker']}</td>
          <td style="padding:8px;text-align:center">{row['close']:,.0f}</td>
          <td style="padding:8px;text-align:center;color:#e53935">
              {row.get('STOP_LOSS',0):,.0f}</td>
          <td style="padding:8px;text-align:center;color:#43a047">
              {row.get('TARGET_1',0):,.0f}</td>
          <td style="padding:8px;text-align:center;color:#1b5e20">
              {row.get('TARGET_2',0):,.0f}</td>
          <td style="padding:8px;text-align:center">
              {row.get('RISK_PCT',0):.1f}%</td>
          <td style="padding:8px;text-align:center">
              {row.get('SL_VALUE_TỶ',0):.2f} tỷ</td>
          <td style="padding:8px;text-align:center">
              {row.get('SL_PCT_VỐN',0):.1f}%</td>
        </tr>"""

    if risk_rows != '':
        risk_table = f"""
    <h3 style="color:#1a237e;margin-top:24px">
        🛡️ Risk Parameters (Vốn: {TOTAL_CAPITAL/1e9:.0f} tỷ VND)
    </h3>
    <table border="1" cellspacing="0" 
           style="width:100%;border-collapse:collapse;font-size:12px">
      <thead>
        <tr style="background:#b71c1c;color:white">
          <th style="padding:8px">Rank</th>
          <th style="padding:8px">Ticker</th>
          <th style="padding:8px">Entry</th>
          <th style="padding:8px">Stop Loss</th>
          <th style="padding:8px">Target 1</th>
          <th style="padding:8px">Target 2</th>
          <th style="padding:8px">Risk %</th>
          <th style="padding:8px">Giá trị vị thế</th>
          <th style="padding:8px">% Vốn</th>
        </tr>
      </thead>
      <tbody>{risk_rows}</tbody>
    </table>
    <p style="font-size:11px;color:#999;margin-top:8px">
      * Stop Loss = Entry - 2×ATR(14) | 
      Target 1 = Entry + 2×ATR(14) | 
      Target 2 = Entry + 3×ATR(14) | 
      Risk/lệnh ≤ 1% vốn
    </p>"""
    else:
        risk_table = ""

    html = f"""
    <html>
    <body style="font-family:Arial,sans-serif;max-width:900px;margin:20px auto;padding:0 16px">

    <h2 style="color:#1a237e;border-bottom:2px solid #1a237e;padding-bottom:6px;margin-top:6px">
        📊 VN Quant Signal Report — {today_str}
    </h2>
    <p style="color:#333;font-size:14px;margin-top:6px;font-weight:bold">✅ Top 10 cổ phiếu tạo tín hiệu mua ngắn hạn</p>

    <table border="1" cellspacing="0" style="width:100%;border-collapse:collapse;font-size:13px;margin-top:10px">
      <thead>
        <tr style="background:#1a237e;color:white">
          <th style="padding:10px">Rank</th>
          {headers_html}
        </tr>
      </thead>
      <tbody>
        {rows_html}
      </tbody>
    </table>

    {risk_table}

    <h3 style="margin-top:16px;color:#1a237e">📈 Strategy Performance (Backtest 2016–2026)</h3>
    <table border="1" cellspacing="0" style="width:100%;border-collapse:collapse;font-size:13px">
      <tr style="background:#e8eaf6">
        <th style="padding:8px">Strategy</th>
        <th style="padding:8px">Hold</th>
        <th style="padding:8px">Trades</th>
        <th style="padding:8px">Win Rate</th>
        <th style="padding:8px">Profit Factor</th>
        <th style="padding:8px">Năm tốt</th>
      </tr>
      <tr>
        <td style="padding:8px">Golden Cross + Ichimoku</td>
        <td style="padding:8px;text-align:center">20d</td>
        <td style="padding:8px;text-align:center">359</td>
        <td style="padding:8px;text-align:center">55.15%</td>
        <td style="padding:8px;text-align:center">2.02</td>
        <td style="padding:8px;text-align:center">6/11 năm</td>
      </tr>
      <tr style="background:#f5f5f5">
        <td style="padding:8px">Ichimoku Trend</td>
        <td style="padding:8px;text-align:center">20d</td>
        <td style="padding:8px;text-align:center">3,223</td>
        <td style="padding:8px;text-align:center">52.78%</td>
        <td style="padding:8px;text-align:center">1.61</td>
        <td style="padding:8px;text-align:center">7/11 năm</td>
      </tr>
    </table>

    <p style="margin-top:16px;color:#777;font-size:12px;border-top:1px solid #eee;padding-top:10px">
      ⌛ Pipeline chạy lúc {run_time} ngày {today_str} &nbsp;|&nbsp; Dữ liệu: vnstock &nbsp;|&nbsp; ⚠️ Thông tin tham khảo — không phải khuyến nghị đầu tư
    </p>
    </body>
    </html>
    """
    return html


def send_email_report(html_content: str, subject: Optional[str] = None) -> bool:
    """Gửi HTML email qua SMTP. Credentials từ config.settings / .env"""
    from config.settings import SMTP_HOST, SMTP_PORT, SMTP_USER, SMTP_PASS, REPORT_RECIPIENTS

    if not SMTP_USER or not REPORT_RECIPIENTS:
        print("⚠️  SMTP chưa cấu hình trong .env — skip email")
        return False

    subject = subject or f"[VN Signal] Top 10 — {date.today():%d/%m/%Y}"

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"]    = SMTP_USER
    msg["To"]      = ", ".join(REPORT_RECIPIENTS)
    msg.attach(MIMEText(html_content, "html", "utf-8"))

    LOG_DIR.mkdir(parents=True, exist_ok=True)
    log_path = LOG_DIR / "email_log.txt"

    try:
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=10) as server:
            server.ehlo()
            server.starttls()
            server.login(SMTP_USER, SMTP_PASS)
            server.sendmail(SMTP_USER, REPORT_RECIPIENTS, msg.as_string())
        with log_path.open("a", encoding="utf-8") as f:
            f.write(f"{datetime.now()} | SUCCESS | {subject}\n")
        print(f"[SUCCESS] Email gửi thành công đến {len(REPORT_RECIPIENTS)} người nhận (đã ẩn địa chỉ vì bảo mật)")
        return True
    except Exception as e:
        with log_path.open("a", encoding="utf-8") as f:
            f.write(f"{datetime.now()} | FAILED | {e}\n")
        print(f"[FAILED] Email thất bại: {e}")
        return False
