"""Test bộ phân tích tiền cục bộ (0 token) — chạy hoàn toàn offline."""

from datetime import datetime, timedelta

from money_parser import parse_expenses


def _one(text):
    """Tiện ích: parse và trả về khoản duy nhất."""
    result = parse_expenses(text)
    assert result is not None and len(result) == 1, f"{text!r} -> {result}"
    return result[0]


# ── Các kiểu viết tiền ────────────────────────────────────────────────────
def test_cac_kieu_viet_tien():
    cases = {
        "ăn phở 37k5": 37500,
        "ăn sáng 15k": 15000,
        "trà đá 5 nghìn": 5000,
        "đóng tiền điện 1tr2": 1200000,
        "mua áo 1tr rưỡi": 1500000,
        "sửa xe 2 củ": 2000000,
        "taxi 5 lít": 500000,
        "cơm trưa 50.000đ": 50000,
        "cà phê 1,5 lít": 150000,
    }
    for text, amount in cases.items():
        assert _one(text)["amount"] == amount, text


def test_khong_dau_van_hieu():
    e = _one("an sang 15k")
    assert e["amount"] == 15000 and e["category"] == "ăn uống"


# ── Thu / chi và phân nhóm ────────────────────────────────────────────────
def test_tien_thu():
    e = _one("nhận lương 15tr")
    assert e["type"] == "thu" and e["category"] == "thu nhập" and e["amount"] == 15000000
    assert _one("được thưởng nửa củ")["amount"] == 500000
    assert _one("bán đồ cũ 300k")["type"] == "thu"


def test_tien_kiem_duoc_khong_bi_xep_thanh_chi():
    # Lỗi 19/07/2026: "chạy grab thu đc 103k" bị ghi thành CHI nhóm đi lại
    # vì thấy chữ "grab" — dù "thu đc" nghĩa là tiền KIẾM ĐƯỢC (thu).
    e = _one("làm thêm chạy grab thu đc 103k")
    assert e["type"] == "thu" and e["category"] == "thu nhập" and e["amount"] == 103000
    assert _one("kiếm được 500k tiền ship")["type"] == "thu"
    assert _one("chạy xe ôm được trả 80k")["type"] == "thu"


def test_ban_khac_ban():
    # "bán" (thu) phải phân biệt với "bàn" (mua đồ) — so khớp có dấu
    assert _one("mua cái bàn 2tr")["type"] == "chi"
    assert _one("bán cái ghế cũ 500k")["type"] == "thu"


def test_phan_nhom():
    # "sửa xe" bỏ dấu = "sua xe" — không được dính từ khóa "sữa" (ăn uống);
    # quy tắc: từ khóa dài hơn ("sua xe") thắng từ ngắn ("sua")
    assert _one("sửa xe 2 củ")["category"] == "đi lại"
    assert _one("mua sữa cho con 45k")["category"] == "ăn uống"
    assert _one("đổ xăng 50k")["category"] == "đi lại"
    assert _one("mua thuốc cảm 35k")["category"] == "sức khỏe"
    assert _one("đóng tiền wifi 200k")["category"] == "hóa đơn"


def test_nhieu_khoan_mot_tin():
    result = parse_expenses("ăn trưa 45k, cà phê 30k")
    assert [e["amount"] for e in result] == [45000, 30000]


def test_hom_qua():
    e = _one("hôm qua ăn tối 200k")
    yesterday = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
    assert e["date"] == yesterday and e["amount"] == 200000


# ── Khi nào phải nhường AI (trả None) ────────────────────────────────────
def test_nhuong_ai_khi_khong_chac():
    unsure = [
        "iPhone giá 20tr có đắt không?",   # câu hỏi
        "lương tháng này chắc được tăng 2tr",  # dự đoán
        "định mua áo 500k",                 # dự định
        "thứ 2 vừa rồi đổ xăng 60k",        # ngày phức tạp
        "chuyển 500k với 200k cho 2 đứa",   # 2 số tiền 1 đoạn
        "37k5",                             # có tiền nhưng không có tên khoản
        "hôm nay trời đẹp",                 # không có tiền
        "abc xyz 15k",                      # không đoán được nhóm chi
    ]
    for text in unsure:
        assert parse_expenses(text) is None, f"{text!r} lẽ ra phải nhường AI"
