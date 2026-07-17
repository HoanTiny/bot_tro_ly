# -*- coding: utf-8 -*-
"""Test HỒI QUY cho bóc tách chi tiêu — GỌI API THẬT (Haiku, tốn vài trăm đồng).

Bộ test chính chạy offline nên mặc định pytest BỎ QUA cả file này.
Chạy riêng mỗi khi sửa EXTRACT_SYSTEM_PROMPT trong ai.py:

    RUN_LIVE_AI=1 pytest tests/test_extract_live.py -v        (Git Bash)
    set RUN_LIVE_AI=1 && pytest tests/test_extract_live.py -v (cmd)

Mỗi test ở đây là một câu bot TỪNG hiểu sai ngoài đời — sửa prompt xong
phải chạy lại để chắc chắn lỗi cũ không quay lại (regression test).
Thêm case mới: cứ mỗi lần bot ghi sai, thêm câu đó vào đây trước, sửa
prompt sau — sửa xong test xanh là biết chắc đã hết.
"""

import asyncio
import os

import pytest

pytestmark = pytest.mark.skipif(
    os.getenv("RUN_LIVE_AI") != "1",
    reason="Gọi API thật, tốn tiền — bật bằng biến môi trường RUN_LIVE_AI=1",
)

import ai


def extract(text: str) -> list[dict]:
    # ai.extract_expenses là hàm async; test thường không await được
    # nên dùng asyncio.run để chạy trọn vòng đời event loop trong 1 lệnh
    return asyncio.run(ai.extract_expenses(text))


def test_mot_so_tien_chung_cho_nhieu_hoat_dong_chi_ghi_mot_khoan_gop():
    """Lỗi 17/07/2026: 'đi tàu với thuê xe hết 39k' bị ghi thành 2 khoản
    39k (nhân đôi tiền). Đúng: MỘT khoản gộp 39k."""
    result = extract("Nay đi tàu với thuê xe hết 39k")
    assert len(result) == 1, f"phải là 1 khoản gộp, nhận được: {result}"
    assert result[0]["amount"] == 39000
    assert result[0]["type"] == "chi"


def test_moi_mon_co_tien_rieng_thi_van_tach_du():
    """Chiều ngược lại của case trên — sửa prompt không được làm hỏng nó."""
    result = extract("hôm nay ăn trưa hết 45k, cà phê 30k")
    assert sorted(e["amount"] for e in result) == [30000, 45000]


def test_hoi_gia_khong_phai_khoan_chi():
    result = extract("con iphone đó giờ khoảng 20tr phải không nhỉ?")
    assert result == [], f"hỏi giá mà vẫn ghi sổ: {result}"


def test_tien_thu_nhan_dung_loai():
    result = extract("hôm nay nhận lương 15tr")
    assert len(result) == 1
    assert result[0]["type"] == "thu"
    assert result[0]["amount"] == 15_000_000


# ── Đợt huấn luyện 2 (17/07/2026): các tình huống khó hơn ─────────────────
def test_ghi_lui_ngay_hom_qua():
    from datetime import datetime, timedelta

    result = extract("hôm qua ăn tối hết 120k")
    assert len(result) == 1
    assert result[0]["amount"] == 120_000
    yesterday = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
    assert result[0].get("date") == yesterday, f"date sai: {result[0].get('date')}"


def test_thu_va_chi_lan_trong_mot_tin():
    result = extract("nhận lương 15tr, đóng tiền nhà 4tr5")
    assert len(result) == 2
    by_type = {e["type"]: e["amount"] for e in result}
    assert by_type == {"thu": 15_000_000, "chi": 4_500_000}


def test_tien_viet_bang_chu():
    result = extract("gửi xe 5 ngàn với mua chai nước 12 nghìn")
    assert sorted(e["amount"] for e in result) == [5_000, 12_000]


def test_chuyen_tien_cho_nguoi_than_la_chi():
    result = extract("vừa chuyển cho mẹ 2tr")
    assert len(result) == 1
    assert result[0]["type"] == "chi"
    assert result[0]["amount"] == 2_000_000


def test_du_dinh_mua_khong_ghi_so():
    result = extract("mai chắc phải mua cái quạt mới, tầm 500k")
    assert result == [], f"dự định mua mà vẫn ghi sổ: {result}"


def test_tien_cua_nguoi_khac_khong_ghi_so():
    result = extract("nghe nói sếp mới mua con xe 2 tỷ, ghê thật")
    assert result == [], f"tiền của người khác mà vẫn ghi sổ: {result}"


def test_mot_so_tien_chung_cach_noi_khac():
    """Biến thể của vụ 39k — 'tổng cộng' thay vì 'hết'."""
    result = extract("sáng nay ăn phở với gửi xe tổng cộng 45k")
    assert len(result) == 1, f"phải là 1 khoản gộp: {result}"
    assert result[0]["amount"] == 45_000
