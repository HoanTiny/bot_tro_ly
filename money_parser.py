"""
Bộ phân tích tiền CỤC BỘ — bóc tách khoản thu/chi bằng regex thuần Python,
KHÔNG gọi AI, 0 token, phản hồi tức thì.

Triết lý thiết kế: "tự tin thì làm, không chắc thì nhường AI".
- parse_expenses() trả về danh sách khoản thu/chi khi câu ĐƠN GIẢN, RÕ RÀNG
  (dạng "món gì đó + số tiền": "ăn phở 37k5", "nhận lương 15tr")
- Trả về None khi có bất kỳ dấu hiệu mơ hồ nào (câu hỏi, dự định, ngày phức
  tạp, nhiều số lẫn lộn...) — bên gọi sẽ chuyển cho Claude xử lý.

Nguyên tắc quan trọng: thà nhường AI oan (tốn vài đồng) còn hơn tự đoán sai
(ghi bậy vào sổ của người dùng). Độ CHÍNH XÁC quan trọng hơn độ PHỦ.
"""

import re
import unicodedata
from datetime import datetime, timedelta

# Giá trị của từng đơn vị tiền (lóng) kiểu Việt
UNIT_VALUES = {
    "k": 1_000, "nghìn": 1_000, "ngàn": 1_000,
    "lít": 100_000, "xị": 100_000,
    "tr": 1_000_000, "triệu": 1_000_000, "củ": 1_000_000, "chai": 1_000_000,
    "đ": 1, "d": 1, "đồng": 1, "vnd": 1,
}

# Một biểu thức tiền: "37k5", "2 củ", "nửa chai", "1tr rưỡi", "50.000đ"
MONEY_RE = re.compile(
    r"(?:(?P<num>\d+(?:[.,]\d+)?)|(?P<half>nửa))\s*"
    r"(?P<unit>k|nghìn|ngàn|triệu|tr|củ|chai|lít|xị|đồng|đ|d|vnd)"
    r"(?P<frac>\d)?"
    r"(?:\s*(?P<ruoi>rưỡi))?"
    r"\b",
    re.IGNORECASE,
)

# Thấy các từ này -> câu hỏi/dự đoán/so sánh, KHÔNG phải giao dịch -> nhường AI
UNSURE_WORDS = [
    "?", "bao nhiêu", "giá", "đắt", "rẻ", "nhỉ", "chắc", "sẽ", "định",
    "muốn", "nên", "dự", "khoảng", "tầm", "nếu", "hay là",
]

# Thấy các từ này -> có yếu tố thời gian phức tạp mà parser chưa hiểu -> nhường AI
# (riêng "hôm qua"/"hôm kia" thì parser tự xử lý được)
COMPLEX_TIME_WORDS = ["thứ", "tuần trước", "tháng trước", "chủ nhật", "hôm 1", "hôm 2", "hôm 3"]

# Từ khóa nhận diện tiền THU. Gồm cả các cụm "kiếm được" kiểu việc làm thêm
# ("chạy grab thu đc 103k", "kiếm được 500k") — nếu thiếu, parser sẽ thấy
# "grab" rồi xếp nhầm thành CHI nhóm đi lại. Ưu tiên CỤM RÕ NGHĨA thay vì
# từ đơn dễ nhầm: "thu được" (thu nhập) chứ không phải "thu" đơn (còn nghĩa
# "thu hộ", "mùa thu"); câu mơ hồ cứ để rơi xuống AI.
INCOME_WORDS = [
    "lương", "thưởng", "bán", "hoàn tiền", "hoàn", "trúng", "nhận", "cho tiền",
    "thu được", "thu đc", "thu nhập", "kiếm được", "kiếm đc",
    "tiền công", "trả công", "được trả", "tiền boa", "tiền tip",
]

# Từ khóa phân nhóm chi tiêu (so khớp KHÔNG DẤU để "an sang" cũng trúng)
CATEGORY_KEYWORDS = {
    "ăn uống": ["ăn", "uống", "cơm", "phở", "bún", "cháo", "xôi", "bánh", "cà phê",
                "cafe", "trà", "chè", "nhậu", "bia", "sữa", "nước ngọt", "đồ ăn", "quán"],
    "đi lại": ["xăng", "taxi", "grab", "xe ôm", "gửi xe", "rửa xe", "sửa xe",
               "vé tàu", "vé xe", "vé máy bay", "bus", "buýt"],
    "hóa đơn": ["điện", "nước", "internet", "wifi", "mạng", "cước", "tiền nhà",
                "thuê nhà", "học phí", "phí"],
    "giải trí": ["phim", "game", "nhạc", "karaoke", "du lịch", "vé xem"],
    "sức khỏe": ["thuốc", "khám", "bệnh viện", "gym", "viện phí"],
    "mua sắm": ["mua", "áo", "quần", "giày", "dép", "túi", "mỹ phẩm", "đồ"],
}

# Từ "đệm" bị loại khỏi tên khoản: "ăn phở HẾT 37k5" -> item "ăn phở"
FILLER_WORDS = {"hết", "mất", "có", "là", "tiền", "được", "về", "cả", "tổng"}


def _no_accent(text: str) -> str:
    """Bỏ dấu tiếng Việt để so khớp từ khóa: "ăn sáng" -> "an sang"."""
    text = text.replace("đ", "d").replace("Đ", "D")
    return "".join(c for c in unicodedata.normalize("NFD", text) if not unicodedata.combining(c))


def _parse_amount(match: re.Match) -> int:
    """Đổi 1 biểu thức tiền đã khớp regex ra số VND."""
    unit = UNIT_VALUES[match["unit"].lower()]

    if match["half"]:  # "nửa củ" = 0.5 triệu
        base = 0.5 * unit
    else:
        num_text = match["num"]
        if unit == 1:
            # Đơn vị đồng: "50.000đ" — dấu chấm/phẩy là NGĂN CÁCH NGHÌN
            base = float(num_text.replace(".", "").replace(",", ""))
        else:
            # Đơn vị k/tr/củ...: "1,5tr" — dấu phẩy/chấm là PHẦN THẬP PHÂN
            base = float(num_text.replace(",", ".")) * unit

    if match["frac"]:  # "37k5" = 37k + 5 phần mười của k
        base += int(match["frac"]) * unit / 10
    if match["ruoi"]:  # "1tr rưỡi" = 1tr + nửa tr
        base += unit / 2
    return int(round(base))


def _has_word(keyword: str, text: str) -> bool:
    """Từ khóa có xuất hiện như MỘT TỪ TRỌN VẸN trong text không.

    Bắt buộc dùng \\b (ranh giới từ) chứ không dùng `in`: từ khóa "ăn"
    (bỏ dấu "an") nằm bên trong chữ "x[an]g" — so chuỗi con sẽ xếp nhầm
    "đổ xăng" vào nhóm ăn uống!
    """
    return re.search(rf"\b{re.escape(keyword)}\b", text) is not None


# Bảng (từ khóa bỏ dấu, nhóm) xếp TỪ DÀI TRƯỚC: bỏ dấu làm nhiều từ trùng
# nhau ("sữa" và "sửa" đều thành "sua") — từ khóa dài ("sua xe") cụ thể hơn
# nên phải được thử trước từ ngắn ("sua").
_KEYWORD_TABLE = sorted(
    (
        (_no_accent(keyword), category)
        for category, keywords in CATEGORY_KEYWORDS.items()
        for keyword in keywords
    ),
    key=lambda pair: len(pair[0]),
    reverse=True,
)


def _guess_category(segment_no_accent: str) -> str | None:
    """Đoán nhóm chi tiêu bằng từ khóa (đều đã bỏ dấu). None = không đoán được."""
    for keyword, category in _KEYWORD_TABLE:
        if _has_word(keyword, segment_no_accent):
            return category
    return None


def parse_expenses(text: str) -> list[dict] | None:
    """Bóc tách các khoản thu/chi từ tin nhắn — hoặc None nếu không đủ tự tin.

    Trả về cùng định dạng với ai.extract_expenses():
    [{"item", "amount", "category", "type", ("date")}, ...]
    """
    lowered = text.lower().strip()

    # Câu hỏi / dự đoán / thời gian phức tạp -> nhường AI phán đoán
    if any(word in lowered for word in UNSURE_WORDS):
        return None
    if any(word in lowered for word in COMPLEX_TIME_WORDS):
        return None

    # "hôm qua"/"hôm kia" thì parser tự quy ra ngày rồi bỏ khỏi câu
    date = None
    for phrase, days_ago in (("hôm qua", 1), ("hôm kia", 2)):
        if phrase in lowered:
            date = (datetime.now() - timedelta(days=days_ago)).strftime("%Y-%m-%d")
            lowered = lowered.replace(phrase, " ")

    # Mỗi đoạn (ngăn bởi , hoặc ;) là một khoản: "ăn trưa 45k, cà phê 30k".
    # ,(?!\d): KHÔNG cắt dấu phẩy đứng ngay trước chữ số — đó là phẩy
    # thập phân ("1,5 lít"), không phải phẩy ngăn cách 2 khoản.
    expenses = []
    for segment in re.split(r";|,(?!\d)", lowered):
        segment = segment.strip()
        if not segment:
            continue

        matches = list(MONEY_RE.finditer(segment))
        if len(matches) != 1:
            return None  # 0 hoặc 2+ biểu thức tiền trong 1 đoạn -> mơ hồ
        match = matches[0]

        # Tên khoản = phần còn lại sau khi bỏ biểu thức tiền + từ đệm
        item_text = (segment[: match.start()] + " " + segment[match.end():]).strip()
        words = [w for w in item_text.split() if w not in FILLER_WORDS]
        item = " ".join(words).strip(" .!")
        if not item or any(c.isdigit() for c in item):
            return None  # không có tên, hoặc còn số thừa chưa hiểu -> nhường AI

        segment_no_accent = _no_accent(segment)
        # Từ khóa THU so khớp CÓ DẤU: "bán" (thu) khác "bàn" (mua đồ) —
        # bỏ dấu là lẫn nhau ngay. Người gõ không dấu sẽ rơi xuống AI, chấp nhận.
        is_income = any(_has_word(w, segment) for w in INCOME_WORDS)

        if is_income:
            category = "thu nhập"
        else:
            category = _guess_category(segment_no_accent)
            if category is None:
                return None  # không đoán được nhóm -> để AI phân loại

        expense = {
            "item": item,
            "amount": _parse_amount(match),
            "category": category,
            "type": "thu" if is_income else "chi",
        }
        if date:
            expense["date"] = date
        expenses.append(expense)

    return expenses or None
