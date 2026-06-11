"""
schema_cache.py
Dynamically build schema context for LLM prompts from Neo4j and PostgreSQL.
Cached in memory, refreshed every hour.
"""
import time
import logging

_cache = {
    "neo4j_schema": None,
    "pg_schema": None,
    "last_refresh": 0,
}
CACHE_TTL = 3600  # 1 hour


def _build_neo4j_schema() -> str:
    """Query Neo4j for all labels, relationship types, and sample properties."""
    try:
        from neo4j_sync import get_driver
        driver = get_driver()
        if not driver:
            return ""

        lines = ["Neo4j Knowledge Graph Schema:"]

        with driver.session() as session:
            # Get all relationship types with source/target labels
            rels = session.run("""
                MATCH (a)-[r]->(b)
                WITH labels(a)[0] AS src, type(r) AS rel, labels(b)[0] AS tgt
                RETURN DISTINCT src, rel, tgt
                ORDER BY src, rel
            """).data()

            if rels:
                lines.append("\nRelasi (arah: source -[REL]-> target):")
                for r in rels:
                    lines.append(f"  ({r['src']})-[:{r['rel']}]->({r['tgt']})")

            # Get properties per label (sample from first node)
            labels_result = session.run(
                "CALL db.labels() YIELD label RETURN label ORDER BY label"
            ).data()

            lines.append("\nProperty per node label:")
            for row in labels_result:
                lbl = row["label"]
                try:
                    sample = session.run(
                        f"MATCH (n:{lbl}) RETURN keys(n) AS props LIMIT 1"
                    ).single()
                    if sample and sample["props"]:
                        props = ", ".join(sorted(sample["props"])[:15])
                        lines.append(f"  {lbl}: {props}")
                except Exception:
                    pass

        driver.close()
        return "\n".join(lines)

    except Exception as e:
        logging.warning(f"[SCHEMA] Neo4j schema build failed: {e}")
        return ""


def _build_pg_schema() -> str:
    """Query PostgreSQL for all tables and their columns."""
    try:
        from db_equipment import get_conn, TABLE_CATALOG
        lines = ["PostgreSQL Tables (semua terhubung via tag number equipment):"]

        with get_conn() as conn:
            for entry in TABLE_CATALOG:
                table = entry["table"]
                tag_col = entry.get("tag_col", "")
                try:
                    cols = conn.execute("""
                        SELECT column_name
                        FROM information_schema.columns
                        WHERE table_name = %s
                        ORDER BY ordinal_position
                    """, (table,)).fetchall()
                    col_names = [c["column_name"] for c in cols]
                    lines.append(f"- {table}({', '.join(col_names)})  [tag_col: {tag_col}]")
                except Exception:
                    lines.append(f"- {table}  [tag_col: {tag_col}]")

        # Add join hint
        lines.append("""
PENTING - kolom tag berbeda per tabel, gunakan ini untuk JOIN:
- master_data_equipment: equipment
- bad_actor_monitoring: tag_number
- icu_monitoring: tag_no
- atg_monitoring: tag_no_tangki
- metering_monitoring: tag_number
- boc: equipment
- sap_notifications: equipment
- sap_work_orders: equipment
- pipeline_inspection: tag_number
- zero_clamp: tag_no_ln
- irkap_program: equipment_tag_no
- irkap_actual: tag_no
- inspection_plan: tag_no_ln
- readiness_jetty: tag_no
- readiness_tank: tag_number
- readiness_spm: tag_no
- workplan_jetty: tag_no
- workplan_tank: tag_no
- spm_workplan: tag_no""")

        return "\n".join(lines)

    except Exception as e:
        logging.warning(f"[SCHEMA] PG schema build failed: {e}")
        return ""


def get_neo4j_schema(force_refresh: bool = False) -> str:
    """Return cached Neo4j schema, refresh if stale."""
    now = time.time()
    if force_refresh or not _cache["neo4j_schema"] or (now - _cache["last_refresh"]) > CACHE_TTL:
        _cache["neo4j_schema"] = _build_neo4j_schema()
        _cache["last_refresh"] = now
    return _cache["neo4j_schema"] or ""


def get_pg_schema(force_refresh: bool = False) -> str:
    """Return cached PostgreSQL schema, refresh if stale."""
    now = time.time()
    if force_refresh or not _cache["pg_schema"] or (now - _cache["last_refresh"]) > CACHE_TTL:
        _cache["pg_schema"] = _build_pg_schema()
        _cache["last_refresh"] = now
    return _cache["pg_schema"] or ""


def refresh_all():
    """Force refresh all schema caches."""
    _cache["neo4j_schema"] = _build_neo4j_schema()
    _cache["pg_schema"] = _build_pg_schema()
    _cache["last_refresh"] = time.time()
    logging.info("[SCHEMA] Cache refreshed")
