"""
chatbot.py
Core chatbot engine dengan 3 kemampuan:
1. RAG — vector search dokumen
2. SQL — query data dari 26 tabel
3. Graph — query Neo4j Knowledge Graph
"""
import os
import json
import re
import time
import logging
from openai import OpenAI
from dotenv import load_dotenv

load_dotenv()

client = OpenAI(
    api_key=os.getenv("LLM_API_KEY"),
    base_url=os.getenv("LLM_BASE_URL", "https://ai.dinoiki.com/v1")
)
MODEL = "gpt-4o"


# ─── Streaming helper ─────────────────────────────────────────────────────────

def _stream_generate(messages, max_tokens, status_cb):
    """Stream completion, emit tokens via status_cb, return full text."""
    stream = client.chat.completions.create(
        model=MODEL,
        max_tokens=max_tokens,
        messages=messages,
        stream=True
    )
    full_text = ""
    for chunk in stream:
        delta = chunk.choices[0].delta.content if chunk.choices else None
        if delta:
            full_text += delta
            if status_cb:
                try:
                    status_cb({"type": "token", "text": delta})
                except:
                    pass
    return full_text


# ─── Categorical value cache ──────────────────────────────────────────────────

# Kolom kategorikal yang nilainya sering disebut user dalam pertanyaan
CATEGORICAL_COLUMNS = [
    ("master_data_equipment",       "criticality"),
    ("bad_actor_monitoring",        "status"),
    ("bad_actor_monitoring",        "action_plan_category"),
    ("icu_monitoring",              "icu_status"),
    ("icu_monitoring",              "mitigasi_category"),
    ("icu_monitoring",              "solution_category"),
    ("boc",                         "status"),
    ("boc",                         "hasil"),
    ("atg_monitoring",              "status_atg"),
    ("atg_monitoring",              "status_interkoneksi_atg"),
    ("atg_monitoring",              "action_plan_category"),
    ("metering_monitoring",         "status_metering"),
    ("metering_monitoring",         "action_plan_category"),
    ("irkap_program",               "status_step"),
    ("irkap_program",               "status_prognosa"),
    ("irkap_program",               "kategori_rkap"),
    ("irkap_program",               "disiplin"),
    ("irkap_actual",                "status_step"),
    ("irkap_actual",                "status_prognosa"),
    ("irkap_actual",                "kategori_rkap"),
    ("sap_notifications",           "notif_type"),
    ("sap_notifications",           "system_status"),
    ("sap_work_orders",             "system_status"),
    ("sap_work_orders",             "order_type"),
    ("sap_work_orders",             "maint_act_type"),
    ("pipeline_inspection",         "fluida_service"),
    ("zero_clamp",                  "type_damage"),
    ("zero_clamp",                  "type_perbaikan"),
    ("zero_clamp",                  "status"),
    ("inspection_plan",             "type_inspection"),
    ("inspection_plan",             "grand_result"),
    ("readiness_jetty",             "status_operation"),
    ("readiness_tank",              "status_operational"),
    ("readiness_tank",              "type_tangki"),
    ("readiness_spm",               "status_operation"),
    ("readiness_spm",               "status_laik_operasi"),
    ("workplan_jetty",              "action_plan_category"),
    ("workplan_tank",               "action_plan_category"),
    ("spm_workplan",                "action_plan_category"),
    ("power_stream",                "status_operation"),
    ("power_stream",                "type_equipment"),
    ("critical_eqp_prim_sec",       "traffic_corrective"),
    ("critical_eqp_prim_sec",       "traffic_mitigasi"),
    # Tabel baru
    ("rotor_monitoring",            "status_readiness_spare"),
    ("rotor_monitoring",            "status_workplan"),
    ("rotor_monitoring",            "action_plan_category"),
    ("paf",                         "ru"),
    ("paf",                         "type"),
    ("paf",                         "plan_unplan"),
    ("issue_paf",                   "type"),
    ("issue_paf",                   "ru"),
    ("jumlah_eqp_utl",              "status_equipment"),
    ("jumlah_eqp_utl",              "type_equipment"),
    ("critical_eqp_utl",            "traffic_corrective"),
    ("critical_eqp_utl",            "traffic_mitigasi"),
    ("monitoring_operasi",          "refinery_unit"),
    ("anggaran_maintenance",        "ru"),
    ("anggaran_maintenance",        "kategori"),
    ("anggaran_maintenance",        "tipe"),
    ("rcps",                        "kilang"),
    ("rcps",                        "traffic"),
    ("rcps",                        "disiplin"),
    ("rcps_rekomendasi",            "kilang"),
    ("rcps_rekomendasi",            "traffic"),
    ("rcps_rekomendasi",            "recommendation_category"),
    ("program_kerja_atg",           "refinery_unit"),
    ("program_kerja_atg",           "action_plan_category"),
]

_categorical_cache: dict = {}
_categorical_last_refresh: float = 0
_CATEGORICAL_TTL = 3600  # refresh tiap 1 jam


def _build_categorical_values() -> dict:
    """Query DISTINCT values dari semua kolom kategorikal."""
    result = {}
    try:
        from db_equipment import get_conn
        with get_conn() as conn:
            for table, col in CATEGORICAL_COLUMNS:
                try:
                    rows = conn.execute(
                        f"SELECT DISTINCT {col} FROM {table} "
                        f"WHERE {col} IS NOT NULL AND {col} != '' "
                        f"ORDER BY {col} LIMIT 30"
                    ).fetchall()
                    vals = [r[0] for r in rows if r[0]]
                    if vals:
                        key = f"{table}.{col}"
                        result[key] = vals
                except Exception:
                    pass
    except Exception as e:
        logging.warning(f"[CATEGORICAL] Build failed: {e}")
    return result


def _get_categorical_values() -> dict:
    """Return cached categorical values, refresh if stale."""
    global _categorical_cache, _categorical_last_refresh
    now = time.time()
    if not _categorical_cache or (now - _categorical_last_refresh) > _CATEGORICAL_TTL:
        _categorical_cache = _build_categorical_values()
        _categorical_last_refresh = now
        logging.info(f"[CATEGORICAL] Refreshed {len(_categorical_cache)} columns")
    return _categorical_cache


def _build_categorical_prompt() -> str:
    """Format categorical values jadi teks untuk dimasukkan ke prompt."""
    vals = _get_categorical_values()
    if not vals:
        return ""
    lines = ["\n=== NILAI AKTUAL DI DATABASE (gunakan ini untuk filter) ==="]
    for key, values in vals.items():
        formatted = ", ".join(f"'{v}'" for v in values[:20])
        lines.append(f"{key}: {formatted}")
    return "\n".join(lines)


# Neo4j categorical values
NEO4J_CATEGORICAL_PROPERTIES = [
    ("Equipment",        "criticality"),
    ("BadActor",         "status"),
    ("BadActor",         "action_plan_category"),
    ("ICUMonitoring",    "icu_status"),
    ("ICUMonitoring",    "mitigasi_category"),
    ("ICUMonitoring",    "solution_category"),
    ("BOC",              "status"),
    ("BOC",              "hasil"),
    ("ATGMonitoring",    "status_atg"),
    ("MeteringMonitor",  "status_metering"),
    ("IRKAPProgram",     "status_step"),
    ("IRKAPProgram",     "status_prognosa"),
    ("IRKAPProgram",     "kategori_rkap"),
    ("IRKAPActual",      "status_step"),
    ("IRKAPActual",      "status_prognosa"),
    ("SAPNotification",  "notif_type"),
    ("SAPNotification",  "system_status"),
    ("SAPWorkOrder",     "system_status"),
    ("SAPWorkOrder",     "order_type"),
    ("ZeroClamp",        "status"),
    ("InspectionPlan",   "grand_result"),
    ("ReadinessJetty",   "status_operation"),
    ("ReadinessTank",    "status_operational"),
    ("ReadinessSPM",     "status_operation"),
    ("PowerStream",      "status_operation"),
]

_neo4j_categorical_cache: dict = {}
_neo4j_categorical_last_refresh: float = 0


def _build_neo4j_categorical_values() -> dict:
    """Query DISTINCT property values dari Neo4j nodes."""
    result = {}
    try:
        from neo4j_sync import get_driver
        driver = get_driver()
        if not driver:
            return result
        with driver.session() as session:
            for label, prop in NEO4J_CATEGORICAL_PROPERTIES:
                try:
                    rows = session.run(
                        f"MATCH (n:{label}) WHERE n.{prop} IS NOT NULL "
                        f"RETURN DISTINCT n.{prop} AS val ORDER BY val LIMIT 30",
                        timeout=8
                    ).data()
                    vals = [r["val"] for r in rows if r["val"]]
                    if vals:
                        result[f"{label}.{prop}"] = vals
                except Exception:
                    pass
        driver.close()
    except Exception as e:
        logging.warning(f"[NEO4J CATEGORICAL] Build failed: {e}")
    return result


def _get_neo4j_categorical_values() -> dict:
    global _neo4j_categorical_cache, _neo4j_categorical_last_refresh
    now = time.time()
    if not _neo4j_categorical_cache or (now - _neo4j_categorical_last_refresh) > _CATEGORICAL_TTL:
        _neo4j_categorical_cache = _build_neo4j_categorical_values()
        _neo4j_categorical_last_refresh = now
    return _neo4j_categorical_cache


def _build_neo4j_categorical_prompt() -> str:
    vals = _get_neo4j_categorical_values()
    if not vals:
        return ""
    lines = ["\n=== NILAI AKTUAL DI NEO4J (gunakan ini untuk filter) ==="]
    for key, values in vals.items():
        formatted = ", ".join(f"'{v}'" for v in values[:20])
        lines.append(f"{key}: {formatted}")
    return "\n".join(lines)


# ─── Dynamic schema helpers ───────────────────────────────────────────────────

def _get_db_schema() -> str:
    return DB_SCHEMA_FALLBACK + _build_categorical_prompt()


def _get_neo4j_schema() -> str:
    return NEO4J_SCHEMA_FALLBACK


# ─── Fallback static schema (used if dynamic build fails) ────────────────────

DB_SCHEMA_FALLBACK = """
PostgreSQL Database Schema — Pertamina Data Governance

=== KOLOM TAG PER TABEL (WAJIB dipakai untuk JOIN) ===
master_data_equipment  → equipment
bad_actor_monitoring   → tag_number
icu_monitoring         → tag_no
atg_monitoring         → tag_no_tangki
metering_monitoring    → tag_number
boc                    → equipment
sap_notifications      → equipment
sap_work_orders        → equipment
pipeline_inspection    → tag_number
zero_clamp             → tag_no_ln
inspection_plan        → tag_no_ln
irkap_program          → equipment_tag_no
irkap_actual           → tag_no
readiness_jetty        → tag_no
readiness_tank         → tag_number
readiness_spm          → tag_no
workplan_jetty         → tag_no
workplan_tank          → tag_no
spm_workplan           → tag_no
critical_eqp_prim_sec  → equipment
power_stream           → equipment
doc_tag_links          → tag_number
-- Tabel berikut tidak punya kolom tag (agregat/operasional) --
anggaran_maintenance   → filter by ru, tahun, kategori
rotor_monitoring       → filter by refinery_unit, bulan
program_kerja_atg      → filter by refinery_unit
paf                    → filter by ru, type, month_update
issue_paf              → filter by ru, type, month_update
jumlah_eqp_utl         → filter by refinery_unit, type_equipment
critical_eqp_utl       → filter by refinery_unit, type_equipment
monitoring_operasi     → filter by refinery_unit, unit_proses
tkdn                   → filter by refinery_unit, tahun, bulan
rcps                   → filter by kilang, disiplin, traffic
rcps_rekomendasi       → filter by kilang, traffic, recommendation_category

=== TABEL MASTER ===

master_data_equipment
  Kolom: equipment (PK, tag number), description, functional_location,
         maintenance_plant (kode RU: RU2/RU3/RU4/RU5/RU6/RU7),
         location, criticality (A=sangat kritis, B=kritis, C=kurang kritis, Z=tidak kritis),
         equipment_category, technical_obj_type, manufacturer, model_type,
         material, material_description, size_dimension, cost_center, sort_field_ata

=== TABEL SAP ===

sap_notifications
  Kolom: notification, equipment, notif_type, notif_date, system_status,
         req_start, required_end, description, order_no,
         functional_loc, location, criticality,
         planner_group, main_workctr, maint_plant, uploaded_at

sap_work_orders
  Kolom: order_no, equipment, order_type, created_on,
         bas_start_date, basic_fin_date, actual_finish, actual_release,
         description, system_status, user_status,
         functional_loc, location, criticality,
         planner_group, main_workctr, maint_act_type,
         total_plan_cost, total_act_cost, priority,
         notification, po_number, plant

=== TABEL MONITORING ===

bad_actor_monitoring
  Kolom: tag_number, ru, status, problem, action_plan, progress,
         target_date, periode, action_plan_category,
         external_resource, no_irkap, action_plan_remark

icu_monitoring
  Kolom: tag_no, ru, icu_status (contoh: ICU / Non-ICU / Closed),
         issue, mitigation, mitigasi_category,
         permanent_solution, solution_category,
         progress, target_closed, report_date,
         info, remark_mitigation, remark_solution

atg_monitoring
  Kolom: tag_no_tangki, tag_no_atg, refinery_unit,
         status_atg, status_interkoneksi_atg,
         cert_no_atg, date_expired_atg,
         remark, rtl, action_plan_category, status_rtl, month_update

metering_monitoring
  Kolom: tag_number, refinery_unit,
         status_metering, cert_no_metering, date_expired_metering,
         remark, rtl, action_plan_category, status_rtl, month_update

boc
  Kolom: equipment, ru, area, unit, grup_equipment, status,
         frequency, running_hours, mttr, mtbf,
         hasil (N+0=tidak ada standby KRITIS, N+1=ada 1 standby,
                N+2=ada 2 standby, Single=tunggal tanpa grup)

pipeline_inspection
  Kolom: tag_number, refinery_unit, area, unit, fluida_service, nps,
         from_location, to_location,
         last_inspection_date, next_inspection_date,
         last_measured_thickness, rem_life_years,
         jumlah_temporary_repair, remarks, bulan, tahun

zero_clamp
  Kolom: tag_no_ln, ru, area, unit, services, description,
         type_damage, posisi, type_perbaikan,
         tanggal_dipasang, tanggal_dilepas, tanggal_rencana_perbaikan,
         status, remarks, no_irkap

power_stream
  Kolom: equipment, refinery_unit, type_equipment,
         status_operation, status_n0,
         unit_measurement, desain, kapasitas_max, average_actual,
         remark, date_update, month_update

critical_eqp_prim_sec
  Kolom: equipment, refinery_unit, unit_proses,
         highlight_issue, corrective_action, target_corrective, traffic_corrective,
         mitigasi_action, target_mitigasi, traffic_mitigasi, month_update

=== TABEL INSPECTION ===

inspection_plan
  Kolom: tag_no_ln, refinery_unit, area, unit,
         type_equipment, type_inspection, type_pekerjaan,
         due_date, due_year, plan_date, plan_year,
         actual_date, actual_year, update_date,
         result_remaining_life, result_visual, grand_result

=== TABEL READINESS ===

readiness_jetty
  Kolom: tag_no, refinery_unit, area, unit,
         status_operation, status_tuks, expired_tuks,
         status_ijin_ops, status_isps, status_struktur, remark_struktur,
         status_trestle, status_mla, status_fire_protection, month_update

readiness_tank
  Kolom: tag_number, refinery_unit, area, unit,
         type_tangki, service_tangki, prioritas, status_operational,
         atg_certification_validity, status_coi,
         internal_inspection, plan_internal_inspection,
         status_atg, status_grounding, status_shell_course,
         status_roof, status_cathodic, month_update

readiness_spm
  Kolom: tag_no, refinery_unit, area, unit,
         status_operation, status_laik_operasi, expired_laik_operasi,
         status_ijin_spl, status_mbc, status_lds,
         status_mooring_hawser, status_floating_hose,
         status_cathodic_spl, month_update

=== TABEL WORKPLAN ===

workplan_jetty
  Kolom: tag_no, refinery_unit, area, unit,
         item, status_item, remark,
         rtl_action_plan, action_plan_category, target, status_rtl, month_update

workplan_tank
  Kolom: tag_no, unit, item, remark,
         rtl_action_plan, action_plan_category, target, status_rtl, month_update

spm_workplan
  Kolom: tag_no, refinery_unit, area, unit, item, remark,
         rtl_action_plan, action_plan_category, target, status_rtl, month_update

=== TABEL IRKAP ===

irkap_program
  Kolom: equipment_tag_no (TAG), refinery_unit, disiplin,
         kategori_rkap, no_program_kerja, type_equipment, program_kerja,
         status_step, status_prognosa,
         start_plan, finish_plan,
         nilai_anggaran_idr, nilai_anggaran_usd,
         top_risk, asset_integrity

irkap_actual
  Kolom: tag_no (TAG), no_program, kategori_rkap, program_kerja,
         refinery_unit, area, disiplin,
         status_step, status_prognosa, current_step,
         notif_no, wo_no, pr, po, anggaran_idr,
         jadwal_pelaksanaan, actual_start1, actual_finish1,
         actual_start3, actual_finish3,
         failure_impact, rekomendasi

=== TABEL OPERASI ===

paf
  Kolom: ru, type, target_realisasi, value, plan_unplan, type2, month, value2,
         ru2, target, month_update
  Catatan: PAF = Plant Availability Factor, indikator ketersediaan kilang

issue_paf
  Kolom: type, ru, date, issue, month_update
  Catatan: Isu yang mempengaruhi PAF per kilang

monitoring_operasi
  Kolom: refinery_unit, unit_proses, unit, design, minimal_capacity,
         plant_readiness, actual, type_limitasi_process, equipment_process,
         limitasi_alert_process, mitigasi_process, target_sts, actual,
         type_limitasi_sts, equipment_sts, mitigasi_sts, month_update
  Catatan: Monitoring kapasitas dan limitasi operasi per unit proses

jumlah_eqp_utl
  Kolom: refinery_unit, type_equipment, status_equipment, jumlah, month_update
  Catatan: Rekap jumlah equipment utilitas per status per RU

critical_eqp_utl
  Kolom: refinery_unit, type_equipment, highlight_issue, corrective_action,
         target_corrective, traffic_corrective, mitigasi_action,
         target_mitigasi, traffic_mitigasi, month_update
  Catatan: Equipment kritis utilitas dengan highlight isu dan tindakan

rotor_monitoring
  Kolom: refinery_unit, bulan, rotor, program, brand,
         status_readiness_spare, status_workplan, detail_status_workplan,
         keterangan, action_plan_category, no_irkap,
         finish_date_eksekusi, readiness_rotor, last_update
  Catatan: Monitoring kesiapan rotor dan spare per RU

program_kerja_atg
  Kolom: refinery_unit, type, atg_eksisting, program_2024, prokja,
         action_plan_category, no_irkap, target, month_update
  Catatan: Program kerja pemasangan/perbaikan ATG

=== TABEL KEUANGAN ===

anggaran_maintenance
  Kolom: ru, tahun, kategori, tipe, nilai_usd
  Catatan: Rekap anggaran maintenance per RU per tahun per kategori

tkdn
  Kolom: refinery_unit, bulan, nominal, kdn, persentase, tahun
  Catatan: TKDN = Tingkat Komponen Dalam Negeri, persentase penggunaan produk lokal

=== TABEL RCPS ===

rcps
  Kolom: kilang, traffic, sum_of_progress, link, disiplin, date,
         judul_rcps, rcps_no, criticallity
  Catatan: Root Cause Problem Solving per kilang

rcps_rekomendasi
  Kolom: kilang, rcps_no, judul_rcps, rekomendasi, description,
         traffic, pic, target, recommendation_category,
         no_irkap, remark
  Catatan: Rekomendasi hasil RCPS yang harus ditindaklanjuti

=== TABEL DOKUMEN ===

doc_registry
  Kolom: id, judul, tipe_dokumen, ru, nomor_dokumen, deskripsi,
         file_name, file_type, status, total_pages, total_chunks, uploaded_at

doc_tag_links
  Kolom: doc_id, tag_number, link_type (manual/auto)

=== CONTOH JOIN QUERY ===

-- Bad actor + ICU monitoring per equipment:
SELECT m.equipment, m.description, b.status AS bad_actor_status, i.icu_status
FROM master_data_equipment m
JOIN bad_actor_monitoring b ON m.equipment = b.tag_number
JOIN icu_monitoring i ON m.equipment = i.tag_no
LIMIT 20

-- Equipment critical A yang punya bad actor:
SELECT m.equipment, m.description, m.criticality, b.status, b.problem
FROM master_data_equipment m
JOIN bad_actor_monitoring b ON m.equipment = b.tag_number
WHERE m.criticality = 'A'
LIMIT 20

-- IRKAP program + actual per equipment:
SELECT m.equipment, m.description, p.program_kerja, p.status_prognosa,
       a.current_step, a.status_step
FROM master_data_equipment m
JOIN irkap_program p ON m.equipment = p.equipment_tag_no
LEFT JOIN irkap_actual a ON m.equipment = a.tag_no AND p.no_program_kerja = a.no_program
LIMIT 20

-- BOC equipment tanpa standby (N+0) criticality A:
SELECT m.equipment, m.description, b.hasil, b.mtbf, b.mttr, b.area
FROM master_data_equipment m
JOIN boc b ON m.equipment = b.equipment
WHERE b.hasil = 'N+0' AND m.criticality = 'A'
LIMIT 20

-- SAP notification + work order terkait:
SELECT m.equipment, n.notification, n.description AS notif_desc,
       n.notif_date, w.order_no, w.system_status, w.total_act_cost
FROM master_data_equipment m
JOIN sap_notifications n ON m.equipment = n.equipment
LEFT JOIN sap_work_orders w ON n.order_no = w.notification
LIMIT 20

-- Zero clamp yang masih terpasang (belum dilepas):
SELECT m.equipment, m.description, z.area, z.type_damage,
       z.tanggal_dipasang, z.status
FROM master_data_equipment m
JOIN zero_clamp z ON m.equipment = z.tag_no_ln
WHERE z.tanggal_dilepas IS NULL
LIMIT 20

-- Jumlah bad actor per RU:
SELECT ru, COUNT(*) AS jumlah FROM bad_actor_monitoring
GROUP BY ru ORDER BY jumlah DESC

-- ATG yang sertifikasinya expired:
SELECT tag_no_tangki, refinery_unit, status_atg, cert_no_atg, date_expired_atg
FROM atg_monitoring
WHERE date_expired_atg < CURRENT_DATE
LIMIT 20

-- Dokumen terkait suatu equipment:
SELECT d.judul, d.tipe_dokumen, d.ru, d.uploaded_at, t.link_type
FROM doc_registry d
JOIN doc_tag_links t ON d.id = t.doc_id
WHERE t.tag_number = 'XX-XXXX'
LIMIT 10

-- === CONTOH QUERY AGREGASI ===

-- Jumlah bad actor per RU:
SELECT ru, COUNT(*) AS jumlah
FROM bad_actor_monitoring
GROUP BY ru ORDER BY jumlah DESC

-- Total anggaran IRKAP per RU:
SELECT refinery_unit, SUM(nilai_anggaran_idr) AS total_idr, SUM(nilai_anggaran_usd) AS total_usd
FROM irkap_program
GROUP BY refinery_unit ORDER BY total_idr DESC

-- Rata-rata MTBF per RU:
SELECT b.ru, ROUND(AVG(b.mtbf)::numeric, 2) AS avg_mtbf, COUNT(*) AS jumlah_equipment
FROM boc b WHERE b.mtbf > 0
GROUP BY b.ru ORDER BY avg_mtbf ASC

-- Top 10 equipment dengan bad actor terbanyak:
SELECT tag_number, COUNT(*) AS jumlah
FROM bad_actor_monitoring
GROUP BY tag_number ORDER BY jumlah DESC LIMIT 10

-- Jumlah equipment per criticality:
SELECT criticality, COUNT(*) AS jumlah
FROM master_data_equipment
GROUP BY criticality ORDER BY criticality

-- Equipment dengan sisa umur pipeline terpendek:
SELECT tag_number, refinery_unit, rem_life_years, fluida_service
FROM pipeline_inspection
WHERE rem_life_years IS NOT NULL
ORDER BY rem_life_years ASC LIMIT 10

-- Jumlah work order per status:
SELECT system_status, COUNT(*) AS jumlah
FROM sap_work_orders
GROUP BY system_status ORDER BY jumlah DESC

-- Total zero clamp yang masih terpasang per RU:
SELECT ru, COUNT(*) AS masih_terpasang
FROM zero_clamp
WHERE tanggal_dilepas IS NULL
GROUP BY ru ORDER BY masih_terpasang DESC

=== PEMETAAN ISTILAH BAHASA MANUSIA → KOLOM DATABASE ===

Jika user menyebut kondisi/status tanpa menyebut nama kolom/tabel,
petakan ke kolom yang relevan dan gunakan ILIKE '%nilai%':

"running" / "beroperasi" / "jalan"
  → boc.status, power_stream.status_operation,
    readiness_jetty.status_operation, readiness_tank.status_operational,
    readiness_spm.status_operation, critical_eqp_prim_sec.traffic_corrective

"standby" / "siaga"
  → boc.status, boc.hasil (ILIKE '%N+1%' atau '%N+2%')

"shutdown" / "mati" / "tidak beroperasi" / "off"
  → boc.status, power_stream.status_operation,
    readiness_jetty.status_operation

"kritis" / "critical"
  → master_data_equipment.criticality (A atau B),
    critical_eqp_prim_sec (tabel ini = equipment kritis)

"bad actor"
  → tabel bad_actor_monitoring

"ICU" / "integrity concern"
  → icu_monitoring.icu_status ILIKE '%ICU%'

"selesai" / "closed" / "complete"
  → bad_actor_monitoring.status, icu_monitoring.icu_status,
    irkap_program.status_prognosa, irkap_actual.status_step,
    sap_work_orders.system_status

"belum selesai" / "open" / "in progress" / "ongoing"
  → bad_actor_monitoring.status, icu_monitoring.icu_status,
    irkap_program.status_prognosa, irkap_actual.status_step,
    sap_work_orders.system_status

"expired" / "kadaluarsa" / "habis masa berlaku"
  → atg_monitoring.date_expired_atg < CURRENT_DATE,
    metering_monitoring.date_expired_metering < CURRENT_DATE,
    readiness_jetty.expired_tuks < CURRENT_DATE,
    readiness_spm.expired_laik_operasi < CURRENT_DATE

"rusak" / "masalah" / "bermasalah" / "gangguan" / "isu" / "problem"
  → bad_actor_monitoring (ada entri), icu_monitoring (ada entri),
    critical_eqp_prim_sec.highlight_issue IS NOT NULL

"berisiko" / "risiko tinggi" / "kritis"
  → bad_actor_monitoring + icu_monitoring + critical_eqp_prim_sec (criticality A/B)

"keandalan" / "reliability" / "andal"
  → bad_actor_monitoring.mtbf, boc.hasil, boc.mtbf

"aset" / "peralatan" / "alat" / "mesin"
  → master_data_equipment (tabel utama semua equipment)

"overview" / "ringkasan" / "rekap" / "summary" / "gambaran"
  → agregasi COUNT(*) per RU atau per domain

"highlight" / "perlu diperhatikan" / "prioritas"
  → equipment di bad_actor_monitoring + icu_monitoring + expired + N+0

"anggaran" / "budget" / "biaya" / "cost"
  → irkap_program.nilai_anggaran_idr, irkap_actual

"realisasi" / "aktual" / "progress anggaran"
  → irkap_actual.status_step, irkap_actual

"usia pakai" / "umur" / "sudah berapa lama"
  → boc.running_hours, master_data_equipment (tanggal install jika ada)

"sertifikat" / "sertifikasi" / "perizinan"
  → atg_monitoring.cert_no_atg + date_expired_atg,
    metering_monitoring.cert_no_metering + date_expired_metering

"PAF" / "plant availability" / "ketersediaan kilang" / "availability"
  → paf (target vs value, plan vs unplan per RU)

"isu PAF" / "gangguan operasi" / "penyebab unplanned"
  → issue_paf (issue per RU per bulan)

"kapasitas" / "limitasi" / "plant readiness" / "kapasitas operasi"
  → monitoring_operasi (design, minimal_capacity, actual, plant_readiness)

"rotor" / "spare rotor" / "kesiapan rotor"
  → rotor_monitoring (status_readiness_spare, status_workplan, readiness_rotor)

"ATG program" / "program ATG" / "rencana pasang ATG"
  → program_kerja_atg

"anggaran maintenance" / "budget maintenance" / "biaya perawatan"
  → anggaran_maintenance (nilai_usd per ru/tahun/kategori)

"TKDN" / "komponen dalam negeri" / "lokal konten"
  → tkdn (persentase, nominal, kdn per refinery_unit/tahun)

"RCPS" / "root cause" / "analisis akar masalah"
  → rcps + rcps_rekomendasi

"rekomendasi RCPS" / "tindak lanjut RCPS"
  → rcps_rekomendasi (rekomendasi, traffic, pic, target)

"utilitas" / "equipment utilitas"
  → jumlah_eqp_utl + critical_eqp_utl

"tanpa standby" / "single" / "tidak ada cadangan"
  → boc.hasil = 'N+0' ATAU boc.hasil = 'Single'

"redundan" / "ada cadangan" / "ada standby"
  → boc.hasil ILIKE '%N+1%' ATAU boc.hasil ILIKE '%N+2%'

"program kerja" / "rkap" / "anggaran"
  → irkap_program, irkap_actual

"inspeksi" / "inspection"
  → inspection_plan, pipeline_inspection

"readiness" / "kesiapan" / "laik operasi"
  → readiness_jetty, readiness_tank, readiness_spm

"workplan" / "rencana kerja" / "action plan"
  → workplan_jetty, workplan_tank, spm_workplan

"notifikasi" / "SAP notif"
  → sap_notifications

"work order" / "WO" / "pekerjaan"
  → sap_work_orders

"zero clamp" / "clamp sementara"
  → zero_clamp — cari yang tanggal_dilepas IS NULL untuk yang masih terpasang

"pipeline" / "pipa"
  → pipeline_inspection

"tangki" / "tank"
  → atg_monitoring, readiness_tank, workplan_tank

"jetty" / "dermaga"
  → readiness_jetty, workplan_jetty

"SPM" / "buoy"
  → readiness_spm, spm_workplan

"metering" / "meter"
  → metering_monitoring

"ATG" / "automatic tank gauge"
  → atg_monitoring

"dokumen" / "file" / "laporan" / "SOP"
  → doc_registry JOIN doc_tag_links

=== CONTOH PERTANYAAN EKSEKUTIF / MANAJERIAL ===

-- "Berapa total aset/equipment kita?" → total semua equipment di master_data_equipment
SELECT COUNT(*) AS total_equipment FROM master_data_equipment

-- "Ringkasan kondisi equipment per RU" → agregasi status per kilang
SELECT refinery_unit, COUNT(*) AS total,
  SUM(CASE WHEN LOWER(status_operation) LIKE '%running%' THEN 1 ELSE 0 END) AS running,
  SUM(CASE WHEN LOWER(status_operation) LIKE '%shutdown%' OR LOWER(status_operation) LIKE '%mati%' THEN 1 ELSE 0 END) AS shutdown
FROM power_stream GROUP BY refinery_unit ORDER BY total DESC

-- "Equipment mana yang paling berisiko?" → bad actor + ICU
SELECT COALESCE(b.tag_number, i.tag_no) AS tag, b.problem, b.status AS bad_actor_status, i.icu_status
FROM bad_actor_monitoring b
FULL OUTER JOIN icu_monitoring i ON b.tag_number = i.tag_no
WHERE b.tag_number IS NOT NULL OR i.tag_no IS NOT NULL
LIMIT 20

-- "Berapa equipment tanpa cadangan (rentan)?"
SELECT ru, COUNT(*) AS jumlah_rentan FROM boc
WHERE hasil IN ('N+0','Single') GROUP BY ru ORDER BY jumlah_rentan DESC

-- "Progress anggaran pemeliharaan tahun ini?"
SELECT refinery_unit,
  SUM(nilai_anggaran_idr) AS total_plan_idr,
  COUNT(*) AS jumlah_program
FROM irkap_program WHERE EXTRACT(YEAR FROM created_at) = EXTRACT(YEAR FROM CURRENT_DATE)
GROUP BY refinery_unit ORDER BY total_plan_idr DESC

-- "Berapa aset yang expired sertifikasinya?"
SELECT 'ATG' AS jenis, COUNT(*) AS jumlah FROM atg_monitoring WHERE date_expired_atg < CURRENT_DATE
UNION ALL
SELECT 'Metering', COUNT(*) FROM metering_monitoring WHERE date_expired_metering < CURRENT_DATE

=== ATURAN PENTING ===
- Hanya SELECT, tidak boleh INSERT/UPDATE/DELETE/DROP
- LIMIT maksimal 50
- SELALU gunakan ILIKE '%nilai%' untuk pencarian nilai teks — jangan exact match
- Filter RU/kilang/plant: gunakan kolom refinery_unit (nilai: 'RU II','RU III','RU IV','RU V','RU VI','RU VII'),
  kolom ru (nilai: 'RU II','RU III', dst), atau maintenance_plant (nilai: 'RU2','RU3','RU4','RU5','RU6','RU7') — tergantung tabel
- Kata "kilang", "plant", "refinery", "RU" semuanya merujuk hal yang sama → filter kolom yang sesuai per tabel
- Gunakan ILIKE '%RU IV%' atau ILIKE '%RU4%' sesuai format kolom di tabel yang bersangkutan
- JOIN selalu lewat kolom tag sesuai tabel (lihat daftar TAG di atas)
- Jika istilah ambigu, cari di semua kolom status yang relevan sekaligus dengan OR
- Jangan tanya nama tabel/kolom ke user — petakan sendiri dari konteks
"""

NEO4J_SCHEMA_FALLBACK = """
Knowledge Graph Neo4j — Schema Lengkap

=== RELASI UTAMA (Equipment sebagai hub) ===
(Equipment)-[:HAS_BAD_ACTOR]->(BadActor)
(Equipment)-[:HAS_ICU]->(ICUMonitoring)
(Equipment)-[:HAS_NOTIFICATION]->(SAPNotification)
(Equipment)-[:HAS_WORK_ORDER]->(SAPWorkOrder)
(Equipment)-[:HAS_BOC]->(BOC)
(Equipment)-[:HAS_IRKAP_PROGRAM]->(IRKAPProgram)
(Equipment)-[:HAS_IRKAP_ACTUAL]->(IRKAPActual)
(Equipment)-[:HAS_ATG]->(ATGMonitoring)
(Equipment)-[:HAS_METERING]->(MeteringMonitor)
(Equipment)-[:HAS_PIPELINE_INSPECTION]->(PipelineInspection)
(Equipment)-[:HAS_INSPECTION_PLAN]->(InspectionPlan)
(Equipment)-[:HAS_ZERO_CLAMP]->(ZeroClamp)
(Equipment)-[:HAS_READINESS]->(ReadinessJetty)
(Equipment)-[:HAS_READINESS]->(ReadinessTank)
(Equipment)-[:HAS_READINESS]->(ReadinessSPM)
(Equipment)-[:HAS_WORKPLAN]->(WorkplanJetty)
(Equipment)-[:HAS_WORKPLAN]->(WorkplanTank)
(Equipment)-[:HAS_WORKPLAN]->(WorkplanSPM)
(Equipment)-[:IS_CRITICAL]->(CriticalEquipment)
(Equipment)-[:HAS_POWER_STREAM]->(PowerStream)
(Document)-[:TERKAIT_DENGAN]->(Equipment)

=== RELASI DOMAIN (lintas node) ===
(SAPNotification)-[:GENERATED_WO]->(SAPWorkOrder)
(BadActor)-[:HAS_IRKAP]->(IRKAPProgram)
(IRKAPProgram)-[:HAS_ACTUAL]->(IRKAPActual)

=== NODE PROPERTIES ===

NODE: Equipment  [tag: e.tag_number — SELALU gunakan e.tag_number bukan e.equipment]
  tag_number, description, functional_location, maintenance_plant (kode RU),
  location, criticality (A=sangat kritis, B=kritis, C=kurang kritis, Z=tidak kritis),
  equipment_category, technical_obj_type, manufacturer, model_type, material

NODE: BadActor  [tag: ba.tag_number]
  tag_number, ru, status, problem, action_plan, progress,
  target_date, periode, action_plan_category, no_irkap

NODE: ICUMonitoring  [tag: icu.tag_no]
  tag_no, ru, icu_status (contoh: "ICU", "Non-ICU", "Closed"),
  issue, mitigation, mitigasi_category, permanent_solution,
  solution_category, progress, target_closed, report_date

NODE: SAPNotification  [tag: n.equipment]
  notification, equipment, notif_type, notif_date, system_status,
  description, order_no, functional_loc, location, criticality,
  planner_group, main_workctr, maint_plant

NODE: SAPWorkOrder  [tag: wo.equipment]
  order_no, equipment, order_type, created_on, bas_start_date,
  basic_fin_date, actual_finish, description, system_status, user_status,
  total_plan_cost, total_act_cost, priority, maint_act_type, plant

NODE: BOC  [tag: b.equipment]
  equipment, ru, area, unit, grup_equipment, status,
  frequency, running_hours, mttr, mtbf,
  hasil (N+0=tidak ada standby KRITIS, N+1=ada 1 standby, N+2=ada 2 standby, Single=tunggal tanpa grup)

NODE: IRKAPProgram  [tag: ip.equipment_tag_no]
  equipment_tag_no, refinery_unit, disiplin, kategori_rkap,
  no_program_kerja, type_equipment, program_kerja,
  status_step, status_prognosa, start_plan, finish_plan,
  nilai_anggaran_idr, nilai_anggaran_usd, top_risk, asset_integrity

NODE: IRKAPActual  [tag: ia.tag_no]
  tag_no, no_program, kategori_rkap, program_kerja, refinery_unit,
  area, disiplin, status_step, status_prognosa, current_step,
  notif_no, wo_no, pr, po, anggaran_idr, jadwal_pelaksanaan,
  actual_start1, actual_finish1, failure_impact, rekomendasi

NODE: ATGMonitoring  [tag: atg.tag_no_tangki]
  tag_no_tangki, tag_no_atg, refinery_unit,
  status_atg, status_interkoneksi_atg,
  cert_no_atg, date_expired_atg, remark, rtl, action_plan_category,
  status_rtl, month_update

NODE: MeteringMonitor  [tag: m.tag_number]
  tag_number, refinery_unit, status_metering,
  cert_no_metering, date_expired_metering, remark,
  rtl, action_plan_category, status_rtl, month_update

NODE: PipelineInspection  [tag: pi.tag_number]
  tag_number, refinery_unit, area, unit, fluida_service, nps,
  from_location, to_location, last_inspection_date, next_inspection_date,
  last_measured_thickness, rem_life_years, jumlah_temporary_repair, remarks

NODE: InspectionPlan  [tag: insp.tag_no_ln]
  tag_no_ln, refinery_unit, area, unit, type_equipment, type_inspection,
  type_pekerjaan, due_date, plan_date, actual_date,
  result_remaining_life, result_visual, grand_result

NODE: ZeroClamp  [tag: zc.tag_no_ln]
  tag_no_ln, ru, area, unit, services, description,
  type_damage, posisi, type_perbaikan,
  tanggal_dipasang, tanggal_dilepas, tanggal_rencana_perbaikan,
  status, remarks, no_irkap

NODE: ReadinessJetty / ReadinessTank / ReadinessSPM  [tag: r.tag_no / r.tag_number]
  refinery_unit, area, unit, status_operation,
  status_laik_operasi, expired_laik_operasi, remark, month_update
  (ReadinessTank juga punya: type_tangki, service_tangki, prioritas,
   atg_certification_validity, status_coi, internal_inspection)

NODE: WorkplanJetty / WorkplanTank / WorkplanSPM  [tag: wp.tag_no]
  refinery_unit, area, unit, item, status_item,
  remark, rtl_action_plan, action_plan_category, target, status_rtl, month_update

NODE: CriticalEquipment  [tag: crit.equipment]
  equipment, refinery_unit, unit_proses, highlight_issue,
  corrective_action, target_corrective, traffic_corrective,
  mitigasi_action, target_mitigasi, traffic_mitigasi

NODE: PowerStream  [tag: ps.equipment]
  equipment, refinery_unit, type_equipment, status_operation,
  status_n0, unit_measurement, desain, kapasitas_max,
  average_actual, remark, date_update

=== CONTOH CYPHER QUERY ===

-- Equipment critical A tanpa standby (BOC N+0):
MATCH (e:Equipment)-[:HAS_BOC]->(b:BOC)
WHERE e.criticality = 'A' AND b.hasil = 'N+0'
RETURN e.tag_number, e.description, e.maintenance_plant, b.area, b.hasil LIMIT 20

-- Equipment yang punya bad actor DAN ICU monitoring:
MATCH (e:Equipment)-[:HAS_BAD_ACTOR]->(ba:BadActor)
MATCH (e)-[:HAS_ICU]->(icu:ICUMonitoring)
RETURN e.tag_number, e.description, ba.status, icu.icu_status LIMIT 20

-- Equipment dengan program IRKAP dan actual-nya:
MATCH (e:Equipment)-[:HAS_IRKAP_PROGRAM]->(ip:IRKAPProgram)
OPTIONAL MATCH (ip)-[:HAS_ACTUAL]->(ia:IRKAPActual)
RETURN e.tag_number, e.description, ip.program_kerja, ip.status_prognosa, ia.current_step LIMIT 20

-- Cek status SAP notification dan work order terkait:
MATCH (e:Equipment)-[:HAS_NOTIFICATION]->(n:SAPNotification)
OPTIONAL MATCH (n)-[:GENERATED_WO]->(wo:SAPWorkOrder)
RETURN e.tag_number, n.notification, n.description, wo.order_no, wo.system_status LIMIT 20

-- Equipment dengan MTBF terendah:
MATCH (e:Equipment)-[:HAS_BOC]->(b:BOC)
WHERE b.mtbf IS NOT NULL AND b.mtbf > 0
RETURN e.tag_number, e.description, b.mtbf, b.mttr ORDER BY b.mtbf ASC LIMIT 10

-- Equipment yang masuk bad actor per RU:
MATCH (e:Equipment)-[:HAS_BAD_ACTOR]->(ba:BadActor)
WHERE ba.ru IS NOT NULL
RETURN ba.ru, count(ba) AS jumlah ORDER BY jumlah DESC

-- Status readiness equipment jetty:
MATCH (e:Equipment)-[:HAS_READINESS]->(r:ReadinessJetty)
RETURN e.tag_number, e.description, r.status_operation, r.refinery_unit LIMIT 20

-- Zero clamp yang masih terpasang:
MATCH (e:Equipment)-[:HAS_ZERO_CLAMP]->(zc:ZeroClamp)
WHERE zc.tanggal_dilepas IS NULL
RETURN e.tag_number, zc.area, zc.type_damage, zc.tanggal_dipasang, zc.status LIMIT 20

=== PEMETAAN ISTILAH BAHASA MANUSIA → NODE/PROPERTY GRAPH ===

Jika user menyebut kondisi tanpa tahu nama property/node:

"running" / "beroperasi"        → BOC.status, PowerStream.status_operation CONTAINS 'running' (case-insensitive)
"standby"                       → BOC.hasil CONTAINS 'N+1' atau 'N+2'
"tanpa standby" / "kritis sekali" → BOC.hasil = 'N+0'
"kritis"                        → Equipment.criticality = 'A' atau 'B'
"bad actor"                     → node BadActor (Equipment HAS_BAD_ACTOR)
"ICU" / "integrity concern"     → node ICUMonitoring (Equipment HAS_ICU)
"selesai" / "closed"            → status/status_prognosa/status_step CONTAINS 'close' atau 'selesai'
"belum selesai" / "open"        → status property IS NOT NULL (biarkan LLM interpretasi)
"expired"                       → property date_expired < date('today')
"program kerja" / "rkap"        → node IRKAPProgram (Equipment HAS_IRKAP_PROGRAM)
"inspeksi"                      → node InspectionPlan atau PipelineInspection
"readiness" / "laik operasi"    → node ReadinessJetty/ReadinessTank/ReadinessSPM
"workplan" / "rencana"          → node WorkplanJetty/WorkplanTank/WorkplanSPM
"notifikasi SAP"                → node SAPNotification
"work order"                    → node SAPWorkOrder
"zero clamp"                    → node ZeroClamp (yang tanggal_dilepas IS NULL = masih terpasang)
"pipeline" / "pipa"             → node PipelineInspection
"metering"                      → node MeteringMonitor
"ATG" / "tangki"                → node ATGMonitoring atau ReadinessTank
"dokumen"                       → node Document (Document TERKAIT_DENGAN Equipment)

Untuk nilai ambigu: gunakan toLower() dan CONTAINS, contoh:
  WHERE toLower(b.status) CONTAINS 'running'
  WHERE toLower(icu.icu_status) CONTAINS 'icu'

=== ATURAN PENTING ===
- SELALU gunakan e.tag_number untuk Equipment (BUKAN e.equipment)
- Arah relasi SELALU (Equipment)-[:REL]->(Node), bukan sebaliknya
- Untuk Document: (Document)-[:TERKAIT_DENGAN]->(Equipment)
- Untuk domain relation: (BadActor)-[:HAS_IRKAP]->(IRKAPProgram) dan (IRKAPProgram)-[:HAS_ACTUAL]->(IRKAPActual)
- Gunakan toLower() + CONTAINS untuk filter nilai teks — jangan exact match kecuali criticality
- Selalu tambahkan LIMIT
"""

# ─── Graph Context Helper ─────────────────────────────────────────────────────

def get_equipment_context_from_graph(tag: str) -> dict:
    """Get all connected data for a tag from Neo4j."""
    try:
        from neo4j_sync import get_driver
        with get_driver() as driver:
            with driver.session() as session:
                result = session.run("""
                    MATCH (e:Equipment {tag_number: $tag})
                    OPTIONAL MATCH (e)-[:HAS_BAD_ACTOR]->(ba:BadActor)
                    OPTIONAL MATCH (e)-[:HAS_ICU]->(icu:ICUMonitoring)
                    OPTIONAL MATCH (e)-[:HAS_NOTIFICATION]->(n:SAPNotification)
                    OPTIONAL MATCH (e)-[:HAS_WORK_ORDER]->(wo:SAPWorkOrder)
                    OPTIONAL MATCH (e)-[:HAS_BOC]->(boc:BOC)
                    OPTIONAL MATCH (e)-[:HAS_IRKAP_PROGRAM]->(irkap:IRKAPProgram)
                    OPTIONAL MATCH (e)-[:HAS_IRKAP_ACTUAL]->(irkap_a:IRKAPActual)
                    OPTIONAL MATCH (e)-[:HAS_ATG]->(atg:ATGMonitoring)
                    OPTIONAL MATCH (e)-[:HAS_METERING]->(meter:MeteringMonitor)
                    OPTIONAL MATCH (e)-[:HAS_PIPELINE_INSPECTION]->(pipe:PipelineInspection)
                    OPTIONAL MATCH (e)-[:HAS_INSPECTION_PLAN]->(insp:InspectionPlan)
                    OPTIONAL MATCH (e)-[:HAS_ZERO_CLAMP]->(zc:ZeroClamp)
                    OPTIONAL MATCH (e)-[:HAS_READINESS]->(ready)
                    OPTIONAL MATCH (e)-[:HAS_WORKPLAN]->(wp)
                    OPTIONAL MATCH (e)-[:IS_CRITICAL]->(crit:CriticalEquipment)
                    OPTIONAL MATCH (e)-[:HAS_POWER_STREAM]->(ps:PowerStream)
                    OPTIONAL MATCH (doc:Document)-[:TERKAIT_DENGAN]->(e)
                    RETURN e,
                        collect(DISTINCT ba)[..5] as bad_actors,
                        collect(DISTINCT icu)[..5] as icus,
                        collect(DISTINCT n)[..5] as notifications,
                        collect(DISTINCT wo)[..5] as work_orders,
                        collect(DISTINCT boc)[..3] as bocs,
                        collect(DISTINCT irkap)[..3] as irkaps,
                        collect(DISTINCT irkap_a)[..3] as irkap_actuals,
                        collect(DISTINCT atg)[..3] as atgs,
                        collect(DISTINCT meter)[..3] as meterings,
                        collect(DISTINCT pipe)[..3] as pipelines,
                        collect(DISTINCT insp)[..3] as inspection_plans,
                        collect(DISTINCT zc)[..3] as zero_clamps,
                        collect(DISTINCT ready)[..3] as readiness,
                        collect(DISTINCT wp)[..3] as workplans,
                        collect(DISTINCT crit)[..2] as critical,
                        collect(DISTINCT ps)[..2] as power_streams,
                        collect(DISTINCT doc)[..5] as documents
                """, {"tag": tag})
                row = result.single()
                if not row:
                    return None
                return dict(row)
    except Exception:
        return None


def _format_graph_context(tag: str, ctx: dict) -> str:
    """Format Neo4j graph context into readable text for LLM."""
    lines = [f"Data Knowledge Graph untuk equipment {tag}:"]
    e = dict(ctx.get("e", {})) if ctx.get("e") else {}
    if e:
        lines.append(f"- Deskripsi: {e.get('description', '—')}")
        lines.append(f"- Plant/RU: {e.get('maintenance_plant', '—')}")
        lines.append(f"- Criticality: {e.get('criticality', '—')}")

    for key, label in [
        ("bad_actors", "Bad Actor"), ("icus", "ICU Monitoring"),
        ("notifications", "SAP Notifications"), ("work_orders", "SAP Work Orders"),
        ("bocs", "BOC"), ("irkaps", "IRKAP Program"), ("irkap_actuals", "IRKAP Actual"),
        ("atgs", "ATG Monitoring"), ("meterings", "Metering"),
        ("pipelines", "Pipeline Inspection"), ("inspection_plans", "Inspection Plan"),
        ("zero_clamps", "Zero Clamp"), ("readiness", "Readiness"),
        ("workplans", "Workplan"), ("critical", "Critical Equipment"),
        ("power_streams", "Power Stream"), ("documents", "Dokumen Terkait"),
    ]:
        items = ctx.get(key, [])
        if items:
            lines.append(f"\n{label} ({len(items)} record):")
            for item in items[:3]:
                d = dict(item)
                # Show first 3 key-value pairs
                kv = [f"{k}: {v}" for k, v in list(d.items())[:3] if v and k not in ('tag_number', 'equipment', 'tag_no', 'tag_no_ln', 'tag_no_tangki', 'equipment_tag_no')]
                lines.append(f"  • {', '.join(kv)}")

    return "\n".join(lines)


def extract_tag_from_message(message: str) -> str | None:
    """Extract equipment tag pattern like XX-XXXX or similar from message."""
    pattern = r'\b[A-Z0-9]{2,}-[A-Z0-9][-A-Z0-9]*\b'
    matches = re.findall(pattern, message.upper())
    return matches[0] if matches else None


# ─── Query Rewriter ───────────────────────────────────────────────────────────

REWRITER_PROMPT = """Kamu adalah query normalizer untuk sistem data governance Pertamina.

Tugasmu: susun ulang pertanyaan user menjadi pertanyaan yang jelas, lengkap, dan mudah dipahami sistem.
Pengguna bisa siapa saja: engineer, supervisor, manager, hingga direktur — jadi istilah bisnis harus diterjemahkan ke istilah teknis yang sistem mengerti.

PANDUAN NORMALISASI:

1. PERBAIKI typo, singkatan, bahasa informal:
   "yg" → "yang", "blm" → "belum", "udh" → "sudah", "gak/ga" → "tidak"
   "critA" → "criticality A", "BA" → "bad actor", "WO" → "work order"
   "notif" → "notifikasi SAP", "insp" → "inspection plan"

2. TERJEMAHKAN istilah bisnis/eksekutif ke istilah teknis:
   "aset" / "peralatan" / "alat" / "mesin"        → "equipment"
   "kondisi aset" / "kesehatan aset"               → "status equipment dan bad actor dan ICU"
   "kinerja" / "performa" / "produktivitas"        → "status operasi equipment (running/standby/shutdown)"
   "risiko" / "berisiko tinggi"                    → "equipment bad actor atau criticality A atau ICU"
   "pemeliharaan" / "perawatan" / "maintenance"    → "work order dan inspection plan"
   "anggaran" / "budget" / "biaya"                 → "IRKAP program dan actual"
   "progress" / "realisasi"                        → "status IRKAP actual"
   "masalah" / "problem" / "gangguan" / "isu"      → "bad actor atau ICU monitoring"
   "keandalan" / "reliability" / "handal"          → "MTBF, bad actor, BOC standby"
   "kesiapan" / "readiness" / "laik"               → "readiness jetty/tank/SPM"
   "ringkasan" / "summary" / "overview" / "rekap"  → "total agregasi per kategori/RU"
   "highlight" / "perlu diperhatikan"              → "equipment dengan bad actor atau ICU atau expired"
   "sudah berapa lama" / "usia"                    → "tanggal dipasang atau running hours"
   "paling banyak masalah"                         → "equipment dengan jumlah bad actor atau notifikasi terbanyak"
   "tidak ada cadangan" / "rentan"                 → "BOC hasil N+0 atau Single"

3. LENGKAPI konteks dari history percakapan:
   "berapa jumlahnya?" + history ada tentang bad actor RU IV
   → "Berapa jumlah bad actor di RU IV?"

   "yang itu detail dong" + history ada tag XX-XXXX
   → "Tampilkan detail lengkap equipment XX-XXXX"

3. NORMALISASI nama RU / kilang / plant ke bentuk lengkap:
   RU II / RU2 / Dumai / kilang Dumai / plant Dumai       → "RU II Dumai"
   RU III / RU3 / Plaju / kilang Plaju / plant Plaju       → "RU III Plaju"
   RU IV / RU4 / Cilacap / kilang Cilacap / plant Cilacap  → "RU IV Cilacap"
   RU V / RU5 / Balikpapan / kilang Balikpapan             → "RU V Balikpapan"
   RU VI / RU6 / Balongan / kilang Balongan                → "RU VI Balongan"
   RU VII / RU7 / Kasim / kilang Kasim                     → "RU VII Kasim"
   "kilang" / "plant" / "refinery" tanpa nama spesifik → pertahankan konteks, jangan ubah

4. KONVERSI waktu relatif ke konteks yang jelas:
   "bulan ini"  → "bulan Juni 2026"
   "tahun ini"  → "tahun 2026"
   "tahun lalu" → "tahun 2025"
   "terbaru"    → "paling baru berdasarkan tanggal"
   "sudah lewat" / "expired" → "tanggal sudah melewati hari ini (2026-06-12)"

5. PERTAHANKAN semua info penting dari pertanyaan asli:
   tag number, nama equipment, RU, angka, kondisi yang disebut

6. Jika pertanyaan sudah jelas → kembalikan apa adanya tanpa ubah

OUTPUT: kembalikan HANYA pertanyaan yang sudah dinormalisasi, tanpa penjelasan apapun."""


def _needs_rewrite(message: str, history: list) -> bool:
    """Cek apakah pertanyaan perlu di-rewrite. Skip kalau sudah jelas."""
    # Pertanyaan sangat pendek atau ambigu → perlu rewrite
    if len(message.strip()) < 10:
        return True
    # Ada history → mungkin ada referensi konteks ("yang itu", "berapa", dll)
    if history:
        AMBIGUOUS = ["itu", "tadi", "tersebut", "nya", "mereka", "dia",
                     "berapa", "siapa", "mana", "kapan", "gimana"]
        if any(w in message.lower().split() for w in AMBIGUOUS):
            return True
    # Ada singkatan / typo umum → perlu rewrite
    INFORMAL = ["yg", "utk", "dg", "dgn", "blm", "udh", "gak", "ga ",
                "critA", "critB", "BA ", " WO ", "IRKAP", "bulan ini",
                "tahun ini", "tahun lalu", "terbaru", "terbesar"]
    if any(w in message for w in INFORMAL):
        return True
    return False


def rewrite_query(message: str, history: list) -> str:
    """Normalisasi pertanyaan user sebelum diproses sistem. Skip jika tidak perlu."""
    if not _needs_rewrite(message, history):
        return message

    history_text = ""
    if history:
        history_text = "\n".join([
            f"{m['role']}: {m['content'][:150]}" for m in history[-4:]
        ])

    try:
        resp = client.chat.completions.create(
            model=MODEL,
            max_tokens=200,
            temperature=0,
            messages=[
                {"role": "system", "content": REWRITER_PROMPT},
                {"role": "user", "content": f"History:\n{history_text}\n\nPertanyaan: {message}"}
            ]
        )
        rewritten = resp.choices[0].message.content.strip()
        if rewritten:
            return rewritten
    except Exception:
        pass
    return message


# ─── Intent Router ────────────────────────────────────────────────────────────

def detect_intent(message: str, history: list) -> str:
    """
    Deteksi intent: 'rag', 'sql', 'graph', 'hybrid', 'general'
    """
    msg_lower = message.lower()

    # Domain keywords per tabel — kalau menyebut 2+ domain → langsung graph
    DOMAIN_KEYWORDS = [
        ["bad actor", "badactor"],
        ["icu", "integrity"],
        ["irkap", "program kerja"],
        ["boc", "biaya operasi"],
        ["atg", "tangki"],
        ["metering"],
        ["sap notif", "notifikasi sap"],
        ["work order", "sap wo"],
        ["inspection", "inspeksi"],
        ["readiness", "kesiapan"],
        ["workplan", "rencana kerja"],
        ["pipeline"],
        ["critical equipment", "equipment kritis"],
    ]
    domain_hits = sum(
        1 for kws in DOMAIN_KEYWORDS if any(kw in msg_lower for kw in kws)
    )
    if domain_hits >= 2:
        return "graph"

    # Analytic keywords → graph
    ANALYTIC_KEYWORDS = [
        "korelasi", "hubungan", "lintas tabel", "analisa", "analisis",
        "scorecard", "ranking", "prioritas", "efektivitas", "bandingkan",
        "gap", "root cause", "paradoks", "rekomendasi", "prediksi",
        "mendalam", "komprehensif", "sekaligus", "bersamaan"
    ]
    if any(kw in msg_lower for kw in ANALYTIC_KEYWORDS):
        return "graph"

    # Tag detected → graph
    if extract_tag_from_message(message):
        return "graph"

    # Agregasi + 1 domain → sql (SQL lebih tepat untuk hitung-hitungan)
    AGGREGATION_KEYWORDS = [
        "berapa", "jumlah", "total", "rata-rata", "average", "terbanyak",
        "terkecil", "terbesar", "tertinggi", "terendah", "count", "sum"
    ]
    if domain_hits == 1 and any(kw in msg_lower for kw in AGGREGATION_KEYWORDS):
        return "sql"

    # Document keywords → rag
    DOC_KEYWORDS = ["dokumen", "sop", "prosedur", "manual", "laporan", "pdf", "upload"]
    if any(kw in msg_lower for kw in DOC_KEYWORDS):
        return "rag"

    # Fallback: let LLM decide for ambiguous cases
    history_text = "\n".join([
        f"{m['role']}: {m['content'][:100]}" for m in history[-4:]
    ]) if history else ""

    try:
        resp = client.chat.completions.create(
            model=MODEL,
            max_tokens=20,
            messages=[{
                "role": "user",
                "content": f"""Klasifikasikan pertanyaan berikut ke salah satu intent:
- 'rag': tanya isi dokumen, SOP, prosedur, laporan, manual
- 'sql': tanya data angka, jumlah, status, list dari SATU tabel saja
- 'graph': tanya relasi/analisis lintas tabel, kondisi equipment dari berbagai sumber
- 'hybrid': butuh kombinasi dokumen DAN data
- 'general': sapaan, pertanyaan umum

Konteks percakapan:
{history_text}

Pertanyaan: {message}

Jawab HANYA satu kata: rag / sql / graph / hybrid / general"""
            }]
        )
        intent = resp.choices[0].message.content.strip().lower()
        if intent not in ("rag", "sql", "graph", "hybrid", "general"):
            intent = "sql"
        return intent
    except Exception:
        return "sql"

# ─── RAG Handler ──────────────────────────────────────────────────────────────

def handle_rag(message: str, filters: dict = None, status_cb=None) -> dict:
    """Vector search dokumen lalu generate jawaban."""
    def _emit(step, label):
        if status_cb:
            try: status_cb({"step": step, "label": label})
            except: pass

    from embedder import get_embedding
    from db import vector_search

    _emit("embed", "Membuat embedding pertanyaan...")
    query_emb = get_embedding(message)
    _emit("search", "Mencari dokumen relevan di vector database...")
    results = vector_search(
        query_embedding=query_emb,
        ru=filters.get("ru") if filters else None,
        limit=5,
        threshold=0.3
    )

    if not results:
        return {
            "type": "rag",
            "answer": (
                "Saya tidak menemukan dokumen yang relevan untuk pertanyaan ini di perpustakaan dokumen.\n\n"
                "Beberapa kemungkinan:\n"
                "- Dokumen terkait belum diunggah ke sistem\n"
                "- Coba gunakan kata kunci yang berbeda\n"
                "- Atau tanyakan langsung tentang data equipment, misalnya: "
                "*\"Berapa jumlah equipment di RU IV?\"* atau *\"Cari tag 10-P-101\"*"
            ),
            "sources": [],
            "context_used": ""
        }

    # Susun context dari chunks
    context_parts = []
    sources = []
    for r in results:
        r = dict(r)
        loc = f"Hal. {r['halaman']}" if r.get("halaman") else \
              f"Slide {r['slide_number']}" if r.get("slide_number") else \
              f"Sheet: {r['sheet_name']}" if r.get("sheet_name") else ""
        context_parts.append(
            f"[Dokumen: {r['judul']} | {r['ru'] or ''} | {loc}]\n{r['content']}"
        )
        sources.append({
            "doc_id":   r["doc_id"],
            "judul":    r["judul"],
            "ru":       r["ru"],
            "tipe":     r["tipe_dokumen"],
            "halaman":  r.get("halaman"),
            "score":    round(float(r["score"]), 3),
            "preview":  r["content"][:150]
        })

    context = "\n\n---\n\n".join(context_parts)

    _emit("generate", "Merangkum jawaban dari dokumen...")
    answer = _stream_generate(
        messages=[
            {
                "role": "system",
                "content": """Kamu adalah asisten data governance Pertamina yang membantu menjawab pertanyaan berdasarkan dokumen internal.
Jawab dalam Bahasa Indonesia. Gunakan konteks dokumen yang diberikan.
Jika informasi tidak ada di konteks, katakan dengan jelas.
Selalu sebutkan sumber dokumen di akhir jawaban."""
            },
            {
                "role": "user",
                "content": f"Konteks dokumen:\n{context}\n\nPertanyaan: {message}"
            }
        ],
        max_tokens=800,
        status_cb=status_cb
    )

    return {
        "type": "rag",
        "answer": answer,
        "sources": sources,
        "context_used": context[:500]
    }

# ─── Session Memory ───────────────────────────────────────────────────────────

_session_store: dict = {}
_SESSION_MAX_MESSAGES = 20
_SESSION_TTL = 3600  # 1 jam


def _get_session_history(session_id: str) -> list:
    entry = _session_store.get(session_id)
    if not entry:
        return []
    if time.time() - entry["last_active"] > _SESSION_TTL:
        del _session_store[session_id]
        return []
    return entry["history"]


def _save_session_history(session_id: str, history: list):
    _session_store[session_id] = {
        "history": history[-_SESSION_MAX_MESSAGES:],
        "last_active": time.time()
    }


def _cleanup_sessions():
    now = time.time()
    expired = [sid for sid, e in _session_store.items() if now - e["last_active"] > _SESSION_TTL]
    for sid in expired:
        del _session_store[sid]


# ─── SQL Handler ──────────────────────────────────────────────────────────────

def _generate_sql(message: str, previous_sql: str = None, error: str = None) -> str:
    """Generate SQL, dengan retry context kalau ada error sebelumnya."""
    extra = ""
    if previous_sql and error:
        extra = f"\n\nQuery sebelumnya GAGAL:\n{previous_sql}\nError: {error}\nPerbaiki query tersebut."

    resp = client.chat.completions.create(
        model=MODEL,
        max_tokens=500,
        messages=[
            {
                "role": "system",
                "content": f"""Kamu adalah SQL generator untuk database PostgreSQL Pertamina.
Generate SQL query yang menjawab pertanyaan user berdasarkan schema dan contoh query berikut.

{_get_db_schema()}

INSTRUKSI:
- Ikuti contoh JOIN query di atas sebagai referensi pola yang benar
- Kembalikan HANYA query SQL, tanpa penjelasan, tanpa markdown backtick"""
            },
            {
                "role": "user",
                "content": message + extra
            }
        ]
    )
    sql = resp.choices[0].message.content.strip()
    return re.sub(r'```sql|```', '', sql).strip()


def handle_sql(message: str, history: list = None, status_cb=None) -> dict:
    """Generate SQL dari pertanyaan, execute, lalu format jawaban. Retry 1x jika gagal."""
    def _emit(step, label):
        if status_cb:
            try: status_cb({"step": step, "label": label})
            except: pass

    from db_equipment import get_conn

    _emit("gen_sql", "Membuat query SQL...")
    sql = _generate_sql(message)

    if not sql.upper().startswith("SELECT"):
        return {
            "type": "sql",
            "answer": (
                "Maaf, saya hanya bisa membaca data (query SELECT) — "
                "tidak bisa mengubah, menghapus, atau menambah data.\n\n"
                "Coba ajukan pertanyaan seperti:\n"
                "- *\"Berapa total equipment di RU IV?\"*\n"
                "- *\"Tampilkan work order yang statusnya OPEN\"*"
            ),
            "sql": sql,
            "data": [],
            "error": "Non-SELECT query blocked"
        }

    # Execute — retry 1x jika error
    _emit("exec_sql", "Menjalankan query ke database...")
    data = []
    last_error = None
    for attempt in range(2):
        try:
            with get_conn() as conn:
                rows = conn.execute(sql).fetchall()
                data = [dict(r) for r in rows]
            last_error = None
            break
        except Exception as e:
            last_error = str(e)
            if attempt == 0:
                logging.warning(f"[SQL RETRY] attempt 1 failed: {e}")
                _emit("retry_sql", "Memperbaiki query SQL dan mencoba ulang...")
                sql = _generate_sql(message, previous_sql=sql, error=last_error)
                if not sql.upper().startswith("SELECT"):
                    break

    if last_error:
        return {
            "type": "sql",
            "answer": (
                "Pertanyaan ini tidak berhasil diproses ke database.\n\n"
                "Kemungkinan penyebab:\n"
                "- Pertanyaan terlalu ambigu atau menyebut hal yang tidak ada di sistem\n"
                "- Coba lebih spesifik, misalnya sebutkan nama RU, tag number, atau status yang dimaksud\n\n"
                f"*Detail error: {last_error}*"
            ),
            "sql": sql,
            "data": [],
            "error": last_error
        }

    if not data:
        return {
            "type": "sql",
            "answer": (
                "Data tidak ditemukan untuk pertanyaan ini.\n\n"
                "Kemungkinan:\n"
                "- Filter yang kamu gunakan terlalu spesifik (RU, status, tag tidak cocok)\n"
                "- Data memang belum tersedia di sistem\n\n"
                "Coba ubah filter atau gunakan kata kunci yang lebih umum."
            ),
            "sql": sql,
            "data": [],
            "error": None
        }

    data_preview = json.dumps(data[:10], default=str, ensure_ascii=False)
    _emit("format_sql", "Memformat hasil data...")
    fmt_answer = _stream_generate(
        messages=[
            {
                "role": "system",
                "content": """Kamu adalah asisten data analyst Pertamina.
Formatkan hasil query database berikut menjadi jawaban yang mudah dibaca dalam Bahasa Indonesia.
Gunakan bullet points atau tabel teks jika data banyak.
Sertakan insight singkat jika relevan."""
            },
            {
                "role": "user",
                "content": f"Pertanyaan: {message}\n\nHasil query ({len(data)} baris):\n{data_preview}"
            }
        ],
        max_tokens=600,
        status_cb=status_cb
    )

    return {
        "type": "sql",
        "answer": fmt_answer,
        "sql": sql,
        "data": data[:20],
        "total_rows": len(data),
        "error": None
    }

# ─── SQL Fallback untuk tag yang tidak ada di Neo4j ──────────────────────────

def _handle_tag_sql_fallback(tag: str, message: str) -> dict | None:
    """Query semua tabel PostgreSQL untuk tag yang tidak ada di Neo4j."""
    from db_equipment import get_conn

    TAG_TABLES = [
        ("master_data_equipment",  "equipment"),
        ("bad_actor_monitoring",   "tag_number"),
        ("icu_monitoring",         "tag_no"),
        ("boc",                    "equipment"),
        ("sap_notifications",      "equipment"),
        ("sap_work_orders",        "equipment"),
        ("atg_monitoring",         "tag_no_tangki"),
        ("metering_monitoring",    "tag_number"),
        ("pipeline_inspection",    "tag_number"),
        ("zero_clamp",             "tag_no_ln"),
        ("inspection_plan",        "tag_no_ln"),
        ("irkap_program",          "equipment_tag_no"),
        ("irkap_actual",           "tag_no"),
        ("readiness_jetty",        "tag_no"),
        ("readiness_tank",         "tag_number"),
        ("readiness_spm",          "tag_no"),
        ("workplan_jetty",         "tag_no"),
        ("workplan_tank",          "tag_no"),
        ("spm_workplan",           "tag_no"),
        ("critical_eqp_prim_sec",  "equipment"),
        ("power_stream",           "equipment"),
    ]

    found = {}
    try:
        with get_conn() as conn:
            for table, col in TAG_TABLES:
                try:
                    rows = conn.execute(
                        f"SELECT * FROM {table} WHERE {col} = %s LIMIT 5",
                        (tag,)
                    ).fetchall()
                    if rows:
                        found[table] = [dict(r) for r in rows]
                except Exception:
                    pass
    except Exception:
        return None

    if not found:
        return None

    # Format semua data yang ditemukan
    context = f"Data ditemukan di PostgreSQL untuk tag {tag}:\n"
    for table, rows in found.items():
        context += f"\n[{table}] ({len(rows)} baris):\n"
        context += json.dumps(rows[:3], default=str, ensure_ascii=False)

    fmt_resp = client.chat.completions.create(
        model=MODEL,
        max_tokens=600,
        messages=[
            {
                "role": "system",
                "content": """Kamu adalah asisten data governance Pertamina.
Tag equipment ini tidak ditemukan di Knowledge Graph tapi datanya ada di database.
Jawab pertanyaan berdasarkan data yang ditemukan. Jawab dalam Bahasa Indonesia."""
            },
            {
                "role": "user",
                "content": f"Pertanyaan: {message}\n\n{context}"
            }
        ]
    )

    return {
        "type": "sql",
        "answer": fmt_resp.choices[0].message.content,
        "sql": f"SQL fallback for tag: {tag} (found in: {', '.join(found.keys())})",
        "data": {t: rows for t, rows in list(found.items())[:5]},
        "tag": tag,
        "note": "Data dari PostgreSQL (tag belum tersinkron ke Knowledge Graph)"
    }


# ─── Graph Handler ────────────────────────────────────────────────────────────

def _get_cypher_prompt() -> str:
    return f"""Kamu adalah Cypher query generator untuk Neo4j Knowledge Graph Pertamina.

{NEO4J_SCHEMA_FALLBACK}
{_build_neo4j_categorical_prompt()}

INSTRUKSI:
- Generate Cypher query yang menjawab pertanyaan user
- Ikuti contoh query di atas sebagai referensi pola yang benar
- Gunakan nilai aktual dari daftar di atas untuk filter (jangan tebak)
- SELALU gunakan e.tag_number untuk Equipment node
- Arah relasi SELALU (Equipment)-[:REL]->(Node) sesuai schema
- Kembalikan HANYA Cypher query, tanpa penjelasan, tanpa markdown backtick"""


def handle_graph(message: str, status_cb=None) -> dict:
    """Generate Cypher query, execute di Neo4j, format jawaban.
    If an equipment tag is detected, use GraphRAG for rich context first."""
    def _emit(step, label):
        if status_cb:
            try: status_cb({"step": step, "label": label})
            except: pass

    try:
        from neo4j_sync import get_driver

        # Try GraphRAG first if tag detected
        tag = extract_tag_from_message(message)
        if tag:
            try:
                _emit("graph_tag", f"Mencari data equipment {tag} di Knowledge Graph...")
                graph_ctx = get_equipment_context_from_graph(tag)
                if graph_ctx:
                    _emit("format_graph", "Merangkum data dari Knowledge Graph...")
                    ctx_text = _format_graph_context(tag, graph_ctx)
                    graph_answer = _stream_generate(
                        messages=[
                            {
                                "role": "system",
                                "content": """Kamu adalah asisten data governance Pertamina.
Berikan jawaban komprehensif dalam Bahasa Indonesia berdasarkan data Knowledge Graph equipment berikut.
Sertakan informasi penting seperti status bad actor, notifikasi SAP, work order, program IRKAP, dll jika tersedia.
Jawab secara terstruktur dan informatif."""
                            },
                            {
                                "role": "user",
                                "content": f"Pertanyaan: {message}\n\n{ctx_text}"
                            }
                        ],
                        max_tokens=800,
                        status_cb=status_cb
                    )
                    return {
                        "type": "graph",
                        "answer": graph_answer,
                        "cypher": f"GraphRAG lookup for tag: {tag}",
                        "data": [],
                        "tag": tag
                    }
            except Exception:
                pass  # Fall back to Cypher generation

        # Generate Cypher (fallback or no tag) — retry 1x jika error
        _emit("gen_cypher", "Membuat Cypher query untuk Knowledge Graph...")
        def _run_cypher(prev_cypher=None, error=None):
            extra = ""
            if prev_cypher and error:
                extra = f"\n\nQuery sebelumnya GAGAL:\n{prev_cypher}\nError: {error}\nPerbaiki query tersebut."
            resp = client.chat.completions.create(
                model=MODEL,
                max_tokens=300,
                messages=[
                    {"role": "system", "content": _get_cypher_prompt()},
                    {"role": "user", "content": message + extra}
                ]
            )
            q = resp.choices[0].message.content.strip()
            return re.sub(r'```cypher|```', '', q).strip()

        cypher = _run_cypher()
        _emit("exec_cypher", "Menjalankan query di Knowledge Graph...")
        data = []
        last_error = None
        for attempt in range(2):
            try:
                with get_driver() as driver:
                    with driver.session() as session:
                        data = [dict(r) for r in session.run(cypher, timeout=20)]
                last_error = None
                break
            except Exception as e:
                last_error = str(e)
                if attempt == 0:
                    logging.warning(f"[CYPHER RETRY] attempt 1 failed: {e}")
                    _emit("retry_cypher", "Memperbaiki Cypher query...")
                    cypher = _run_cypher(prev_cypher=cypher, error=last_error)

        if last_error:
            return {
                "type": "graph",
                "answer": f"Query Knowledge Graph gagal setelah retry: {last_error}",
                "cypher": cypher,
                "data": [],
                "error": last_error
            }

        if not data:
            # Fallback ke SQL jika tag terdeteksi tapi tidak ada di graph
            if tag:
                sql_result = _handle_tag_sql_fallback(tag, message)
                if sql_result:
                    return sql_result
            return {
                "type": "graph",
                "answer": (
                    "Tidak ditemukan data di Knowledge Graph untuk pertanyaan ini.\n\n"
                    "Kemungkinan:\n"
                    "- Tag number belum tersinkron ke Knowledge Graph\n"
                    "- Coba cek di halaman *Sync Management* untuk memastikan tabel sudah tersinkron\n"
                    "- Atau tanyakan langsung ke database: *\"Cari data tag [nomor tag] di semua tabel\"*"
                ),
                "cypher": cypher,
                "data": []
            }

        data_text = json.dumps(data[:10], default=str, ensure_ascii=False)
        _emit("format_graph", "Memformat hasil dari Knowledge Graph...")
        graph_answer = _stream_generate(
            messages=[
                {
                    "role": "system",
                    "content": "Formatkan hasil Knowledge Graph query menjadi jawaban Bahasa Indonesia yang informatif."
                },
                {
                    "role": "user",
                    "content": f"Pertanyaan: {message}\n\nHasil ({len(data)} records):\n{data_text}"
                }
            ],
            max_tokens=500,
            status_cb=status_cb
        )

        return {
            "type": "graph",
            "answer": graph_answer,
            "cypher": cypher,
            "data": data[:10]
        }

    except Exception as e:
        import traceback, logging
        logging.error(f"[GRAPH ERROR] {e}\n{traceback.format_exc()}")
        return {
            "type": "graph",
            "answer": (
                "Knowledge Graph sedang tidak dapat diakses saat ini.\n\n"
                "Coba beberapa saat lagi, atau tanyakan hal yang sama menggunakan data dari database:\n"
                "misalnya *\"Berapa work order untuk tag [nomor tag]?\"*"
            ),
            "cypher": "",
            "data": [],
            "error": str(e)
        }

# ─── Hybrid Handler ───────────────────────────────────────────────────────────

def handle_hybrid(message: str, history: list = None, status_cb=None) -> dict:
    """Kombinasi RAG + SQL + Graph (jika tag terdeteksi), gabungkan hasilnya."""
    try:
        rag_result = handle_rag(message, status_cb=status_cb)
    except Exception:
        rag_result = {"answer": "", "sources": [], "data": []}
    try:
        sql_result = handle_sql(message, history, status_cb=status_cb)
    except Exception:
        sql_result = {"answer": "", "data": [], "sql": ""}

    # Gabungkan context
    combined_context = ""
    if rag_result.get("sources"):
        combined_context += f"**Dari dokumen:**\n{rag_result['answer']}\n\n"
    if sql_result.get("data"):
        combined_context += f"**Dari database:**\n{sql_result['answer']}"

    if not combined_context:
        combined_context = rag_result["answer"] or sql_result["answer"]

    # Also include graph context if tag detected
    graph_context_text = ""
    tag = extract_tag_from_message(message)
    if tag:
        try:
            if status_cb:
                try: status_cb({"step": "graph_tag", "label": f"Mencari data equipment {tag} di Knowledge Graph..."})
                except: pass
            graph_ctx = get_equipment_context_from_graph(tag)
            if graph_ctx:
                graph_context_text = _format_graph_context(tag, graph_ctx)
                combined_context += f"\n\n**Dari Knowledge Graph (equipment {tag}):**\n{graph_context_text[:2000]}"
        except Exception:
            pass

    # Final synthesis
    if status_cb:
        try: status_cb({"step": "synthesize", "label": "Menyintesis jawaban dari semua sumber..."})
        except: pass
    try:
        answer = _stream_generate(
            messages=[
                {
                    "role": "system",
                    "content": """Kamu adalah asisten data governance Pertamina senior.
Gabungkan informasi dari dokumen, database, dan Knowledge Graph untuk menjawab pertanyaan secara komprehensif.
Jawab dalam Bahasa Indonesia. Berikan insight yang berguna."""
                },
                {
                    "role": "user",
                    "content": f"Pertanyaan: {message}\n\nInformasi yang tersedia:\n{combined_context}"
                }
            ],
            max_tokens=800,
            status_cb=status_cb
        )
    except Exception as e:
        import traceback, logging
        logging.error(f"[HYBRID SYNTHESIS ERROR] {e}\n{traceback.format_exc()}")
        answer = combined_context or "Tidak dapat memproses pertanyaan saat ini."

    return {
        "type": "hybrid",
        "answer": answer,
        "sources": rag_result.get("sources", []),
        "sql": sql_result.get("sql", ""),
        "sql_data": sql_result.get("data", []),
        "total_rows": sql_result.get("total_rows", 0),
        "graph_tag": tag if graph_context_text else None
    }

# ─── General Handler ──────────────────────────────────────────────────────────

def handle_general(message: str, history: list = None, status_cb=None) -> dict:
    messages = [
        {
            "role": "system",
            "content": """Kamu adalah asisten data governance Pertamina DGS.
Pengguna bisa engineer, supervisor, manager, hingga direktur — jawab sesuai konteks tanpa jargon teknis berlebihan.

Bantu user dengan:
- Pertanyaan umum tentang equipment, maintenance, reliability, anggaran, kesiapan operasi
- Menjelaskan apa yang bisa ditanyakan ke sistem
- Memberikan panduan cara bertanya yang tepat

Contoh pertanyaan yang bisa dijawab sistem:
- "Berapa total aset kita di RU IV?"
- "Equipment mana yang paling berisiko?"
- "Bagaimana kondisi readiness jetty saat ini?"
- "Berapa equipment yang expired sertifikasinya?"
- "Cari semua informasi tentang tag 10-P-101"

Jika pertanyaan di luar konteks Pertamina (resep, berita umum, dll):
tolak dengan ramah, arahkan ke topik equipment/maintenance/data governance.

Jawab dalam Bahasa Indonesia. Singkat, jelas, tidak perlu menyebut nama tabel atau kolom database."""
        }
    ]
    if history:
        messages.extend(history[-6:])
    messages.append({"role": "user", "content": message})

    answer = _stream_generate(messages=messages, max_tokens=400, status_cb=status_cb)
    return {
        "type": "general",
        "answer": answer
    }

# ─── Main Chat Function ───────────────────────────────────────────────────────

def chat(message: str, history: list = None, filters: dict = None,
         session_id: str = None, status_cb=None) -> dict:
    """
    Entry point utama. Router ke handler yang tepat.
    history   : list of {role, content} dari frontend (opsional)
    filters   : {ru, tag_number}
    session_id: untuk server-side session memory
    status_cb : optional callable(dict) for real-time step updates
    """
    def _emit(step, label):
        if status_cb:
            try: status_cb({"step": step, "label": label})
            except: pass

    # Ambil history dari server jika ada session_id
    if session_id:
        server_history = _get_session_history(session_id)
        history = server_history if server_history else (history or [])
    elif not history:
        history = []

    # Layer 1: Rewrite — normalisasi pertanyaan
    _emit("rewrite", "Memahami dan menormalisasi pertanyaan...")
    clean_message = rewrite_query(message, history)

    _emit("intent", "Menentukan sumber data yang relevan...")
    intent = detect_intent(clean_message, history)

    _INTENT_LABELS = {
        "rag": "Mode: RAG Dokumen",
        "sql": "Mode: SQL Database",
        "graph": "Mode: Knowledge Graph",
        "hybrid": "Mode: Hybrid (semua sumber)",
        "general": "Mode: General"
    }
    _emit("intent_result", _INTENT_LABELS.get(intent, f"Mode: {intent}"))

    if intent == "rag":
        result = handle_rag(clean_message, filters, status_cb=status_cb)
    elif intent == "sql":
        result = handle_sql(clean_message, history, status_cb=status_cb)
    elif intent == "graph":
        try:
            result = handle_graph(clean_message, status_cb=status_cb)
            # If graph returned empty/error, fallback to SQL
            if result.get("error") or not result.get("answer"):
                raise ValueError("graph empty")
        except Exception as e:
            logging.warning(f"[GRAPH FALLBACK] {e} — retrying as SQL")
            if status_cb:
                try: status_cb({"step": "gen_sql", "label": "Knowledge Graph tidak tersedia, beralih ke database..."})
                except: pass
            result = handle_sql(clean_message, history, status_cb=status_cb)
    elif intent == "hybrid":
        result = handle_hybrid(clean_message, history, status_cb=status_cb)
    else:
        result = handle_general(clean_message, history, status_cb=status_cb)

    # Simpan ke session memory
    if session_id:
        history = history + [
            {"role": "user",      "content": message},
            {"role": "assistant", "content": result.get("answer", "")}
        ]
        _save_session_history(session_id, history)
        _cleanup_sessions()

    result["intent"] = intent
    result["message"] = message
    result["rewritten"] = clean_message
    return result
