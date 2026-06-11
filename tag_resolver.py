"""
tag_resolver.py
Resolve tag number variants to canonical SAP tags from master_data_equipment.
"""
import re
import difflib
from db_equipment import get_conn, TABLE_CATALOG


SAP_TABLES = {"sap_notifications", "sap_work_orders"}


def normalize_tag(tag: str) -> str:
    """Normalize a tag number: uppercase, strip whitespace, remove separators."""
    if not tag:
        return ""
    tag = tag.upper().strip()
    tag = re.sub(r"[-/_. ]", "", tag)
    return tag


def _build_canonical_index(conn, batch_size: int = 1000):
    """Build canonical tag lookup structures from master_data_equipment."""
    norm_to_canonical = {}
    canonical_set = set()
    # prefix index: first 4 chars of normalized tag -> list of (norm, canon)
    prefix_index = {}

    offset = 0
    while True:
        rows = conn.execute(
            "SELECT equipment FROM master_data_equipment "
            "WHERE equipment IS NOT NULL "
            "ORDER BY equipment "
            "LIMIT %s OFFSET %s",
            (batch_size, offset)
        ).fetchall()
        if not rows:
            break
        for row in rows:
            canon = row["equipment"]
            canonical_set.add(canon)
            norm = normalize_tag(canon)
            if norm not in norm_to_canonical:
                norm_to_canonical[norm] = canon
                prefix = norm[:4]
                if prefix not in prefix_index:
                    prefix_index[prefix] = []
                prefix_index[prefix].append((norm, canon))
        offset += batch_size

    return canonical_set, norm_to_canonical, prefix_index


def _upsert_mapping(conn, tag_canonical, variant, table_name, confidence, method, status):
    try:
        conn.execute("""
            INSERT INTO tag_mapping
                (tag_canonical, tag_variant, source_table,
                 confidence, match_method, status)
            VALUES (%s, %s, %s, %s, %s, %s)
            ON CONFLICT (tag_variant, source_table) DO UPDATE
                SET confidence   = GREATEST(tag_mapping.confidence, EXCLUDED.confidence),
                    match_method = CASE
                        WHEN EXCLUDED.confidence > tag_mapping.confidence
                        THEN EXCLUDED.match_method
                        ELSE tag_mapping.match_method
                    END,
                    tag_canonical = CASE
                        WHEN EXCLUDED.confidence > tag_mapping.confidence
                        THEN EXCLUDED.tag_canonical
                        ELSE tag_mapping.tag_canonical
                    END
        """, (tag_canonical, variant, table_name, confidence, method, status))
    except Exception:
        pass


def _process_variant(variant, table_name, canonical_set, norm_to_canonical, prefix_index):
    """Resolve a single tag variant. Returns (canonical, confidence, method, status) or None."""
    if not variant:
        return None

    # Exact match
    if variant in canonical_set:
        return variant, 1.0, "exact", "approved"

    if table_name in SAP_TABLES:
        return None

    # Token/normalize match
    norm_variant = normalize_tag(variant)
    if norm_variant and norm_variant in norm_to_canonical:
        return norm_to_canonical[norm_variant], 0.95, "fuzzy_token", "approved"

    # Levenshtein - only compare against same-prefix candidates (much faster)
    if norm_variant:
        prefix = norm_variant[:4]
        # Try same prefix first, then adjacent prefixes for better recall
        candidates = prefix_index.get(prefix, [])
        if len(candidates) < 50:
            # Also check nearby prefixes if few candidates
            for p in [norm_variant[:3], norm_variant[:5]]:
                for k, v in prefix_index.items():
                    if k.startswith(p) or p.startswith(k[:3]):
                        candidates = candidates + v
                        if len(candidates) > 200:
                            break
                if len(candidates) > 200:
                    break

        best_ratio = 0.0
        best_canon = None
        for norm_canon, canon in candidates:
            ratio = difflib.SequenceMatcher(None, norm_variant, norm_canon).ratio()
            if ratio > best_ratio:
                best_ratio = ratio
                best_canon = canon

        if best_ratio >= 0.85 and best_canon is not None:
            return best_canon, round(best_ratio, 3), "fuzzy_levenshtein", "pending"

    return None


def generate_candidates(source_table: str = None, batch_size: int = 1000) -> dict:
    """
    Generate tag_mapping candidates for all tables or a specific table.
    Returns summary: {table: {total, exact, fuzzy_token, fuzzy_levenshtein, skipped}}
    """
    summary = {}
    skip_tables = {"master_data_equipment", "doc_registry"}

    with get_conn() as conn:
        canonical_set, norm_to_canonical, prefix_index = _build_canonical_index(conn, batch_size)

        tables_to_process = []
        for entry in TABLE_CATALOG:
            tname = entry["table"]
            tag_col = entry.get("tag_col")
            if tname in skip_tables or not tag_col:
                continue
            if source_table and tname != source_table:
                continue
            tables_to_process.append((tname, tag_col))

        for table_name, tag_col in tables_to_process:
            counts = {"total": 0, "exact": 0, "fuzzy_token": 0,
                      "fuzzy_levenshtein": 0, "skipped": 0}

            try:
                variants = conn.execute(
                    f"SELECT DISTINCT {tag_col} FROM {table_name} "
                    f"WHERE {tag_col} IS NOT NULL"
                ).fetchall()
            except Exception as e:
                summary[table_name] = {"error": str(e)}
                continue

            for row in variants:
                variant = row[tag_col]
                counts["total"] += 1

                result = _process_variant(
                    variant, table_name, canonical_set, norm_to_canonical, prefix_index
                )
                if result is None:
                    counts["skipped"] += 1
                    continue

                tag_canonical, confidence, method, status = result
                _upsert_mapping(conn, tag_canonical, variant, table_name, confidence, method, status)
                counts[method] += 1

            try:
                conn.commit()
            except Exception:
                pass

            summary[table_name] = counts

    return summary


def get_ru_list() -> list:
    """Get distinct RU/maintenance_plant values from master_data_equipment."""
    with get_conn() as conn:
        # Try maintenance_plant first
        try:
            rows = conn.execute("""
                SELECT DISTINCT maintenance_plant
                FROM master_data_equipment
                WHERE maintenance_plant IS NOT NULL
                  AND maintenance_plant <> ''
                ORDER BY maintenance_plant
            """).fetchall()
            result = [r["maintenance_plant"] for r in rows]
            if result:
                return result
        except Exception:
            pass

        # Fallback: refinery_unit
        try:
            rows = conn.execute("""
                SELECT DISTINCT refinery_unit
                FROM master_data_equipment
                WHERE refinery_unit IS NOT NULL
                  AND refinery_unit <> ''
                ORDER BY refinery_unit
            """).fetchall()
            return [r["refinery_unit"] for r in rows]
        except Exception:
            return []


def get_kertas_kerja(
    ru: str = None,
    status: str = "pending",
    page: int = 1,
    limit: int = 50
) -> tuple:
    """
    Return (rows, total) of tag_mapping joined with master_data_equipment.
    Filter by status and optionally by RU (maintenance_plant or refinery_unit).
    """
    offset = (page - 1) * limit
    params = []
    where_clauses = ["tm.status = %s"]
    params.append(status)

    ru_join = ""
    if ru:
        # We'll join to master and filter by maintenance_plant or refinery_unit
        ru_join = """
            JOIN master_data_equipment mde
                ON mde.equipment = tm.tag_canonical
        """
        where_clauses.append(
            "(mde.maintenance_plant = %s OR mde.refinery_unit = %s)"
        )
        params.extend([ru, ru])
    else:
        ru_join = """
            LEFT JOIN master_data_equipment mde
                ON mde.equipment = tm.tag_canonical
        """

    where_sql = " AND ".join(where_clauses)

    query = f"""
        SELECT
            tm.id,
            tm.tag_canonical,
            tm.tag_variant,
            tm.source_table,
            tm.confidence,
            tm.match_method,
            tm.status,
            tm.validated_by,
            tm.validated_at,
            tm.created_at,
            mde.equipment_description,
            mde.maintenance_plant,
            mde.refinery_unit
        FROM tag_mapping tm
        {ru_join}
        WHERE {where_sql}
        ORDER BY tm.confidence DESC, tm.id
        LIMIT %s OFFSET %s
    """
    count_query = f"""
        SELECT COUNT(*) AS cnt
        FROM tag_mapping tm
        {ru_join}
        WHERE {where_sql}
    """

    with get_conn() as conn:
        total_row = conn.execute(count_query, params).fetchone()
        total = total_row["cnt"] if total_row else 0
        rows = conn.execute(query, params + [limit, offset]).fetchall()
        return [dict(r) for r in rows], total


def approve_mapping(mapping_id: int, validated_by: str) -> bool:
    """Approve a single tag mapping."""
    with get_conn() as conn:
        conn.execute("""
            UPDATE tag_mapping
            SET status       = 'approved',
                validated_by = %s,
                validated_at = NOW()
            WHERE id = %s
        """, (validated_by, mapping_id))
        conn.commit()
    return True


def reject_mapping(mapping_id: int, validated_by: str) -> bool:
    """Reject a single tag mapping."""
    with get_conn() as conn:
        conn.execute("""
            UPDATE tag_mapping
            SET status       = 'rejected',
                validated_by = %s,
                validated_at = NOW()
            WHERE id = %s
        """, (validated_by, mapping_id))
        conn.commit()
    return True


def bulk_approve(ids: list, validated_by: str) -> int:
    """Bulk approve a list of mapping IDs. Returns count approved."""
    if not ids:
        return 0
    with get_conn() as conn:
        result = conn.execute("""
            UPDATE tag_mapping
            SET status       = 'approved',
                validated_by = %s,
                validated_at = NOW()
            WHERE id = ANY(%s)
              AND status = 'pending'
        """, (validated_by, ids))
        conn.commit()
        return result.rowcount


def get_mapping_stats() -> dict:
    """Return counts by status and by source_table."""
    with get_conn() as conn:
        by_status = conn.execute("""
            SELECT status, COUNT(*) AS cnt
            FROM tag_mapping
            GROUP BY status
            ORDER BY status
        """).fetchall()

        by_table = conn.execute("""
            SELECT source_table, status, COUNT(*) AS cnt
            FROM tag_mapping
            GROUP BY source_table, status
            ORDER BY source_table, status
        """).fetchall()

        return {
            "by_status": {r["status"]: r["cnt"] for r in by_status},
            "by_table": [dict(r) for r in by_table],
        }
