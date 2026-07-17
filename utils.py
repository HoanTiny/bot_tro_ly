"""Các hàm tiện ích nhỏ, không phụ thuộc Telegram hay Claude — dễ test nhất."""

from datetime import datetime


def format_money(amount: int) -> str:
    """15000 -> "15.000đ" (kiểu Việt Nam: chấm ngăn cách hàng nghìn)."""
    return f"{amount:,}".replace(",", ".") + "đ"


def local_date_to_utc_timestamp(date_str: str) -> str:
    """Ngày địa phương "2026-07-16" -> thời điểm UTC "2026-07-16 05:00:00".

    Database lưu created_at theo UTC, còn người dùng nghĩ theo ngày Việt Nam.
    Mẹo: chọn 12h TRƯA địa phương của ngày đó làm mốc rồi trừ chênh lệch múi
    giờ — trưa nằm giữa ngày nên đổi đi đổi lại múi giờ vẫn ra đúng ngày,
    không bị "tràn" sang ngày bên cạnh như nếu chọn 0h.
    """
    local_noon = datetime.strptime(date_str, "%Y-%m-%d").replace(hour=12)
    utc_offset = datetime.now() - datetime.utcnow()  # VN: ~7 tiếng
    return (local_noon - utc_offset).strftime("%Y-%m-%d %H:%M:%S")
