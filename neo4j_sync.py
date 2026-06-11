"""
neo4j_sync.py
Sync Document nodes dan relasi ke Neo4j.
Auto-triggered saat upload selesai, bisa juga full sync manual.
"""
import os
from neo4j import GraphDatabase
from dotenv import load_dotenv

load_dotenv()

NEO4J_URI      = os.getenv("NEO4J_URI", "bolt://localhost:7687")
NEO4J_USER     = os.getenv("NEO4J_USER", "neo4j")
NEO4J_PASSWORD = os.getenv("NEO4J_PASSWORD", "")

def get_driver():
    return GraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USER, NEO4J_PASSWORD))

def test_connection() -> bool:
    try:
        with get_driver() as driver:
            driver.verify_connectivity()
        return True
    except Exception:
        return False

# ─── Constraints & Indexes ───────────────────────────────────────────────────

def ensure_constraints():
    """Buat constraint dan index kalau belum ada."""
    with get_driver() as driver:
        with driver.session() as session:
            session.run("""
                CREATE CONSTRAINT doc_id_unique IF NOT EXISTS
                FOR (d:Document) REQUIRE d.doc_id IS UNIQUE
            """)
            session.run("""
                CREATE INDEX doc_judul_idx IF NOT EXISTS
                FOR (d:Document) ON (d.judul)
            """)

# ─── Single Document Sync ────────────────────────────────────────────────────

def sync_document(doc: dict, tag_links: list) -> dict:
    """
    Buat/update satu node Document dan relasi ke Equipment.
    Dipanggil otomatis setelah processing selesai.
    
    doc: row dari doc_registry
    tag_links: list of {tag_number, link_type}
    """
    if not test_connection():
        return {"success": False, "error": "Neo4j tidak dapat diakses"}

    try:
        with get_driver() as driver:
            with driver.session() as session:
                # Upsert node Document
                session.run("""
                    MERGE (d:Document {doc_id: $doc_id})
                    SET d.judul        = $judul,
                        d.tipe         = $tipe,
                        d.ru           = $ru,
                        d.nomor        = $nomor,
                        d.deskripsi    = $deskripsi,
                        d.file_name    = $file_name,
                        d.file_type    = $file_type,
                        d.status       = $status,
                        d.total_chunks = $total_chunks,
                        d.uploaded_at  = $uploaded_at
                """, {
                    "doc_id":       doc["id"],
                    "judul":        doc.get("judul", ""),
                    "tipe":         doc.get("tipe_dokumen", ""),
                    "ru":           doc.get("ru", ""),
                    "nomor":        doc.get("nomor_dokumen", ""),
                    "deskripsi":    doc.get("deskripsi", ""),
                    "file_name":    doc.get("file_name", ""),
                    "file_type":    doc.get("file_type", ""),
                    "status":       doc.get("status", ""),
                    "total_chunks": doc.get("total_chunks", 0),
                    "uploaded_at":  str(doc.get("uploaded_at", ""))
                })

                # Buat relasi ke Equipment untuk setiap tag
                linked = 0
                not_found = []
                for link in tag_links:
                    tag = link["tag_number"]
                    link_type = link.get("link_type", "manual")

                    result = session.run("""
                        MATCH (e:Equipment {tag_number: $tag})
                        MATCH (d:Document {doc_id: $doc_id})
                        MERGE (d)-[r:TERKAIT_DENGAN]->(e)
                        SET r.link_type  = $link_type,
                            r.created_at = datetime()
                        RETURN e.tag_number as tag
                    """, {
                        "tag":      tag,
                        "doc_id":   doc["id"],
                        "link_type": link_type
                    })

                    if result.single():
                        linked += 1
                    else:
                        not_found.append(tag)

                return {
                    "success":   True,
                    "doc_id":    doc["id"],
                    "linked":    linked,
                    "not_found": not_found
                }

    except Exception as e:
        return {"success": False, "error": str(e)}

# ─── Full Sync ───────────────────────────────────────────────────────────────

def full_sync(progress_callback=None) -> dict:
    """
    Sync semua dokumen dari PostgreSQL ke Neo4j.
    Dipanggil dari endpoint /api/neo4j/sync.
    """
    from db import get_conn as pg_conn

    if not test_connection():
        return {"success": False, "error": "Neo4j tidak dapat diakses"}

    ensure_constraints()

    total, synced, failed, skipped = 0, 0, 0, 0
    errors = []

    with pg_conn() as conn:
        docs = conn.execute("""
            SELECT * FROM doc_registry WHERE status = 'ready'
            ORDER BY uploaded_at DESC
        """).fetchall()

        total = len(docs)

        for i, doc in enumerate(docs):
            doc = dict(doc)
            try:
                tags = conn.execute("""
                    SELECT tag_number, link_type FROM doc_tag_links
                    WHERE doc_id = %s
                """, (doc["id"],)).fetchall()

                result = sync_document(doc, [dict(t) for t in tags])

                if result["success"]:
                    synced += 1
                else:
                    failed += 1
                    errors.append(f"doc {doc['id']}: {result['error']}")

                if progress_callback:
                    progress_callback(i + 1, total)

            except Exception as e:
                failed += 1
                errors.append(f"doc {doc['id']}: {str(e)}")

    return {
        "success":  True,
        "total":    total,
        "synced":   synced,
        "failed":   failed,
        "skipped":  skipped,
        "errors":   errors[:10]  # max 10 error ditampilkan
    }

# ─── Delete Document Node ────────────────────────────────────────────────────

def delete_document_node(doc_id: int) -> bool:
    """Hapus node Document dari Neo4j saat dokumen dihapus dari PostgreSQL."""
    try:
        with get_driver() as driver:
            with driver.session() as session:
                session.run("""
                    MATCH (d:Document {doc_id: $doc_id})
                    DETACH DELETE d
                """, {"doc_id": doc_id})
        return True
    except Exception:
        return False

# ─── Neo4j Stats ─────────────────────────────────────────────────────────────

def get_neo4j_stats() -> dict:
    """Statistik node dan relasi untuk halaman governance."""
    try:
        with get_driver() as driver:
            with driver.session() as session:
                # Count nodes per label
                labels_result = session.run("""
                    CALL db.labels() YIELD label
                    CALL apoc.cypher.run('MATCH (n:' + label + ') RETURN count(n) as count', {})
                    YIELD value
                    RETURN label, value.count as count
                    ORDER BY count DESC
                """)
                labels = []
                try:
                    labels = [{"label": r["label"], "count": r["count"]}
                              for r in labels_result]
                except Exception:
                    # Fallback tanpa APOC
                    for lbl in ["Equipment", "Document", "BOC", "ICUMonitoring",
                                "ATGMonitoring", "MeteringMonitor", "BadActor"]:
                        try:
                            r = session.run(
                                f"MATCH (n:{lbl}) RETURN count(n) as count"
                            ).single()
                            if r:
                                labels.append({"label": lbl, "count": r["count"]})
                        except Exception:
                            pass

                # Count relasi Document-Equipment
                doc_rel = session.run("""
                    MATCH (:Document)-[r:TERKAIT_DENGAN]->(:Equipment)
                    RETURN count(r) as count
                """).single()

                # Count total relasi
                total_rel = session.run("""
                    MATCH ()-[r]->() RETURN count(r) as count
                """).single()

                # Dokumen terbaru di Neo4j
                recent = session.run("""
                    MATCH (d:Document)
                    RETURN d.doc_id as doc_id, d.judul as judul,
                           d.ru as ru, d.tipe as tipe,
                           d.uploaded_at as uploaded_at
                    ORDER BY d.uploaded_at DESC LIMIT 5
                """).data()

                return {
                    "connected":    True,
                    "labels":       labels,
                    "doc_relations": doc_rel["count"] if doc_rel else 0,
                    "total_relations": total_rel["count"] if total_rel else 0,
                    "recent_docs":  recent
                }

    except Exception as e:
        return {"connected": False, "error": str(e)}
