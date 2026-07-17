"""
Tầng AI: mọi thứ liên quan tới Claude nằm ở đây — client, prompt,
structured extraction (chi tiêu, lời nhắc) và agent loop với tool use.

Các module khác chỉ cần gọi: ask_claude(), extract_expenses(), extract_reminder().
"""

import json
import logging
import re
from datetime import datetime

from anthropic import AsyncAnthropic

import db
from config import (
    ANTHROPIC_API_KEY,
    CLAUDE_MODEL,
    EXPENSE_CATEGORIES,
    EXTRACT_MODEL,
    MAX_HISTORY_MESSAGES,
    SYSTEM_PROMPT,
)

logger = logging.getLogger(__name__)

# AsyncAnthropic thay vì Anthropic: phiên bản bất đồng bộ — trong lúc chờ
# Claude trả lời (3-10 giây), bot vẫn rảnh để xử lý tin nhắn khác và chạy
# job nhắc việc. Bản sync sẽ "đơ" toàn bộ bot trong suốt thời gian chờ.
claude_client = AsyncAnthropic(api_key=ANTHROPIC_API_KEY)

# Prompt cho việc bóc tách chi tiêu (structured extraction). Khác với chat:
# ta KHÔNG cần Claude trò chuyện, chỉ cần nó trả về đúng định dạng JSON
# để code parse được. Chú ý các kỹ thuật: nói rõ "DUY NHẤT JSON", cho ví dụ
# cụ thể, liệt kê giá trị được phép, và quy định trường hợp thất bại ([]).
EXTRACT_SYSTEM_PROMPT = (
    "Bạn là công cụ bóc tách các khoản TIỀN RA (chi) và TIỀN VÀO (thu) từ tin nhắn tiếng Việt.\n"
    "Trả về DUY NHẤT một mảng JSON, không giải thích, không markdown.\n"
    'Mỗi khoản là một phần tử: {"item": "tên khoản", "amount": <số tiền VND, số nguyên>, '
    '"category": "<nhóm>", "type": "chi" hoặc "thu"}\n'
    '- type "chi": tiền ra (mua, ăn, trả, đóng, nạp...). '
    f"category chọn một trong: {', '.join(EXPENSE_CATEGORIES)}.\n"
    '- type "thu": tiền vào (nhận lương, thưởng, được cho, bán đồ, hoàn tiền, trúng...). '
    'category luôn là "thu nhập".\n'
    "Cách nói tiền kiểu Việt Nam:\n"
    "- 15k = 15000; 37k5 = 37500; 2tr = 2000000; 1tr2 = 1200000; 1tr rưỡi = 1500000\n"
    "- 200 nghìn/ngàn = 200000; 5 lít = 5 xị = 500000 (1 lít/xị = 100 nghìn)\n"
    "- 2 củ = 2 chai = 2000000 (1 củ/chai = 1 triệu); nửa củ = 500000\n"
    'Ví dụ: "ăn sáng 15k, nhận lương 15tr" -> '
    '[{"item": "ăn sáng", "amount": 15000, "category": "ăn uống", "type": "chi"}, '
    '{"item": "nhận lương", "amount": 15000000, "category": "thu nhập", "type": "thu"}]\n'
    "CHỈ tính khi tiền ĐÃ thực sự ra/vào. Hỏi giá, so sánh, dự định mua, "
    "hay nhắc tới tiền trong câu chuyện chung KHÔNG tính.\n"
    'Nếu người dùng nói rõ thời điểm khác ("hôm qua", "thứ 2 vừa rồi", "hôm 12/7"), '
    'thêm trường "date": "YYYY-MM-DD". Không nhắc gì đến thời điểm '
    "thì BỎ trường date (mặc định là hôm nay).\n"
    "Nếu tin nhắn không chứa khoản thu/chi nào rõ ràng, trả về []."
)

# Bộ lọc rẻ trước khi gọi AI đắt: tin nhắn phải có "mùi tiền" (15k, 37k5,
# 2tr, 50.000đ, 200 nghìn, 2 củ...) thì mới đáng gọi Claude bóc tách.
# k\d* / tr\d* bắt được cả kiểu viết dính số: "37k5", "1tr2".
MONEY_HINT = re.compile(
    r"\d[\d.,]*\s*(k\d*|tr\d*|triệu|nghìn|ngàn|đồng|đ|d|vnd|củ|chai|lít|xị)\b"
    r"|nửa\s+(củ|chai|triệu|tr)\b",  # "nửa củ" không có chữ số nhưng vẫn là tiền
    re.IGNORECASE,
)

# ── Tool use: các "công cụ" Claude được phép gọi khi chat ─────────────────
# Mỗi tool khai báo tên, mô tả (Claude đọc mô tả để quyết định KHI NÀO dùng)
# và input_schema (định dạng tham số, chuẩn JSON Schema). Claude không chạy
# được code — nó chỉ YÊU CẦU gọi tool, code của ta chạy rồi trả kết quả lại.
TOOLS = [
    {
        "name": "search_documents",
        "description": (
            "Tìm kiếm trong các tài liệu (PDF, Word...) mà người dùng đã gửi cho bot. "
            "Dùng khi câu hỏi liên quan tới nội dung tài liệu của họ: quy định, hợp đồng, "
            "báo cáo, tài liệu kỹ thuật... Trả về các đoạn văn bản liên quan nhất kèm tên tài liệu."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Từ khóa tìm kiếm, nên là các từ quan trọng trong câu hỏi",
                }
            },
            "required": ["query"],
        },
    },
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


def run_tool(chat_id: int, tool_name: str, tool_input: dict) -> str:
    """Thực thi tool mà Claude yêu cầu, trả kết quả dạng chuỗi JSON.

    Chú ý bảo mật: chat_id lấy từ Telegram (người đang chat), KHÔNG cho
    Claude tự truyền vào — nếu không, prompt khéo léo có thể đọc trộm
    dữ liệu chi tiêu/tài liệu của người khác.
    """
    if tool_name == "search_documents":
        results = db.search_chunks(chat_id, tool_input.get("query", ""))
        if not results:
            return json.dumps(
                {"info": "Không tìm thấy đoạn nào phù hợp trong tài liệu của người dùng"},
                ensure_ascii=False,
            )
        return json.dumps(
            [{"document": name, "content": content} for content, name in results],
            ensure_ascii=False,
        )

    year_month = tool_input.get("year_month", datetime.now().strftime("%Y-%m"))

    if tool_name == "expense_summary":
        summary = db.get_month_summary(chat_id, year_month)
        total_chi = sum(a for _, a in summary)
        total_thu = db.get_month_income(chat_id, year_month)
        return json.dumps(
            {
                "month": year_month,
                "tổng_chi": total_chi,
                "tổng_thu": total_thu,
                "cân_đối": total_thu - total_chi,
                "chi_theo_nhóm": dict(summary),
            },
            ensure_ascii=False,
        )

    if tool_name == "expense_list":
        rows = db.get_month_expenses(chat_id, year_month)
        return json.dumps(
            [
                {"item": i, "amount": a, "category": c, "day": d, "kind": k}
                for i, a, c, d, k in rows
            ],
            ensure_ascii=False,
        )

    return json.dumps({"error": f"Không có tool tên {tool_name}"}, ensure_ascii=False)


def _strip_code_fence(raw: str) -> str:
    """Phòng khi Claude bọc JSON trong ```json ... ``` dù đã dặn không."""
    if raw.startswith("```"):
        raw = raw.strip("`").removeprefix("json").strip()
    return raw


def _parse_json(raw: str):
    """Parse JSON "khoan dung": chịu được code fence và chữ thừa quanh JSON.

    Model nhỏ (Haiku) thỉnh thoảng kèm câu giải thích trước/sau khối JSON dù
    đã dặn "DUY NHẤT JSON". Thay vì json.loads (chết ngay khi có chữ thừa),
    ta tìm vị trí { hoặc [ đầu tiên rồi dùng raw_decode — parse xong khối JSON
    là dừng, mặc kệ phần đuôi. Ném ValueError nếu không có JSON nào.
    """
    raw = _strip_code_fence(raw.strip())
    starts = [i for i in (raw.find("{"), raw.find("[")) if i != -1]
    if not starts:
        raise ValueError(f"Không tìm thấy JSON trong: {raw[:100]!r}")
    data, _ = json.JSONDecoder().raw_decode(raw[min(starts):])
    return data


# ── Các hàm gọi Claude ────────────────────────────────────────────────────
async def ask_claude(chat_id: int, user_message: str) -> str:
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
        + "\nBạn có công cụ tìm kiếm trong tài liệu người dùng đã gửi (search_documents) — "
        + "hãy dùng khi câu hỏi có thể liên quan tài liệu của họ. Khi trả lời từ tài liệu, "
        + "nêu tên tài liệu nguồn. Nếu không tìm thấy, nói thẳng là không thấy — đừng bịa."
        + "\nSố tiền là VND, viết kiểu 15.000đ."
    )

    # Giới hạn số vòng để phòng Claude gọi tool mãi không dừng
    for _ in range(5):
        response = await claude_client.messages.create(
            model=CLAUDE_MODEL,
            max_tokens=1024,
            system=system,
            messages=messages,
            tools=TOOLS,
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
                result = run_tool(chat_id, block.name, block.input)
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


async def extract_expenses(text: str) -> list[dict]:
    """Nhờ Claude bóc tách tin nhắn thành danh sách khoản chi (structured extraction).

    Trả về [{"item": ..., "amount": ..., "category": ...}, ...] — chỉ gồm
    các phần tử hợp lệ. LLM có thể trả về sai định dạng, nên LUÔN kiểm tra
    lại từng trường trước khi tin (nguyên tắc: không tin đầu ra của AI mù quáng).
    """
    # Ghép ngày hôm nay vào prompt lúc gọi (không để trong hằng số — hằng số
    # chỉ được tạo 1 lần khi khởi động, bot chạy sang ngày mới sẽ sai)
    weekdays = ["thứ 2", "thứ 3", "thứ 4", "thứ 5", "thứ 6", "thứ 7", "chủ nhật"]
    now = datetime.now()
    system = EXTRACT_SYSTEM_PROMPT + f"\nHôm nay là {now.strftime('%Y-%m-%d')}, {weekdays[now.weekday()]}."

    response = await claude_client.messages.create(
        model=EXTRACT_MODEL,  # việc bóc tách đơn giản -> dùng Haiku cho rẻ và nhanh
        max_tokens=500,
        system=system,
        messages=[{"role": "user", "content": text}],
    )
    data = _parse_json(response.content[0].text)  # ném ValueError nếu không có JSON -> handler bắt
    return validate_expenses(data)


def validate_expenses(data: list) -> list[dict]:
    """Lọc lại đầu ra của Claude: chỉ giữ các khoản chi đúng định dạng.

    Tách thành hàm riêng (không gọi API) để viết test được mà không tốn tiền.
    """
    valid = []
    for e in data:
        # type: 'chi' (mặc định) hoặc 'thu'; khoản thu có nhóm riêng "thu nhập"
        kind = e.get("type", "chi") if isinstance(e, dict) else "chi"
        allowed_categories = ["thu nhập"] if kind == "thu" else EXPENSE_CATEGORIES
        if not (
            isinstance(e, dict)
            and kind in ("chi", "thu")
            and isinstance(e.get("item"), str) and e["item"].strip()
            and isinstance(e.get("amount"), int) and e["amount"] > 0
            and e.get("category") in allowed_categories
        ):
            continue
        e["type"] = kind  # điền mặc định nếu Claude bỏ trống
        # Trường date (tùy chọn): phải đúng định dạng và không ở tương lai —
        # sai thì chỉ bỏ trường date (coi như chi hôm nay), vẫn giữ khoản chi
        if "date" in e:
            try:
                parsed = datetime.strptime(e["date"], "%Y-%m-%d").date()
                if parsed > datetime.now().date():
                    raise ValueError
            except (ValueError, TypeError):
                e = {k: v for k, v in e.items() if k != "date"}
        valid.append(e)
    return valid


async def extract_reminder(text: str) -> dict | None:
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
        'Trả về DUY NHẤT một JSON: {"content": "<việc cần nhắc>", "remind_at": "YYYY-MM-DD HH:MM", '
        '"repeat": "once" | "daily" | "weekly"}\n'
        "repeat: \"daily\" khi nói 'mỗi ngày/hằng ngày/mỗi sáng/mỗi tối...', "
        "\"weekly\" khi nói 'mỗi tuần/thứ 2 hằng tuần...', còn lại là \"once\".\n"
        "Với lời nhắc lặp lại, remind_at là LẦN NHẮC ĐẦU TIÊN (gần nhất trong tương lai).\n"
        "Quy đổi thời gian tương đối ra thời điểm tuyệt đối: '15 phút nữa', '8h sáng mai', "
        "'tối nay', 'thứ 6 tuần này'... Nếu nói giờ mà không nói ngày, chọn thời điểm "
        "GẦN NHẤT trong tương lai. 'sáng' = 08:00, 'trưa' = 12:00, 'chiều' = 15:00, 'tối' = 20:00 "
        "nếu không nói giờ cụ thể.\n"
        "Nếu tin nhắn KHÔNG nhắc gì đến thời gian (không có giờ, không có sáng/trưa/chiều/tối, "
        'không có hôm nay/mai/thứ mấy...), TUYỆT ĐỐI không tự bịa — trả về {"error": "không rõ thời gian"}.\n'
        'Nếu không xác định được thời gian hoặc nội dung, trả về {"error": "lý do"}.'
    )
    response = await claude_client.messages.create(
        model=EXTRACT_MODEL,  # bóc tách thời gian cũng là việc đơn giản -> Haiku
        max_tokens=200,
        system=system,
        messages=[{"role": "user", "content": text}],
    )
    data = _parse_json(response.content[0].text)
    return validate_reminder(data)


def validate_reminder(data: dict) -> dict | None:
    """Kiểm tra đầu ra bóc tách lời nhắc. Tách riêng để test không cần gọi API."""
    if "error" in data or not data.get("content") or not data.get("remind_at"):
        return None
    # Kiểm tra định dạng thời gian bằng cách parse thử — sai định dạng sẽ ném
    # ValueError, coi như không bóc tách được
    try:
        datetime.strptime(data["remind_at"], "%Y-%m-%d %H:%M")
    except (ValueError, TypeError):
        return None
    repeat = data.get("repeat", "once")
    if repeat not in ("once", "daily", "weekly"):
        repeat = "once"  # giá trị lạ -> coi như nhắc 1 lần, không vứt cả lời nhắc
    return {"content": str(data["content"]), "remind_at": data["remind_at"], "repeat": repeat}


async def write_weekly_comment(this_week: dict, last_week: dict) -> str:
    """Nhờ Claude viết 2-3 câu nhận xét cho báo cáo chi tiêu tuần."""
    response = await claude_client.messages.create(
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
                {"tuần_này": this_week, "tuần_trước": last_week},
                ensure_ascii=False,
            ),
        }],
    )
    return response.content[0].text.strip()
