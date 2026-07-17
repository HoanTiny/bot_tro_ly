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
