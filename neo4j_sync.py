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

TABLE_NEO4J_CONFIG = {
    "sap_notifications":      {"label": "SAPNotification",    "rel": "HAS_NOTIFICATION"},
    "sap_work_orders":        {"label": "SAPWorkOrder",       "rel": "HAS_WORK_ORDER"},
    "bad_actor_monitoring":   {"label": "BadActor",           "rel": "HAS_BAD_ACTOR"},
    "icu_monitoring":         {"label": "ICUMonitoring",      "rel": "HAS_ICU"},
    "atg_monitoring":         {"label": "ATGMonitoring",      "rel": "HAS_ATG"},
    "metering_monitoring":    {"label": "MeteringMonitor",    "rel": "HAS_METERING"},
    "boc":                    {"label": "BOC",                "rel": "HAS_BOC"},
    "pipeline_inspection":    {"label": "PipelineInspection", "rel": "HAS_PIPELINE_INSPECTION"},
    "zero_clamp":             {"label": "ZeroClamp",          "rel": "HAS_ZERO_CLAMP"},
    "power_stream":           {"label": "PowerStream",        "rel": "HAS_POWER_STREAM"},
    "critical_eqp_prim_sec":  {"label": "CriticalEquipment",  "rel": "IS_CRITICAL"},
    "inspection_plan":        {"label": "InspectionPlan",     "rel": "HAS_INSPECTION_PLAN"},
    "readiness_jetty":        {"label": "ReadinessJetty",     "rel": "HAS_READINESS"},
    "readiness_tank":         {"label": "ReadinessTank",      "rel": "HAS_READINESS"},
    "readiness_spm":          {"label": "ReadinessSPM",       "rel": "HAS_READINESS"},
    "workplan_jetty":         {"label": "WorkplanJetty",      "rel": "HAS_WORKPLAN"},
    "workplan_tank":          {"label": "WorkplanTank",       "rel": "HAS_WORKPLAN"},
    "spm_workplan":           {"label": "WorkplanSPM",        "rel": "HAS_WORKPLAN"},
    "irkap_program":          {"label": "IRKAPProgram",       "rel": "HAS_IRKAP"},
    "irkap_actual":           {"label": "IRKAPActual",        "rel": "HAS_IRKAP_ACTUAL"},
}

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
                # Count nodes per label (tanpa APOC)
                labels = []
                for lbl in ["Equipment", "Document", "BOC", "ICUMonitoring",
                            "ATGMonitoring", "MeteringMonitor", "BadActor"]:
                    try:
                        r = session.run(
                            f"MATCH (n:{lbl}) RETURN count(n) as count"
                        ).single()
                        if r and r["count"] > 0:
                            labels.append({"label": lbl, "count": r["count"]})
                    except Exception:
                        pass
                labels.sort(key=lambda x: x["count"], reverse=True)

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


def sync_table(table_name: str, tag_col: str, neo4j_label: str, rel_type: str, batch_size: int = 500):
    """Sync a PostgreSQL table to Neo4j. Returns dict with nodes_created and rels_created."""
    from db_equipment import get_conn

    nodes_created = 0
    rels_created = 0

    driver = get_driver()
    if driver is None:
        return {"error": "Neo4j connection unavailable", "nodes_created": 0, "rels_created": 0}

    try:
        with get_conn() as conn:
            # Get total count
            count_row = conn.execute(f"SELECT COUNT(*) AS cnt FROM {table_name}").fetchone()
            total = count_row["cnt"] if count_row else 0

            offset = 0
            while offset < total:
                rows = conn.execute(
                    f"SELECT * FROM {table_name} LIMIT %s OFFSET %s", (batch_size, offset)
                ).fetchall()
                if not rows:
                    break

                batch = []
                for i, row in enumerate(rows):
                    row_dict = {}
                    for col, val in row.items():
                        # Convert non-serializable types
                        if hasattr(val, 'isoformat'):
                            val = val.isoformat()
                        elif val is not None and not isinstance(val, (str, int, float, bool)):
                            val = str(val)
                        row_dict[col] = val
                    row_dict['_row_index'] = offset + i
                    batch.append(row_dict)

                # Build cypher - label must be interpolated (comes from our config, not user input)
                cypher = f"""
UNWIND $batch AS row
MERGE (e:Equipment {{tag_number: row.{tag_col}}})
CREATE (n:{neo4j_label})
SET n = row
MERGE (e)-[r:{rel_type}]->(n)
ON CREATE SET r.created_at = datetime()
RETURN count(n) AS nc
"""
                with driver.session() as session:
                    result = session.run(cypher, batch=batch)
                    record = result.single()
                    if record:
                        nodes_created += record["nc"]
                        rels_created += record["nc"]

                offset += batch_size

    except Exception as e:
        return {"error": str(e), "nodes_created": nodes_created, "rels_created": rels_created}
    finally:
        driver.close()

    return {"nodes_created": nodes_created, "rels_created": rels_created}


def sync_all_tables(progress_callback=None):
    """Sync all tables defined in TABLE_NEO4J_CONFIG. Returns summary dict."""
    from db_equipment import TABLE_CATALOG

    # Build lookup from TABLE_CATALOG for tag_col
    tag_col_lookup = {entry["table"]: entry["tag_col"] for entry in TABLE_CATALOG}

    summary = {}
    for table_name, config in TABLE_NEO4J_CONFIG.items():
        tag_col = tag_col_lookup.get(table_name)
        if not tag_col:
            summary[table_name] = {"error": "tag_col not found in TABLE_CATALOG"}
            continue

        if progress_callback:
            progress_callback(table_name)

        result = sync_table(
            table_name=table_name,
            tag_col=tag_col,
            neo4j_label=config["label"],
            rel_type=config["rel"],
        )
        summary[table_name] = result

    return summary


def get_graph_for_tag(tag: str):
    """Query Neo4j for 1-hop neighborhood of an Equipment node. Returns vis.js format."""
    driver = get_driver()
    if driver is None:
        return {"nodes": [], "edges": [], "error": "Neo4j connection unavailable"}

    nodes = []
    edges = []
    seen_node_ids = set()

    try:
        with driver.session() as session:
            # Get the equipment node itself
            eq_result = session.run(
                "MATCH (e:Equipment {tag_number: $tag}) RETURN e LIMIT 1",
                tag=tag
            )
            eq_record = eq_result.single()
            if not eq_record:
                return {"nodes": [], "edges": [], "error": f"Equipment '{tag}' not found"}

            eq_node = eq_record["e"]
            eq_props = dict(eq_node.items())
            eq_id = f"E:{tag}"

            title_parts = [f"{k}: {v}" for k, v in list(eq_props.items())[:5] if v is not None]
            nodes.append({
                "id": eq_id,
                "label": tag,
                "group": "Equipment",
                "title": "\n".join(title_parts),
            })
            seen_node_ids.add(eq_id)

            # Get all 1-hop connected nodes (limit 50 per relationship type)
            rel_result = session.run(
                """
                MATCH (e:Equipment {tag_number: $tag})-[r]->(n)
                RETURN type(r) AS rel_type, labels(n) AS node_labels, id(n) AS node_id, properties(n) AS props
                LIMIT 200
                """,
                tag=tag
            )

            for record in rel_result:
                rel_type = record["rel_type"]
                node_labels = record["node_labels"]
                node_id = record["node_id"]
                props = dict(record["props"])

                vis_node_id = f"N:{node_id}"
                group = node_labels[0] if node_labels else "Unknown"

                # Build label from props
                label_val = (
                    props.get("tag_number") or
                    props.get("tag_no") or
                    props.get("equipment") or
                    props.get("description") or
                    group
                )
                if label_val and len(str(label_val)) > 20:
                    label_val = str(label_val)[:17] + "..."

                title_parts = [f"{k}: {v}" for k, v in list(props.items())[:6] if v is not None]

                if vis_node_id not in seen_node_ids:
                    nodes.append({
                        "id": vis_node_id,
                        "label": str(label_val),
                        "group": group,
                        "title": "\n".join(title_parts),
                    })
                    seen_node_ids.add(vis_node_id)

                edges.append({
                    "from": eq_id,
                    "to": vis_node_id,
                    "label": rel_type,
                })

            # Also check incoming relationships
            in_result = session.run(
                """
                MATCH (n)-[r]->(e:Equipment {tag_number: $tag})
                RETURN type(r) AS rel_type, labels(n) AS node_labels, id(n) AS node_id, properties(n) AS props
                LIMIT 50
                """,
                tag=tag
            )
            for record in in_result:
                rel_type = record["rel_type"]
                node_labels = record["node_labels"]
                node_id = record["node_id"]
                props = dict(record["props"])

                vis_node_id = f"N:{node_id}"
                group = node_labels[0] if node_labels else "Unknown"

                label_val = (
                    props.get("tag_number") or
                    props.get("tag_no") or
                    props.get("equipment") or
                    props.get("description") or
                    group
                )
                if label_val and len(str(label_val)) > 20:
                    label_val = str(label_val)[:17] + "..."

                title_parts = [f"{k}: {v}" for k, v in list(props.items())[:6] if v is not None]

                if vis_node_id not in seen_node_ids:
                    nodes.append({
                        "id": vis_node_id,
                        "label": str(label_val),
                        "group": group,
                        "title": "\n".join(title_parts),
                    })
                    seen_node_ids.add(vis_node_id)

                edges.append({
                    "from": vis_node_id,
                    "to": eq_id,
                    "label": rel_type,
                })

    except Exception as e:
        return {"nodes": nodes, "edges": edges, "error": str(e)}

    return {"nodes": nodes, "edges": edges}
