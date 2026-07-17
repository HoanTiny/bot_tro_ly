# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

Bot Telegram trợ lý cá nhân tiếng Việt chạy trên Claude API: chat, ghi sổ thu/chi, nhắc việc, đọc hóa đơn qua Vision, RAG tài liệu. Toàn bộ comment, docstring, commit message và trả lời người dùng đều bằng **tiếng Việt** — giữ nguyên quy ước này. Code có tính chất giáo dục (dự án học Python), docstring thường giải thích "vì sao" khá kỹ.

## Lệnh thường dùng

```bash
pip install -r requirements.txt   # cài dependency (Python 3.10+)
python telegram_ai_bot.py         # chạy bot (cần .env: TELEGRAM_BOT_TOKEN, ANTHROPIC_API_KEY)
pytest                            # chạy toàn bộ test (không gọi API, không đụng bot.db thật)
pytest tests/test_money_parser.py             # chạy 1 file test
pytest tests/test_db.py::test_add_expense -v  # chạy 1 test cụ thể
```

**Quan trọng khi chạy bot trên máy này**: bot có thể đang chạy 24/7 qua Task Scheduler (task `TelegramAIBot`, vòng lặp trong `run_bot.bat`, log ở `logs\bot.log`). Chạy `python telegram_ai_bot.py` bằng tay khi task đang chạy sẽ gây lỗi Telegram Conflict (2 tiến trình cùng polling) — phải chạy `stop_bot.bat` trước.

`requirements.txt` ghim `httpx==0.27.2` vì `anthropic==0.34.2` chưa tương thích httpx >= 0.28 — đừng nâng httpx riêng lẻ.

## Kiến trúc

Mỗi file một trách nhiệm; `telegram_ai_bot.py` chỉ lắp ráp (đăng ký handler + job, chạy polling). Import một chiều: `handlers.py`/`jobs.py` gọi xuống `ai.py`, `db.py`, `money_parser.py`, `rag.py`, `report.py`; tất cả đọc hằng số từ `config.py`.

### Luồng xử lý tin nhắn thường (handlers.handle_message)

Kiến trúc 3 tầng, rẻ trước đắt sau:

1. **Regex `ai.MONEY_HINT`** — tin nhắn không có "mùi tiền" thì bỏ qua thẳng bước bóc tách.
2. **`money_parser.parse_expenses()`** — parser regex cục bộ (0 token). Nguyên tắc thiết kế: "tự tin thì làm, không chắc thì nhường AI" — trả về `None` (nhường Claude) khi câu có dấu hiệu mơ hồ (câu hỏi, dự định, thời gian phức tạp). Độ chính xác quan trọng hơn độ phủ: thà gọi AI oan còn hơn ghi sai vào sổ.
3. **`ai.extract_expenses()`** (Haiku) — structured extraction ra JSON; nếu vẫn rỗng thì rơi xuống `ai.ask_claude()` chat bình thường.

### Tầng AI (ai.py)

- **Chiến lược 2 model** (đặt trong `config.py`): `CLAUDE_MODEL` (Sonnet) cho chat/agent loop/đọc hóa đơn Vision; `EXTRACT_MODEL` (Haiku) cho bóc tách chi tiêu/lời nhắc. Dùng `AsyncAnthropic` — mọi lời gọi đều `await` để bot không đơ.
- **`ask_claude()` là agent loop**: Claude được cấp 5 tool (`search_documents`, `expense_summary`, `expense_list`, `update_expense`, `delete_expense`), lặp tối đa 5 vòng đến khi `stop_reason != "tool_use"`.
- **Bảo mật tool**: `chat_id` LUÔN do code truyền vào `run_tool()`, không bao giờ nằm trong input schema — Claude không thể đọc/sửa dữ liệu của chat khác. Mọi query db đều lọc theo `chat_id`. Giữ nguyên tắc này khi thêm tool mới.
- **Không tin đầu ra LLM mù quáng**: mỗi hàm `extract_*` có hàm `validate_*` thuần Python đi kèm (kiểm tra từng trường, vứt phần tử sai). Các hàm validate tách riêng, không gọi API, để test được — pattern này áp dụng cho mọi extraction mới.
- LLM không biết thời gian hiện tại — ngày/giờ + thứ trong tuần được ghép vào system prompt **lúc gọi** (không đặt trong hằng số, vì hằng số chỉ tạo 1 lần khi khởi động).
- `_parse_json()` parse "khoan dung": chịu được code fence và chữ thừa quanh JSON (Haiku hay vi phạm "DUY NHẤT JSON").

### Tầng dữ liệu (db.py)

- SQLite file `bot.db` cùng thư mục code; mỗi thao tác mở/đóng kết nối riêng. Tiền lưu VND **số nguyên** (không bao giờ float).
- Bảng `expenses` dùng chung cho cả thu và chi, phân biệt bằng cột `kind` (`'chi'`/`'thu'`); khoản thu có category cố định `"thu nhập"` (nằm ngoài `EXPENSE_CATEGORIES`).
- **Quy ước thời gian dễ nhầm**: `created_at` lưu UTC (SQLite tự điền), mọi query lọc theo ngày/tháng đều bọc `datetime(created_at, 'localtime')`. Riêng `reminders.remind_at` lưu **giờ địa phương** dạng `"YYYY-MM-DD HH:MM"` — so sánh chuỗi trực tiếp với giờ máy.
- Migration schema bằng `_add_column_if_missing()` (ALTER TABLE) trong `init_db()` — thêm cột mới cho bảng đã có dữ liệu theo cách này.
- RAG: bảng ảo FTS5 `chunks` với tokenizer `unicode61 remove_diacritics 2` (tìm không dấu vẫn trúng), xếp hạng BM25. `rag.py` chỉ lo đọc file (PDF/Word/TXT) và chia đoạn; tìm kiếm nằm trong `db.search_chunks()`.

### Nền và múi giờ

- `jobs.py` chạy qua JobQueue: kiểm tra lời nhắc mỗi 30s, báo cáo tuần chạy `run_daily` 20h rồi tự kiểm tra "có phải Chủ nhật". `mark_reminder_sent()` chỉ gọi SAU khi gửi thành công (gửi lỗi thì lần sau thử lại).
- `TIMEZONE = "Asia/Ho_Chi_Minh"` được truyền vào `Defaults(tzinfo=...)` của Application — thiếu nó thì giờ trong `run_daily` bị hiểu là UTC.

### Test (tests/)

Fixture `fresh_db` trong `conftest.py` là `autouse`: mỗi test tự động được monkeypatch `db.DB_PATH` sang file tạm. Test không bao giờ gọi Claude API — logic cần test phải tách khỏi lời gọi API (như các hàm `validate_*`, `money_parser`).

## Quy ước khác

- Nhóm chi tiêu hợp lệ liệt kê cứng trong `config.EXPENSE_CATEGORIES` — thêm nhóm mới phải sửa ở đây (prompt extraction tự cập nhật theo).
- Đổi tính cách bot: sửa `SYSTEM_PROMPT` trong `config.py`.
- Thêm lệnh Telegram mới cần 3 chỗ: handler trong `handlers.py`, `add_handler` trong `telegram_ai_bot.py`, và `BotCommand` trong `setup_commands()` (menu "/" của Telegram).
