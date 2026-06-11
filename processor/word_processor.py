from docx import Document
from docx.oxml.ns import qn
import re

TAG_PATTERN = re.compile(r'\b[0-9][A-Z]{1,2}-[A-Z]{1,3}-[0-9]{3,4}[A-Z]?\b')

def detect_tag_numbers(text: str) -> list[str]:
    return list(set(TAG_PATTERN.findall(text.upper())))

def serialize_table(table) -> list[str]:
    rows_data = []
    headers = []
    for i, row in enumerate(table.rows):
        cells = [cell.text.strip() for cell in row.cells]
        if i == 0:
            headers = cells
        else:
            if not any(cells):
                continue
            parts = []
            for h, c in zip(headers, cells):
                if c:
                    parts.append(f"{h}: {c}")
            if parts:
                rows_data.append(" | ".join(parts))
    return rows_data

def is_heading(para) -> bool:
    return para.style.name.startswith("Heading")

def process_word(doc_id: int, file_path: str) -> dict:
    doc = Document(file_path)
    chunks = []
    table_rows = []
    chunk_index = 0
    current_heading = ""
    buffer = ""
    pseudo_page = 1  # Word tidak punya halaman eksplisit

    def flush_buffer():
        nonlocal buffer, chunk_index, pseudo_page
        if buffer.strip():
            chunks.append({
                "doc_id": doc_id,
                "chunk_index": chunk_index,
                "halaman": pseudo_page,
                "slide_number": None,
                "slide_title": current_heading or None,
                "sheet_name": None,
                "content": buffer.strip(),
                "embedding": None
            })
            chunk_index += 1
            buffer = ""

    # Iterasi semua elemen dokumen (paragraf + tabel)
    body = doc.element.body
    for child in body:
        tag = child.tag.split("}")[-1] if "}" in child.tag else child.tag

        if tag == "p":
            # Paragraf
            para_text = "".join([r.text for r in child.iter() if r.tag.endswith("}t")])
            style_name = ""
            pPr = child.find(qn("w:pPr"))
            if pPr is not None:
                pStyle = pPr.find(qn("w:pStyle"))
                if pStyle is not None:
                    style_name = pStyle.get(qn("w:val"), "")

            if "Heading" in style_name or "heading" in style_name:
                flush_buffer()
                current_heading = para_text.strip()
                buffer = f"[{current_heading}]\n"
            else:
                if para_text.strip():
                    buffer += para_text.strip() + "\n"
                    if len(buffer) > 1500:
                        flush_buffer()

        elif tag == "tbl":
            flush_buffer()
            # Proses tabel
            tbl_rows = []
            headers = []
            row_elements = child.findall(f".//{qn('w:tr')}")
            for r_idx, tr in enumerate(row_elements):
                cells_text = []
                for tc in tr.findall(f".//{qn('w:tc')}"):
                    cell_text = "".join(
                        t.text for t in tc.iter() if t.tag.endswith("}t") and t.text
                    ).strip()
                    cells_text.append(cell_text)

                if r_idx == 0:
                    headers = cells_text
                else:
                    if not any(cells_text):
                        continue
                    row_dict = {}
                    parts = []
                    for h, c in zip(headers, cells_text):
                        if c:
                            row_dict[h] = c
                            parts.append(f"{h}: {c}")

                    row_text = " | ".join(parts)
                    tags = detect_tag_numbers(row_text)
                    tag_num = tags[0] if tags else None

                    table_rows.append({
                        "doc_id": doc_id,
                        "halaman": pseudo_page,
                        "sheet_name": None,
                        "row_index": r_idx,
                        "row_data": row_dict,
                        "tag_number": tag_num
                    })

                    chunks.append({
                        "doc_id": doc_id,
                        "chunk_index": chunk_index,
                        "halaman": pseudo_page,
                        "slide_number": None,
                        "slide_title": current_heading or None,
                        "sheet_name": None,
                        "content": f"[TABEL] {row_text}",
                        "embedding": None
                    })
                    chunk_index += 1

        elif tag == "sectPr":
            pseudo_page += 1

    flush_buffer()

    all_tags = []
    for chunk in chunks:
        all_tags.extend(detect_tag_numbers(chunk["content"]))

    return {
        "chunks": chunks,
        "table_rows": table_rows,
        "auto_tags": list(set(all_tags)),
        "total_pages": pseudo_page
    }
