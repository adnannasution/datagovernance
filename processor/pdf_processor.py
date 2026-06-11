import fitz  # PyMuPDF
import pdfplumber
import re

TAG_PATTERN = re.compile(r'\b[0-9][A-Z]{1,2}-[A-Z]{1,3}-[0-9]{3,4}[A-Z]?\b')

def detect_tag_numbers(text: str) -> list[str]:
    return list(set(TAG_PATTERN.findall(text.upper())))

def serialize_table(table: list[list]) -> list[str]:
    """Ubah tabel jadi list string 'key: val | key: val' per baris."""
    if not table or len(table) < 2:
        return []
    headers = [str(h).strip() if h else f"col{i}" for i, h in enumerate(table[0])]
    rows = []
    for row in table[1:]:
        if not any(cell for cell in row):
            continue
        parts = []
        for h, cell in zip(headers, row):
            if cell is not None and str(cell).strip():
                parts.append(f"{h}: {str(cell).strip()}")
        if parts:
            rows.append(" | ".join(parts))
    return rows

def process_pdf(doc_id: int, file_path: str) -> dict:
    chunks = []
    table_rows = []
    chunk_index = 0

    # pdfplumber untuk tabel, fitz untuk teks
    with pdfplumber.open(file_path) as pdf_plumber:
        pdf_fitz = fitz.open(file_path)
        total_pages = len(pdf_fitz)

        for page_num in range(total_pages):
            page_fitz = pdf_fitz[page_num]
            page_plumber = pdf_plumber.pages[page_num]
            halaman = page_num + 1

            # Ekstrak tabel dulu
            tables = page_plumber.extract_tables()
            table_bboxes = [t.bbox for t in page_plumber.find_tables()] if tables else []

            for table in tables:
                serialized = serialize_table(table)
                for row_idx, row_text in enumerate(serialized):
                    tags = detect_tag_numbers(row_text)
                    tag = tags[0] if tags else None

                    # simpan ke table_rows (structured)
                    raw_row = {}
                    if table and len(table) > 0:
                        headers = [str(h).strip() if h else f"col{i}"
                                   for i, h in enumerate(table[0])]
                        if row_idx + 1 < len(table):
                            for h, cell in zip(headers, table[row_idx + 1]):
                                raw_row[h] = str(cell).strip() if cell else ""
                    table_rows.append({
                        "doc_id": doc_id,
                        "halaman": halaman,
                        "sheet_name": None,
                        "row_index": row_idx,
                        "row_data": raw_row,
                        "tag_number": tag
                    })

                    # juga masuk ke chunks untuk RAG
                    chunks.append({
                        "doc_id": doc_id,
                        "chunk_index": chunk_index,
                        "halaman": halaman,
                        "slide_number": None,
                        "slide_title": None,
                        "sheet_name": None,
                        "content": f"[TABEL Halaman {halaman}] {row_text}",
                        "embedding": None
                    })
                    chunk_index += 1

            # Ekstrak teks di luar area tabel
            full_text = page_fitz.get_text("text")
            if full_text.strip():
                # Chunk per paragraf/blok
                blocks = [b.strip() for b in full_text.split("\n\n") if b.strip()]
                buffer = ""
                for block in blocks:
                    if len(buffer) + len(block) < 1500:
                        buffer += " " + block
                    else:
                        if buffer.strip():
                            chunks.append({
                                "doc_id": doc_id,
                                "chunk_index": chunk_index,
                                "halaman": halaman,
                                "slide_number": None,
                                "slide_title": None,
                                "sheet_name": None,
                                "content": buffer.strip(),
                                "embedding": None
                            })
                            chunk_index += 1
                        buffer = block
                if buffer.strip():
                    chunks.append({
                        "doc_id": doc_id,
                        "chunk_index": chunk_index,
                        "halaman": halaman,
                        "slide_number": None,
                        "slide_title": None,
                        "sheet_name": None,
                        "content": buffer.strip(),
                        "embedding": None
                    })
                    chunk_index += 1

        pdf_fitz.close()

    # Kumpulkan semua tag number dari chunks
    all_tags = []
    for chunk in chunks:
        all_tags.extend(detect_tag_numbers(chunk["content"]))

    return {
        "chunks": chunks,
        "table_rows": table_rows,
        "auto_tags": list(set(all_tags)),
        "total_pages": total_pages
    }
