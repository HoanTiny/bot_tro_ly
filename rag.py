"""
RAG (Retrieval-Augmented Generation) — phần xử lý tài liệu:
đọc văn bản từ file (PDF/Word/TXT) và chia nhỏ thành đoạn (chunking).

Quy trình RAG đầy đủ trong bot này:
1. Người dùng gửi file -> extract_text() đọc chữ -> chunk_text() chia đoạn
2. Các đoạn được lưu vào SQLite FTS5 (db.add_document) — bước "đánh chỉ mục"
3. Khi người dùng hỏi, Claude gọi tool search_documents -> db.search_chunks
   tìm các đoạn liên quan nhất (BM25) -> Claude đọc và trả lời kèm nguồn

Vì sao phải CHIA NHỎ? Không thể nhét cả tài liệu 50 trang vào mỗi câu hỏi
(tốn token, loãng thông tin). Chia thành đoạn vài trăm chữ để chỉ lấy đúng
vài đoạn liên quan — nhanh, rẻ, và Claude tập trung hơn.

Bản này dùng tìm kiếm theo TỪ KHÓA (lexical search, thuật toán BM25 của
FTS5). Bước nâng cấp sau này là tìm theo NGỮ NGHĨA (semantic search bằng
embeddings) — tìm được cả khi câu hỏi không chứa từ nào trùng với tài liệu.
"""

import re
from io import BytesIO

SUPPORTED_EXTENSIONS = (".pdf", ".docx", ".txt", ".md")


def extract_text(filename: str, data: bytes) -> str:
    """Đọc chữ từ nội dung file. Hỗ trợ: PDF, Word (.docx), text (.txt, .md).

    Import pypdf/docx bên trong hàm (lazy import): chỉ nạp thư viện khi
    thật sự cần đọc định dạng đó.
    """
    name = filename.lower()

    if name.endswith(".pdf"):
        from pypdf import PdfReader

        reader = PdfReader(BytesIO(data))
        # Mỗi trang một đoạn; extract_text() có thể trả None với trang ảnh
        return "\n\n".join(page.extract_text() or "" for page in reader.pages)

    if name.endswith(".docx"):
        from docx import Document

        document = Document(BytesIO(data))
        return "\n\n".join(p.text for p in document.paragraphs)

    if name.endswith((".txt", ".md")):
        # errors="replace": gặp byte lạ thì thay bằng � chứ không crash
        return data.decode("utf-8", errors="replace")

    raise ValueError(f"Định dạng không hỗ trợ: {filename}")


def chunk_text(text: str, max_chars: int = 800, overlap: int = 100) -> list[str]:
    """Chia văn bản thành các đoạn <= max_chars, ưu tiên cắt ở ranh giới đoạn văn.

    - Tôn trọng đoạn văn (ngăn bởi dòng trống): không cắt giữa chừng nếu tránh được
    - Đoạn văn nào dài quá max_chars mới phải cắt cứng, kèm overlap (phần chồng
      lấn) để câu bị cắt đôi vẫn xuất hiện trọn vẹn ở ít nhất một chunk
    - Các đoạn văn ngắn liên tiếp được gộp lại cho đỡ vụn
    """
    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]

    # Bước 1: đảm bảo không mẩu nào dài quá max_chars
    pieces = []
    for para in paragraphs:
        while len(para) > max_chars:
            pieces.append(para[:max_chars])
            para = para[max_chars - overlap:]  # lùi lại `overlap` ký tự
        if para:
            pieces.append(para)

    # Bước 2: gộp các mẩu ngắn liên tiếp thành chunk gần max_chars
    chunks: list[str] = []
    current = ""
    for piece in pieces:
        if current and len(current) + len(piece) + 2 > max_chars:
            chunks.append(current)
            current = piece
        else:
            current = f"{current}\n\n{piece}" if current else piece
    if current:
        chunks.append(current)

    return chunks
