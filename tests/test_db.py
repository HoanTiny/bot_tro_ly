"""Test tầng lưu trữ SQLite. Chạy: pytest tests/test_db.py -v"""

from datetime import datetime

import db


# ── Lịch sử chat ──────────────────────────────────────────────────────────
def test_luu_va_doc_dung_thu_tu():
    db.add_message(111, "user", "xin chào")
    db.add_message(111, "assistant", "chào bạn!")
    db.add_message(111, "user", "hôm nay thứ mấy?")

    assert db.get_history(111, 20) == [
        {"role": "user", "content": "xin chào"},
        {"role": "assistant", "content": "chào bạn!"},
        {"role": "user", "content": "hôm nay thứ mấy?"},
    ]


def test_tach_lich_su_theo_chat_id():
    db.add_message(111, "user", "của người 111")
    db.add_message(222, "user", "của người 222")

    assert len(db.get_history(111, 20)) == 1
    assert len(db.get_history(222, 20)) == 1


def test_limit_va_tin_dau_phai_la_user():
    for i in range(30):
        db.add_message(333, "user", f"câu hỏi {i}")
        db.add_message(333, "assistant", f"trả lời {i}")
    db.add_message(333, "user", "câu hỏi mới")

    history = db.get_history(333, 20)
    assert len(history) <= 20
    # Claude API bắt buộc tin đầu tiên là "user"
    assert history[0]["role"] == "user"
    assert history[-1] == {"role": "user", "content": "câu hỏi mới"}


def test_reset_chi_xoa_dung_chat():
    db.add_message(111, "user", "aaa")
    db.add_message(222, "user", "bbb")

    db.clear_history(111)
    assert db.get_history(111, 20) == []
    assert len(db.get_history(222, 20)) == 1


# ── Ghi chú ───────────────────────────────────────────────────────────────
def test_them_va_liet_ke_ghi_chu():
    id1 = db.add_note(111, "mua sữa")
    id2 = db.add_note(111, "họp 9h sáng mai")

    notes = db.get_notes(111)
    assert [(n[0], n[1]) for n in notes] == [(id1, "mua sữa"), (id2, "họp 9h sáng mai")]
    # created_at phải theo giờ máy (localtime), sát thời điểm hiện tại
    for _, _, created_at in notes:
        saved = datetime.strptime(created_at, "%Y-%m-%d %H:%M:%S")
        assert abs((datetime.now() - saved).total_seconds()) < 60


def test_xoa_ghi_chu_dung_quyen_so_huu():
    note_cua_111 = db.add_note(111, "của tôi")
    note_cua_999 = db.add_note(999, "của người khác")

    assert db.delete_note(111, note_cua_111) is True
    assert db.delete_note(111, note_cua_111) is False  # xóa lần 2: không còn
    assert db.delete_note(111, note_cua_999) is False  # không xóa được của người khác
    assert len(db.get_notes(999)) == 1


# ── Chi tiêu ──────────────────────────────────────────────────────────────
def test_chi_tieu_loc_thang_va_tong_theo_nhom():
    db.add_expense(111, "ăn sáng", 15000, "ăn uống")
    db.add_expense(111, "ăn trưa", 45000, "ăn uống")
    db.add_expense(111, "đổ xăng", 50000, "đi lại")
    db.add_expense(555, "của người khác", 99000, "khác")

    month = datetime.now().strftime("%Y-%m")
    rows = db.get_month_expenses(111, month)
    assert len(rows) == 3
    assert rows[0][0] == "đổ xăng"  # mới nhất trước

    assert db.get_month_summary(111, month) == [("ăn uống", 60000), ("đi lại", 50000)]
    assert db.get_month_expenses(111, "2020-01") == []


def test_tong_chi_theo_khoang_ngay():
    db.add_expense(111, "ăn sáng", 15000, "ăn uống")
    db.add_expense(111, "đổ xăng", 50000, "đi lại")

    today = datetime.now().strftime("%Y-%m-%d")
    assert dict(db.get_summary_between(111, today, today)) == {
        "ăn uống": 15000,
        "đi lại": 50000,
    }
    assert 111 in db.get_chat_ids_with_expenses(today, today)
    assert db.get_summary_between(111, "2020-01-01", "2020-01-07") == []


def test_tien_thu_va_can_doi():
    """Khoản thu không được lẫn vào tổng chi theo nhóm, và tính riêng được."""
    db.add_expense(111, "ăn sáng", 15000, "ăn uống")  # kind mặc định 'chi'
    db.add_expense(111, "nhận lương", 15000000, "thu nhập", kind="thu")

    month = datetime.now().strftime("%Y-%m")
    # Tổng chi theo nhóm KHÔNG chứa khoản thu
    assert dict(db.get_month_summary(111, month)) == {"ăn uống": 15000}
    # Tổng thu tính riêng
    assert db.get_month_income(111, month) == 15000000
    assert db.get_month_income(111, "2020-01") == 0  # tháng không có gì -> 0
    # Danh sách chi tiết chứa cả hai, kèm kind
    kinds = {row[4] for row in db.get_month_expenses(111, month)}
    assert kinds == {"chi", "thu"}


def test_nhac_lap_lai_doi_lich():
    """Lời nhắc lặp: sau khi dời lịch vẫn sent=0 và remind_at mới."""
    rid = db.add_reminder(111, "uống thuốc", "2020-01-01 08:00", repeat="daily")

    due = db.get_due_reminders("2026-01-01 00:00")
    assert due[0][3] == "daily" and due[0][4] == "2020-01-01 08:00"

    db.reschedule_reminder(rid, "2099-01-02 08:00")
    # Không còn đến hạn, nhưng vẫn nằm trong danh sách sắp tới (sent = 0)
    assert db.get_due_reminders("2026-01-01 00:00") == []
    pending = db.get_pending_reminders(111)
    assert pending[0][2] == "2099-01-02 08:00" and pending[0][3] == "daily"


def test_chi_tieu_ghi_lui_ngay():
    """Khoản chi "hôm qua" phải xuất hiện đúng ngày hôm qua trong mọi truy vấn."""
    from datetime import timedelta

    from utils import local_date_to_utc_timestamp

    yesterday = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
    db.add_expense(111, "ăn tối", 200000, "ăn uống", created_at=local_date_to_utc_timestamp(yesterday))

    # Lọc theo khoảng ngày: hôm qua có, hôm nay không
    assert dict(db.get_summary_between(111, yesterday, yesterday)) == {"ăn uống": 200000}
    today = datetime.now().strftime("%Y-%m-%d")
    assert db.get_summary_between(111, today, today) == []

    # Cột ngày hiển thị phải là hôm qua (trừ khi hôm qua thuộc tháng trước)
    rows = db.get_month_expenses(111, yesterday[:7])
    assert rows[0][3] == f"{yesterday[8:10]}/{yesterday[5:7]}"


# ── Lời nhắc ──────────────────────────────────────────────────────────────
def test_loi_nhac_den_gio_va_danh_dau_da_gui():
    qua_khu = db.add_reminder(111, "họp với khách", "2020-01-01 08:00")
    tuong_lai = db.add_reminder(111, "đón con", "2099-01-01 17:00")

    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    due_ids = [r[0] for r in db.get_due_reminders(now)]
    assert qua_khu in due_ids
    assert tuong_lai not in due_ids

    db.mark_reminder_sent(qua_khu)
    assert qua_khu not in [r[0] for r in db.get_due_reminders(now)]  # không nhắc lặp


def test_huy_loi_nhac():
    rid = db.add_reminder(111, "đón con", "2099-01-01 17:00")

    assert [p[0] for p in db.get_pending_reminders(111)] == [rid]
    assert db.delete_reminder(111, rid) is True
    assert db.get_pending_reminders(111) == []
    assert db.delete_reminder(111, rid) is False  # đã xóa rồi
