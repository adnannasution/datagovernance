"""
db_equipment.py
Query functions untuk semua tabel equipment domain.
Diambil dari db.py project Equipment 360° — satu DB connection.
"""
import os
import psycopg
from psycopg.rows import dict_row
from dotenv import load_dotenv

load_dotenv()
DATABASE_URL = os.getenv("DATABASE_URL", "")

def get_conn():
    url = DATABASE_URL.replace("postgres://", "postgresql://", 1)
    return psycopg.connect(url, row_factory=dict_row)

# ─── CATALOG META ────────────────────────────────────────────────────────────
# Definisi semua tabel: nama, domain, kolom tag, deskripsi

TABLE_CATALOG = [
    # Master
    {"table": "master_data_equipment",   "domain": "Master",      "tag_col": "equipment",        "label": "Master Equipment"},
    # SAP
    {"table": "sap_notifications",       "domain": "SAP",         "tag_col": "equipment",        "label": "SAP Notifications"},
    {"table": "sap_work_orders",         "domain": "SAP",         "tag_col": "equipment",        "label": "SAP Work Orders"},
    {"table": "sap_bom",                 "domain": "SAP",         "tag_col": "equipment",        "label": "SAP BOM (Bill of Materials)"},
    # Monitoring
    {"table": "bad_actor_monitoring",    "domain": "Monitoring",  "tag_col": "equipment",        "label": "Bad Actor"},
    {"table": "icu_monitoring",          "domain": "Monitoring",  "tag_col": "equipment",        "label": "ICU Monitoring"},
    {"table": "atg_monitoring",          "domain": "Monitoring",  "tag_col": "equipment_tangki", "label": "ATG Monitoring"},
    {"table": "metering_monitoring",     "domain": "Monitoring",  "tag_col": "equipment",        "label": "Metering"},
    {"table": "boc",                     "domain": "Monitoring",  "tag_col": "equipment",        "label": "BOC"},
    {"table": "pipeline_inspection",     "domain": "Monitoring",  "tag_col": "equipment",        "label": "Pipeline Inspection"},
    {"table": "zero_clamp",             "domain": "Monitoring",  "tag_col": "equipment",        "label": "Zero Clamp"},
    {"table": "power_stream",            "domain": "Monitoring",  "tag_col": "equipment",        "label": "Power & Stream"},
    {"table": "critical_eqp_prim_sec",   "domain": "Monitoring",  "tag_col": "equipment",        "label": "Critical Equipment"},
    {"table": "rotor_monitoring",        "domain": "Monitoring",  "tag_col": None,               "label": "Rotor Monitoring"},
    {"table": "program_kerja_atg",       "domain": "Monitoring",  "tag_col": None,               "label": "Program Kerja ATG"},
    # Operasi
    {"table": "paf",                     "domain": "Operasi",     "tag_col": None,               "label": "PAF (Plant Availability Factor)"},
    {"table": "issue_paf",               "domain": "Operasi",     "tag_col": None,               "label": "Issue PAF"},
    {"table": "monitoring_operasi",      "domain": "Operasi",     "tag_col": None,               "label": "Monitoring Operasi"},
    {"table": "jumlah_eqp_utl",          "domain": "Operasi",     "tag_col": None,               "label": "Jumlah Equipment Utilitas"},
    {"table": "critical_eqp_utl",        "domain": "Operasi",     "tag_col": None,               "label": "Critical Equipment Utilitas"},
    # Inspection
    {"table": "inspection_plan",         "domain": "Inspection",  "tag_col": "equipment",        "label": "Inspection Plan"},
    # Readiness
    {"table": "readiness_jetty",         "domain": "Readiness",   "tag_col": "equipment",        "label": "Readiness Jetty"},
    {"table": "readiness_tank",          "domain": "Readiness",   "tag_col": "equipment",        "label": "Readiness Tank"},
    {"table": "readiness_spm",           "domain": "Readiness",   "tag_col": "equipment",        "label": "Readiness SPM"},
    # Workplan
    {"table": "workplan_jetty",          "domain": "Workplan",    "tag_col": "equipment",        "label": "Workplan Jetty"},
    {"table": "workplan_tank",           "domain": "Workplan",    "tag_col": "equipment",        "label": "Workplan Tank"},
    {"table": "spm_workplan",            "domain": "Workplan",    "tag_col": "equipment",        "label": "Workplan SPM"},
    # IRKAP
    {"table": "irkap_program",           "domain": "IRKAP",       "tag_col": "equipment",        "label": "IRKAP Program"},
    {"table": "irkap_actual",            "domain": "IRKAP",       "tag_col": "equipment",        "label": "IRKAP Actual"},
    # Keuangan
    {"table": "anggaran_maintenance",    "domain": "Keuangan",    "tag_col": None,               "label": "Anggaran Maintenance"},
    {"table": "tkdn",                    "domain": "Keuangan",    "tag_col": None,               "label": "TKDN"},
    # RCPS
    {"table": "rcps",                    "domain": "RCPS",        "tag_col": None,               "label": "RCPS"},
    {"table": "rcps_rekomendasi",        "domain": "RCPS",        "tag_col": None,               "label": "RCPS Rekomendasi"},
    # Dokumen
    {"table": "doc_registry",            "domain": "Dokumen",     "tag_col": None,               "label": "Document Registry"},
]

DOMAIN_ORDER = ["Master", "SAP", "Monitoring", "Inspection", "Readiness", "Workplan", "IRKAP", "Operasi", "Keuangan", "RCPS", "Dokumen"]

# ─── DATA CATALOG ─────────────────────────────────────────────────────────────

def get_catalog_stats():
    """Statistik semua tabel: row count, ukuran, kolom."""
    with get_conn() as conn:
        results = []
        for tbl in TABLE_CATALOG:
            name = tbl["table"]
            try:
                row = conn.execute(f"""
                    SELECT
                        COUNT(*) as total_rows,
                        pg_size_pretty(pg_total_relation_size('{name}')) as ukuran,
                        (SELECT COUNT(*) FROM information_schema.columns
                         WHERE table_name = '{name}' AND table_schema = 'public') as total_cols
                    FROM {name}
                """).fetchone()
                results.append({
                    **tbl,
                    "total_rows": row["total_rows"] if row else 0,
                    "ukuran": row["ukuran"] if row else "—",
                    "total_cols": row["total_cols"] if row else 0,
                    "exists": True
                })
            except Exception:
                results.append({**tbl, "total_rows": 0, "ukuran": "—", "total_cols": 0, "exists": False})
        return results

def get_table_columns(table_name: str) -> list:
    """Kolom lengkap satu tabel dari information_schema."""
    with get_conn() as conn:
        return conn.execute("""
            SELECT column_name, data_type, is_nullable,
                   character_maximum_length, column_default
            FROM information_schema.columns
            WHERE table_schema = 'public' AND table_name = %s
            ORDER BY ordinal_position
        """, (table_name,)).fetchall()

def get_table_sample(table_name: str, limit: int = 10) -> list:
    """Sample data dari satu tabel."""
    with get_conn() as conn:
        try:
            return conn.execute(
                f"SELECT * FROM {table_name} LIMIT %s", (limit,)
            ).fetchall()
        except Exception:
            return []

def get_table_rows(table_name: str, search: str = None,
                   limit: int = 10, offset: int = 0,
                   sort_col: str = None, sort_dir: str = "asc") -> tuple:
    """Browse data tabel dengan search, pagination, dan sorting."""
    import re
    with get_conn() as conn:
        cols = get_table_columns(table_name)
        col_names = [c["column_name"] for c in cols]
        text_cols = [c["column_name"] for c in cols
                     if "char" in c["data_type"] or "text" in c["data_type"]]

        where = ""
        params = []
        if search and text_cols:
            conditions = [f"{c} ILIKE %s" for c in text_cols[:5]]
            where = "WHERE " + " OR ".join(conditions)
            params = [f"%{search}%"] * len(conditions)

        order = ""
        if sort_col and sort_col in col_names:
            direction = "DESC" if sort_dir and sort_dir.lower() == "desc" else "ASC"
            order = f'ORDER BY "{sort_col}" {direction}'

        try:
            total = conn.execute(
                f"SELECT COUNT(*) as n FROM {table_name} {where}", params
            ).fetchone()["n"]
            rows = conn.execute(
                f"SELECT * FROM {table_name} {where} {order} LIMIT %s OFFSET %s",
                params + [limit, offset]
            ).fetchall()
            return list(rows), total
        except Exception as e:
            return [], 0

def get_data_quality(table_name: str, tag_col: str = None) -> dict:
    """Analisa kualitas data: null counts, distinct values, dll."""
    with get_conn() as conn:
        try:
            cols = get_table_columns(table_name)
            total = conn.execute(
                f"SELECT COUNT(*) as n FROM {table_name}"
            ).fetchone()["n"]

            null_stats = []
            for col in cols[:15]:  # max 15 kolom
                cn = col["column_name"]
                r = conn.execute(f"""
                    SELECT
                        COUNT(*) FILTER (WHERE {cn} IS NULL) as nulls,
                        COUNT(DISTINCT {cn}) as distinct_vals
                    FROM {table_name}
                """).fetchone()
                null_pct = round(r["nulls"] * 100 / total, 1) if total > 0 else 0
                null_stats.append({
                    "column": cn,
                    "data_type": col["data_type"],
                    "null_count": r["nulls"],
                    "null_pct": null_pct,
                    "distinct_vals": r["distinct_vals"],
                    "completeness": 100 - null_pct
                })

            # Tag coverage
            tag_coverage = None
            if tag_col:
                try:
                    r2 = conn.execute(f"""
                        SELECT
                            COUNT(DISTINCT {tag_col}) as unique_tags,
                            COUNT(*) FILTER (WHERE {tag_col} IS NULL OR {tag_col} = '') as no_tag
                        FROM {table_name}
                    """).fetchone()
                    tag_coverage = {
                        "unique_tags": r2["unique_tags"],
                        "no_tag": r2["no_tag"],
                        "coverage_pct": round((total - r2["no_tag"]) * 100 / total, 1) if total > 0 else 0
                    }
                except Exception:
                    pass

            avg_completeness = round(
                sum(c["completeness"] for c in null_stats) / len(null_stats), 1
            ) if null_stats else 0

            return {
                "total_rows": total,
                "total_cols": len(cols),
                "avg_completeness": avg_completeness,
                "null_stats": null_stats,
                "tag_coverage": tag_coverage
            }
        except Exception as e:
            return {"error": str(e), "total_rows": 0}

# ─── EQUIPMENT SEARCH ─────────────────────────────────────────────────────────

def search_equipment(q: str = "", plant: str = "", limit: int = 50) -> list:
    with get_conn() as conn:
        conditions, params = [], []
        if q:
            conditions.append(
                "(equipment ILIKE %s OR description ILIKE %s OR functional_location ILIKE %s)"
            )
            params += [f"%{q}%", f"%{q}%", f"%{q}%"]
        if plant:
            conditions.append("maintenance_plant = %s")
            params.append(plant)
        where = ("WHERE " + " AND ".join(conditions)) if conditions else ""
        params.append(limit)
        return conn.execute(f"""
            SELECT equipment, description, functional_location,
                   maintenance_plant, location, criticality,
                   equipment_category, technical_obj_type
            FROM master_data_equipment {where}
            ORDER BY equipment LIMIT %s
        """, params).fetchall()

def get_plant_list() -> list:
    with get_conn() as conn:
        rows = conn.execute("""
            SELECT DISTINCT maintenance_plant FROM master_data_equipment
            WHERE maintenance_plant IS NOT NULL AND maintenance_plant != ''
              AND maintenance_plant NOT LIKE 'K%'
            ORDER BY maintenance_plant
        """).fetchall()
        return [r["maintenance_plant"] for r in rows]

# ─── EQUIPMENT 360° ───────────────────────────────────────────────────────────

def resolve_tag_variants(tag: str) -> list[str]:
    """Return canonical tag + all approved variants from tag_mapping."""
    with get_conn() as conn:
        rows = conn.execute("""
            SELECT DISTINCT tag_variant FROM tag_mapping
            WHERE tag_canonical = %s AND status = 'approved'
        """, (tag,)).fetchall()
    variants = {tag}
    for r in rows:
        if r["tag_variant"]:
            variants.add(r["tag_variant"])
    return list(variants)


def _ph(tags: list) -> tuple[str, list]:
    """Return (IN (%s,...), params) for a list of tags."""
    return ','.join(['%s'] * len(tags)), tags


def get_master_equipment(tag):
    with get_conn() as conn:
        r = conn.execute(
            "SELECT * FROM master_data_equipment WHERE equipment = %s LIMIT 1", (tag,)
        ).fetchone()
        return dict(r) if r else None

def get_sap_notifications(tag, tags=None):
    ph, params = _ph(tags or [tag])
    with get_conn() as conn:
        return conn.execute(f"""
            SELECT notification, notif_type, notif_date, system_status,
                   req_start, required_end, description, order_no,
                   functional_loc, location, criticality, planner_group,
                   main_workctr, maint_plant, has_long_text, uploaded_at
            FROM sap_notifications WHERE equipment IN ({ph})
            ORDER BY notif_date DESC NULLS LAST
        """, params).fetchall()

def get_sap_notifications_summary(tag, tags=None):
    ph, params = _ph(tags or [tag])
    with get_conn() as conn:
        r = conn.execute(f"""
            SELECT COUNT(*) AS total,
                SUM(CASE WHEN order_no IS NULL OR order_no='' THEN 1 ELSE 0 END) AS no_wo,
                SUM(CASE WHEN system_status ILIKE '%OSNO%' THEN 1 ELSE 0 END) AS osno,
                SUM(CASE WHEN required_end < CURRENT_DATE
                     AND (order_no IS NULL OR order_no='') THEN 1 ELSE 0 END) AS overdue
            FROM sap_notifications WHERE equipment = %s
        """, (tag,)).fetchone()
        return dict(r) if r else {"total":0,"no_wo":0,"osno":0,"overdue":0}

def get_sap_work_orders(tag, tags=None):
    ph, params = _ph(tags or [tag])
    with get_conn() as conn:
        return conn.execute(f"""
            SELECT order_no, order_type, created_on, bas_start_date,
                   basic_fin_date, actual_finish, actual_release,
                   description, system_status, user_status,
                   functional_loc, location, criticality,
                   planner_group, main_workctr, maint_act_type,
                   total_plan_cost, total_act_cost, priority,
                   notification, po_number, plant
            FROM sap_work_orders WHERE equipment IN ({ph})
            ORDER BY created_on DESC NULLS LAST
        """, params).fetchall()

def get_sap_wo_summary(tag, tags=None):
    ph, params = _ph(tags or [tag])
    with get_conn() as conn:
        r = conn.execute(f"""
            SELECT COUNT(*) AS total,
                SUM(CASE WHEN system_status ILIKE '%TECO%'
                     OR system_status ILIKE '%CLSD%' THEN 1 ELSE 0 END) AS closed,
                SUM(CASE WHEN system_status ILIKE '%REL%'
                     AND actual_finish IS NULL THEN 1 ELSE 0 END) AS open,
                SUM(CASE WHEN basic_fin_date < CURRENT_DATE
                     AND system_status NOT ILIKE '%TECO%'
                     AND system_status NOT ILIKE '%CLSD%' THEN 1 ELSE 0 END) AS overdue,
                COALESCE(SUM(total_act_cost),0) AS total_cost
            FROM sap_work_orders WHERE equipment IN ({ph})
        """, params).fetchone()
        return dict(r) if r else {"total":0,"closed":0,"open":0,"overdue":0,"total_cost":0}

def get_bad_actor(tag, tags=None):
    ph, params = _ph(tags or [tag])
    with get_conn() as conn:
        return conn.execute(f"""
            SELECT ru, status, problem, action_plan, progress,
                   target_date, periode, action_plan_category,
                   external_resource, no_irkap, action_plan_remark
            FROM bad_actor_monitoring WHERE equipment IN ({ph})
            ORDER BY periode DESC NULLS LAST
        """, params).fetchall()

def get_icu(tag, tags=None):
    ph, params = _ph(tags or [tag])
    with get_conn() as conn:
        return conn.execute(f"""
            SELECT ru, icu_status, issue, mitigation, mitigasi_category,
                   permanent_solution, solution_category, progress,
                   target_closed, report_date, info,
                   remark_mitigation, remark_solution
            FROM icu_monitoring WHERE equipment IN ({ph})
            ORDER BY report_date DESC NULLS LAST
        """, params).fetchall()

def get_atg(tag, tags=None):
    ph, params = _ph(tags or [tag])
    with get_conn() as conn:
        return conn.execute(f"""
            SELECT refinery_unit, equipment_tangki, equipment_atg,
                   status_atg, status_interkoneksi_atg,
                   cert_no_atg, date_expired_atg, remark,
                   rtl, action_plan_category, status_rtl, month_update
            FROM atg_monitoring
            WHERE equipment_tangki IN ({ph}) OR equipment_atg IN ({ph})
            ORDER BY month_update DESC NULLS LAST
        """, params + params).fetchall()

def get_metering(tag, tags=None):
    ph, params = _ph(tags or [tag])
    with get_conn() as conn:
        return conn.execute(f"""
            SELECT refinery_unit, status_metering, cert_no_metering,
                   date_expired_metering, remark, rtl,
                   action_plan_category, status_rtl, month_update
            FROM metering_monitoring WHERE equipment IN ({ph})
            ORDER BY month_update DESC NULLS LAST
        """, params).fetchall()

def get_boc(tag, tags=None):
    ph, params = _ph(tags or [tag])
    with get_conn() as conn:
        return conn.execute(f"""
            SELECT ru, area, unit, grup_equipment, status,
                   frequency, running_hours, mttr, mtbf, hasil
            FROM boc WHERE equipment IN ({ph}) ORDER BY ru
        """, params).fetchall()

def get_pipeline(tag, tags=None):
    ph, params = _ph(tags or [tag])
    with get_conn() as conn:
        return conn.execute(f"""
            SELECT refinery_unit, area, unit, fluida_service, nps,
                   from_location, to_location,
                   last_inspection_date, next_inspection_date,
                   last_measured_thickness, rem_life_years,
                   jumlah_temporary_repair, remarks, bulan, tahun
            FROM pipeline_inspection WHERE equipment IN ({ph})
            ORDER BY tahun DESC NULLS LAST, bulan DESC NULLS LAST
        """, params).fetchall()

def get_inspection_plan(tag, tags=None):
    ph, params = _ph(tags or [tag])
    with get_conn() as conn:
        return conn.execute(f"""
            SELECT refinery_unit, area, unit, type_equipment,
                   type_inspection, type_pekerjaan,
                   due_date, due_year, plan_date, plan_year,
                   actual_date, actual_year, update_date,
                   result_remaining_life, result_visual, grand_result
            FROM inspection_plan WHERE equipment IN ({ph})
            ORDER BY due_year DESC NULLS LAST
        """, params).fetchall()

def get_zero_clamp(tag, tags=None):
    ph, params = _ph(tags or [tag])
    with get_conn() as conn:
        return conn.execute(f"""
            SELECT ru, area, unit, services, description,
                   type_damage, posisi, type_perbaikan,
                   tanggal_dipasang, tanggal_dilepas,
                   tanggal_rencana_perbaikan, status, remarks, no_irkap
            FROM zero_clamp WHERE equipment IN ({ph})
            ORDER BY tanggal_dipasang DESC NULLS LAST
        """, params).fetchall()

def get_readiness_jetty(tag, tags=None):
    ph, params = _ph(tags or [tag])
    with get_conn() as conn:
        return conn.execute(f"""
            SELECT refinery_unit, area, unit, status_operation,
                   status_tuks, expired_tuks, status_ijin_ops,
                   status_isps, status_struktur, remark_struktur,
                   status_trestle, status_mla, status_fire_protection,
                   month_update
            FROM readiness_jetty WHERE equipment IN ({ph})
            ORDER BY month_update DESC NULLS LAST
        """, params).fetchall()

def get_readiness_tank(tag, tags=None):
    ph, params = _ph(tags or [tag])
    with get_conn() as conn:
        return conn.execute(f"""
            SELECT refinery_unit, area, unit, type_tangki, service_tangki,
                   prioritas, status_operational, atg_certification_validity,
                   status_coi, internal_inspection, plan_internal_inspection,
                   status_atg, status_grounding, status_shell_course,
                   status_roof, status_cathodic, month_update
            FROM readiness_tank WHERE equipment IN ({ph})
            ORDER BY month_update DESC NULLS LAST
        """, params).fetchall()

def get_readiness_spm(tag, tags=None):
    ph, params = _ph(tags or [tag])
    with get_conn() as conn:
        return conn.execute(f"""
            SELECT refinery_unit, area, unit, status_operation,
                   status_laik_operasi, expired_laik_operasi,
                   status_ijin_spl, status_mbc, status_lds,
                   status_mooring_hawser, status_floating_hose,
                   status_cathodic_spl, month_update
            FROM readiness_spm WHERE equipment IN ({ph})
            ORDER BY month_update DESC NULLS LAST
        """, params).fetchall()

def get_workplan_jetty(tag, tags=None):
    ph, params = _ph(tags or [tag])
    with get_conn() as conn:
        return conn.execute(f"""
            SELECT refinery_unit, area, unit, item, status_item,
                   remark, rtl_action_plan, action_plan_category,
                   target, status_rtl, month_update
            FROM workplan_jetty WHERE equipment IN ({ph})
            ORDER BY month_update DESC NULLS LAST
        """, params).fetchall()

def get_workplan_tank(tag, tags=None):
    ph, params = _ph(tags or [tag])
    with get_conn() as conn:
        return conn.execute(f"""
            SELECT unit, item, remark, rtl_action_plan,
                   action_plan_category, target, status_rtl, month_update
            FROM workplan_tank WHERE equipment IN ({ph})
            ORDER BY month_update DESC NULLS LAST
        """, params).fetchall()

def get_workplan_spm(tag, tags=None):
    ph, params = _ph(tags or [tag])
    with get_conn() as conn:
        return conn.execute(f"""
            SELECT refinery_unit, area, unit, item, remark,
                   rtl_action_plan, action_plan_category,
                   target, status_rtl, month_update
            FROM spm_workplan WHERE equipment IN ({ph})
            ORDER BY month_update DESC NULLS LAST
        """, params).fetchall()

def get_irkap_program(tag, tags=None):
    ph, params = _ph(tags or [tag])
    with get_conn() as conn:
        return conn.execute(f"""
            SELECT refinery_unit, disiplin, kategori_rkap,
                   no_program_kerja, type_equipment, program_kerja,
                   status_step, status_prognosa, start_plan, finish_plan,
                   nilai_anggaran_idr, nilai_anggaran_usd,
                   top_risk, asset_integrity
            FROM irkap_program WHERE equipment IN ({ph})
            ORDER BY start_plan DESC NULLS LAST
        """, params).fetchall()

def get_irkap_actual(tag, tags=None):
    ph, params = _ph(tags or [tag])
    with get_conn() as conn:
        return conn.execute(f"""
            SELECT no_program, kategori_rkap, program_kerja,
                   refinery_unit, area, disiplin,
                   status_step, status_prognosa, current_step,
                   notif_no, wo_no, pr, po,
                   anggaran_idr, jadwal_pelaksanaan,
                   actual_start1, actual_finish1,
                   actual_start3, actual_finish3,
                   failure_impact, rekomendasi
            FROM irkap_actual WHERE equipment IN ({ph})
            ORDER BY no DESC NULLS LAST
        """, params).fetchall()

def get_critical_prim_sec(tag, tags=None):
    ph, params = _ph(tags or [tag])
    with get_conn() as conn:
        return conn.execute(f"""
            SELECT refinery_unit, unit_proses, highlight_issue,
                   corrective_action, target_corrective, traffic_corrective,
                   mitigasi_action, target_mitigasi, traffic_mitigasi,
                   month_update
            FROM critical_eqp_prim_sec WHERE equipment IN ({ph})
            ORDER BY month_update DESC NULLS LAST
        """, params).fetchall()

def get_power_stream(tag, tags=None):
    ph, params = _ph(tags or [tag])
    with get_conn() as conn:
        return conn.execute(f"""
            SELECT refinery_unit, type_equipment, status_operation,
                   status_n0, unit_measurement, desain,
                   kapasitas_max, average_actual, remark,
                   date_update, month_update
            FROM power_stream WHERE equipment IN ({ph})
            ORDER BY month_update DESC NULLS LAST
        """, params).fetchall()

def get_equipment_360(tag: str) -> dict:
    tags      = resolve_tag_variants(tag)
    master    = get_master_equipment(tag)
    notif     = get_sap_notifications(tag, tags=tags)
    notif_sum = get_sap_notifications_summary(tag, tags=tags)
    wo        = get_sap_work_orders(tag, tags=tags)
    wo_sum    = get_sap_wo_summary(tag, tags=tags)
    bad_actor = get_bad_actor(tag, tags=tags)
    icu       = get_icu(tag, tags=tags)
    atg       = get_atg(tag, tags=tags)
    metering  = get_metering(tag, tags=tags)
    boc       = get_boc(tag, tags=tags)
    pipeline  = get_pipeline(tag, tags=tags)
    insp_plan = get_inspection_plan(tag, tags=tags)
    zc        = get_zero_clamp(tag, tags=tags)
    r_jetty   = get_readiness_jetty(tag, tags=tags)
    r_tank    = get_readiness_tank(tag, tags=tags)
    r_spm     = get_readiness_spm(tag, tags=tags)
    wp_jetty  = get_workplan_jetty(tag, tags=tags)
    wp_tank   = get_workplan_tank(tag, tags=tags)
    wp_spm    = get_workplan_spm(tag, tags=tags)
    irkap_p   = get_irkap_program(tag, tags=tags)
    irkap_a   = get_irkap_actual(tag, tags=tags)
    crit      = get_critical_prim_sec(tag, tags=tags)
    power     = get_power_stream(tag, tags=tags)

    # Health Score
    score, alerts = 100, []
    active_ba = [b for b in bad_actor if str(b.get("status","")).upper() not in ("CLOSED","SELESAI","CLOSE")]
    if active_ba:
        score -= 30
        alerts.append({"level":"red","msg":f"Masuk Bad Actor ({len(active_ba)} aktif)"})
    high_icu = [i for i in icu if str(i.get("icu_status","")).upper() in ("HIGH","CRITICAL")]
    if high_icu:
        score -= 20
        alerts.append({"level":"red","msg":f"ICU High/Critical ({len(high_icu)} issue)"})
    if notif_sum.get("overdue",0) > 0:
        score -= min(notif_sum["overdue"]*5, 20)
        alerts.append({"level":"yellow","msg":f"{notif_sum['overdue']} notifikasi overdue"})
    if wo_sum.get("overdue",0) > 0:
        score -= min(wo_sum["overdue"]*5, 15)
        alerts.append({"level":"yellow","msg":f"{wo_sum['overdue']} WO overdue"})
    active_zc = [z for z in zc if str(z.get("status","")).upper() not in ("CLOSED","SELESAI","LEPAS")]
    if active_zc:
        score -= 10
        alerts.append({"level":"yellow","msg":f"{len(active_zc)} Zero Clamp aktif"})
    score = max(score, 0)
    health_label = "Baik" if score >= 80 else "Perhatian" if score >= 60 else "Kritis"
    health_color = "green" if score >= 80 else "yellow" if score >= 60 else "red"

    return {
        "master": master, "notif": [dict(r) for r in notif],
        "notif_sum": notif_sum, "wo": [dict(r) for r in wo],
        "wo_sum": wo_sum, "bad_actor": [dict(r) for r in bad_actor],
        "icu": [dict(r) for r in icu], "atg": [dict(r) for r in atg],
        "metering": [dict(r) for r in metering], "boc": [dict(r) for r in boc],
        "pipeline": [dict(r) for r in pipeline],
        "insp_plan": [dict(r) for r in insp_plan],
        "zero_clamp": [dict(r) for r in zc],
        "readiness": {"jetty":[dict(r) for r in r_jetty],
                      "tank":[dict(r) for r in r_tank],
                      "spm":[dict(r) for r in r_spm]},
        "workplan": {"jetty":[dict(r) for r in wp_jetty],
                     "tank":[dict(r) for r in wp_tank],
                     "spm":[dict(r) for r in wp_spm]},
        "irkap_program": [dict(r) for r in irkap_p],
        "irkap_actual": [dict(r) for r in irkap_a],
        "critical": [dict(r) for r in crit],
        "power_stream": [dict(r) for r in power],
        "health": {"score": score, "label": health_label,
                   "color": health_color, "alerts": alerts},
    }

# ─── GOVERNANCE OVERVIEW ──────────────────────────────────────────────────────

def get_governance_overview():
    """Stats untuk halaman governance dashboard."""
    stats = get_catalog_stats()
    total_rows = sum(s["total_rows"] for s in stats if s["exists"])
    total_tables = sum(1 for s in stats if s["exists"])
    by_domain = {}
    for s in stats:
        d = s["domain"]
        if d not in by_domain:
            by_domain[d] = {"domain": d, "tables": 0, "rows": 0}
        if s["exists"]:
            by_domain[d]["tables"] += 1
            by_domain[d]["rows"] += s["total_rows"]

    # Total equipment unik
    with get_conn() as conn:
        try:
            total_eq = conn.execute(
                "SELECT COUNT(*) as n FROM master_data_equipment"
            ).fetchone()["n"]
        except Exception:
            total_eq = 0

    return {
        "total_tables": total_tables,
        "total_rows": total_rows,
        "total_equipment": total_eq,
        "by_domain": [by_domain[d] for d in DOMAIN_ORDER if d in by_domain],
        "tables": stats
    }
