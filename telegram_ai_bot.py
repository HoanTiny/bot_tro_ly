"""
Chatbot Telegram dùng Claude API — file chính, chỉ làm nhiệm vụ LẮP RÁP.

Cấu trúc dự án (mỗi file một trách nhiệm):
    config.py   — đọc .env, các hằng số dùng chung
    db.py       — lưu trữ SQLite (chat, ghi chú, chi tiêu, lời nhắc)
    ai.py       — mọi thứ gọi Claude: chat + tool use, bóc tách chi tiêu/lời nhắc
    handlers.py — các hàm xử lý lệnh và tin nhắn Telegram
    jobs.py     — việc chạy nền: kiểm tra lời nhắc, báo cáo tuần
    utils.py    — hàm tiện ích nhỏ (format tiền...)
    tests/      — bộ test tự động, chạy bằng: pytest

Chạy bot:
    pip install -r requirements.txt
    cp .env.example .env   # rồi điền token thật vào .env
    python telegram_ai_bot.py
"""

import logging
from datetime import time

import pytz
from telegram import BotCommand
from telegram.ext import Application, CommandHandler, Defaults, MessageHandler, filters

import db
import handlers
import jobs
from config import TELEGRAM_BOT_TOKEN, TIMEZONE

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)


async def setup_commands(app: Application) -> None:
    """Đăng ký menu lệnh với Telegram — chạy 1 lần khi bot khởi động.

    Đây là danh sách hiện ra khi user gõ "/" trong khung chat. Telegram lưu
    danh sách này ở phía server, nên bot tắt rồi menu vẫn còn; mỗi lần khởi
    động ta ghi đè lại để menu luôn khớp với code hiện tại.
    """
    await app.bot.set_my_commands(
        [
            BotCommand("start", "Giới thiệu bot và các lệnh"),
            BotCommand("chi", "Ghi chi tiêu: /chi ăn sáng 15k"),
            BotCommand("chitieu", "Báo cáo chi tiêu tháng này"),
            BotCommand("baocao", "Xuất Excel chi tiêu: /baocao hoặc /baocao 6"),
            BotCommand("undo", "Xóa khoản thu/chi vừa ghi nhầm"),
            BotCommand("remind", "Đặt nhắc: /remind 8h sáng mai họp"),
            BotCommand("reminders", "Xem các lời nhắc sắp tới"),
            BotCommand("delremind", "Hủy lời nhắc theo số"),
            BotCommand("docs", "Xem tài liệu đã gửi cho bot"),
            BotCommand("deldoc", "Xóa tài liệu theo số"),
            BotCommand("note", "Lưu ghi chú: /note mua sữa"),
            BotCommand("notes", "Xem các ghi chú đã lưu"),
            BotCommand("delnote", "Xóa ghi chú theo số: /delnote 1"),
            BotCommand("reset", "Xóa lịch sử chat, bắt đầu lại"),
        ]
    )


def main() -> None:
    db.init_db()  # tạo file bot.db + các bảng nếu chưa có

    # post_init: hàm async được gọi 1 lần sau khi bot khởi tạo xong.
    # Các *_timeout: nới thời gian chờ mạng lên 20-30s (mặc định 5s hơi gắt —
    # mạng tới api.telegram.org thỉnh thoảng chập chờn là bị TimedOut ngay).
    # defaults(tzinfo=...): múi giờ cho JobQueue — không khai báo thì giờ
    # trong run_daily bị hiểu là giờ UTC, báo cáo "20h tối" sẽ gửi lúc 3h sáng!
    app = (
        Application.builder()
        .token(TELEGRAM_BOT_TOKEN)
        .post_init(setup_commands)
        .connect_timeout(20)
        .read_timeout(30)
        .write_timeout(30)
        .defaults(Defaults(tzinfo=pytz.timezone(TIMEZONE)))
        .build()
    )

    app.add_handler(CommandHandler("start", handlers.start_command))
    app.add_handler(CommandHandler("reset", handlers.reset_command))
    app.add_handler(CommandHandler("note", handlers.note_command))
    app.add_handler(CommandHandler("notes", handlers.notes_command))
    app.add_handler(CommandHandler("delnote", handlers.delnote_command))
    app.add_handler(CommandHandler("chi", handlers.chi_command))
    app.add_handler(CommandHandler("chitieu", handlers.chitieu_command))
    app.add_handler(CommandHandler("baocao", handlers.baocao_command))
    app.add_handler(CommandHandler("undo", handlers.undo_command))
    app.add_handler(CommandHandler("remind", handlers.remind_command))
    app.add_handler(CommandHandler("reminders", handlers.reminders_command))
    app.add_handler(CommandHandler("delremind", handlers.delremind_command))
    app.add_handler(CommandHandler("docs", handlers.docs_command))
    app.add_handler(CommandHandler("deldoc", handlers.deldoc_command))
    # Nhận file gửi vào chat (PDF/Word/TXT) để đọc vào bộ nhớ tài liệu (RAG)
    app.add_handler(MessageHandler(filters.Document.ALL, handlers.handle_document))
    # Nhận ảnh (hóa đơn) -> Claude Vision đọc và ghi sổ chi tiêu
    app.add_handler(MessageHandler(filters.PHOTO, handlers.handle_photo))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handlers.handle_message))
    app.add_error_handler(handlers.error_handler)

    # Đăng ký các job chạy nền:
    # - kiểm tra lời nhắc mỗi 30 giây (bắt đầu sau 10s để bot khởi động xong)
    # - báo cáo tuần: chạy 20h mỗi tối, trong hàm tự kiểm tra "có phải chủ nhật"
    app.job_queue.run_repeating(jobs.check_reminders_job, interval=30, first=10)
    app.job_queue.run_daily(jobs.weekly_report_job, time=time(20, 0))

    logger.info("Bot đang chạy... Nhấn Ctrl+C để dừng.")
    app.run_polling()


if __name__ == "__main__":
    main()
