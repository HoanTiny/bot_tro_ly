"""
Các handler Telegram: mỗi hàm ứng với một lệnh (/start, /chi...) hoặc
tin nhắn thường. Handler chỉ làm 3 việc: đọc input -> gọi ai.py / db.py
-> trả lời. Logic AI nằm ở ai.py, lưu trữ nằm ở db.py.
"""

import logging
from datetime import datetime

from telegram import Update
from telegram.ext import ContextTypes

import ai
import db
import rag
import report
from utils import format_money, local_date_to_utc_timestamp

MAX_DOCUMENT_MB = 15  # chặn file quá to (Bot API cũng chỉ cho bot tải tối đa 20MB)

logger = logging.getLogger(__name__)


def record_expenses(chat_id: int, expenses: list[dict]) -> str:
    """Lưu các khoản chi vào database và trả về tin nhắn xác nhận.

    Dùng chung cho /chi và tin nhắn tự nhiên — logic một nơi, sửa một chỗ.
    """
    lines = []
    for e in expenses:
        # Có trường date (người dùng nói "hôm qua"...) -> ghi lùi về ngày đó
        backdate = e.get("date")
        db.add_expense(
            chat_id, e["item"], e["amount"], e["category"],
            created_at=local_date_to_utc_timestamp(backdate) if backdate else None,
        )
        day_note = f" — hôm {backdate[8:10]}/{backdate[5:7]}" if backdate else ""
        lines.append(f"• {e['item']}: {format_money(e['amount'])} ({e['category']}){day_note}")

    month_total = sum(
        amount for _, amount in db.get_month_summary(chat_id, datetime.now().strftime("%Y-%m"))
    )
    return (
        "Đã ghi:\n" + "\n".join(lines)
        + f"\n\nTổng tháng này: {format_money(month_total)} — xem chi tiết: /chitieu"
    )


async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "Chào bạn! Mình là chatbot dùng Claude AI.\n"
        "Gửi tin nhắn bất kỳ để bắt đầu trò chuyện.\n\n"
        "Các lệnh:\n"
        "/chi <khoản chi> — ghi chi tiêu (vd: /chi ăn sáng 15k)\n"
        "/chitieu — báo cáo chi tiêu tháng này\n"
        "/baocao — xuất file Excel chi tiêu\n"
        "Gửi file PDF/Word/TXT — mình đọc và trả lời câu hỏi về tài liệu\n"
        "/docs — xem tài liệu đã gửi, /deldoc <số> — xóa\n"
        "/remind <khi nào + việc gì> — đặt nhắc (vd: /remind 8h sáng mai họp)\n"
        "/reminders — xem lời nhắc sắp tới\n"
        "/delremind <số> — hủy lời nhắc\n"
        "/note <nội dung> — lưu một ghi chú\n"
        "/notes — xem các ghi chú đã lưu\n"
        "/delnote <số> — xóa ghi chú theo số\n"
        "/reset — xóa lịch sử chat, bắt đầu lại"
    )


async def reset_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    db.clear_history(update.effective_chat.id)
    await update.message.reply_text("Đã xóa lịch sử chat. Bắt đầu cuộc trò chuyện mới nhé!")


async def note_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Lưu ghi chú: /note mua sữa cho con.

    context.args là phần sau tên lệnh, đã tách sẵn theo khoảng trắng:
    gõ "/note mua sữa" thì context.args = ["mua", "sữa"].
    """
    content = " ".join(context.args).strip()
    if not content:
        await update.message.reply_text("Cách dùng: /note <nội dung ghi chú>")
        return

    note_id = db.add_note(update.effective_chat.id, content)
    await update.message.reply_text(f"Đã lưu ghi chú #{note_id}: {content}")


async def notes_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Liệt kê tất cả ghi chú của người dùng."""
    notes = db.get_notes(update.effective_chat.id)
    if not notes:
        await update.message.reply_text("Bạn chưa có ghi chú nào. Tạo bằng /note <nội dung>.")
        return

    lines = []
    for note_id, content, created_at in notes:
        # created_at dạng "2026-07-16 11:22:05" -> cắt chuỗi lấy "16/07"
        day = f"{created_at[8:10]}/{created_at[5:7]}"
        lines.append(f"#{note_id} ({day}): {content}")
    await update.message.reply_text("Ghi chú của bạn:\n" + "\n".join(lines))


async def delnote_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Xóa ghi chú theo số: /delnote 3."""
    # Kiểm tra input trước khi dùng — user có thể gõ /delnote hoặc /delnote abc
    if len(context.args) != 1 or not context.args[0].isdigit():
        await update.message.reply_text("Cách dùng: /delnote <số ghi chú> (xem số bằng /notes)")
        return

    note_id = int(context.args[0])
    if db.delete_note(update.effective_chat.id, note_id):
        await update.message.reply_text(f"Đã xóa ghi chú #{note_id}.")
    else:
        await update.message.reply_text(f"Không tìm thấy ghi chú #{note_id}. Xem danh sách bằng /notes.")


async def chi_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Ghi chi tiêu bằng ngôn ngữ tự nhiên: /chi ăn trưa 45k, cà phê 30k."""
    content = " ".join(context.args).strip()
    if not content:
        await update.message.reply_text("Cách dùng: /chi <khoản chi> — ví dụ: /chi ăn sáng 15k")
        return

    chat_id = update.effective_chat.id
    await context.bot.send_chat_action(chat_id=chat_id, action="typing")

    try:
        expenses = await ai.extract_expenses(content)
    except Exception:
        logger.exception("Lỗi khi bóc tách chi tiêu")
        await update.message.reply_text("Xin lỗi, mình gặp lỗi khi xử lý. Thử lại sau nhé.")
        return

    if not expenses:
        await update.message.reply_text(
            "Mình không nhận ra khoản chi nào. Thử ghi rõ hơn, ví dụ: /chi ăn sáng 15k"
        )
        return

    await update.message.reply_text(record_expenses(chat_id, expenses))


async def chitieu_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Báo cáo chi tiêu tháng hiện tại: tổng, theo nhóm, các khoản gần nhất."""
    chat_id = update.effective_chat.id
    year_month = datetime.now().strftime("%Y-%m")  # ví dụ "2026-07"

    rows = db.get_month_expenses(chat_id, year_month)
    if not rows:
        await update.message.reply_text(
            "Tháng này chưa có khoản chi nào. Ghi bằng /chi <khoản chi> nhé."
        )
        return

    summary = db.get_month_summary(chat_id, year_month)
    total = sum(amount for _, amount in summary)

    lines = [f"Chi tiêu tháng {datetime.now().strftime('%m/%Y')}: {format_money(total)}", ""]
    lines += [f"• {category}: {format_money(amount)}" for category, amount in summary]

    lines += ["", "5 khoản gần nhất:"]
    for item, amount, category, day in rows[:5]:
        lines.append(f"• {day} — {item}: {format_money(amount)}")

    await update.message.reply_text("\n".join(lines))


async def baocao_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Xuất Excel chi tiêu: /baocao (tháng này) hoặc /baocao 6 (tháng 6 năm nay)."""
    chat_id = update.effective_chat.id
    now = datetime.now()

    if context.args:
        if len(context.args) != 1 or not context.args[0].isdigit() or not 1 <= int(context.args[0]) <= 12:
            await update.message.reply_text("Cách dùng: /baocao (tháng này) hoặc /baocao <1-12>")
            return
        year_month = f"{now.year}-{int(context.args[0]):02d}"
    else:
        year_month = now.strftime("%Y-%m")

    rows = db.get_month_expenses(chat_id, year_month)
    if not rows:
        await update.message.reply_text(f"Tháng {year_month[5:7]}/{year_month[:4]} chưa có khoản chi nào.")
        return

    await context.bot.send_chat_action(chat_id=chat_id, action="upload_document")
    buffer = report.build_month_report(rows, db.get_month_summary(chat_id, year_month), year_month)
    await update.message.reply_document(
        document=buffer,
        filename=f"chi-tieu-{year_month}.xlsx",
        caption=f"📄 Báo cáo chi tiêu tháng {year_month[5:7]}/{year_month[:4]} ({len(rows)} khoản)",
    )


async def remind_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Đặt lời nhắc bằng ngôn ngữ tự nhiên: /remind 8h sáng mai uống thuốc."""
    content = " ".join(context.args).strip()
    if not content:
        await update.message.reply_text(
            "Cách dùng: /remind <khi nào + việc gì>\n"
            "Ví dụ: /remind 15 phút nữa gọi cho khách\n"
            "       /remind 8h sáng mai nộp báo cáo"
        )
        return

    chat_id = update.effective_chat.id
    await context.bot.send_chat_action(chat_id=chat_id, action="typing")

    try:
        reminder = await ai.extract_reminder(content)
    except Exception:
        logger.exception("Lỗi khi bóc tách lời nhắc")
        reminder = None

    if reminder is None:
        await update.message.reply_text(
            "Mình chưa hiểu thời gian nhắc. Thử nói rõ hơn, ví dụ: /remind 15 phút nữa họp"
        )
        return

    remind_at = datetime.strptime(reminder["remind_at"], "%Y-%m-%d %H:%M")
    if remind_at <= datetime.now():
        await update.message.reply_text(
            f"Thời điểm đó ({remind_at.strftime('%H:%M %d/%m')}) đã qua rồi. Thử lại nhé."
        )
        return

    reminder_id = db.add_reminder(chat_id, reminder["content"], reminder["remind_at"])
    await update.message.reply_text(
        f"⏰ Đã đặt nhắc #{reminder_id}: {reminder['content']}\n"
        f"Lúc: {remind_at.strftime('%H:%M %d/%m/%Y')}\n"
        f"Hủy bằng: /delremind {reminder_id}"
    )


async def reminders_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Xem các lời nhắc sắp tới."""
    pending = db.get_pending_reminders(update.effective_chat.id)
    if not pending:
        await update.message.reply_text("Không có lời nhắc nào sắp tới. Đặt bằng /remind nhé.")
        return

    lines = []
    for reminder_id, content, remind_at in pending:
        at = datetime.strptime(remind_at, "%Y-%m-%d %H:%M")
        lines.append(f"#{reminder_id} — {at.strftime('%H:%M %d/%m')}: {content}")
    await update.message.reply_text("Lời nhắc sắp tới:\n" + "\n".join(lines))


async def delremind_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Hủy lời nhắc theo số: /delremind 3."""
    if len(context.args) != 1 or not context.args[0].isdigit():
        await update.message.reply_text("Cách dùng: /delremind <số> (xem số bằng /reminders)")
        return

    reminder_id = int(context.args[0])
    if db.delete_reminder(update.effective_chat.id, reminder_id):
        await update.message.reply_text(f"Đã hủy lời nhắc #{reminder_id}.")
    else:
        await update.message.reply_text(f"Không tìm thấy lời nhắc #{reminder_id}.")


async def handle_document(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Người dùng gửi file vào chat -> đọc, chia đoạn, đánh chỉ mục (RAG bước 1).

    Sau bước này, cứ hỏi bình thường — Claude sẽ tự tìm trong tài liệu.
    """
    chat_id = update.effective_chat.id
    doc = update.message.document

    if not doc.file_name.lower().endswith(rag.SUPPORTED_EXTENSIONS):
        await update.message.reply_text(
            f"Mình chỉ đọc được các định dạng: {', '.join(rag.SUPPORTED_EXTENSIONS)}"
        )
        return
    if doc.file_size and doc.file_size > MAX_DOCUMENT_MB * 1024 * 1024:
        await update.message.reply_text(f"File to quá (giới hạn {MAX_DOCUMENT_MB}MB).")
        return

    await context.bot.send_chat_action(chat_id=chat_id, action="typing")

    try:
        # Tải file từ server Telegram về RAM rồi bóc chữ
        file = await context.bot.get_file(doc.file_id)
        data = bytes(await file.download_as_bytearray())
        text = rag.extract_text(doc.file_name, data)
    except Exception:
        logger.exception("Lỗi khi đọc tài liệu %s", doc.file_name)
        await update.message.reply_text("Mình không đọc được file này. Thử file khác nhé.")
        return

    chunks = rag.chunk_text(text)
    if not chunks:
        await update.message.reply_text(
            "File không có chữ nào đọc được (PDF scan ảnh thì mình chưa đọc được)."
        )
        return

    db.add_document(chat_id, doc.file_name, chunks)
    await update.message.reply_text(
        f"📚 Đã đọc xong '{doc.file_name}': {len(text):,} ký tự, chia thành {len(chunks)} đoạn.\n"
        f"Giờ cứ hỏi mình bất cứ điều gì về tài liệu này!\n"
        f"Xem danh sách tài liệu: /docs"
    )


async def docs_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Liệt kê tài liệu đã tải lên."""
    docs = db.list_documents(update.effective_chat.id)
    if not docs:
        await update.message.reply_text(
            "Chưa có tài liệu nào. Gửi file PDF/Word/TXT vào đây để mình đọc nhé."
        )
        return

    lines = [
        f"#{doc_id} — {name} ({n_chunks} đoạn, tải {created_at[8:10]}/{created_at[5:7]})"
        for doc_id, name, n_chunks, created_at in docs
    ]
    await update.message.reply_text(
        "Tài liệu của bạn:\n" + "\n".join(lines) + "\n\nXóa bằng: /deldoc <số>"
    )


async def deldoc_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Xóa tài liệu theo số: /deldoc 2."""
    if len(context.args) != 1 or not context.args[0].isdigit():
        await update.message.reply_text("Cách dùng: /deldoc <số tài liệu> (xem số bằng /docs)")
        return

    doc_id = int(context.args[0])
    if db.delete_document(update.effective_chat.id, doc_id):
        await update.message.reply_text(f"Đã xóa tài liệu #{doc_id}.")
    else:
        await update.message.reply_text(f"Không tìm thấy tài liệu #{doc_id}. Xem bằng /docs.")


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.effective_chat.id
    user_message = update.message.text

    # Báo cho user biết bot đang xử lý (tránh cảm giác bot bị treo)
    await context.bot.send_chat_action(chat_id=chat_id, action="typing")

    # Tin nhắn có "mùi tiền" -> thử bóc tách chi tiêu trước. Nếu Claude xác
    # nhận đây là khoản chi thì ghi luôn, không cần gõ /chi. Nếu không phải
    # (hỏi giá, nói chuyện có nhắc tiền...) thì rơi xuống chat bình thường.
    if ai.MONEY_HINT.search(user_message):
        try:
            expenses = await ai.extract_expenses(user_message)
        except Exception:
            logger.exception("Lỗi khi bóc tách chi tiêu, chuyển sang chat thường")
            expenses = []
        if expenses:
            await update.message.reply_text(record_expenses(chat_id, expenses))
            return

    try:
        reply_text = await ai.ask_claude(chat_id, user_message)
    except Exception:
        logger.exception("Lỗi khi gọi Claude API")
        reply_text = "Xin lỗi, mình gặp lỗi khi xử lý tin nhắn. Thử lại sau nhé."

    await update.message.reply_text(reply_text)


async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Bắt mọi lỗi chưa xử lý từ các handler — "lưới an toàn" cuối cùng.

    Không có hàm này, lỗi bất ngờ (mạng rớt, bug...) sẽ văng nguyên traceback
    ra console và người dùng không nhận được gì. Có nó: log một dòng gọn,
    và cố gắng báo cho người dùng biết.
    """
    logger.error("Lỗi không bắt được khi xử lý update: %s", context.error)

    # Cố báo cho user — bọc try vì chính việc gửi tin cũng có thể lỗi
    # (ví dụ lỗi gốc là mất mạng thì gửi tiếp cũng thất bại)
    if isinstance(update, Update) and update.effective_message:
        try:
            await update.effective_message.reply_text(
                "Mạng chập chờn, tin nhắn vừa rồi xử lý không trọn vẹn. Thử lại nhé."
            )
        except Exception:
            pass
