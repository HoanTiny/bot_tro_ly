"""Test phần kiểm tra đầu ra của Claude — KHÔNG gọi API (nhanh, miễn phí).

Đây là lý do ta tách validate_expenses/validate_reminder thành hàm riêng
trong ai.py: phần gọi API khó test (chậm, tốn tiền, kết quả thay đổi),
còn phần validate là logic thuần túy — test dễ và đáng test nhất.
"""

import pytest

from ai import MONEY_HINT, _parse_json, _strip_code_fence, validate_expenses, validate_reminder


# ── Bộ lọc "mùi tiền" (regex chạy trước khi gọi AI) ──────────────────────
def test_money_hint_bat_dung_tien():
    for text in ["ăn trưa 35k", "mua áo 2tr", "taxi 50.000đ", "nạp 200 nghìn"]:
        assert MONEY_HINT.search(text), text


def test_money_hint_bo_qua_tin_thuong():
    for text in ["hôm nay trời đẹp", "ngày mai họp lúc 3h", "năm 2026 thế nào"]:
        assert not MONEY_HINT.search(text), text


# ── Validate chi tiêu ─────────────────────────────────────────────────────
def test_giu_khoan_chi_hop_le():
    data = [{"item": "ăn sáng", "amount": 15000, "category": "ăn uống"}]
    assert validate_expenses(data) == data


def test_loai_khoan_chi_sai_dinh_dang():
    assert validate_expenses([{"item": "x", "amount": -5, "category": "ăn uống"}]) == []
    assert validate_expenses([{"item": "x", "amount": "15k", "category": "ăn uống"}]) == []
    assert validate_expenses([{"item": "", "amount": 15000, "category": "ăn uống"}]) == []
    # Claude bịa nhóm mới không có trong danh sách -> loại
    assert validate_expenses([{"item": "x", "amount": 15000, "category": "linh tinh"}]) == []
    assert validate_expenses(["không phải dict"]) == []


def test_giu_cai_dung_loai_cai_sai_trong_cung_danh_sach():
    data = [
        {"item": "ăn sáng", "amount": 15000, "category": "ăn uống"},
        {"item": "hỏng", "amount": 0, "category": "ăn uống"},
    ]
    assert validate_expenses(data) == [data[0]]


def test_truong_date_hop_le_duoc_giu():
    data = [{"item": "ăn tối", "amount": 200000, "category": "ăn uống", "date": "2020-01-15"}]
    assert validate_expenses(data)[0]["date"] == "2020-01-15"


def test_truong_date_sai_bi_bo_nhung_khoan_chi_van_giu():
    # date ở tương lai hoặc sai định dạng -> bỏ date (coi như hôm nay), giữ khoản chi
    for bad_date in ["2099-01-01", "hôm qua", 123]:
        data = [{"item": "x", "amount": 1000, "category": "khác", "date": bad_date}]
        result = validate_expenses(data)
        assert len(result) == 1
        assert "date" not in result[0], bad_date


# ── Validate lời nhắc ─────────────────────────────────────────────────────
def test_loi_nhac_hop_le():
    data = {"content": "họp", "remind_at": "2099-01-01 08:00"}
    assert validate_reminder(data) == data


def test_loi_nhac_khong_hop_le():
    assert validate_reminder({"error": "không rõ thời gian"}) is None
    assert validate_reminder({"content": "họp"}) is None  # thiếu remind_at
    assert validate_reminder({"content": "họp", "remind_at": "8h sáng mai"}) is None  # sai định dạng
    assert validate_reminder({"content": "", "remind_at": "2099-01-01 08:00"}) is None


# ── Gỡ code fence ─────────────────────────────────────────────────────────
def test_strip_code_fence():
    assert _strip_code_fence('[{"a": 1}]') == '[{"a": 1}]'
    assert _strip_code_fence('```json\n[{"a": 1}]\n```') == '[{"a": 1}]'
    assert _strip_code_fence('```\n[{"a": 1}]\n```') == '[{"a": 1}]'


# ── Parse JSON khoan dung (model nhỏ hay kèm chữ thừa quanh JSON) ─────────
def test_parse_json_sach():
    assert _parse_json('{"a": 1}') == {"a": 1}
    assert _parse_json('[1, 2]') == [1, 2]


def test_parse_json_co_chu_thua():
    # Bug thật gặp với Haiku: JSON đúng nhưng kèm giải thích phía sau
    assert _parse_json('{"a": 1}\n\nGiải thích: tôi chọn...') == {"a": 1}
    assert _parse_json('Đây là kết quả:\n{"a": 1}') == {"a": 1}
    assert _parse_json('```json\n{"a": 1}\n```\nghi chú') == {"a": 1}


def test_parse_json_khong_co_json():
    with pytest.raises(ValueError):
        _parse_json("xin lỗi, tôi không hiểu")
