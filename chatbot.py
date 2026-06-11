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
from openai import OpenAI
from dotenv import load_dotenv

load_dotenv()

client = OpenAI(
    api_key=os.getenv("LLM_API_KEY"),
    base_url=os.getenv("LLM_BASE_URL", "https://ai.dinoiki.com/v1")
)
MODEL = "gpt-4o"

# ─── Dynamic schema helpers ───────────────────────────────────────────────────

def _get_db_schema() -> str:
    return DB_SCHEMA_FALLBACK


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

"rusak" / "masalah" / "bermasalah"
  → bad_actor_monitoring (ada entri), icu_monitoring (ada entri),
    critical_eqp_prim_sec.highlight_issue IS NOT NULL

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

=== ATURAN PENTING ===
- Hanya SELECT, tidak boleh INSERT/UPDATE/DELETE/DROP
- LIMIT maksimal 50
- SELALU gunakan ILIKE '%nilai%' untuk pencarian nilai teks — jangan exact match
- Filter RU: coba kolom refinery_unit, ru, maintenance_plant (tergantung tabel)
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

def handle_rag(message: str, filters: dict = None) -> dict:
    """Vector search dokumen lalu generate jawaban."""
    from embedder import get_embedding
    from db import vector_search

    query_emb = get_embedding(message)
    results = vector_search(
        query_embedding=query_emb,
        ru=filters.get("ru") if filters else None,
        limit=5,
        threshold=0.3
    )

    if not results:
        return {
            "type": "rag",
            "answer": "Tidak ditemukan dokumen yang relevan dengan pertanyaan ini.",
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

    resp = client.chat.completions.create(
        model=MODEL,
        max_tokens=800,
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
        ]
    )

    return {
        "type": "rag",
        "answer": resp.choices[0].message.content,
        "sources": sources,
        "context_used": context[:500]
    }

# ─── SQL Handler ──────────────────────────────────────────────────────────────

def handle_sql(message: str, history: list = None) -> dict:
    """Generate SQL dari pertanyaan, execute, lalu format jawaban."""
    from db_equipment import get_conn

    # Generate SQL
    sql_resp = client.chat.completions.create(
        model=MODEL,
        max_tokens=500,
        messages=[
            {
                "role": "system",
                "content": f"""Kamu adalah SQL generator untuk database PostgreSQL Pertamina.
Generate SQL query yang menjawab pertanyaan user berdasarkan schema dan contoh query berikut.

{DB_SCHEMA_FALLBACK}

INSTRUKSI:
- Ikuti contoh JOIN query di atas sebagai referensi pola yang benar
- Kembalikan HANYA query SQL, tanpa penjelasan, tanpa markdown backtick"""
            },
            {
                "role": "user",
                "content": message
            }
        ]
    )

    sql = sql_resp.choices[0].message.content.strip()
    sql = re.sub(r'```sql|```', '', sql).strip()

    # Validasi — hanya SELECT
    if not sql.upper().startswith("SELECT"):
        return {
            "type": "sql",
            "answer": "Maaf, saya hanya bisa menjalankan query SELECT.",
            "sql": sql,
            "data": [],
            "error": "Non-SELECT query blocked"
        }

    # Execute
    try:
        with get_conn() as conn:
            rows = conn.execute(sql).fetchall()
            data = [dict(r) for r in rows]
    except Exception as e:
        return {
            "type": "sql",
            "answer": f"Query gagal dijalankan: {str(e)}",
            "sql": sql,
            "data": [],
            "error": str(e)
        }

    if not data:
        return {
            "type": "sql",
            "answer": "Query berhasil dijalankan tapi tidak ada data yang ditemukan.",
            "sql": sql,
            "data": [],
            "error": None
        }

    # Format jawaban dari data
    data_preview = json.dumps(data[:10], default=str, ensure_ascii=False)
    fmt_resp = client.chat.completions.create(
        model=MODEL,
        max_tokens=600,
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
        ]
    )

    return {
        "type": "sql",
        "answer": fmt_resp.choices[0].message.content,
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

INSTRUKSI:
- Generate Cypher query yang menjawab pertanyaan user
- Ikuti contoh query di atas sebagai referensi pola yang benar
- SELALU gunakan e.tag_number untuk Equipment node
- Arah relasi SELALU (Equipment)-[:REL]->(Node) sesuai schema
- Kembalikan HANYA Cypher query, tanpa penjelasan, tanpa markdown backtick"""


def handle_graph(message: str) -> dict:
    """Generate Cypher query, execute di Neo4j, format jawaban.
    If an equipment tag is detected, use GraphRAG for rich context first."""
    try:
        from neo4j_sync import get_driver

        # Try GraphRAG first if tag detected
        tag = extract_tag_from_message(message)
        if tag:
            try:
                graph_ctx = get_equipment_context_from_graph(tag)
                if graph_ctx:
                    ctx_text = _format_graph_context(tag, graph_ctx)
                    fmt_resp = client.chat.completions.create(
                        model=MODEL,
                        max_tokens=800,
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
                        ]
                    )
                    return {
                        "type": "graph",
                        "answer": fmt_resp.choices[0].message.content,
                        "cypher": f"GraphRAG lookup for tag: {tag}",
                        "data": [],
                        "tag": tag
                    }
            except Exception:
                pass  # Fall back to Cypher generation

        # Generate Cypher (fallback or no tag)
        cypher_resp = client.chat.completions.create(
            model=MODEL,
            max_tokens=300,
            messages=[
                {"role": "system", "content": _get_cypher_prompt()},
                {"role": "user", "content": message}
            ]
        )

        cypher = cypher_resp.choices[0].message.content.strip()
        cypher = re.sub(r'```cypher|```', '', cypher).strip()

        with get_driver() as driver:
            with driver.session() as session:
                result = session.run(cypher)
                data = [dict(r) for r in result]

        if not data:
            # Fallback ke SQL jika tag terdeteksi tapi tidak ada di graph
            if tag:
                sql_result = _handle_tag_sql_fallback(tag, message)
                if sql_result:
                    return sql_result
            return {
                "type": "graph",
                "answer": "Tidak ditemukan data di Knowledge Graph untuk pertanyaan ini.",
                "cypher": cypher,
                "data": []
            }

        data_text = json.dumps(data[:10], default=str, ensure_ascii=False)
        fmt_resp = client.chat.completions.create(
            model=MODEL,
            max_tokens=500,
            messages=[
                {
                    "role": "system",
                    "content": "Formatkan hasil Knowledge Graph query menjadi jawaban Bahasa Indonesia yang informatif."
                },
                {
                    "role": "user",
                    "content": f"Pertanyaan: {message}\n\nHasil ({len(data)} records):\n{data_text}"
                }
            ]
        )

        return {
            "type": "graph",
            "answer": fmt_resp.choices[0].message.content,
            "cypher": cypher,
            "data": data[:10]
        }

    except Exception as e:
        import traceback, logging
        logging.error(f"[GRAPH ERROR] {e}\n{traceback.format_exc()}")
        return {
            "type": "graph",
            "answer": f"Knowledge Graph tidak dapat diakses: {str(e)}",
            "cypher": "",
            "data": [],
            "error": str(e)
        }

# ─── Hybrid Handler ───────────────────────────────────────────────────────────

def handle_hybrid(message: str, history: list = None) -> dict:
    """Kombinasi RAG + SQL + Graph (jika tag terdeteksi), gabungkan hasilnya."""
    try:
        rag_result = handle_rag(message)
    except Exception:
        rag_result = {"answer": "", "sources": [], "data": []}
    try:
        sql_result = handle_sql(message, history)
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
            graph_ctx = get_equipment_context_from_graph(tag)
            if graph_ctx:
                graph_context_text = _format_graph_context(tag, graph_ctx)
                combined_context += f"\n\n**Dari Knowledge Graph (equipment {tag}):**\n{graph_context_text[:2000]}"
        except Exception:
            pass

    # Final synthesis
    try:
        synth = client.chat.completions.create(
            model=MODEL,
            max_tokens=800,
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
            ]
        )
        answer = synth.choices[0].message.content
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

def handle_general(message: str, history: list = None) -> dict:
    messages = [
        {
            "role": "system",
            "content": """Kamu adalah asisten data governance Pertamina.
Bantu user memahami sistem, menjawab pertanyaan umum, dan mengarahkan ke fitur yang tepat.
Jawab dalam Bahasa Indonesia. Singkat dan helpful."""
        }
    ]
    if history:
        messages.extend(history[-6:])
    messages.append({"role": "user", "content": message})

    resp = client.chat.completions.create(
        model=MODEL, max_tokens=400, messages=messages
    )
    return {
        "type": "general",
        "answer": resp.choices[0].message.content
    }

# ─── Main Chat Function ───────────────────────────────────────────────────────

def chat(message: str, history: list = None, filters: dict = None) -> dict:
    """
    Entry point utama. Router ke handler yang tepat.
    history: list of {role, content}
    filters: {ru, tag_number}
    """
    if not history:
        history = []

    intent = detect_intent(message, history)

    if intent == "rag":
        result = handle_rag(message, filters)
    elif intent == "sql":
        result = handle_sql(message, history)
    elif intent == "graph":
        result = handle_graph(message)
    elif intent == "hybrid":
        result = handle_hybrid(message, history)
    else:
        result = handle_general(message, history)

    result["intent"] = intent
    result["message"] = message
    return result
