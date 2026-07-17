"""
Xuất báo cáo chi tiêu ra file Excel (.xlsx) bằng openpyxl.

Tách thành module riêng, không đụng Telegram hay Claude: nhận dữ liệu vào,
trả file ra — nhờ vậy test được offline (tạo file trong RAM rồi đọc lại).
"""

from io import BytesIO

from openpyxl import Workbook
from openpyxl.styles import Font

HEADER_FONT = Font(bold=True)


def build_month_report(
    rows: list[tuple[str, int, str, str, str]],
    summary: list[tuple[str, int]],
    year_month: str,
    total_income: int = 0,
) -> BytesIO:
    """Tạo file Excel 2 sheet: "Chi tiết" (từng khoản) và "Tổng hợp" (theo nhóm).

    rows: [(item, amount, category, "dd/mm", kind), ...] — mới nhất trước (từ db)
    total_income: tổng tiền thu trong tháng (hiện ở sheet Tổng hợp)
    Trả về BytesIO — file nằm trong RAM, gửi thẳng qua Telegram không cần
    ghi ra đĩa (đỡ phải dọn file tạm).
    """
    wb = Workbook()

    # ── Sheet 1: từng khoản thu/chi, cũ -> mới cho dễ đọc ────────────────
    ws = wb.active
    ws.title = "Chi tiết"
    ws.append(["Ngày", "Khoản", "Nhóm", "Loại", "Số tiền (đ)"])
    for cell in ws[1]:
        cell.font = HEADER_FONT

    for item, amount, category, day, kind in reversed(rows):
        ws.append([day, item, category, "Thu" if kind == "thu" else "Chi", amount])

    # Định dạng cột tiền có ngăn cách hàng nghìn (Excel tự hiển thị 15,000)
    for row_idx in range(2, ws.max_row + 1):
        ws.cell(row=row_idx, column=5).number_format = "#,##0"

    # Nới độ rộng cột cho dễ nhìn (đơn vị ~ số ký tự)
    for col, width in zip("ABCDE", (10, 30, 14, 8, 14)):
        ws.column_dimensions[col].width = width

    # ── Sheet 2: tổng theo nhóm + thu, chi, cân đối ──────────────────────
    ws2 = wb.create_sheet("Tổng hợp")
    ws2.append([f"Thu chi tháng {year_month[5:7]}/{year_month[:4]}", ""])
    ws2["A1"].font = HEADER_FONT
    ws2.append(["Nhóm", "Tổng (đ)"])
    for cell in ws2[2]:
        cell.font = HEADER_FONT

    for category, total in summary:
        ws2.append([category, total])

    total_expense = sum(total for _, total in summary)
    for label, value in (
        ("TỔNG CHI", total_expense),
        ("TỔNG THU", total_income),
        ("CÂN ĐỐI", total_income - total_expense),
    ):
        ws2.append([label, value])
        for cell in ws2[ws2.max_row]:
            cell.font = HEADER_FONT

    for row_idx in range(3, ws2.max_row + 1):
        ws2.cell(row=row_idx, column=2).number_format = "#,##0"
    ws2.column_dimensions["A"].width = 20
    ws2.column_dimensions["B"].width = 14

    # Lưu vào RAM thay vì file trên đĩa
    buffer = BytesIO()
    wb.save(buffer)
    buffer.seek(0)  # tua con trỏ về đầu để bên nhận đọc từ byte đầu tiên
    return buffer
