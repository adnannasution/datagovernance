import os
import psycopg
from psycopg.rows import dict_row
from dotenv import load_dotenv

load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL")

def get_conn():
    return psycopg.connect(DATABASE_URL, row_factory=dict_row)

# ─── doc_registry ───────────────────────────────────────────

def insert_doc(judul, tipe_dokumen, ru, nomor_dokumen, deskripsi,
               file_path, file_name, file_type, file_size_bytes, uploaded_by="system"):
    with get_conn() as conn:
        row = conn.execute("""
            INSERT INTO doc_registry
                (judul, tipe_dokumen, ru, nomor_dokumen, deskripsi,
                 file_path, file_name, file_type, file_size_bytes, uploaded_by)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            RETURNING id
        """, (judul, tipe_dokumen, ru, nomor_dokumen, deskripsi,
              file_path, file_name, file_type, file_size_bytes, uploaded_by)).fetchone()
        conn.commit()
        return row["id"]

def update_doc_status(doc_id, status, error_message=None,
                      total_pages=None, total_chunks=None, processing_meta=None):
    import json
    with get_conn() as conn:
        conn.execute("""
            UPDATE doc_registry SET
                status = %s,
                error_message = %s,
                total_pages = COALESCE(%s, total_pages),
                total_chunks = COALESCE(%s, total_chunks),
                processing_meta = COALESCE(%s::jsonb, processing_meta),
                updated_at = NOW()
            WHERE id = %s
        """, (status, error_message, total_pages, total_chunks,
              json.dumps(processing_meta) if processing_meta else None, doc_id))
        conn.commit()

def get_doc(doc_id):
    with get_conn() as conn:
        return conn.execute(
            "SELECT * FROM doc_registry WHERE id = %s", (doc_id,)
        ).fetchone()

def list_docs(ru=None, tipe=None, status=None, search=None, limit=50, offset=0):
    filters, params = [], []
    if ru:
        filters.append("ru = %s"); params.append(ru)
    if tipe:
        filters.append("tipe_dokumen = %s"); params.append(tipe)
    if status:
        filters.append("status = %s"); params.append(status)
    if search:
        filters.append("(judul ILIKE %s OR nomor_dokumen ILIKE %s)")
        params += [f"%{search}%", f"%{search}%"]
    where = ("WHERE " + " AND ".join(filters)) if filters else ""
    params += [limit, offset]
    with get_conn() as conn:
        rows = conn.execute(f"""
            SELECT d.*, 
                   COUNT(DISTINCT t.tag_number) as total_tags
            FROM doc_registry d
            LEFT JOIN doc_tag_links t ON t.doc_id = d.id
            {where}
            GROUP BY d.id
            ORDER BY d.uploaded_at DESC
            LIMIT %s OFFSET %s
        """, params).fetchall()
        total = conn.execute(f"""
            SELECT COUNT(*) as n FROM doc_registry d {where}
        """, params[:-2]).fetchone()["n"]
        return rows, total

def delete_doc(doc_id):
    with get_conn() as conn:
        doc = conn.execute(
            "SELECT file_path FROM doc_registry WHERE id = %s", (doc_id,)
        ).fetchone()
        conn.execute("DELETE FROM doc_registry WHERE id = %s", (doc_id,))
        conn.commit()
        return doc

def get_dashboard_stats():
    with get_conn() as conn:
        stats = conn.execute("""
            SELECT
                COUNT(*) FILTER (WHERE status = 'ready') as total_ready,
                COUNT(*) FILTER (WHERE status = 'pending' OR status = 'processing') as total_processing,
                COUNT(*) FILTER (WHERE status = 'error') as total_error,
                COUNT(*) as total_docs,
                COUNT(*) FILTER (WHERE uploaded_at::date = CURRENT_DATE) as today_uploads
            FROM doc_registry
        """).fetchone()
        chunks = conn.execute(
            "SELECT COUNT(*) as n FROM doc_chunks"
        ).fetchone()["n"]
        tags = conn.execute(
            "SELECT COUNT(DISTINCT tag_number) as n FROM doc_tag_links"
        ).fetchone()["n"]
        by_ru = conn.execute("""
            SELECT ru, COUNT(*) as n FROM doc_registry
            WHERE ru IS NOT NULL GROUP BY ru ORDER BY n DESC
        """).fetchall()
        by_tipe = conn.execute("""
            SELECT tipe_dokumen, COUNT(*) as n FROM doc_registry
            GROUP BY tipe_dokumen ORDER BY n DESC
        """).fetchall()
        recent = conn.execute("""
            SELECT id, judul, ru, tipe_dokumen, status, uploaded_at
            FROM doc_registry ORDER BY uploaded_at DESC LIMIT 8
        """).fetchall()
        return {
            **dict(stats),
            "total_chunks": chunks,
            "total_tags": tags,
            "by_ru": list(by_ru),
            "by_tipe": list(by_tipe),
            "recent": list(recent)
        }

# ─── doc_chunks ─────────────────────────────────────────────

def insert_chunks(chunks: list[dict]):
    if not chunks:
        return
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.executemany("""
                INSERT INTO doc_chunks
                    (doc_id, chunk_index, halaman, slide_number, slide_title, sheet_name, content, embedding)
                VALUES (%(doc_id)s, %(chunk_index)s, %(halaman)s, %(slide_number)s,
                        %(slide_title)s, %(sheet_name)s, %(content)s, %(embedding)s)
            """, chunks)
        conn.commit()

def get_chunks_preview(doc_id, limit=5):
    with get_conn() as conn:
        return conn.execute("""
            SELECT chunk_index, halaman, slide_number, slide_title, sheet_name,
                   LEFT(content, 300) as content_preview
            FROM doc_chunks WHERE doc_id = %s
            ORDER BY chunk_index LIMIT %s
        """, (doc_id, limit)).fetchall()

# ─── doc_table_rows ─────────────────────────────────────────

def insert_table_rows(rows: list[dict]):
    if not rows:
        return
    import json
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.executemany("""
                INSERT INTO doc_table_rows
                    (doc_id, halaman, sheet_name, row_index, row_data, tag_number)
                VALUES (%(doc_id)s, %(halaman)s, %(sheet_name)s, %(row_index)s,
                        %(row_data)s, %(tag_number)s)
            """, [{**r, "row_data": json.dumps(r["row_data"])} for r in rows])
        conn.commit()

# ─── doc_tag_links ──────────────────────────────────────────

def insert_tag_links(doc_id, tag_numbers: list[str], link_type="manual"):
    if not tag_numbers:
        return
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.executemany("""
                INSERT INTO doc_tag_links (doc_id, tag_number, link_type)
                VALUES (%s, %s, %s)
                ON CONFLICT (doc_id, tag_number) DO NOTHING
            """, [(doc_id, tag.strip().upper(), link_type) for tag in tag_numbers if tag.strip()])
        conn.commit()

def get_tag_links(doc_id):
    with get_conn() as conn:
        return conn.execute("""
            SELECT tag_number, link_type, created_at
            FROM doc_tag_links WHERE doc_id = %s ORDER BY tag_number
        """, (doc_id,)).fetchall()

def delete_tag_link(doc_id, tag_number):
    with get_conn() as conn:
        conn.execute(
            "DELETE FROM doc_tag_links WHERE doc_id = %s AND tag_number = %s",
            (doc_id, tag_number)
        )
        conn.commit()

# ─── doc_versions ───────────────────────────────────────────

def insert_version(doc_id, file_path, file_name, catatan=None, uploaded_by="system"):
    with get_conn() as conn:
        last = conn.execute("""
            SELECT COALESCE(MAX(version_number), 0) as v
            FROM doc_versions WHERE doc_id = %s
        """, (doc_id,)).fetchone()["v"]
        conn.execute("""
            INSERT INTO doc_versions (doc_id, version_number, file_path, file_name, catatan, uploaded_by)
            VALUES (%s, %s, %s, %s, %s, %s)
        """, (doc_id, last + 1, file_path, file_name, catatan, uploaded_by))
        conn.commit()
        return last + 1

def get_versions(doc_id):
    with get_conn() as conn:
        return conn.execute("""
            SELECT * FROM doc_versions WHERE doc_id = %s ORDER BY version_number DESC
        """, (doc_id,)).fetchall()

# ─── smart search ───────────────────────────────────────────

def vector_search(query_embedding: list[float], ru=None, tipe=None,
                  tag_number=None, limit=10, threshold=0.4):
    emb_str = '[' + ','.join(map(str, query_embedding)) + ']'
    filters = ["d.status = 'ready'", "1 - (c.embedding <=> %s::vector) >= %s"]
    extra_params = []

    if ru:
        filters.append("d.ru = %s"); extra_params.append(ru)
    if tipe:
        filters.append("d.tipe_dokumen = %s"); extra_params.append(tipe)

    join_tag = ""
    if tag_number:
        join_tag = "JOIN doc_tag_links t ON t.doc_id = d.id"
        filters.append("t.tag_number = %s")
        extra_params.append(tag_number.upper())

    where = "WHERE " + " AND ".join(filters)

    # SQL %s order: SELECT emb, WHERE emb, WHERE threshold, extra_filters, ORDER emb, LIMIT
    final_params = [emb_str, emb_str, threshold] + extra_params + [emb_str, limit]

    with get_conn() as conn:
        return conn.execute(f"""
            SELECT
                c.id as chunk_id,
                c.content,
                c.halaman,
                c.slide_number,
                c.slide_title,
                c.sheet_name,
                d.id as doc_id,
                d.judul,
                d.ru,
                d.tipe_dokumen,
                d.file_path,
                d.file_name,
                1 - (c.embedding <=> %s::vector) AS score
            FROM doc_chunks c
            JOIN doc_registry d ON d.id = c.doc_id
            {join_tag}
            {where}
            ORDER BY c.embedding <=> %s::vector
            LIMIT %s
        """, final_params).fetchall()
