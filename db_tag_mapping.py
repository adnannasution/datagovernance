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
    """
    Return (rows, total) from tag_mapping, optionally filtered by RU and source_table.
    RU filter joins master_data_equipment on tag_canonical = equipment.
    """
    offset = (page - 1) * limit
    params: list = []
    where_clauses: list = []

    if status:
        where_clauses.append("tm.status = %s")
        params.append(status)

    if source_table:
        where_clauses.append("tm.source_table = %s")
        params.append(source_table)

    ru_join = ""
    if ru:
        ru_join = "JOIN master_data_equipment mde ON mde.equipment = tm.tag_canonical"
        where_clauses.append(
            "(mde.maintenance_plant = %s OR mde.refinery_unit = %s)"
        )
        params.extend([ru, ru])
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
            mde.equipment_description,
            mde.maintenance_plant,
            mde.refinery_unit
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


def approve_mapping(mapping_id: int, validated_by: str) -> bool:
    """Set a mapping to approved."""
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
    """Set a mapping to rejected."""
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
    """Bulk approve a list of mapping IDs. Returns count actually updated."""
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
    """Return counts by status and per source_table."""
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


def get_ru_list() -> list:
    """Return distinct maintenance_plant values from master_data_equipment."""
    with get_conn() as conn:
        try:
            rows = conn.execute(
                """
                SELECT DISTINCT maintenance_plant
                FROM master_data_equipment
                WHERE maintenance_plant IS NOT NULL
                  AND maintenance_plant <> ''
                ORDER BY maintenance_plant
                """
            ).fetchall()
            result = [r["maintenance_plant"] for r in rows]
            if result:
                return result
        except Exception:
            pass

        try:
            rows = conn.execute(
                """
                SELECT DISTINCT refinery_unit
                FROM master_data_equipment
                WHERE refinery_unit IS NOT NULL
                  AND refinery_unit <> ''
                ORDER BY refinery_unit
                """
            ).fetchall()
            return [r["refinery_unit"] for r in rows]
        except Exception:
            return []


def get_source_table_list() -> list:
    """Return distinct source_table values present in tag_mapping."""
    with get_conn() as conn:
        try:
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
