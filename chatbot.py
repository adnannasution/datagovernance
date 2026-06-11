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

# ─── Schema context untuk SQL generation ─────────────────────────────────────

DB_SCHEMA = """
Tabel-tabel yang tersedia (semua terhubung via tag number equipment):

MASTER:
- master_data_equipment(equipment PK, description, functional_location, maintenance_plant, location, criticality, equipment_category, technical_obj_type)

SAP:
- sap_notifications(notification, equipment, notif_type, notif_date, system_status, req_start, required_end, description, order_no, functional_loc, location, criticality, planner_group, main_workctr, maint_plant, uploaded_at)
- sap_work_orders(order_no, equipment, order_type, created_on, bas_start_date, basic_fin_date, actual_finish, actual_release, description, system_status, user_status, functional_loc, location, criticality, planner_group, main_workctr, maint_act_type, total_plan_cost, total_act_cost, priority, notification, po_number, plant)

MONITORING:
- bad_actor_monitoring(tag_number, ru, status, problem, action_plan, progress, target_date, periode, action_plan_category, external_resource, no_irkap, action_plan_remark)
- icu_monitoring(tag_no, ru, icu_status, issue, mitigation, mitigasi_category, permanent_solution, solution_category, progress, target_closed, report_date, info, remark_mitigation, remark_solution)
- atg_monitoring(tag_no_tangki, tag_no_atg, refinery_unit, status_atg, status_interkoneksi_atg, cert_no_atg, date_expired_atg, remark, rtl, action_plan_category, status_rtl, month_update)
- metering_monitoring(tag_number, refinery_unit, status_metering, cert_no_metering, date_expired_metering, remark, rtl, action_plan_category, status_rtl, month_update)
- boc(equipment, ru, area, unit, grup_equipment, status, frequency, running_hours, mttr, mtbf, hasil)
- pipeline_inspection(tag_number, refinery_unit, area, unit, fluida_service, nps, from_location, to_location, last_inspection_date, next_inspection_date, last_measured_thickness, rem_life_years, jumlah_temporary_repair, remarks, bulan, tahun)
- zero_clamp(tag_no_ln, ru, area, unit, services, description, type_damage, posisi, type_perbaikan, tanggal_dipasang, tanggal_dilepas, tanggal_rencana_perbaikan, status, remarks, no_irkap)
- power_stream(equipment, refinery_unit, type_equipment, status_operation, status_n0, unit_measurement, desain, kapasitas_max, average_actual, remark, date_update, month_update)
- critical_eqp_prim_sec(equipment, refinery_unit, unit_proses, highlight_issue, corrective_action, target_corrective, traffic_corrective, mitigasi_action, target_mitigasi, traffic_mitigasi, month_update)

INSPECTION:
- inspection_plan(tag_no_ln, refinery_unit, area, unit, type_equipment, type_inspection, type_pekerjaan, due_date, due_year, plan_date, plan_year, actual_date, actual_year, update_date, result_remaining_life, result_visual, grand_result)

READINESS:
- readiness_jetty(tag_no, refinery_unit, area, unit, status_operation, status_tuks, expired_tuks, status_ijin_ops, status_isps, status_struktur, remark_struktur, status_trestle, status_mla, status_fire_protection, month_update)
- readiness_tank(tag_number, refinery_unit, area, unit, type_tangki, service_tangki, prioritas, status_operational, atg_certification_validity, status_coi, internal_inspection, plan_internal_inspection, status_atg, status_grounding, status_shell_course, status_roof, status_cathodic, month_update)
- readiness_spm(tag_no, refinery_unit, area, unit, status_operation, status_laik_operasi, expired_laik_operasi, status_ijin_spl, status_mbc, status_lds, status_mooring_hawser, status_floating_hose, status_cathodic_spl, month_update)

WORKPLAN:
- workplan_jetty(tag_no, refinery_unit, area, unit, item, status_item, remark, rtl_action_plan, action_plan_category, target, status_rtl, month_update)
- workplan_tank(tag_no, unit, item, remark, rtl_action_plan, action_plan_category, target, status_rtl, month_update)
- spm_workplan(tag_no, refinery_unit, area, unit, item, remark, rtl_action_plan, action_plan_category, target, status_rtl, month_update)

IRKAP:
- irkap_program(equipment_tag_no, refinery_unit, disiplin, kategori_rkap, no_program_kerja, type_equipment, program_kerja, status_step, status_prognosa, start_plan, finish_plan, nilai_anggaran_idr, nilai_anggaran_usd, top_risk, asset_integrity)
- irkap_actual(tag_no, no_program, kategori_rkap, program_kerja, refinery_unit, area, disiplin, status_step, status_prognosa, current_step, notif_no, wo_no, pr, po, anggaran_idr, jadwal_pelaksanaan, actual_start1, actual_finish1, actual_start3, actual_finish3, failure_impact, rekomendasi)

DOKUMEN:
- doc_registry(id, judul, tipe_dokumen, ru, nomor_dokumen, deskripsi, file_name, file_type, status, total_pages, total_chunks, uploaded_at)
- doc_tag_links(doc_id, tag_number, link_type)
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
                    OPTIONAL MATCH (e)<-[:HAS_BAD_ACTOR]-(ba:BadActor)
                    OPTIONAL MATCH (e)<-[:HAS_ICU]-(icu:ICUMonitoring)
                    OPTIONAL MATCH (e)<-[:HAS_NOTIFICATION]-(n:SAPNotification)
                    OPTIONAL MATCH (e)<-[:HAS_WORK_ORDER]-(wo:SAPWorkOrder)
                    OPTIONAL MATCH (e)<-[:HAS_BOC]-(boc:BOC)
                    OPTIONAL MATCH (e)<-[:HAS_IRKAP]-(irkap:IRKAPProgram)
                    OPTIONAL MATCH (e)<-[:HAS_IRKAP_ACTUAL]-(irkap_a:IRKAPActual)
                    OPTIONAL MATCH (e)<-[:HAS_ATG]-(atg:ATGMonitoring)
                    OPTIONAL MATCH (e)<-[:HAS_METERING]-(meter:MeteringMonitor)
                    OPTIONAL MATCH (e)<-[:HAS_PIPELINE_INSPECTION]-(pipe:PipelineInspection)
                    OPTIONAL MATCH (e)<-[:HAS_INSPECTION_PLAN]-(insp:InspectionPlan)
                    OPTIONAL MATCH (e)<-[:HAS_ZERO_CLAMP]-(zc:ZeroClamp)
                    OPTIONAL MATCH (e)<-[:HAS_READINESS]-(ready)
                    OPTIONAL MATCH (e)<-[:HAS_WORKPLAN]-(wp)
                    OPTIONAL MATCH (e)<-[:IS_CRITICAL]-(crit:CriticalEquipment)
                    OPTIONAL MATCH (e)<-[:HAS_POWER_STREAM]-(ps:PowerStream)
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
    # If an equipment tag is detected, bias towards graph or hybrid
    tag_hint = ""
    if extract_tag_from_message(message):
        tag_hint = "\nCATATAN: Pesan mengandung tag equipment. Pertimbangkan 'graph' atau 'hybrid' jika pertanyaan tentang data/relasi equipment tersebut.\n"

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
- 'sql': tanya data angka, jumlah, status, list equipment, rekap dari database
- 'graph': tanya relasi antar entitas, koneksi equipment-dokumen, network, data lengkap satu equipment
- 'hybrid': butuh kombinasi dokumen DAN data database/graph
- 'general': sapaan, pertanyaan umum, tidak spesifik
{tag_hint}
Konteks percakapan sebelumnya:
{history_text}

Pertanyaan: {message}

Jawab HANYA satu kata: rag / sql / graph / hybrid / general"""
            }]
        )
        intent = resp.choices[0].message.content.strip().lower()
        if intent not in ("rag", "sql", "graph", "hybrid", "general"):
            intent = "hybrid"
        return intent
    except Exception:
        return "hybrid"

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
        max_tokens=400,
        messages=[
            {
                "role": "system",
                "content": f"""Kamu adalah SQL generator untuk database PostgreSQL Pertamina.
Berdasarkan skema berikut, generate SQL query yang menjawab pertanyaan user.

{DB_SCHEMA}

ATURAN:
- Hanya SELECT, tidak boleh INSERT/UPDATE/DELETE/DROP
- Gunakan LIMIT maksimal 50
- Gunakan ILIKE untuk pencarian teks
- Jika filter RU, gunakan kolom refinery_unit atau ru atau maintenance_plant
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

# ─── Graph Handler ────────────────────────────────────────────────────────────

CYPHER_SYSTEM_PROMPT = """Generate Cypher query untuk Neo4j berdasarkan pertanyaan.

Node labels yang tersedia:
Equipment, Document, BOC, ICUMonitoring, ATGMonitoring, MeteringMonitor,
BadActor, SAPNotification, SAPWorkOrder, InspectionPlan, PipelineInspection, ZeroClamp, PowerStream,
CriticalEquipment, ReadinessJetty, ReadinessTank, ReadinessSPM, WorkplanJetty, WorkplanTank,
WorkplanSPM, IRKAPProgram, IRKAPActual

Relasi yang tersedia (semua berasal dari Equipment kecuali disebutkan lain):
- (Equipment)<-[:HAS_NOTIFICATION]-(SAPNotification)
- (Equipment)<-[:HAS_WORK_ORDER]-(SAPWorkOrder)
- (Equipment)<-[:HAS_BOC]-(BOC)
- (Equipment)<-[:HAS_ICU]-(ICUMonitoring)
- (Equipment)<-[:HAS_BAD_ACTOR]-(BadActor)
- (Equipment)<-[:HAS_ATG]-(ATGMonitoring)
- (Equipment)<-[:HAS_METERING]-(MeteringMonitor)
- (Equipment)<-[:HAS_IRKAP]-(IRKAPProgram)
- (Equipment)<-[:HAS_IRKAP_ACTUAL]-(IRKAPActual)
- (Equipment)<-[:HAS_PIPELINE_INSPECTION]-(PipelineInspection)
- (Equipment)<-[:HAS_ZERO_CLAMP]-(ZeroClamp)
- (Equipment)<-[:HAS_POWER_STREAM]-(PowerStream)
- (Equipment)<-[:IS_CRITICAL]-(CriticalEquipment)
- (Equipment)<-[:HAS_INSPECTION_PLAN]-(InspectionPlan)
- (Equipment)<-[:HAS_READINESS]-(ReadinessJetty / ReadinessTank / ReadinessSPM)
- (Equipment)<-[:HAS_WORKPLAN]-(WorkplanJetty / WorkplanTank / WorkplanSPM)
- (Document)-[:TERKAIT_DENGAN]->(Equipment)
- (SAPNotification)-[:GENERATED_WO]->(SAPWorkOrder)
- (BadActor)-[:HAS_IRKAP]->(IRKAPProgram)
- (IRKAPProgram)-[:HAS_ACTUAL]->(IRKAPActual)

Equipment punya property: tag_number, description, maintenance_plant, criticality
Document punya property: doc_id, judul, tipe, ru

Kembalikan HANYA Cypher query, tanpa penjelasan, tanpa backtick, dengan LIMIT 20."""


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
                {"role": "system", "content": CYPHER_SYSTEM_PROMPT},
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
    rag_result = handle_rag(message)
    sql_result = handle_sql(message, history)

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

    return {
        "type": "hybrid",
        "answer": synth.choices[0].message.content,
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
