"""Test các hàm tiện ích."""

from datetime import datetime, timedelta

from utils import format_money, local_date_to_utc_timestamp


def test_format_money():
    assert format_money(15000) == "15.000đ"
    assert format_money(2000000) == "2.000.000đ"
    assert format_money(500) == "500đ"
    assert format_money(0) == "0đ"


def test_local_date_to_utc_timestamp():
    result = local_date_to_utc_timestamp("2026-07-16")
    # Đổi ngược lại: UTC + chênh lệch múi giờ phải rơi vào đúng ngày 16/07,
    # quanh 12h trưa (mốc an toàn giữa ngày)
    utc = datetime.strptime(result, "%Y-%m-%d %H:%M:%S")
    offset = datetime.now() - datetime.utcnow()
    local = utc + offset
    assert local.strftime("%Y-%m-%d") == "2026-07-16"
    assert 11 <= local.hour <= 13
