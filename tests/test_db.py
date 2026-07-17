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
