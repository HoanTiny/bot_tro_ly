"""
conftest.py — file đặc biệt của pytest: các fixture khai báo ở đây
tự động dùng được trong mọi file test cùng thư mục.

Fixture = "đồ nghề chuẩn bị sẵn" cho mỗi test. Ở đây: mỗi test được cấp
một database MỚI TINH trong thư mục tạm, nên các test không dẫm chân nhau
và không bao giờ đụng vào bot.db thật.
"""

import sys
from pathlib import Path

import pytest

# Cho phép `import db` khi chạy pytest từ thư mục gốc dự án
sys.path.insert(0, str(Path(__file__).parent.parent))

import db


@pytest.fixture(autouse=True)  # autouse: mọi test tự động được áp dụng
def fresh_db(tmp_path, monkeypatch):
    """Trỏ db.DB_PATH sang file tạm rồi tạo bảng — mỗi test một DB sạch.

    - tmp_path: pytest tự cấp một thư mục tạm riêng cho từng test
    - monkeypatch: thay giá trị biến trong lúc test, hết test tự trả lại
    """
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "test_bot.db")
    db.init_db()
