"""
db_tag_mapping.py
Database CRUD functions for the tag_mapping table.
"""
import os
import psycopg
from psycopg.rows import dict_row
from dotenv import load_dotenv

load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL")


def get_conn():
    return psycopg.connect(DATABASE_URL, row_factory=dict_row)


def get_pending_mappings(
    ru: str = None,
    source_table: str = None,
    status: str = "pending",
    page: int = 1,
    limit: int = 50,
) -> tuple:
    try:
        offset = (page - 1) * limit
        params: list = []
        where_clauses: list = []

        if status:
            where_clauses.append("tm.status = %s")
            params.append(status)

        if status == 'pending':
            where_clauses.append("tm.match_method != 'exact'")

        if source_table:
            where_clauses.append("tm.source_table = %s")
            params.append(source_table)

        ru_join = ""
        if ru:
            ru_join = "JOIN master_data_equipment mde ON mde.equipment = tm.tag_canonical"
            where_clauses.append("mde.maintenance_plant = %s")
            params.append(ru)
        else:
            ru_join = "LEFT JOIN master_data_equipment mde ON mde.equipment = tm.tag_canonical"

        where_sql = ("WHERE " + " AND ".join(where_clauses)) if where_clauses else ""

        count_sql = f"""
            SELECT COUNT(*) AS cnt
            FROM tag_mapping tm
            {ru_join}
            {where_sql}
        """
        data_sql = f"""
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
                mde.description,
                mde.maintenance_plant
            FROM tag_mapping tm
            {ru_join}
            {where_sql}
            ORDER BY tm.confidence DESC, tm.id
            LIMIT %s OFFSET %s
        """

        with get_conn() as conn:
            total_row = conn.execute(count_sql, params).fetchone()
            total = total_row["cnt"] if total_row else 0
            rows = conn.execute(data_sql, params + [limit, offset]).fetchall()
            return [dict(r) for r in rows], total
    except Exception:
        return [], 0


def get_grouped_mappings(ru=None, source_table=None, status='pending', page=1, limit=20):
    """Return mappings grouped by tag_canonical for kertas kerja display."""
    try:
        offset = (page - 1) * limit
        params = []
        where_clauses = ["tm.match_method != 'exact'"]  # never show exact matches

        if status:
            where_clauses.append("tm.status = %s")
            params.append(status)
        if source_table:
            where_clauses.append("tm.source_table = %s")
            params.append(source_table)

        ru_join = ""
        if ru:
            ru_join = "JOIN master_data_equipment mde ON mde.equipment = tm.tag_canonical"
            where_clauses.append("mde.maintenance_plant = %s")
            params.append(ru)
        else:
            ru_join = "LEFT JOIN master_data_equipment mde ON mde.equipment = tm.tag_canonical"

        where_sql = "WHERE " + " AND ".join(where_clauses)

        # Count distinct tag_canonical groups
        count_sql = f"""
            SELECT COUNT(DISTINCT tm.tag_canonical) AS cnt
            FROM tag_mapping tm {ru_join} {where_sql}
        """

        # Get paginated tag_canonical list
        canonical_sql = f"""
            SELECT DISTINCT tm.tag_canonical,
                   MAX(mde.description) as description,
                   MAX(mde.maintenance_plant) as maintenance_plant
            FROM tag_mapping tm {ru_join} {where_sql}
            GROUP BY tm.tag_canonical
            ORDER BY tm.tag_canonical
            LIMIT %s OFFSET %s
        """

        with get_conn() as conn:
            total_groups = conn.execute(count_sql, params).fetchone()["cnt"]
            canonicals = conn.execute(canonical_sql, params + [limit, offset]).fetchall()

            groups = []
            for c in canonicals:
                canonical = c["tag_canonical"]
                # Get all variants for this canonical
                variant_params = [canonical]
                variant_where = ["tm.tag_canonical = %s", "tm.match_method != 'exact'"]
                if status:
                    variant_where.append("tm.status = %s")
                    variant_params.append(status)
                if source_table:
                    variant_where.append("tm.source_table = %s")
                    variant_params.append(source_table)

                variants = conn.execute(f"""
                    SELECT tm.id, tm.tag_variant, tm.source_table,
                           tm.confidence, tm.match_method, tm.status,
                           tm.validated_by, tm.validated_at
                    FROM tag_mapping tm
                    WHERE {" AND ".join(variant_where)}
                    ORDER BY tm.confidence DESC
                """, variant_params).fetchall()

                groups.append({
                    "tag_canonical": canonical,
                    "description": c["description"] or "—",
                    "maintenance_plant": c["maintenance_plant"] or "—",
                    "variants": [dict(v) for v in variants],
                    "variant_count": len(variants),
                    "pending_count": sum(1 for v in variants if dict(v)["status"] == "pending")
                })

            return groups, total_groups
    except Exception:
        return [], 0


def approve_mapping(mapping_id: int, validated_by: str) -> bool:
    with get_conn() as conn:
        conn.execute(
            """
            UPDATE tag_mapping
            SET status       = 'approved',
                validated_by = %s,
                validated_at = NOW()
            WHERE id = %s
            """,
            (validated_by, mapping_id),
        )
        conn.commit()
    return True


def reject_mapping(mapping_id: int, validated_by: str) -> bool:
    with get_conn() as conn:
        conn.execute(
            """
            UPDATE tag_mapping
            SET status       = 'rejected',
                validated_by = %s,
                validated_at = NOW()
            WHERE id = %s
            """,
            (validated_by, mapping_id),
        )
        conn.commit()
    return True


def bulk_approve(ids: list, validated_by: str) -> int:
    if not ids:
        return 0
    with get_conn() as conn:
        result = conn.execute(
            """
            UPDATE tag_mapping
            SET status       = 'approved',
                validated_by = %s,
                validated_at = NOW()
            WHERE id = ANY(%s)
              AND status = 'pending'
            """,
            (validated_by, ids),
        )
        conn.commit()
        return result.rowcount


def get_mapping_stats() -> dict:
    try:
        with get_conn() as conn:
            by_status = conn.execute(
                """
                SELECT status, COUNT(*) AS cnt
                FROM tag_mapping
                GROUP BY status
                ORDER BY status
                """
            ).fetchall()

            by_table = conn.execute(
                """
                SELECT source_table, status, COUNT(*) AS cnt
                FROM tag_mapping
                GROUP BY source_table, status
                ORDER BY source_table, status
                """
            ).fetchall()

            return {
                "by_status": {r["status"]: r["cnt"] for r in by_status},
                "by_table": [dict(r) for r in by_table],
            }
    except Exception:
        return {"by_status": {}, "by_table": []}


def get_ru_list() -> list:
    try:
        with get_conn() as conn:
            rows = conn.execute(
                """
                SELECT DISTINCT maintenance_plant
                FROM master_data_equipment
                WHERE maintenance_plant IS NOT NULL
                  AND maintenance_plant <> ''
                ORDER BY maintenance_plant
                """
            ).fetchall()
            return [r["maintenance_plant"] for r in rows]
    except Exception:
        return []


def get_source_table_list() -> list:
    try:
        with get_conn() as conn:
            rows = conn.execute(
                """
                SELECT DISTINCT source_table
                FROM tag_mapping
                ORDER BY source_table
                """
            ).fetchall()
            return [r["source_table"] for r in rows]
    except Exception:
        return []
