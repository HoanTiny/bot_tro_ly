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

- Nhắn tự nhiên `ăn trưa 35k` (không cần lệnh) — bot tự nhận ra khoản chi và ghi vào sổ. Nói `hôm qua ăn tối 200k` hay `thứ 2 vừa rồi đổ xăng 60k` thì ghi đúng ngày đó. Tin nhắn có nhắc tiền nhưng không phải chi tiêu (hỏi giá, so sánh...) vẫn được chat bình thường.
- Hỏi về chi tiêu bằng ngôn ngữ tự nhiên: "tháng này tiêu bao nhiêu tiền ăn?", "khoản chi lớn nhất là gì?" — Claude tự truy vấn database (tool use) rồi trả lời bằng số liệu thật.
- `/chi <khoản chi>` — ghi chi tiêu tường minh bằng lệnh (ví dụ: `/chi ăn trưa 45k, cà phê 30k`).
- `/chitieu` — báo cáo chi tiêu tháng này: tổng tiền, chia theo nhóm, các khoản gần nhất.
- `/baocao` — xuất file Excel chi tiêu tháng này (2 sheet: chi tiết + tổng hợp theo nhóm); `/baocao 6` xuất tháng 6.
- `/remind <khi nào + việc gì>` — đặt nhắc bằng ngôn ngữ tự nhiên: `/remind 15 phút nữa họp`, `/remind 8h sáng mai nộp báo cáo`. Bot tự nhắn đúng giờ.
- `/reminders` — xem lời nhắc sắp tới; `/delremind <số>` — hủy.
- Tối Chủ nhật 20h bot tự gửi tổng kết chi tiêu tuần, kèm nhận xét do Claude viết (so sánh với tuần trước).
- **Gửi file PDF/Word/TXT vào chat** — bot đọc, đánh chỉ mục, rồi trả lời mọi câu hỏi về tài liệu kèm tên nguồn (RAG). `/docs` xem danh sách, `/deldoc <số>` xóa.
- `/note <nội dung>` — lưu một ghi chú (ví dụ: `/note mua sữa`).
- `/notes` — xem danh sách ghi chú đã lưu.
- `/delnote <số>` — xóa ghi chú theo số hiển thị trong `/notes`.
- `/reset` — xóa lịch sử chat, bắt đầu cuộc trò chuyện mới.

## Cấu trúc dự án (mỗi file một trách nhiệm)

- `telegram_ai_bot.py` — file chính, chỉ **lắp ráp**: khởi tạo app, đăng ký handler + job, chạy polling.
- `config.py` — đọc `.env`, các hằng số dùng chung (model, prompt hệ thống, nhóm chi tiêu, múi giờ).
- `db.py` — tầng lưu trữ SQLite (file `bot.db`): lịch sử chat, ghi chú, chi tiêu, lời nhắc. Muốn xem dữ liệu, cài "DB Browser for SQLite" mở `bot.db`.
- `ai.py` — tầng AI, dùng `AsyncAnthropic` (mọi lệnh gọi đều `await`, bot không đơ khi chờ):
  - `ask_claude()`: agent loop — Claude được cấp tool truy vấn sổ chi tiêu, tự quyết định gọi tool nào rồi trả lời bằng số liệu thật.
  - `extract_expenses()` / `extract_reminder()`: structured extraction — biến câu tự nhiên thành JSON, có hàm `validate_*` kiểm tra lại từng trường trước khi tin.
- `handlers.py` — các hàm xử lý lệnh Telegram (`/chi`, `/remind`...) và tin nhắn thường; đọc tham số qua `context.args`.
- `jobs.py` — việc chạy nền bằng JobQueue: kiểm tra lời nhắc mỗi 30 giây, báo cáo tuần tối Chủ nhật.
- `rag.py` — phần xử lý tài liệu của RAG: đọc chữ từ PDF/Word/TXT (`extract_text`) và chia nhỏ thành đoạn (`chunk_text`). Tìm kiếm dùng SQLite FTS5 với BM25 (trong `db.py`), tìm được cả khi gõ không dấu; Claude truy cập qua tool `search_documents` trong agent loop.
- `report.py` — xuất báo cáo Excel bằng openpyxl (tạo file trong RAM, gửi thẳng qua Telegram).
- `utils.py` — tiện ích nhỏ (`format_money`, đổi ngày địa phương sang UTC).
- `tests/` — bộ test tự động (41 test, không gọi API). Chạy: `pytest` (mỗi test được cấp database tạm riêng, không đụng `bot.db` thật).

## Vài hướng mở rộng để luyện tập thêm

- Thêm lệnh `/img` cho phép gửi ảnh và hỏi Claude về nội dung ảnh (Claude hỗ trợ vision).
- Giới hạn số tin nhắn mỗi user được gửi trong 1 giờ để kiểm soát chi phí API.
- Deploy bot lên một server nhỏ (VPS, Railway, Render...) để bot chạy 24/7 thay vì chỉ chạy trên máy bạn.

## Lưu ý về chi phí

Mỗi tin nhắn gửi tới Claude API sẽ tốn một khoản phí nhỏ theo số token. Với việc học và test cá nhân, chi phí thường rất thấp, nhưng nên theo dõi usage tại https://console.anthropic.com để tránh bất ngờ.
