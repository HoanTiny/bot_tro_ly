# Chatbot Telegram dùng Claude AI

Bot Telegram đơn giản: người dùng gửi tin nhắn, bot chuyển sang Claude API để lấy câu trả lời rồi gửi lại. Đây là project học Python + gọi API + xây bot, phù hợp cho người mới bắt đầu.

## Bạn cần gì trước khi chạy

1. Python 3.10 trở lên.
2. Telegram Bot Token (bạn đã có, lấy từ @BotFather).
3. Anthropic API key: vào https://console.anthropic.com/settings/keys, đăng nhập/đăng ký, tạo key mới. Tài khoản mới thường có một khoản credit dùng thử miễn phí.

## Cài đặt

```bash
# 1. Tạo virtual environment (khuyến khích, để không ảnh hưởng Python hệ thống)
python -m venv venv
source venv/bin/activate        # Windows: venv\Scripts\activate

# 2. Cài các thư viện cần thiết
pip install -r requirements.txt

# 3. Tạo file .env từ mẫu, rồi mở lên điền token thật của bạn
cp .env.example .env
```

Mở file `.env` và điền:

```
TELEGRAM_BOT_TOKEN=token_bot_cua_ban
ANTHROPIC_API_KEY=api_key_cua_ban
```

## Chạy bot

```bash
python telegram_ai_bot.py
```

Thấy dòng log "Bot đang chạy..." là thành công. Mở Telegram, tìm bot của bạn, bấm Start hoặc gõ `/start`, rồi gửi tin nhắn để test.

Các lệnh có sẵn trong chat:

- Nhắn tự nhiên `ăn trưa 35k` (không cần lệnh) — bot tự nhận ra khoản chi và ghi vào sổ. Tin nhắn có nhắc tiền nhưng không phải chi tiêu (hỏi giá, so sánh...) vẫn được chat bình thường.
- Hỏi về chi tiêu bằng ngôn ngữ tự nhiên: "tháng này tiêu bao nhiêu tiền ăn?", "khoản chi lớn nhất là gì?" — Claude tự truy vấn database (tool use) rồi trả lời bằng số liệu thật.
- `/chi <khoản chi>` — ghi chi tiêu tường minh bằng lệnh (ví dụ: `/chi ăn trưa 45k, cà phê 30k`).
- `/chitieu` — báo cáo chi tiêu tháng này: tổng tiền, chia theo nhóm, các khoản gần nhất.
- `/remind <khi nào + việc gì>` — đặt nhắc bằng ngôn ngữ tự nhiên: `/remind 15 phút nữa họp`, `/remind 8h sáng mai nộp báo cáo`. Bot tự nhắn đúng giờ.
- `/reminders` — xem lời nhắc sắp tới; `/delremind <số>` — hủy.
- Tối Chủ nhật 20h bot tự gửi tổng kết chi tiêu tuần, kèm nhận xét do Claude viết (so sánh với tuần trước).
- `/note <nội dung>` — lưu một ghi chú (ví dụ: `/note mua sữa`).
- `/notes` — xem danh sách ghi chú đã lưu.
- `/delnote <số>` — xóa ghi chú theo số hiển thị trong `/notes`.
- `/reset` — xóa lịch sử chat, bắt đầu cuộc trò chuyện mới.

## Cấu trúc code (để hiểu, không chỉ chạy)

- `telegram_ai_bot.py` — file chính:
  - `ask_claude()`: agent loop — gửi tin nhắn + lịch sử tới Claude kèm danh sách tool (`EXPENSE_TOOLS`); nếu Claude yêu cầu tool thì chạy `run_expense_tool()` rồi gửi kết quả lại, lặp đến khi Claude trả lời xong.
  - `extract_expenses()`: dùng Claude làm structured extraction — biến câu tự nhiên ("ăn sáng 15k") thành JSON có cấu trúc (món, số tiền, nhóm) rồi kiểm tra lại từng trường trước khi lưu.
  - `start_command`, `reset_command`, `note_command`, `notes_command`, `delnote_command`, `handle_message`: các "handler" — hàm được gọi khi user gõ lệnh tương ứng hoặc gửi tin nhắn thường. Các handler lệnh đọc tham số qua `context.args`.
  - `check_reminders_job()`, `weekly_report_job()`: các job chạy nền bằng JobQueue — kiểm tra lời nhắc đến giờ mỗi 30 giây, và gửi báo cáo tuần 20h tối Chủ nhật. Lời nhắc lưu trong SQLite nên restart bot không mất.
  - `main()`: khởi tạo database + bot Telegram (kèm múi giờ Asia/Ho_Chi_Minh cho JobQueue) và bắt đầu polling (liên tục hỏi Telegram server xem có tin nhắn mới không).
- `db.py` — lớp lưu trữ SQLite: lịch sử chat lưu trong file `bot.db` (tự tạo khi chạy lần đầu), nên restart bot không mất lịch sử. Muốn xem dữ liệu bên trong, cài "DB Browser for SQLite" rồi mở file `bot.db`.

## Vài hướng mở rộng để luyện tập thêm

- Thêm lệnh `/img` cho phép gửi ảnh và hỏi Claude về nội dung ảnh (Claude hỗ trợ vision).
- Giới hạn số tin nhắn mỗi user được gửi trong 1 giờ để kiểm soát chi phí API.
- Deploy bot lên một server nhỏ (VPS, Railway, Render...) để bot chạy 24/7 thay vì chỉ chạy trên máy bạn.

## Lưu ý về chi phí

Mỗi tin nhắn gửi tới Claude API sẽ tốn một khoản phí nhỏ theo số token. Với việc học và test cá nhân, chi phí thường rất thấp, nhưng nên theo dõi usage tại https://console.anthropic.com để tránh bất ngờ.
