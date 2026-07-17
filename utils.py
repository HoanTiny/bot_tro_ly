"""Các hàm tiện ích nhỏ, không phụ thuộc Telegram hay Claude — dễ test nhất."""


def format_money(amount: int) -> str:
    """15000 -> "15.000đ" (kiểu Việt Nam: chấm ngăn cách hàng nghìn)."""
    return f"{amount:,}".replace(",", ".") + "đ"
