# VN Quant Signal Engine 🚀📈

Hệ thống phân tích định lượng và tạo tín hiệu giao dịch chứng khoán Việt Nam tự động (HOSE, HNX, UPCOM) sử dụng dữ liệu trực tiếp từ `vnstock`, tích hợp bộ lọc thanh khoản, phân tích cấu trúc Smart Money Concepts (SMC), Ichimoku Cloud, ADX và tự động gửi cảnh báo qua **Telegram Bot** & **Email Report**.

---

## ✨ Tính Năng Nổi Bật

1. **Bộ lọc chất lượng & thanh khoản (Quality & Liquidity Filters)**:
   - Loại bỏ các cổ phiếu thanh khoản thấp, giá dưới 5,000 VND hoặc giao dịch ngắt quãng.
   - Tính toán theo giá trị giao dịch thực tế (`ADTV_20d`).
2. **5 Chiến lược Định lượng (Quantitative Strategies)**:
   - **ICHIMOKU_TREND**: Theo dấu xu hướng mây Kumo kết hợp xác nhận phá vỡ cấu trúc đỉnh (`BOS_BULL`) và động lượng `ADX > 25`.
   - **SMC_REVERSAL**: Nhận diện vùng Order Block (OB) và Change of Character (`CHOCH`).
   - **MOMENTUM_BREAKOUT**: Bứt phá khối lượng và động lượng RSI/MACD.
   - **GOLDEN_CROSS_PLUS**: Giao cắt đường trung bình kết hợp xu hướng mây.
   - **OB_BOUNCE**: Bật nảy tại vùng hỗ trợ tổ chức.
3. **Quản trị Rủi ro & Đi lệnh Tự động (Risk & Position Sizing)**:
   - Tự động tính toán điểm vào (`Entry`), điểm cắt lỗ (`Stop Loss = Entry - 2*ATR`), và mục tiêu chốt lời (`Target 1/2`).
   - Kiểm soát khối lượng vào lệnh theo tỷ lệ rủi ro tối đa $\le 1\%$ tài khoản.
4. **Tự động hóa Đa kênh (Multi-channel Automation)**:
   - Gửi tin nhắn ngắn gọn qua **Telegram Bot** tới điện thoại ngay sau phiên giao dịch.
   - Gửi báo cáo chi tiết **HTML Report** qua Email.
   - Tích hợp sẵn **GitHub Actions** (`.github/workflows/daily_pipeline.yml`) để tự động hóa 100% trên Đám mây vào 16:00 hàng ngày.

---

## 🛠️ Cài đặt & Sử dụng cục bộ (Local Setup)

### 1. Cài đặt môi trường
```bash
python -m venv .venv
# Windows
.venv\Scripts\activate
# Linux/macOS
source .venv/bin/activate

pip install -r requirements.txt
```

### 2. Cấu hình bảo mật (`.env`)
Tạo file `.env` từ `.env.example` và điền thông tin cá nhân (file `.env` đã được cấu hình trong `.gitignore`, tuyệt đối không commit lên Git):
```ini
TELEGRAM_TOKEN=your_bot_token
TELEGRAM_CHAT_ID=your_chat_id
SMTP_USER=your_email@gmail.com
SMTP_PASS=your_app_password
REPORT_RECIPIENTS=your_email@gmail.com
```

### 3. Chạy pipeline hàng ngày
```bash
python run_daily.py
```

---

## ☁️ Cấu hình chạy Tự động trên GitHub Actions (Miễn phí 24/7)

1. Đẩy repo này lên GitHub.
2. Vào **Settings** $\rightarrow$ **Secrets and variables** $\rightarrow$ **Actions** $\rightarrow$ **New repository secret**, thêm các biến sau:
   - `TELEGRAM_TOKEN`
   - `TELEGRAM_CHAT_ID`
   - `SMTP_USER`
   - `SMTP_PASS`
   - `REPORT_RECIPIENTS`
3. Hệ thống sẽ tự động chạy lúc **16:00 (09:00 UTC)** từ Thứ 2 đến Thứ 6 hàng tuần và gửi thông báo cho bạn.

---

## 🔒 Bảo mật
Toàn bộ khóa API, Token Telegram và Mật khẩu Email được quản lý qua biến môi trường (`os.getenv`), không hardcode trong mã nguồn. File `.env`, thư mục `data/` và `logs/` được loại bỏ hoàn toàn khỏi Git qua `.gitignore`.
