"""
Chatbot Telegram dùng Claude API để trả lời tin nhắn.

Cách hoạt động (đọc để hiểu, không cần nhớ hết ngay):
1. python-telegram-bot lắng nghe tin nhắn người dùng gửi tới bot trên Telegram.
2. Mỗi tin nhắn được chuyển sang Claude API (anthropic SDK) để lấy câu trả lời.
3. Bot gửi câu trả lời đó ngược lại cho người dùng trên Telegram.
4. Lịch sử chat của mỗi người được lưu vào SQLite (file bot.db, xem db.py)
   để bot "nhớ" ngữ cảnh — tắt bot bật lại vẫn còn lịch sử.

Chạy thử:
    pip install -r requirements.txt
    cp .env.example .env   # rồi điền token thật vào .env
    python telegram_ai_bot.py
"""

import json
import logging
import os
import re
from datetime import datetime, time, timedelta

import pytz
from dotenv import load_dotenv
from telegram import BotCommand, Update
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    Defaults,
    MessageHandler,
    filters,
)
from anthropic import Anthropic

import db

# ── Cấu hình ──────────────────────────────────────────────────────────────
load_dotenv()  # đọc biến môi trường từ file .env

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY")
CLAUDE_MODEL = "claude-sonnet-4-5"  # có thể đổi sang model khác nếu muốn

# Prompt hệ thống: định hình "tính cách" của bot. Sửa tùy ý để luyện tập.
SYSTEM_PROMPT = (
    "Bạn là một trợ lý thân thiện, trả lời ngắn gọn, rõ ràng bằng tiếng Việt "
    "trừ khi người dùng hỏi bằng ngôn ngữ khác."
)

MAX_HISTORY_MESSAGES = 20  # giới hạn số tin nhắn nhớ, tránh phình quá to

# Các nhóm chi tiêu hợp lệ — liệt kê cứng để Claude không tự bịa nhóm mới
EXPENSE_CATEGORIES = ["ăn uống", "đi lại", "mua sắm", "hóa đơn", "giải trí", "sức khỏe", "khác"]

# Prompt cho việc bóc tách chi tiêu (structured extraction). Khác với chat:
# ta KHÔNG cần Claude trò chuyện, chỉ cần nó trả về đúng định dạng JSON
# để code parse được. Chú ý các kỹ thuật: nói rõ "DUY NHẤT JSON", cho ví dụ
# cụ thể, liệt kê giá trị được phép, và quy định trường hợp thất bại ([]).
EXTRACT_SYSTEM_PROMPT = (
    "Bạn là công cụ bóc tách chi tiêu từ tin nhắn tiếng Việt.\n"
    "Trả về DUY NHẤT một mảng JSON, không giải thích, không markdown.\n"
    'Mỗi khoản chi là một phần tử: {"item": "tên khoản chi", "amount": <số tiền VND, số nguyên>, "category": "<nhóm>"}\n'
    f"category chỉ được chọn một trong: {', '.join(EXPENSE_CATEGORIES)}.\n"
    "Hiểu cách viết tiền kiểu Việt Nam: 15k = 15000, 2tr = 2000000, 1tr2 = 1200000.\n"
    'Ví dụ: "ăn sáng 15k, đổ xăng 50k" -> '
    '[{"item": "ăn sáng", "amount": 15000, "category": "ăn uống"}, '
    '{"item": "đổ xăng", "amount": 50000, "category": "đi lại"}]\n'
    "CHỈ tính khi người dùng thông báo ĐÃ chi tiền. Hỏi giá, so sánh, dự định mua, "
    "hay nhắc tới tiền trong câu chuyện chung KHÔNG phải khoản chi.\n"
    "Nếu tin nhắn không chứa khoản chi nào rõ ràng, trả về []."
)

# Bộ lọc rẻ trước khi gọi AI đắt: tin nhắn phải có "mùi tiền" (15k, 2tr,
# 50.000đ, 200 nghìn...) thì mới đáng gọi Claude bóc tách. Nhờ vậy tin nhắn
# chat thường không tốn thêm lệnh gọi API nào.
MONEY_HINT = re.compile(
    r"\d[\d.,]*\s*(k|tr|triệu|nghìn|ngàn|đồng|đ|d|vnd)\b",
    re.IGNORECASE,
)

# ── Tool use: các "công cụ" Claude được phép gọi khi chat ─────────────────
# Mỗi tool khai báo tên, mô tả (Claude đọc mô tả để quyết định KHI NÀO dùng)
# và input_schema (định dạng tham số, chuẩn JSON Schema). Claude không chạy
# được code — nó chỉ YÊU CẦU gọi tool, code của ta chạy rồi trả kết quả lại.
EXPENSE_TOOLS = [
    {
        "name": "expense_summary",
        "description": (
            "Tổng chi tiêu của người dùng trong một tháng, chia theo nhóm "
            "(ăn uống, đi lại, mua sắm...). Dùng khi người dùng hỏi đã tiêu "
            "bao nhiêu tiền, tổng chi tiêu, hoặc chi cho nhóm nào bao nhiêu."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "year_month": {
                    "type": "string",
                    "description": "Tháng cần xem, định dạng YYYY-MM, ví dụ 2026-07",
                }
            },
            "required": ["year_month"],
        },
    },
    {
        "name": "expense_list",
        "description": (
            "Danh sách từng khoản chi của người dùng trong một tháng, mới nhất "
            "trước. Dùng khi người dùng muốn xem chi tiết các khoản đã chi."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "year_month": {
                    "type": "string",
                    "description": "Tháng cần xem, định dạng YYYY-MM, ví dụ 2026-07",
                }
            },
            "required": ["year_month"],
        },
    },
]


def run_expense_tool(chat_id: int, tool_name: str, tool_input: dict) -> str:
    """Thực thi tool mà Claude yêu cầu, trả kết quả dạng chuỗi JSON.

    Chú ý bảo mật: chat_id lấy từ Telegram (người đang chat), KHÔNG cho
    Claude tự truyền vào — nếu không, prompt khéo léo có thể đọc trộm
    dữ liệu chi tiêu của người khác.
    """
    year_month = tool_input.get("year_month", datetime.now().strftime("%Y-%m"))

    if tool_name == "expense_summary":
        summary = db.get_month_summary(chat_id, year_month)
        return json.dumps(
            {"month": year_month, "total": sum(a for _, a in summary), "by_category": dict(summary)},
            ensure_ascii=False,
        )

    if tool_name == "expense_list":
        rows = db.get_month_expenses(chat_id, year_month)
        return json.dumps(
            [{"item": i, "amount": a, "category": c, "day": d} for i, a, c, d in rows],
            ensure_ascii=False,
        )

    return json.dumps({"error": f"Không có tool tên {tool_name}"}, ensure_ascii=False)

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

if not TELEGRAM_BOT_TOKEN or not ANTHROPIC_API_KEY:
    raise RuntimeError(
        "Thiếu TELEGRAM_BOT_TOKEN hoặc ANTHROPIC_API_KEY. "
        "Kiểm tra lại file .env (xem .env.example để biết định dạng)."
    )

claude_client = Anthropic(api_key=ANTHROPIC_API_KEY)


# ── Hàm gọi Claude ────────────────────────────────────────────────────────
def ask_claude(chat_id: int, user_message: str) -> str:
    """Gửi tin nhắn của user tới Claude, kèm lịch sử chat, và trả về câu trả lời.

    Đây là một "agent loop" — vòng lặp: gọi Claude -> Claude muốn dùng tool
    -> ta chạy tool, gửi kết quả lại -> Claude trả lời tiếp (hoặc dùng tool
    khác). Lặp đến khi Claude trả lời bằng chữ. Đây chính là cơ chế lõi của
    mọi AI agent hiện nay.
    """
    db.add_message(chat_id, "user", user_message)
    messages = db.get_history(chat_id, MAX_HISTORY_MESSAGES)

    # Claude không tự biết hôm nay là ngày nào — phải nói trong system prompt
    # để "tháng này", "tháng trước" quy đổi ra đúng tháng.
    system = (
        SYSTEM_PROMPT
        + f"\nHôm nay là {datetime.now().strftime('%d/%m/%Y')}."
        + "\nBạn có công cụ tra cứu sổ chi tiêu của người dùng — hãy dùng khi được hỏi về chi tiêu."
        + "\nSố tiền là VND, viết kiểu 15.000đ."
    )

    # Giới hạn số vòng để phòng Claude gọi tool mãi không dừng
    for _ in range(5):
        response = claude_client.messages.create(
            model=CLAUDE_MODEL,
            max_tokens=1024,
            system=system,
            messages=messages,
            tools=EXPENSE_TOOLS,
        )

        # stop_reason cho biết Claude dừng vì lý do gì:
        # "tool_use" = muốn gọi tool, "end_turn" = đã trả lời xong
        if response.stop_reason != "tool_use":
            break

        # Chạy tất cả tool Claude yêu cầu trong lượt này
        tool_results = []
        for block in response.content:
            if block.type == "tool_use":
                logger.info("Claude gọi tool %s với input %s", block.name, block.input)
                result = run_expense_tool(chat_id, block.name, block.input)
                tool_results.append(
                    {"type": "tool_result", "tool_use_id": block.id, "content": result}
                )

        # Nối tiếp hội thoại: lượt của Claude (có yêu cầu tool) + kết quả tool
        # (đóng vai user theo quy ước của API), rồi vòng lặp gọi Claude tiếp
        messages = messages + [
            {"role": "assistant", "content": response.content},
            {"role": "user", "content": tool_results},
        ]

    # Ghép các khối text trong câu trả lời cuối (bỏ qua khối tool_use nếu có)
    reply_text = "".join(block.text for block in response.content if block.type == "text").strip()
    if not reply_text:
        reply_text = "Xin lỗi, mình chưa trả lời được câu này. Thử hỏi lại cách khác nhé."

    db.add_message(chat_id, "assistant", reply_text)
    return reply_text


def extract_expenses(text: str) -> list[dict]:
    """Nhờ Claude bóc tách tin nhắn thành danh sách khoản chi (structured extraction).

    Trả về [{"item": ..., "amount": ..., "category": ...}, ...] — chỉ gồm
    các phần tử hợp lệ. LLM có thể trả về sai định dạng, nên LUÔN kiểm tra
    lại từng trường trước khi tin (nguyên tắc: không tin đầu ra của AI mù quáng).
    """
    response = claude_client.messages.create(
        model=CLAUDE_MODEL,
        max_tokens=500,
        system=EXTRACT_SYSTEM_PROMPT,
        messages=[{"role": "user", "content": text}],
    )
    raw = response.content[0].text.strip()

    # Phòng khi Claude vẫn bọc JSON trong ```json ... ``` dù đã dặn không
    if raw.startswith("```"):
        raw = raw.strip("`").removeprefix("json").strip()

    data = json.loads(raw)  # ném ValueError nếu không phải JSON -> handler bắt

    valid = []
    for e in data:
        if (
            isinstance(e, dict)
            and isinstance(e.get("item"), str) and e["item"].strip()
            and isinstance(e.get("amount"), int) and e["amount"] > 0
            and e.get("category") in EXPENSE_CATEGORIES
        ):
            valid.append(e)
    return valid


def extract_reminder(text: str) -> dict | None:
    """Nhờ Claude bóc tách lời nhắc: nội dung + thời điểm cụ thể.

    Điểm mấu chốt: LLM không biết "bây giờ" là lúc nào, nên phải đưa thời
    gian hiện tại (kèm thứ trong tuần) vào prompt thì "15 phút nữa",
    "8h sáng mai", "thứ 6 tuần này" mới quy đổi ra được thời điểm tuyệt đối.
    Trả về {"content": ..., "remind_at": "YYYY-MM-DD HH:MM"} hoặc None.
    """
    weekdays = ["thứ 2", "thứ 3", "thứ 4", "thứ 5", "thứ 6", "thứ 7", "chủ nhật"]
    now = datetime.now()
    system = (
        "Bạn là công cụ bóc tách lời nhắc từ tin nhắn tiếng Việt.\n"
        f"Bây giờ là {now.strftime('%Y-%m-%d %H:%M')}, {weekdays[now.weekday()]}.\n"
        'Trả về DUY NHẤT một JSON: {"content": "<việc cần nhắc>", "remind_at": "YYYY-MM-DD HH:MM"}\n'
        "Quy đổi thời gian tương đối ra thời điểm tuyệt đối: '15 phút nữa', '8h sáng mai', "
        "'tối nay', 'thứ 6 tuần này'... Nếu nói giờ mà không nói ngày, chọn thời điểm "
        "GẦN NHẤT trong tương lai. 'sáng' = 08:00, 'trưa' = 12:00, 'chiều' = 15:00, 'tối' = 20:00 "
        "nếu không nói giờ cụ thể.\n"
        'Nếu không xác định được thời gian hoặc nội dung, trả về {"error": "lý do"}.'
    )
    response = claude_client.messages.create(
        model=CLAUDE_MODEL,
        max_tokens=200,
        system=system,
        messages=[{"role": "user", "content": text}],
    )
    raw = response.content[0].text.strip()
    if raw.startswith("```"):
        raw = raw.strip("`").removeprefix("json").strip()
    data = json.loads(raw)

    if "error" in data or not data.get("content") or not data.get("remind_at"):
        return None
    # Kiểm tra định dạng thời gian bằng cách parse thử — sai định dạng sẽ ném
    # ValueError, coi như không bóc tách được
    try:
        datetime.strptime(data["remind_at"], "%Y-%m-%d %H:%M")
    except ValueError:
        return None
    return {"content": str(data["content"]), "remind_at": data["remind_at"]}


def format_money(amount: int) -> str:
    """15000 -> "15.000đ" (kiểu Việt Nam: chấm ngăn cách hàng nghìn)."""
    return f"{amount:,}".replace(",", ".") + "đ"


def record_expenses(chat_id: int, expenses: list[dict]) -> str:
    """Lưu các khoản chi vào database và trả về tin nhắn xác nhận.

    Dùng chung cho /chi và tin nhắn tự nhiên — logic một nơi, sửa một chỗ.
    """
    lines = []
    for e in expenses:
        db.add_expense(chat_id, e["item"], e["amount"], e["category"])
        lines.append(f"• {e['item']}: {format_money(e['amount'])} ({e['category']})")

    month_total = sum(
        amount for _, amount in db.get_month_summary(chat_id, datetime.now().strftime("%Y-%m"))
    )
    return (
        "Đã ghi:\n" + "\n".join(lines)
        + f"\n\nTổng tháng này: {format_money(month_total)} — xem chi tiết: /chitieu"
    )


# ── Handlers Telegram ─────────────────────────────────────────────────────
async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "Chào bạn! Mình là chatbot dùng Claude AI.\n"
        "Gửi tin nhắn bất kỳ để bắt đầu trò chuyện.\n\n"
        "Các lệnh:\n"
        "/chi <khoản chi> — ghi chi tiêu (vd: /chi ăn sáng 15k)\n"
        "/chitieu — báo cáo chi tiêu tháng này\n"
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
        expenses = extract_expenses(content)
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
        reminder = extract_reminder(content)
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


# ── Các job chạy nền (JobQueue) ───────────────────────────────────────────
async def check_reminders_job(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Chạy mỗi 30 giây: tìm lời nhắc đến giờ trong database và gửi đi.

    Kiểu "polling database" này đơn giản mà bền: lời nhắc nằm trong SQLite
    nên bot có tắt đi bật lại cũng không quên (job trong RAM thì quên sạch).
    Nhắc trễ tối đa 30 giây — chấp nhận được với nhắc việc cá nhân.
    """
    due = db.get_due_reminders(datetime.now().strftime("%Y-%m-%d %H:%M"))
    for reminder_id, chat_id, content in due:
        try:
            await context.bot.send_message(chat_id, f"🔔 Nhắc bạn: {content}")
            # Chỉ đánh dấu đã gửi khi gửi THÀNH CÔNG — gửi lỗi thì 30s sau thử lại
            db.mark_reminder_sent(reminder_id)
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
    last_week = ((this_monday - timedelta(days=7)).strftime(fmt), (this_monday - timedelta(days=1)).strftime(fmt))

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
            comment = claude_client.messages.create(
                model=CLAUDE_MODEL,
                max_tokens=300,
                system=(
                    "Bạn là trợ lý tài chính thân thiện. Dựa trên số liệu chi tiêu, viết 2-3 câu "
                    "nhận xét ngắn bằng tiếng Việt: so sánh với tuần trước, nhóm nào tăng/giảm "
                    "đáng chú ý, một lời khuyên nhẹ nhàng nếu phù hợp. Không lặp lại bảng số liệu."
                ),
                messages=[{
                    "role": "user",
                    "content": json.dumps(
                        {"tuần_này": dict(summary), "tuần_trước": dict(last_summary)},
                        ensure_ascii=False,
                    ),
                }],
            ).content[0].text.strip()
            lines += ["", comment]
        except Exception:
            logger.exception("Lỗi khi nhờ Claude viết nhận xét tuần")

        try:
            await context.bot.send_message(chat_id, "\n".join(lines))
        except Exception:
            logger.exception("Lỗi khi gửi báo cáo tuần cho chat %s", chat_id)


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.effective_chat.id
    user_message = update.message.text

    # Báo cho user biết bot đang xử lý (tránh cảm giác bot bị treo)
    await context.bot.send_chat_action(chat_id=chat_id, action="typing")

    # Tin nhắn có "mùi tiền" -> thử bóc tách chi tiêu trước. Nếu Claude xác
    # nhận đây là khoản chi thì ghi luôn, không cần gõ /chi. Nếu không phải
    # (hỏi giá, nói chuyện có nhắc tiền...) thì rơi xuống chat bình thường.
    if MONEY_HINT.search(user_message):
        try:
            expenses = extract_expenses(user_message)
        except Exception:
            logger.exception("Lỗi khi bóc tách chi tiêu, chuyển sang chat thường")
            expenses = []
        if expenses:
            await update.message.reply_text(record_expenses(chat_id, expenses))
            return

    try:
        reply_text = ask_claude(chat_id, user_message)
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
            BotCommand("remind", "Đặt nhắc: /remind 8h sáng mai họp"),
            BotCommand("reminders", "Xem các lời nhắc sắp tới"),
            BotCommand("delremind", "Hủy lời nhắc theo số"),
            BotCommand("note", "Lưu ghi chú: /note mua sữa"),
            BotCommand("notes", "Xem các ghi chú đã lưu"),
            BotCommand("delnote", "Xóa ghi chú theo số: /delnote 1"),
            BotCommand("reset", "Xóa lịch sử chat, bắt đầu lại"),
        ]
    )


def main() -> None:
    db.init_db()  # tạo file bot.db + bảng messages nếu chưa có

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
        .defaults(Defaults(tzinfo=pytz.timezone("Asia/Ho_Chi_Minh")))
        .build()
    )

    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CommandHandler("reset", reset_command))
    app.add_handler(CommandHandler("note", note_command))
    app.add_handler(CommandHandler("notes", notes_command))
    app.add_handler(CommandHandler("delnote", delnote_command))
    app.add_handler(CommandHandler("chi", chi_command))
    app.add_handler(CommandHandler("chitieu", chitieu_command))
    app.add_handler(CommandHandler("remind", remind_command))
    app.add_handler(CommandHandler("reminders", reminders_command))
    app.add_handler(CommandHandler("delremind", delremind_command))
    app.add_error_handler(error_handler)

    # Đăng ký các job chạy nền:
    # - kiểm tra lời nhắc mỗi 30 giây (bắt đầu sau 10s để bot khởi động xong)
    # - báo cáo tuần: chạy 20h mỗi tối, trong hàm tự kiểm tra "có phải chủ nhật"
    app.job_queue.run_repeating(check_reminders_job, interval=30, first=10)
    app.job_queue.run_daily(weekly_report_job, time=time(20, 0))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    logger.info("Bot đang chạy... Nhấn Ctrl+C để dừng.")
    app.run_polling()


if __name__ == "__main__":
    main()
