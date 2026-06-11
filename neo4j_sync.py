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


def _load_tag_mapping(conn, table_name: str) -> dict:
    """
    Load approved tag_mapping entries for a given source_table.
    Returns dict {tag_variant: tag_canonical}.
    Falls back to empty dict if tag_mapping table doesn't exist yet.
    """
    try:
        rows = conn.execute(
            """
            SELECT tag_variant, tag_canonical
            FROM tag_mapping
            WHERE source_table = %s AND status = 'approved'
            """,
            (table_name,),
        ).fetchall()
        return {r["tag_variant"]: r["tag_canonical"] for r in rows}
    except Exception:
        return {}


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
            # Load tag mapping for this table (variant -> canonical)
            tag_mapping = _load_tag_mapping(conn, table_name)

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

                    # Resolve tag via mapping; fall back to direct value for backward compatibility
                    raw_tag = row_dict.get(tag_col)
                    if raw_tag is not None:
                        row_dict[tag_col] = tag_mapping.get(raw_tag, raw_tag)

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


def sync_domain_relations() -> dict:
    """
    Create cross-table relationships in Neo4j:
    1. SAPNotification -[:GENERATED_WO]-> SAPWorkOrder
    2. BadActor -[:HAS_IRKAP]-> IRKAPProgram
    3. IRKAPProgram -[:HAS_ACTUAL]-> IRKAPActual
    Returns summary dict with counts of relations created per type.
    """
    from db_equipment import get_conn

    summary = {
        "GENERATED_WO": 0,
        "HAS_IRKAP": 0,
        "HAS_ACTUAL": 0,
    }

    driver = get_driver()
    if driver is None:
        return {"error": "Neo4j connection unavailable", **summary}

    try:
        with get_conn() as conn:
            # 1. SAPNotification -[:GENERATED_WO]-> SAPWorkOrder
            rows = conn.execute(
                "SELECT DISTINCT order_no FROM sap_notifications WHERE order_no IS NOT NULL AND order_no != ''"
            ).fetchall()
            order_nos = [r["order_no"] for r in rows]

            chunk_size = 500
            created_gw = 0
            for i in range(0, len(order_nos), chunk_size):
                chunk = order_nos[i:i + chunk_size]
                with driver.session() as session:
                    result = session.run(
                        """
                        UNWIND $order_nos AS order_no
                        MATCH (n:SAPNotification) WHERE n.order_no = order_no
                        MATCH (w:SAPWorkOrder) WHERE w.order_no = order_no
                        MERGE (n)-[r:GENERATED_WO]->(w)
                        RETURN count(r) AS cnt
                        """,
                        order_nos=chunk
                    )
                    rec = result.single()
                    if rec:
                        created_gw += rec["cnt"]
            summary["GENERATED_WO"] = created_gw

            # 2. BadActor -[:HAS_IRKAP]-> IRKAPProgram
            rows = conn.execute(
                """
                SELECT DISTINCT ba.no_irkap, ip.no_program_kerja
                FROM bad_actor_monitoring ba
                JOIN irkap_program ip ON ba.no_irkap = ip.no_program_kerja
                WHERE ba.no_irkap IS NOT NULL AND ba.no_irkap != ''
                """
            ).fetchall()
            pairs = [{"no_irkap": r["no_irkap"], "no_program_kerja": r["no_program_kerja"]} for r in rows]

            created_hi = 0
            for i in range(0, len(pairs), chunk_size):
                chunk = pairs[i:i + chunk_size]
                with driver.session() as session:
                    result = session.run(
                        """
                        UNWIND $pairs AS pair
                        MATCH (ba:BadActor) WHERE ba.no_irkap = pair.no_irkap
                        MATCH (ip:IRKAPProgram) WHERE ip.no_program_kerja = pair.no_program_kerja
                        MERGE (ba)-[r:HAS_IRKAP]->(ip)
                        RETURN count(r) AS cnt
                        """,
                        pairs=chunk
                    )
                    rec = result.single()
                    if rec:
                        created_hi += rec["cnt"]
            summary["HAS_IRKAP"] = created_hi

            # 3. IRKAPProgram -[:HAS_ACTUAL]-> IRKAPActual
            rows = conn.execute(
                "SELECT DISTINCT no_program FROM irkap_actual WHERE no_program IS NOT NULL"
            ).fetchall()
            no_programs = [r["no_program"] for r in rows]

            created_ha = 0
            for i in range(0, len(no_programs), chunk_size):
                chunk = no_programs[i:i + chunk_size]
                with driver.session() as session:
                    result = session.run(
                        """
                        UNWIND $no_programs AS no_program
                        MATCH (ip:IRKAPProgram) WHERE ip.no_program_kerja = no_program
                        MATCH (ia:IRKAPActual) WHERE ia.no_program = no_program
                        MERGE (ip)-[r:HAS_ACTUAL]->(ia)
                        RETURN count(r) AS cnt
                        """,
                        no_programs=chunk
                    )
                    rec = result.single()
                    if rec:
                        created_ha += rec["cnt"]
            summary["HAS_ACTUAL"] = created_ha

    except Exception as e:
        summary["error"] = str(e)
    finally:
        driver.close()

    return summary


def get_graph_for_tag(tag: str, depth: int = 1):
    """Query Neo4j for 1-hop (depth=1) or 2-hop (depth=2) neighborhood of an Equipment node. Returns vis.js format."""
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

            # depth=2: include 2nd-hop nodes via GENERATED_WO, HAS_IRKAP, HAS_ACTUAL
            if depth >= 2:
                # SAPNotification -[:GENERATED_WO]-> SAPWorkOrder (2nd hop)
                hop2_gw = session.run(
                    """
                    MATCH (e:Equipment {tag_number: $tag})<-[:HAS_NOTIFICATION]-(n:SAPNotification)-[:GENERATED_WO]->(wo2:SAPWorkOrder)
                    RETURN id(n) AS src_id, id(wo2) AS node_id, labels(wo2) AS node_labels, properties(wo2) AS props
                    LIMIT 100
                    """,
                    tag=tag
                )
                for record in hop2_gw:
                    node_id = record["node_id"]
                    node_labels = record["node_labels"]
                    props = dict(record["props"])
                    src_vis_id = f"N:{record['src_id']}"
                    vis_node_id = f"N:{node_id}"
                    group = node_labels[0] if node_labels else "Unknown"
                    label_val = (props.get("order_no") or props.get("description") or group)
                    if label_val and len(str(label_val)) > 20:
                        label_val = str(label_val)[:17] + "..."
                    title_parts = [f"{k}: {v}" for k, v in list(props.items())[:6] if v is not None]
                    if vis_node_id not in seen_node_ids:
                        nodes.append({"id": vis_node_id, "label": str(label_val), "group": group, "title": "\n".join(title_parts)})
                        seen_node_ids.add(vis_node_id)
                    edges.append({"from": src_vis_id, "to": vis_node_id, "label": "GENERATED_WO"})

                # BadActor -[:HAS_IRKAP]-> IRKAPProgram (2nd hop)
                hop2_irkap = session.run(
                    """
                    MATCH (e:Equipment {tag_number: $tag})<-[:HAS_BAD_ACTOR]-(ba:BadActor)-[:HAS_IRKAP]->(irkap_prog:IRKAPProgram)
                    RETURN id(ba) AS src_id, id(irkap_prog) AS node_id, labels(irkap_prog) AS node_labels, properties(irkap_prog) AS props
                    LIMIT 50
                    """,
                    tag=tag
                )
                for record in hop2_irkap:
                    node_id = record["node_id"]
                    node_labels = record["node_labels"]
                    props = dict(record["props"])
                    src_vis_id = f"N:{record['src_id']}"
                    vis_node_id = f"N:{node_id}"
                    group = node_labels[0] if node_labels else "Unknown"
                    label_val = (props.get("no_program_kerja") or props.get("program_kerja") or group)
                    if label_val and len(str(label_val)) > 20:
                        label_val = str(label_val)[:17] + "..."
                    title_parts = [f"{k}: {v}" for k, v in list(props.items())[:6] if v is not None]
                    if vis_node_id not in seen_node_ids:
                        nodes.append({"id": vis_node_id, "label": str(label_val), "group": group, "title": "\n".join(title_parts)})
                        seen_node_ids.add(vis_node_id)
                    edges.append({"from": src_vis_id, "to": vis_node_id, "label": "HAS_IRKAP"})

                # IRKAPProgram -[:HAS_ACTUAL]-> IRKAPActual (3rd hop from equipment via BadActor or HAS_IRKAP)
                hop2_actual = session.run(
                    """
                    MATCH (e:Equipment {tag_number: $tag})<-[:HAS_BAD_ACTOR]-(ba:BadActor)-[:HAS_IRKAP]->(ip:IRKAPProgram)-[:HAS_ACTUAL]->(irkap_act:IRKAPActual)
                    RETURN id(ip) AS src_id, id(irkap_act) AS node_id, labels(irkap_act) AS node_labels, properties(irkap_act) AS props
                    LIMIT 50
                    """,
                    tag=tag
                )
                for record in hop2_actual:
                    node_id = record["node_id"]
                    node_labels = record["node_labels"]
                    props = dict(record["props"])
                    src_vis_id = f"N:{record['src_id']}"
                    vis_node_id = f"N:{node_id}"
                    group = node_labels[0] if node_labels else "Unknown"
                    label_val = (props.get("no_program") or props.get("program_kerja") or group)
                    if label_val and len(str(label_val)) > 20:
                        label_val = str(label_val)[:17] + "..."
                    title_parts = [f"{k}: {v}" for k, v in list(props.items())[:6] if v is not None]
                    if vis_node_id not in seen_node_ids:
                        nodes.append({"id": vis_node_id, "label": str(label_val), "group": group, "title": "\n".join(title_parts)})
                        seen_node_ids.add(vis_node_id)
                    edges.append({"from": src_vis_id, "to": vis_node_id, "label": "HAS_ACTUAL"})

    except Exception as e:
        return {"nodes": nodes, "edges": edges, "error": str(e)}

    return {"nodes": nodes, "edges": edges}


def get_coverage_stats() -> dict:
    """
    Compare PostgreSQL record counts vs Neo4j connected nodes per table.
    Returns coverage % per table/domain.
    """
    from db_equipment import get_conn as pg_conn, TABLE_CATALOG

    # Get Neo4j counts per label
    neo4j_counts = {}
    try:
        with get_driver() as driver:
            with driver.session() as session:
                for lbl in ["Equipment", "SAPNotification", "SAPWorkOrder",
                            "BadActor", "ICUMonitoring", "ATGMonitoring",
                            "MeteringMonitor", "BOC", "PipelineInspection",
                            "ZeroClamp", "PowerStream", "CriticalEquipment",
                            "InspectionPlan", "ReadinessJetty", "ReadinessTank",
                            "ReadinessSPM", "WorkplanJetty", "WorkplanTank",
                            "WorkplanSPM", "IRKAPProgram", "IRKAPActual", "Document"]:
                    try:
                        r = session.run(f"MATCH (n:{lbl}) RETURN count(n) as cnt").single()
                        neo4j_counts[lbl] = r["cnt"] if r else 0
                    except:
                        neo4j_counts[lbl] = 0
    except:
        pass

    # Map table name to neo4j label
    TABLE_TO_LABEL = {
        "master_data_equipment": "Equipment",
        "sap_notifications": "SAPNotification",
        "sap_work_orders": "SAPWorkOrder",
        "bad_actor_monitoring": "BadActor",
        "icu_monitoring": "ICUMonitoring",
        "atg_monitoring": "ATGMonitoring",
        "metering_monitoring": "MeteringMonitor",
        "boc": "BOC",
        "pipeline_inspection": "PipelineInspection",
        "zero_clamp": "ZeroClamp",
        "power_stream": "PowerStream",
        "critical_eqp_prim_sec": "CriticalEquipment",
        "inspection_plan": "InspectionPlan",
        "readiness_jetty": "ReadinessJetty",
        "readiness_tank": "ReadinessTank",
        "readiness_spm": "ReadinessSPM",
        "workplan_jetty": "WorkplanJetty",
        "workplan_tank": "WorkplanTank",
        "spm_workplan": "WorkplanSPM",
        "irkap_program": "IRKAPProgram",
        "irkap_actual": "IRKAPActual",
        "doc_registry": "Document",
    }

    results = []
    with pg_conn() as conn:
        for tbl in TABLE_CATALOG:
            table_name = tbl["table"]
            label = TABLE_TO_LABEL.get(table_name, "")
            try:
                total_pg = conn.execute(f"SELECT COUNT(*) as n FROM {table_name}").fetchone()["n"]
            except:
                total_pg = 0

            connected = neo4j_counts.get(label, 0)
            coverage_pct = round(connected * 100 / total_pg, 1) if total_pg > 0 else 0

            results.append({
                "table": table_name,
                "label": tbl["label"],
                "domain": tbl["domain"],
                "neo4j_label": label,
                "total_pg": total_pg,
                "connected": connected,
                "not_connected": max(0, total_pg - connected),
                "coverage_pct": coverage_pct,
            })

    # Domain summary
    domains = {}
    for r in results:
        d = r["domain"]
        if d not in domains:
            domains[d] = {"domain": d, "total_pg": 0, "connected": 0}
        domains[d]["total_pg"] += r["total_pg"]
        domains[d]["connected"] += r["connected"]

    for d in domains.values():
        d["coverage_pct"] = round(d["connected"] * 100 / d["total_pg"], 1) if d["total_pg"] > 0 else 0

    total_pg = sum(r["total_pg"] for r in results)
    total_connected = sum(r["connected"] for r in results)

    return {
        "tables": results,
        "domains": list(domains.values()),
        "total_pg": total_pg,
        "total_connected": total_connected,
        "overall_pct": round(total_connected * 100 / total_pg, 1) if total_pg > 0 else 0,
        "neo4j_counts": neo4j_counts,
    }

# ─── Graph Search ────────────────────────────────────────────────────────────

def search_graph(query: str, ru: str = None, limit: int = 10) -> list:
    """
    Search Neo4j for equipment and related nodes matching the query.
    Returns list of results with equipment info + connected node counts.
    """
    try:
        with get_driver() as driver:
            with driver.session() as session:
                # Search equipment by tag_number or description (case-insensitive)
                ru_filter = "AND e.maintenance_plant = $ru" if ru else ""
                result = session.run(f"""
                    MATCH (e:Equipment)
                    WHERE (toLower(e.tag_number) CONTAINS toLower($query)
                           OR toLower(e.description) CONTAINS toLower($query))
                    {ru_filter}
                    WITH e
                    OPTIONAL MATCH (e)<-[r1:HAS_BAD_ACTOR]-()
                    OPTIONAL MATCH (e)<-[r2:HAS_ICU]-()
                    OPTIONAL MATCH (e)<-[r3:HAS_NOTIFICATION]-()
                    OPTIONAL MATCH (e)<-[r4:HAS_WORK_ORDER]-()
                    OPTIONAL MATCH (e)<-[r5:HAS_BOC]-()
                    OPTIONAL MATCH (doc:Document)-[:TERKAIT_DENGAN]->(e)
                    RETURN e.tag_number as tag_number,
                           e.description as description,
                           e.maintenance_plant as maintenance_plant,
                           e.criticality as criticality,
                           count(DISTINCT r1) as bad_actor_count,
                           count(DISTINCT r2) as icu_count,
                           count(DISTINCT r3) as notif_count,
                           count(DISTINCT r4) as wo_count,
                           count(DISTINCT r5) as boc_count,
                           count(DISTINCT doc) as doc_count
                    ORDER BY e.tag_number
                    LIMIT $limit
                """, {"query": query, "ru": ru, "limit": limit})
                return [dict(r) for r in result]
    except Exception as e:
        return []


# ─── Reset All Relations ──────────────────────────────────────────────────────

def reset_all_relations() -> dict:
    """Hapus semua relasi di Neo4j kecuali yang antar Equipment node."""
    try:
        with get_driver() as driver:
            with driver.session() as session:
                result = session.run("""
                    MATCH ()-[r]->()
                    DELETE r
                    RETURN count(r) as deleted
                """)
                row = result.single()
                deleted = row["deleted"] if row else 0
                return {"success": True, "deleted": deleted}
    except Exception as e:
        return {"success": False, "error": str(e)}
