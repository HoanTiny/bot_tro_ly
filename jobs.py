"""
Các job chạy nền bằng JobQueue: việc bot tự làm mà không cần ai nhắn tin.
"""

import logging
from datetime import datetime, timedelta

from telegram.ext import ContextTypes

import ai
import db
from utils import format_money

logger = logging.getLogger(__name__)


async def check_reminders_job(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Chạy mỗi 30 giây: tìm lời nhắc đến giờ trong database và gửi đi.

    Kiểu "polling database" này đơn giản mà bền: lời nhắc nằm trong SQLite
    nên bot có tắt đi bật lại cũng không quên (job trong RAM thì quên sạch).
    Nhắc trễ tối đa 30 giây — chấp nhận được với nhắc việc cá nhân.
    """
    now = datetime.now()
    due = db.get_due_reminders(now.strftime("%Y-%m-%d %H:%M"))
    for reminder_id, chat_id, content, repeat, remind_at in due:
        try:
            tag = {"daily": " (mỗi ngày)", "weekly": " (mỗi tuần)"}.get(repeat, "")
            await context.bot.send_message(chat_id, f"🔔 Nhắc bạn: {content}{tag}")
            # Chỉ xử lý tiếp khi gửi THÀNH CÔNG — gửi lỗi thì 30s sau thử lại
            if repeat == "once":
                db.mark_reminder_sent(reminder_id)
            else:
                # Lời nhắc lặp: dời sang lần kế tiếp thay vì đánh dấu xong.
                # Cộng dồn từ remind_at CŨ (không phải từ "bây giờ") để giữ
                # nguyên giờ gốc (8:00 mãi là 8:00); vòng while phòng khi máy
                # ngủ nhiều ngày — cộng tới khi nào rơi vào tương lai mới thôi.
                step = timedelta(days=1) if repeat == "daily" else timedelta(days=7)
                next_at = datetime.strptime(remind_at, "%Y-%m-%d %H:%M") + step
                while next_at <= now:
                    next_at += step
                db.reschedule_reminder(reminder_id, next_at.strftime("%Y-%m-%d %H:%M"))
        except Exception:
            logger.exception("Lỗi khi gửi lời nhắc #%s", reminder_id)


async def weekly_report_job(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Chạy 20h mỗi tối, nhưng chỉ làm việc vào Chủ nhật: tổng kết chi tiêu
    tuần cho từng người có phát sinh, kèm vài dòng nhận xét do Claude viết."""
    today = datetime.now()
    if today.weekday() != 6:  # 0 = thứ 2 ... 6 = chủ nhật
        return

    # Tuần này: thứ 2 -> chủ nhật (hôm nay); tuần trước: lùi 7 ngày
    this_monday = today - timedelta(days=6)
    fmt = "%Y-%m-%d"
    this_week = (this_monday.strftime(fmt), today.strftime(fmt))
    last_week = (
        (this_monday - timedelta(days=7)).strftime(fmt),
        (this_monday - timedelta(days=1)).strftime(fmt),
    )

    for chat_id in db.get_chat_ids_with_expenses(*this_week):
        summary = db.get_summary_between(chat_id, *this_week)
        last_summary = db.get_summary_between(chat_id, *last_week)
        total = sum(a for _, a in summary)

        lines = [
            f"📊 Tổng kết chi tiêu tuần ({this_monday.strftime('%d/%m')}–{today.strftime('%d/%m')}): {format_money(total)}",
            "",
        ]
        lines += [f"• {category}: {format_money(amount)}" for category, amount in summary]

        # Nhờ Claude viết nhận xét ngắn, so sánh với tuần trước. Bọc try vì
        # báo cáo số liệu vẫn nên gửi được kể cả khi phần AI lỗi.
        try:
            comment = await ai.write_weekly_comment(dict(summary), dict(last_summary))
            lines += ["", comment]
        except Exception:
            logger.exception("Lỗi khi nhờ Claude viết nhận xét tuần")

        try:
            await context.bot.send_message(chat_id, "\n".join(lines))
        except Exception:
            logger.exception("Lỗi khi gửi báo cáo tuần cho chat %s", chat_id)
