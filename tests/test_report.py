"""Test xuất Excel: tạo file trong RAM rồi đọc ngược lại kiểm tra nội dung."""

from openpyxl import load_workbook

from report import build_month_report

ROWS = [  # mới nhất trước, như db trả về
    ("đổ xăng", 50000, "đi lại", "16/07"),
    ("ăn sáng", 15000, "ăn uống", "15/07"),
]
SUMMARY = [("ăn uống", 15000), ("đi lại", 50000)]


def test_bao_cao_co_du_2_sheet_va_dung_so_lieu():
    buffer = build_month_report(ROWS, SUMMARY, "2026-07")
    wb = load_workbook(buffer)

    assert wb.sheetnames == ["Chi tiết", "Tổng hợp"]

    # Sheet chi tiết: header + 2 dòng, đảo lại thành cũ -> mới
    ws = wb["Chi tiết"]
    assert [c.value for c in ws[1]] == ["Ngày", "Khoản chi", "Nhóm", "Số tiền (đ)"]
    assert [c.value for c in ws[2]] == ["15/07", "ăn sáng", "ăn uống", 15000]
    assert [c.value for c in ws[3]] == ["16/07", "đổ xăng", "đi lại", 50000]

    # Sheet tổng hợp: dòng cuối là tổng cộng
    ws2 = wb["Tổng hợp"]
    last_row = [c.value for c in ws2[ws2.max_row]]
    assert last_row == ["TỔNG CỘNG", 65000]


def test_bao_cao_thang_khong_co_khoan_nao():
    # Danh sách rỗng vẫn phải tạo được file hợp lệ (handler đã chặn trước,
    # nhưng hàm không nên crash nếu bị gọi với dữ liệu rỗng)
    buffer = build_month_report([], [], "2026-01")
    wb = load_workbook(buffer)
    assert wb["Tổng hợp"]["A3"].value == "TỔNG CỘNG"
    assert wb["Tổng hợp"]["B3"].value == 0
