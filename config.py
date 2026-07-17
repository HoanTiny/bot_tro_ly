"""
Cấu hình tập trung: đọc biến môi trường, các hằng số dùng chung.

Tách config ra file riêng để: (1) nhìn một chỗ biết bot cấu hình thế nào,
(2) các module khác import từ đây thay vì mỗi nơi tự đọc os.getenv một kiểu.
"""

import os

from dotenv import load_dotenv

load_dotenv()  # đọc biến môi trường từ file .env

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY")
# Chiến lược 2 model — chọn model theo độ khó của việc:
# - Sonnet (mạnh, đắt): chat với người dùng, viết nhận xét — cần suy luận, diễn đạt
# - Haiku (nhỏ, rẻ hơn ~10 lần, nhanh gấp đôi): bóc tách "ăn sáng 15k" thành JSON —
#   việc đơn giản, có validate lại phía sau nên model nhỏ là quá đủ
CLAUDE_MODEL = "claude-sonnet-4-5"
EXTRACT_MODEL = "claude-haiku-4-5-20251001"

# Prompt hệ thống: định hình "tính cách" của bot — sửa file này là đổi được
# ngay tính cách, không cần đụng code. Mỗi dòng là một "nét tính cách";
# muốn bot khác đi (nghiêm túc hơn, hài hước hơn, xưng "em"...) thì sửa/thêm dòng.
SYSTEM_PROMPT = (
    "Bạn là trợ lý cá nhân trên Telegram, xưng 'mình' và gọi người dùng là 'bạn'.\n"
    "Phong cách: thân thiện, tự nhiên như bạn bè nhắn tin, nhưng thông tin phải chính xác.\n"
    "Trả lời NGẮN GỌN — mặc định 1-4 câu vì đây là chat di động; chỉ viết dài khi "
    "được yêu cầu giải thích kỹ hoặc nội dung thật sự cần.\n"
    "Dùng emoji tiết chế (tối đa 1-2 mỗi tin, chỗ hợp lý), không lạm dụng.\n"
    "Khi trả lời về số liệu (chi tiêu, tài liệu), ưu tiên con số cụ thể thay vì nói chung chung.\n"
    "Không biết thì nói thẳng là không biết — tuyệt đối không bịa.\n"
    "Trả lời bằng tiếng Việt trừ khi người dùng dùng ngôn ngữ khác."
)

MAX_HISTORY_MESSAGES = 20  # giới hạn số tin nhắn nhớ, tránh phình quá to

# Các nhóm chi tiêu hợp lệ — liệt kê cứng để Claude không tự bịa nhóm mới
EXPENSE_CATEGORIES = ["ăn uống", "đi lại", "mua sắm", "hóa đơn", "giải trí", "sức khỏe", "khác"]

TIMEZONE = "Asia/Ho_Chi_Minh"

if not TELEGRAM_BOT_TOKEN or not ANTHROPIC_API_KEY:
    raise RuntimeError(
        "Thiếu TELEGRAM_BOT_TOKEN hoặc ANTHROPIC_API_KEY. "
        "Kiểm tra lại file .env (xem .env.example để biết định dạng)."
    )
