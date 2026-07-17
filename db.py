"""
Lớp lưu trữ SQLite cho bot: lưu lịch sử chat vào file bot.db thay vì RAM.

Vì sao dùng SQLite?
- Tắt bot rồi bật lại, lịch sử chat vẫn còn (dict trong RAM thì mất sạch).
- SQLite có sẵn trong Python (module sqlite3), không cần cài server database.
- Toàn bộ dữ liệu nằm trong 1 file bot.db cùng thư mục — dễ xem, dễ xóa, dễ backup.

Mẹo học: cài "DB Browser for SQLite" (miễn phí) rồi mở file bot.db
để nhìn thấy dữ liệu bot đang lưu — rất trực quan.

── Chế độ đám mây (Turso) — để chạy bot luân phiên nhiều máy ──────────────
Điền TURSO_DATABASE_URL + TURSO_AUTH_TOKEN vào .env thì dữ liệu lưu trên
Turso (SQLite trên mây, miễn phí): mọi thao tác GHI đẩy thẳng lên mây, còn
ĐỌC vẫn từ file replica cục bộ (nhanh như cũ) — khởi động bot ở máy nào
cũng tự kéo dữ liệu mới nhất về, không phải copy bot.db qua lại nữa.
Bỏ trống 2 biến đó thì bot chạy 100% cục bộ với bot.db như bình thường —
toàn bộ hàm bên dưới không cần biết mình đang ở chế độ nào.
"""

import logging
import os
import sqlite3
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()  # tự nạp .env — db.py có thể được import trước config.py

logger = logging.getLogger(__name__)

# File database nằm cùng thư mục với code, dù bạn chạy bot từ đâu
DB_PATH = Path(__file__).parent / "bot.db"

# Cấu hình Turso — có đủ cả 2 biến thì bật chế độ đám mây
TURSO_URL = os.getenv("TURSO_DATABASE_URL")
TURSO_TOKEN = os.getenv("TURSO_AUTH_TOKEN")

# Replica cục bộ của Turso — CỐ Ý khác tên bot.db: file replica có metadata
# đồng bộ riêng, không mở lẫn với file SQLite thường được
REPLICA_PATH = Path(__file__).parent / "bot_turso.db"

# Kết nối Turso dùng chung cho cả tiến trình — mở 1 lần rồi giữ, vì mỗi lần
# mở là một lượt bắt tay với server trên mây (chậm hơn mở file cục bộ nhiều)
_turso_conn = None


class _TursoTransaction:
    """Bọc kết nối Turso để dùng được `with _connect() as conn:` y như sqlite3.

    sqlite3.Connection vốn là context manager (commit khi xong, rollback khi
    lỗi); kết nối libsql cũng vậy nhưng ta cần thêm một việc: sync() sau khi
    commit để kéo thay đổi từ mây về replica cục bộ — không sync thì lệnh
    ĐỌC ngay sau đó (ví dụ /chitieu sau /chi) chưa thấy dữ liệu vừa ghi.
    """

    def __init__(self, conn):
        self._conn = conn

    def __enter__(self):
        return self._conn

    def __exit__(self, exc_type, exc, tb):
        if exc_type is None:
            self._conn.commit()
            try:
                self._conn.sync()
            except Exception as e:
                # commit đã đẩy dữ liệu lên mây thành công — sync lỗi chỉ nghĩa là
                # replica cục bộ tạm cũ, lần thao tác sau sẽ sync lại
                logger.warning("Không sync được replica Turso: %s", e)
        else:
            self._conn.rollback()
        return False


def _get_turso_conn():
    """Mở (1 lần duy nhất) kết nối Turso và kéo dữ liệu mới nhất từ mây về."""
    global _turso_conn
    if _turso_conn is None:
        import libsql  # import tại chỗ: chỉ cần cài libsql khi thật sự dùng Turso

        _turso_conn = libsql.connect(
            str(REPLICA_PATH), sync_url=TURSO_URL, auth_token=TURSO_TOKEN
        )
        _turso_conn.sync()
        logger.info("Đã kết nối Turso và đồng bộ dữ liệu về replica cục bộ.")
    return _turso_conn


def _connect():
    """Mở kết nối tới database — mây (Turso) nếu cấu hình, không thì cục bộ.

    Chế độ cục bộ giữ nguyên nết cũ: mỗi thao tác một kết nối riêng —
    đơn giản và an toàn cho bot nhỏ (SQLite mở kết nối rất nhanh).
    """
    if TURSO_URL and TURSO_TOKEN:
        return _TursoTransaction(_get_turso_conn())
    return sqlite3.connect(DB_PATH)


def _add_column_if_missing(conn: sqlite3.Connection, table: str, column: str, decl: str) -> None:
    """Migration: thêm cột vào bảng ĐANG CÓ DỮ LIỆU nếu cột chưa tồn tại.

    CREATE TABLE IF NOT EXISTS chỉ tạo bảng mới, KHÔNG sửa bảng cũ — muốn
    thêm cột vào bảng cũ phải ALTER TABLE. Dòng DEFAULT trong decl đảm bảo
    các dòng dữ liệu cũ tự nhận giá trị mặc định (vd: khoản chi cũ -> 'chi').
    Đây là dạng đơn giản nhất của "database migration" — kỹ thuật bắt buộc
    khi ứng dụng đã có người dùng thật.
    """
    # .fetchall() thay vì lặp cursor trực tiếp — libsql (Turso) không cho lặp cursor
    columns = [row[1] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()]
    if column not in columns:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {decl}")


def init_db() -> None:
    """Tạo bảng messages nếu chưa có. Gọi 1 lần khi bot khởi động.

    Mỗi dòng là 1 tin nhắn:
    - chat_id: ai chat (mỗi user/nhóm Telegram có 1 chat_id riêng)
    - role: "user" (người gửi) hoặc "assistant" (bot trả lời)
    - content: nội dung tin nhắn
    - created_at: thời điểm lưu (SQLite tự điền)
    """
    with _connect() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                chat_id INTEGER NOT NULL,
                role TEXT NOT NULL,
                content TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        # Index giúp truy vấn "lấy tin nhắn của chat_id X" nhanh khi dữ liệu nhiều
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_messages_chat_id ON messages(chat_id, id)"
        )
        # Bảng thứ hai: ghi chú của người dùng (lệnh /note, /notes, /delnote)
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS notes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                chat_id INTEGER NOT NULL,
                content TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        # Bảng thứ tư: lời nhắc (/remind). remind_at lưu GIỜ ĐỊA PHƯƠNG dạng
        # "YYYY-MM-DD HH:MM" — khác created_at (UTC), vì ta luôn so sánh với
        # giờ hiện tại của máy; định dạng có số 0 đệm nên so sánh chuỗi
        # cũng chính là so sánh thời gian. sent: 0 = chưa nhắc, 1 = đã nhắc.
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS reminders (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                chat_id INTEGER NOT NULL,
                content TEXT NOT NULL,
                remind_at TEXT NOT NULL,
                sent INTEGER NOT NULL DEFAULT 0
            )
            """
        )
        # Bảng thứ ba: chi tiêu (lệnh /chi, /chitieu). amount lưu bằng VND
        # dạng số nguyên — tiền KHÔNG BAO GIỜ lưu kiểu số thực (float) vì
        # float làm tròn sai (0.1 + 0.2 != 0.3).
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS expenses (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                chat_id INTEGER NOT NULL,
                item TEXT NOT NULL,
                amount INTEGER NOT NULL,
                category TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        # Tài liệu người dùng gửi (RAG): bảng documents lưu tên file,
        # bảng chunks lưu từng đoạn văn bản đã chia nhỏ.
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS documents (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                chat_id INTEGER NOT NULL,
                name TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        # chunks là bảng FTS5 — công cụ full-text search CÓ SẴN trong SQLite:
        # tự đánh chỉ mục từng từ để tìm kiếm cực nhanh, xếp hạng kết quả
        # bằng thuật toán BM25. remove_diacritics 2: gõ "nghi phep" vẫn tìm
        # thấy "nghỉ phép" — rất hợp tiếng Việt.
        # UNINDEXED = cột đi kèm để lọc/join, không cần đánh chỉ mục tìm kiếm.
        conn.execute(
            """
            CREATE VIRTUAL TABLE IF NOT EXISTS chunks USING fts5(
                content,
                chat_id UNINDEXED,
                doc_id UNINDEXED,
                tokenize = 'unicode61 remove_diacritics 2'
            )
            """
        )
        # Hạn mức chi tiêu theo nhóm (/hanmuc). PRIMARY KEY ghép (chat_id,
        # category): mỗi người mỗi nhóm chỉ có 1 hạn mức — đặt lại là ghi đè.
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS budgets (
                chat_id INTEGER NOT NULL,
                category TEXT NOT NULL,
                amount INTEGER NOT NULL,
                PRIMARY KEY (chat_id, category)
            )
            """
        )
        # Migration cho database đã có dữ liệu:
        # - expenses.kind: 'chi' (tiền ra) hoặc 'thu' (tiền vào)
        # - reminders.repeat: 'once' / 'daily' / 'weekly'
        _add_column_if_missing(conn, "expenses", "kind", "TEXT NOT NULL DEFAULT 'chi'")
        _add_column_if_missing(conn, "reminders", "repeat", "TEXT NOT NULL DEFAULT 'once'")


def add_message(chat_id: int, role: str, content: str) -> None:
    """Lưu 1 tin nhắn vào database.

    Lưu ý dấu ? — đây là "parameterized query": để sqlite3 tự chèn giá trị
    an toàn, KHÔNG BAO GIỜ tự nối chuỗi SQL (tránh lỗi SQL injection).
    """
    with _connect() as conn:
        conn.execute(
            "INSERT INTO messages (chat_id, role, content) VALUES (?, ?, ?)",
            (chat_id, role, content),
        )


def get_history(chat_id: int, limit: int) -> list[dict[str, str]]:
    """Lấy tối đa `limit` tin nhắn gần nhất của chat_id, theo thứ tự cũ → mới.

    Trả về đúng định dạng Claude API cần: [{"role": ..., "content": ...}, ...]
    """
    with _connect() as conn:
        rows = conn.execute(
            """
            SELECT role, content FROM messages
            WHERE chat_id = ?
            ORDER BY id DESC
            LIMIT ?
            """,
            (chat_id, limit),
        ).fetchall()

    # Query lấy MỚI → CŨ (để LIMIT cắt đúng các tin gần nhất), nên phải đảo
    # lại thành CŨ → MỚI trước khi gửi cho Claude
    history = [{"role": role, "content": content} for role, content in reversed(rows)]

    # Claude API yêu cầu tin nhắn đầu tiên phải là "user". Nếu việc cắt LIMIT
    # làm tin đầu là "assistant" (mất tin user đứng trước nó), bỏ tin đó đi.
    while history and history[0]["role"] != "user":
        history.pop(0)

    return history


def clear_history(chat_id: int) -> None:
    """Xóa toàn bộ lịch sử chat của một chat_id (dùng cho lệnh /reset)."""
    with _connect() as conn:
        conn.execute("DELETE FROM messages WHERE chat_id = ?", (chat_id,))


# ── Ghi chú (/note) ───────────────────────────────────────────────────────
def add_note(chat_id: int, content: str) -> int:
    """Lưu 1 ghi chú, trả về id của ghi chú vừa tạo (để user xóa bằng /delnote)."""
    with _connect() as conn:
        cursor = conn.execute(
            "INSERT INTO notes (chat_id, content) VALUES (?, ?)",
            (chat_id, content),
        )
        # lastrowid = giá trị id (PRIMARY KEY) mà SQLite vừa tự sinh cho dòng mới
        return cursor.lastrowid


def get_notes(chat_id: int) -> list[tuple[int, str, str]]:
    """Lấy tất cả ghi chú của chat_id, cũ trước mới sau.

    Trả về [(id, nội dung, thời điểm tạo), ...]. created_at được SQLite lưu
    theo giờ UTC, nên dùng datetime(..., 'localtime') để đổi sang giờ máy
    (giờ Việt Nam) ngay trong câu SQL. Kết quả dạng "2026-07-16 11:22:05".
    """
    with _connect() as conn:
        return conn.execute(
            """
            SELECT id, content, datetime(created_at, 'localtime')
            FROM notes WHERE chat_id = ? ORDER BY id
            """,
            (chat_id,),
        ).fetchall()


def delete_note(chat_id: int, note_id: int) -> bool:
    """Xóa ghi chú theo id. Trả về True nếu xóa được, False nếu không tìm thấy.

    Điều kiện chat_id = ? rất quan trọng: đảm bảo user chỉ xóa được ghi chú
    CỦA MÌNH, không xóa được ghi chú của người khác dù đoán đúng id.
    """
    with _connect() as conn:
        cursor = conn.execute(
            "DELETE FROM notes WHERE id = ? AND chat_id = ?",
            (note_id, chat_id),
        )
        # rowcount = số dòng bị ảnh hưởng; 0 nghĩa là không có gì để xóa
        return cursor.rowcount > 0


# ── Chi tiêu (/chi, /chitieu) ─────────────────────────────────────────────
def add_expense(
    chat_id: int,
    item: str,
    amount: int,
    category: str,
    created_at: str | None = None,
    kind: str = "chi",
) -> int:
    """Lưu 1 khoản thu/chi, trả về id vừa tạo.

    kind: 'chi' (tiền ra) hoặc 'thu' (tiền vào — lương, thưởng, bán đồ...).
    created_at: thời điểm UTC "YYYY-MM-DD HH:MM:SS" — chỉ truyền khi ghi lùi
    ngày ("hôm qua ăn tối 200k"). Bỏ trống thì SQLite tự điền thời điểm bây giờ.
    """
    with _connect() as conn:
        if created_at is None:
            cursor = conn.execute(
                "INSERT INTO expenses (chat_id, item, amount, category, kind) VALUES (?, ?, ?, ?, ?)",
                (chat_id, item, amount, category, kind),
            )
        else:
            cursor = conn.execute(
                "INSERT INTO expenses (chat_id, item, amount, category, kind, created_at) VALUES (?, ?, ?, ?, ?, ?)",
                (chat_id, item, amount, category, kind, created_at),
            )
        return cursor.lastrowid


def get_month_expenses(chat_id: int, year_month: str) -> list[tuple[str, int, str, str, str]]:
    """Các khoản thu/chi trong 1 tháng, mới nhất trước. year_month dạng "2026-07".

    strftime('%Y-%m', ...) cắt phần năm-tháng từ created_at để so sánh —
    đây là cách lọc dữ liệu theo tháng phổ biến trong SQLite.
    Trả về [(item, amount, category, "ngày/tháng", kind), ...].
    """
    with _connect() as conn:
        return conn.execute(
            """
            SELECT item, amount, category,
                   strftime('%d/%m', datetime(created_at, 'localtime')), kind
            FROM expenses
            WHERE chat_id = ?
              AND strftime('%Y-%m', datetime(created_at, 'localtime')) = ?
            ORDER BY id DESC
            """,
            (chat_id, year_month),
        ).fetchall()


def get_month_income(chat_id: int, year_month: str) -> int:
    """Tổng tiền THU trong 1 tháng. COALESCE: SUM trả NULL khi không có dòng
    nào -> đổi thành 0 để bên gọi không phải xử lý None."""
    with _connect() as conn:
        (total,) = conn.execute(
            """
            SELECT COALESCE(SUM(amount), 0) FROM expenses
            WHERE chat_id = ? AND kind = 'thu'
              AND strftime('%Y-%m', datetime(created_at, 'localtime')) = ?
            """,
            (chat_id, year_month),
        ).fetchone()
        return total


def get_summary_between(chat_id: int, start_date: str, end_date: str) -> list[tuple[str, int]]:
    """Tổng chi theo nhóm trong khoảng ngày [start_date, end_date] (dạng YYYY-MM-DD).

    Dùng cho báo cáo tuần: date(...) cắt phần ngày từ created_at để so sánh
    bằng BETWEEN. Trả về [(category, tổng), ...], nhóm lớn nhất trước.
    """
    with _connect() as conn:
        return conn.execute(
            """
            SELECT category, SUM(amount)
            FROM expenses
            WHERE chat_id = ? AND kind = 'chi'
              AND date(datetime(created_at, 'localtime')) BETWEEN ? AND ?
            GROUP BY category
            ORDER BY SUM(amount) DESC
            """,
            (chat_id, start_date, end_date),
        ).fetchall()


def get_chat_ids_with_expenses(start_date: str, end_date: str) -> list[int]:
    """Các chat_id có phát sinh chi tiêu trong khoảng ngày — để biết gửi báo cáo cho ai."""
    with _connect() as conn:
        rows = conn.execute(
            """
            SELECT DISTINCT chat_id FROM expenses
            WHERE date(datetime(created_at, 'localtime')) BETWEEN ? AND ?
            """,
            (start_date, end_date),
        ).fetchall()
    return [chat_id for (chat_id,) in rows]


def get_month_summary(chat_id: int, year_month: str) -> list[tuple[str, int]]:
    """Tổng chi theo từng nhóm trong 1 tháng, nhóm tốn nhiều nhất trước.

    GROUP BY + SUM: để database tự cộng dồn — nhanh và gọn hơn nhiều so với
    kéo hết dữ liệu về rồi cộng bằng Python. Trả về [(category, tổng), ...].
    """
    with _connect() as conn:
        return conn.execute(
            """
            SELECT category, SUM(amount)
            FROM expenses
            WHERE chat_id = ? AND kind = 'chi'
              AND strftime('%Y-%m', datetime(created_at, 'localtime')) = ?
            GROUP BY category
            ORDER BY SUM(amount) DESC
            """,
            (chat_id, year_month),
        ).fetchall()


# ── Hạn mức chi tiêu (/hanmuc) ────────────────────────────────────────────
def set_budget(chat_id: int, category: str, amount: int) -> None:
    """Đặt (hoặc ghi đè) hạn mức tháng cho một nhóm chi.

    ON CONFLICT ... DO UPDATE = "upsert": chưa có thì INSERT, có rồi thì
    UPDATE — một câu SQL thay cho cặp SELECT-rồi-INSERT/UPDATE dễ sai.
    """
    with _connect() as conn:
        conn.execute(
            """
            INSERT INTO budgets (chat_id, category, amount) VALUES (?, ?, ?)
            ON CONFLICT(chat_id, category) DO UPDATE SET amount = excluded.amount
            """,
            (chat_id, category, amount),
        )


def get_budgets(chat_id: int) -> dict[str, int]:
    """Toàn bộ hạn mức của một người: {"ăn uống": 3000000, ...}."""
    with _connect() as conn:
        return dict(
            conn.execute(
                "SELECT category, amount FROM budgets WHERE chat_id = ?", (chat_id,)
            ).fetchall()
        )


def delete_budget(chat_id: int, category: str) -> bool:
    """Bỏ hạn mức một nhóm. True nếu có để xóa."""
    with _connect() as conn:
        cursor = conn.execute(
            "DELETE FROM budgets WHERE chat_id = ? AND category = ?",
            (chat_id, category),
        )
        return cursor.rowcount > 0


# ── Lời nhắc (/remind) ────────────────────────────────────────────────────
def add_reminder(chat_id: int, content: str, remind_at: str, repeat: str = "once") -> int:
    """Lưu 1 lời nhắc, remind_at dạng "YYYY-MM-DD HH:MM" giờ địa phương.

    repeat: 'once' (nhắc 1 lần), 'daily' (mỗi ngày), 'weekly' (mỗi tuần).
    """
    with _connect() as conn:
        cursor = conn.execute(
            "INSERT INTO reminders (chat_id, content, remind_at, repeat) VALUES (?, ?, ?, ?)",
            (chat_id, content, remind_at, repeat),
        )
        return cursor.lastrowid


def get_due_reminders(now: str) -> list[tuple[int, int, str, str, str]]:
    """Các lời nhắc ĐÃ ĐẾN GIỜ mà chưa gửi. now dạng "YYYY-MM-DD HH:MM".

    So sánh chuỗi remind_at <= now hoạt động đúng vì định dạng có số 0 đệm
    (ví dụ "2026-07-16 08:05" < "2026-07-16 10:30" cả về chuỗi lẫn thời gian).
    Trả về [(id, chat_id, content, repeat, remind_at), ...].
    """
    with _connect() as conn:
        return conn.execute(
            "SELECT id, chat_id, content, repeat, remind_at FROM reminders "
            "WHERE sent = 0 AND remind_at <= ?",
            (now,),
        ).fetchall()


def reschedule_reminder(reminder_id: int, next_remind_at: str) -> None:
    """Dời lời nhắc lặp lại sang lần kế tiếp (giữ sent = 0 để còn nhắc tiếp)."""
    with _connect() as conn:
        conn.execute(
            "UPDATE reminders SET remind_at = ? WHERE id = ?",
            (next_remind_at, reminder_id),
        )


def mark_reminder_sent(reminder_id: int) -> None:
    """Đánh dấu đã nhắc — chỉ gọi SAU khi gửi tin thành công, để nếu gửi
    lỗi (mạng rớt) thì lần kiểm tra sau sẽ thử gửi lại."""
    with _connect() as conn:
        conn.execute("UPDATE reminders SET sent = 1 WHERE id = ?", (reminder_id,))


def get_pending_reminders(chat_id: int) -> list[tuple[int, str, str, str]]:
    """Các lời nhắc sắp tới của một người, gần nhất trước.
    [(id, content, remind_at, repeat), ...]"""
    with _connect() as conn:
        return conn.execute(
            """
            SELECT id, content, remind_at, repeat FROM reminders
            WHERE chat_id = ? AND sent = 0
            ORDER BY remind_at
            """,
            (chat_id,),
        ).fetchall()


def delete_reminder(chat_id: int, reminder_id: int) -> bool:
    """Hủy lời nhắc theo id (chỉ của chính mình). True nếu xóa được."""
    with _connect() as conn:
        cursor = conn.execute(
            "DELETE FROM reminders WHERE id = ? AND chat_id = ? AND sent = 0",
            (reminder_id, chat_id),
        )
        return cursor.rowcount > 0


# ── Tài liệu (RAG) ────────────────────────────────────────────────────────
def add_document(chat_id: int, name: str, chunks: list[str]) -> int:
    """Lưu 1 tài liệu cùng các đoạn văn bản đã chia nhỏ. Trả về id tài liệu.

    executemany: chèn hàng loạt trong 1 lệnh — nhanh hơn hẳn vòng lặp execute
    từng dòng khi tài liệu có hàng trăm đoạn.
    """
    with _connect() as conn:
        cursor = conn.execute(
            "INSERT INTO documents (chat_id, name) VALUES (?, ?)",
            (chat_id, name),
        )
        doc_id = cursor.lastrowid
        conn.executemany(
            "INSERT INTO chunks (content, chat_id, doc_id) VALUES (?, ?, ?)",
            [(chunk, str(chat_id), doc_id) for chunk in chunks],
        )
        return doc_id


def search_chunks(chat_id: int, query: str, limit: int = 5) -> list[tuple[str, str]]:
    """Tìm các đoạn liên quan nhất tới câu hỏi. Trả về [(nội dung, tên tài liệu)].

    - Câu hỏi được rút thành các từ, nối bằng OR: chỉ cần khớp 1 từ là ứng viên
    - ORDER BY rank: FTS5 tự xếp hạng bằng BM25 — đoạn chứa nhiều từ khóa
      hiếm sẽ đứng đầu (nguyên lý của mọi search engine)
    """
    import re as _re

    words = _re.findall(r"\w+", query)
    if not words:
        return []
    match_query = " OR ".join(words)

    with _connect() as conn:
        return conn.execute(
            """
            SELECT chunks.content, documents.name
            FROM chunks JOIN documents ON documents.id = chunks.doc_id
            WHERE chunks MATCH ? AND chunks.chat_id = ?
            ORDER BY rank LIMIT ?
            """,
            (match_query, str(chat_id), limit),
        ).fetchall()


def list_documents(chat_id: int) -> list[tuple[int, str, int, str]]:
    """Danh sách tài liệu của một người: [(id, tên, số đoạn, ngày tải), ...]."""
    with _connect() as conn:
        return conn.execute(
            """
            SELECT d.id, d.name,
                   (SELECT COUNT(*) FROM chunks WHERE chunks.doc_id = d.id),
                   datetime(d.created_at, 'localtime')
            FROM documents d WHERE d.chat_id = ? ORDER BY d.id
            """,
            (chat_id,),
        ).fetchall()


def delete_document(chat_id: int, doc_id: int) -> bool:
    """Xóa tài liệu + toàn bộ đoạn của nó (chỉ của chính mình). True nếu xóa được."""
    with _connect() as conn:
        cursor = conn.execute(
            "DELETE FROM documents WHERE id = ? AND chat_id = ?",
            (doc_id, chat_id),
        )
        if cursor.rowcount == 0:
            return False
        conn.execute("DELETE FROM chunks WHERE doc_id = ?", (doc_id,))
        return True


# ── Sửa / xóa khoản thu chi ───────────────────────────────────────────────
def get_month_expenses_with_id(chat_id: int, year_month: str) -> list[tuple]:
    """Như get_month_expenses nhưng kèm id — cho tool sửa/xóa của Claude.
    Trả về [(id, item, amount, category, "dd/mm", kind), ...]."""
    with _connect() as conn:
        return conn.execute(
            """
            SELECT id, item, amount, category,
                   strftime('%d/%m', datetime(created_at, 'localtime')), kind
            FROM expenses
            WHERE chat_id = ?
              AND strftime('%Y-%m', datetime(created_at, 'localtime')) = ?
            ORDER BY id DESC
            """,
            (chat_id, year_month),
        ).fetchall()



def get_last_expense(chat_id: int) -> tuple[int, str, int, str, str] | None:
    """Khoản thu/chi mới nhất của một người (cho /undo).
    Trả về (id, item, amount, category, kind) hoặc None."""
    with _connect() as conn:
        return conn.execute(
            """
            SELECT id, item, amount, category, kind FROM expenses
            WHERE chat_id = ? ORDER BY id DESC LIMIT 1
            """,
            (chat_id,),
        ).fetchone()


def delete_expense(chat_id: int, expense_id: int) -> bool:
    """Xóa 1 khoản theo id (chỉ của chính mình). True nếu xóa được."""
    with _connect() as conn:
        cursor = conn.execute(
            "DELETE FROM expenses WHERE id = ? AND chat_id = ?",
            (expense_id, chat_id),
        )
        return cursor.rowcount > 0


def update_expense(chat_id: int, expense_id: int, fields: dict) -> bool:
    """Sửa 1 khoản theo id. fields chỉ nhận các cột trong danh sách cho phép.

    Tên cột KHÔNG THỂ truyền bằng dấu ? (chỉ dùng được cho giá trị), nên
    phải ghép chuỗi — vì vậy bắt buộc lọc qua danh sách trắng (whitelist)
    để không ai tuồn được SQL lạ vào tên cột.
    """
    allowed = {"item", "amount", "category"}
    updates = {col: val for col, val in fields.items() if col in allowed}
    if not updates:
        return False

    set_clause = ", ".join(f"{col} = ?" for col in updates)
    with _connect() as conn:
        cursor = conn.execute(
            f"UPDATE expenses SET {set_clause} WHERE id = ? AND chat_id = ?",
            (*updates.values(), expense_id, chat_id),
        )
        return cursor.rowcount > 0
