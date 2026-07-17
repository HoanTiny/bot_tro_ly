"""Test các hàm tiện ích."""

from utils import format_money


def test_format_money():
    assert format_money(15000) == "15.000đ"
    assert format_money(2000000) == "2.000.000đ"
    assert format_money(500) == "500đ"
    assert format_money(0) == "0đ"
