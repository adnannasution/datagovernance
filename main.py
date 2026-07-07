import os
import shutil
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, UploadFile, File, Form, BackgroundTasks, HTTPException, Request
from fastapi.responses import HTMLResponse, FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from dotenv import load_dotenv

import db
import executive_mock
import executive_data
from embedder import get_embedding, get_embeddings_batch
from processor import dispatch, SUPPORTED_TYPES

load_dotenv()

app = FastAPI(title=os.getenv("APP_TITLE", "Pertamina Document Governance"))
UPLOAD_DIR = Path(os.getenv("UPLOAD_DIR", "./uploads"))
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")

TIPE_DOKUMEN_LIST = [
    "Inspection Report", "SOP", "Maintenance Record",
    "P&ID / Drawing", "Datasheet", "Manual Book",
    "Work Order", "Master Data", "Laporan Harian", "Lainnya"
]
RU_LIST = [
    "RU II Dumai", "RU III Plaju", "RU IV Cilacap",
    "RU V Balikpapan", "RU VI Balongan", "RU VII Kasim"
]

# ─── Pages ──────────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
async def page_dashboard(request: Request):
    stats = db.get_dashboard_stats()
    return templates.TemplateResponse(request, "index.html", {
        "stats": stats,
        "title": "Dashboard"
    })

@app.get("/dashboard/executive", response_class=HTMLResponse)
async def page_executive(request: Request, period: str = ""):
    # Live data from the reliability tables (executive_data), filtered by the
    # selected period, with automatic per-section fallback to the mock skeleton
    # (executive_mock) when the DB or a specific column is unavailable.
    try:
        snapshot = executive_data.get_executive_snapshot(period)
    except Exception:
        snapshot = executive_mock.get_executive_snapshot()
    try:
        methodology = executive_data.methodology(period)
    except Exception:
        methodology = {"entries": [], "db": "unavailable", "period": period}
    return templates.TemplateResponse(request, "executive.html", {
        "snapshot": snapshot,
        "methodology": methodology,
        "period": period,
        "title": "Executive Dashboard"
    })

@app.get("/dashboard/executive/debug")
async def page_executive_debug(period: str = ""):
    # Diagnostics: shows DB connectivity, real columns/row-counts per source
    # table, RU-string matching, and per-section live/error status. Use this to
    # see WHY a KPI is still mock (empty table, cast error, column mismatch).
    try:
        return JSONResponse(executive_data.diagnostics(period))
    except Exception as e:
        return JSONResponse({"db": f"diagnostics failed: {e!r}"})

@app.get("/dashboard/drilldown", response_class=HTMLResponse)
async def page_drilldown(request: Request, ru: str = ""):
    return templates.TemplateResponse(request, "drilldown.html", {
        "ru": ru,
        "title": "Refinery Drill-down"
    })

@app.get("/upload", response_class=HTMLResponse)
async def page_upload(request: Request):
    return templates.TemplateResponse(request, "upload.html", {
        "tipe_list": TIPE_DOKUMEN_LIST,
        "ru_list": RU_LIST,
        "title": "Upload Dokumen"
    })

@app.get("/library", response_class=HTMLResponse)
async def page_library(request: Request,
                       ru: str = "", tipe: str = "",
                       status: str = "", search: str = "",
                       page: int = 1):
    limit = 20
    offset = (page - 1) * limit
    docs, total = db.list_docs(
        ru=ru or None, tipe=tipe or None,
        status=status or None, search=search or None,
        limit=limit, offset=offset
    )
    return templates.TemplateResponse(request, "library.html", {
        "docs": docs,
        "total": total, "page": page,
        "total_pages": (total + limit - 1) // limit,
        "ru_list": RU_LIST, "tipe_list": TIPE_DOKUMEN_LIST,
        "filters": {"ru": ru, "tipe": tipe, "status": status, "search": search},
        "title": "Perpustakaan Dokumen"
    })

@app.get("/search", response_class=HTMLResponse)
async def page_search(request: Request):
    return templates.TemplateResponse(request, "search.html", {
        "ru_list": RU_LIST,
        "tipe_list": TIPE_DOKUMEN_LIST,
        "title": "Smart Search"
    })

@app.get("/document/{doc_id}", response_class=HTMLResponse)
async def page_detail(request: Request, doc_id: int):
    doc = db.get_doc(doc_id)
    if not doc:
        raise HTTPException(404, "Dokumen tidak ditemukan")
    tags = db.get_tag_links(doc_id)
    chunks = db.get_chunks_preview(doc_id, limit=5)
    versions = db.get_versions(doc_id)
    return templates.TemplateResponse(request, "detail.html", {
        "doc": doc,
        "tags": tags, "chunks": chunks,
        "versions": versions,
        "title": doc["judul"]
    })

# ─── Upload & Processing ─────────────────────────────────────

def _process_document(doc_id: int, file_path: str, file_type: str,
                      manual_tags: list[str]):
    """Background job: extract → chunk → embed → save."""
    try:
        db.update_doc_status(doc_id, "processing")

        # 1. Dispatch ke processor yang sesuai
        result = dispatch(doc_id, file_path, file_type)
        chunks = result["chunks"]
        table_rows = result["table_rows"]
        auto_tags = result.get("auto_tags", [])
        total_pages = result.get("total_pages", 0)
        processing_meta = result.get("processing_meta", {})

        # 2. Generate embeddings batch
        texts = [c["content"] for c in chunks]
        if texts:
            embeddings = get_embeddings_batch(texts)
            for chunk, emb in zip(chunks, embeddings):
                chunk["embedding"] = emb

        # 3. Simpan ke DB
        db.insert_chunks(chunks)
        db.insert_table_rows(table_rows)

        # 4. Tag links: manual + auto-detected
        all_tags = list(set(manual_tags + auto_tags))
        db.insert_tag_links(doc_id, manual_tags, link_type="manual")
        db.insert_tag_links(doc_id, auto_tags, link_type="auto")

        # 5. Update status
        db.update_doc_status(
            doc_id, "ready",
            total_pages=total_pages,
            total_chunks=len(chunks),
            processing_meta={
                **processing_meta,
                "auto_tags_found": auto_tags,
                "table_rows": len(table_rows)
            }
        )

        # 6. Auto sync ke Neo4j
        try:
            from neo4j_sync import sync_document
            doc_row = db.get_doc(doc_id)
            tag_rows = db.get_tag_links(doc_id)
            if doc_row:
                sync_document(dict(doc_row), [dict(t) for t in tag_rows])
        except Exception:
            pass  # Neo4j sync gagal tidak boleh gagalkan upload

    except Exception as e:
        db.update_doc_status(doc_id, "error", error_message=str(e))
        raise

@app.post("/api/upload")
async def upload_document(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    judul: str = Form(...),
    tipe_dokumen: str = Form(...),
    ru: str = Form(""),
    nomor_dokumen: str = Form(""),
    deskripsi: str = Form(""),
    tag_numbers: str = Form(""),  # comma-separated
    uploaded_by: str = Form("user")
):
    ext = Path(file.filename).suffix.lstrip(".").lower()
    if ext not in SUPPORTED_TYPES:
        raise HTTPException(400, f"Format tidak didukung: {ext}")

    file_path = UPLOAD_DIR / f"{file.filename}"
    # Hindari nama file duplikat
    counter = 1
    while file_path.exists():
        stem = Path(file.filename).stem
        file_path = UPLOAD_DIR / f"{stem}_{counter}.{ext}"
        counter += 1

    with open(file_path, "wb") as f:
        shutil.copyfileobj(file.file, f)

    file_size = file_path.stat().st_size
    tags = [t.strip() for t in tag_numbers.split(",") if t.strip()]

    doc_id = db.insert_doc(
        judul=judul,
        tipe_dokumen=tipe_dokumen,
        ru=ru or None,
        nomor_dokumen=nomor_dokumen or None,
        deskripsi=deskripsi or None,
        file_path=str(file_path),
        file_name=file.filename,
        file_type=ext,
        file_size_bytes=file_size,
        uploaded_by=uploaded_by
    )

    background_tasks.add_task(
        _process_document, doc_id, str(file_path), ext, tags
    )

    return {"doc_id": doc_id, "status": "pending", "message": "Dokumen sedang diproses"}

# ─── API Endpoints ───────────────────────────────────────────

@app.get("/api/document/{doc_id}/status")
async def get_status(doc_id: int):
    doc = db.get_doc(doc_id)
    if not doc:
        raise HTTPException(404)
    return {
        "doc_id": doc_id,
        "status": doc["status"],
        "total_chunks": doc["total_chunks"],
        "total_pages": doc["total_pages"],
        "error_message": doc["error_message"]
    }

@app.delete("/api/document/{doc_id}")
async def delete_document(doc_id: int):
    doc = db.delete_doc(doc_id)
    if not doc:
        raise HTTPException(404)
    try:
        Path(doc["file_path"]).unlink(missing_ok=True)
    except Exception:
        pass
    # Hapus node dari Neo4j juga
    try:
        from neo4j_sync import delete_document_node
        delete_document_node(doc_id)
    except Exception:
        pass
    return {"message": "Dokumen berhasil dihapus"}

@app.post("/api/document/{doc_id}/tags")
async def add_tag(doc_id: int, tag_number: str = Form(...)):
    db.insert_tag_links(doc_id, [tag_number], link_type="manual")
    return {"message": "Tag berhasil ditambahkan"}

@app.delete("/api/document/{doc_id}/tags/{tag_number}")
async def remove_tag(doc_id: int, tag_number: str):
    db.delete_tag_link(doc_id, tag_number)
    return {"message": "Tag berhasil dihapus"}

@app.get("/api/document/{doc_id}/download")
async def download_file(doc_id: int):
    doc = db.get_doc(doc_id)
    if not doc:
        raise HTTPException(404)
    path = Path(doc["file_path"])
    if not path.exists():
        raise HTTPException(404, "File tidak ditemukan")
    return FileResponse(path, filename=doc["file_name"])

@app.get("/api/stats")
async def get_stats():
    return db.get_dashboard_stats()

# ─── Smart Search API ────────────────────────────────────────

@app.post("/api/search")
async def smart_search(request: Request):
    body = await request.json()
    query = body.get("query", "").strip()
    ru = body.get("ru") or None
    tipe = body.get("tipe") or None
    tag_number = body.get("tag_number") or None
    limit = int(body.get("limit", 10))
    threshold = float(body.get("threshold", 0.35))

    if not query:
        raise HTTPException(400, "Query tidak boleh kosong")

    query_embedding = get_embedding(query)
    results = db.vector_search(
        query_embedding=query_embedding,
        ru=ru, tipe=tipe,
        tag_number=tag_number,
        limit=limit,
        threshold=threshold
    )

    return {
        "query": query,
        "total": len(results),
        "results": [
            {
                "chunk_id": r["chunk_id"],
                "doc_id": r["doc_id"],
                "judul": r["judul"],
                "ru": r["ru"],
                "tipe_dokumen": r["tipe_dokumen"],
                "halaman": r["halaman"],
                "slide_number": r["slide_number"],
                "slide_title": r["slide_title"],
                "sheet_name": r["sheet_name"],
                "content": r["content"],
                "score": round(float(r["score"]), 4),
                "file_name": r["file_name"]
            }
            for r in results
        ]
    }

@app.get("/api/search/all")
async def api_search_all(q: str = "", ru: str = "", limit: int = 10):
    """Unified search: documents (vector) + equipment graph (Neo4j)."""
    if not q:
        raise HTTPException(400, "Query required")

    import asyncio
    from embedder import get_embedding
    from db import vector_search
    from neo4j_sync import search_graph

    # Vector search
    try:
        emb = get_embedding(q)
        doc_results = vector_search(
            query_embedding=emb,
            ru=ru or None,
            limit=limit,
            threshold=0.3
        )
        docs = [dict(r) for r in doc_results]
    except Exception:
        docs = []

    # Graph search
    graph_results = search_graph(query=q, ru=ru or None, limit=limit)

    return {
        "query": q,
        "documents": docs,
        "equipment": graph_results,
        "total_docs": len(docs),
        "total_equipment": len(graph_results)
    }


@app.get("/api/search/graph")
async def api_search_graph(q: str = "", ru: str = "", limit: int = 10):
    if not q:
        raise HTTPException(400, "Query tidak boleh kosong")
    from neo4j_sync import search_graph
    results = search_graph(query=q, ru=ru or None, limit=limit)
    return {"query": q, "total": len(results), "results": results}

# ─── GOVERNANCE ROUTES ───────────────────────────────────────────────────────

from db_equipment import (
    get_governance_overview, get_catalog_stats, get_table_columns,
    get_table_rows, get_data_quality, search_equipment, get_plant_list,
    get_equipment_360, TABLE_CATALOG, DOMAIN_ORDER
)

@app.get("/governance", response_class=HTMLResponse)
async def page_governance(request: Request):
    overview = get_governance_overview()
    return templates.TemplateResponse(request, "governance.html", {
        "overview": overview,
        "title": "Data Governance"
    })

@app.get("/catalog", response_class=HTMLResponse)
async def page_catalog(request: Request, domain: str = ""):
    stats = get_catalog_stats()
    if domain:
        stats = [s for s in stats if s["domain"] == domain]
    return templates.TemplateResponse(request, "catalog.html", {
        "stats": stats,
        "domain_list": DOMAIN_ORDER,
        "active_domain": domain,
        "title": "Data Catalog"
    })

@app.get("/catalog/{table_name}", response_class=HTMLResponse)
async def page_table_detail(request: Request, table_name: str):
    tbl_meta = next((t for t in TABLE_CATALOG if t["table"] == table_name), None)
    if not tbl_meta:
        raise HTTPException(404, "Tabel tidak ditemukan")
    cols = get_table_columns(table_name)
    quality = get_data_quality(table_name, tbl_meta.get("tag_col"))
    return templates.TemplateResponse(request, "table_detail.html", {
        "tbl": tbl_meta,
        "cols": cols,
        "quality": quality,
        "title": tbl_meta["label"]
    })

@app.get("/api/table/{table_name}/rows")
async def api_table_rows(table_name: str, page: int = 1, limit: int = 10,
                          search: str = "", sort_col: str = "", sort_dir: str = "asc"):
    tbl_meta = next((t for t in TABLE_CATALOG if t["table"] == table_name), None)
    if not tbl_meta:
        raise HTTPException(404, "Tabel tidak ditemukan")
    limit = max(1, min(limit, 200))
    offset = (page - 1) * limit
    rows, total = get_table_rows(
        table_name,
        search=search or None,
        limit=limit,
        offset=offset,
        sort_col=sort_col or None,
        sort_dir=sort_dir
    )
    total_pages = (total + limit - 1) // limit if total > 0 else 1
    return {
        "rows": [dict(r) for r in rows],
        "total": total,
        "page": page,
        "total_pages": total_pages,
        "limit": limit,
        "offset": offset
    }

@app.get("/equipment", response_class=HTMLResponse)
async def page_equipment_list(request: Request, q: str = "", plant: str = ""):
    equipment = search_equipment(q=q, plant=plant, limit=100) if (q or plant) else []
    plants = get_plant_list()
    return templates.TemplateResponse(request, "equipment_list.html", {
        "equipment": equipment,
        "plants": plants,
        "q": q, "plant": plant,
        "title": "Equipment Master"
    })

@app.get("/equipment/{tag}", response_class=HTMLResponse)
async def page_equipment_360(request: Request, tag: str):
    data = get_equipment_360(tag)
    if not data["master"]:
        raise HTTPException(404, f"Equipment {tag} tidak ditemukan")
    # Dokumen terkait dari doc_governance
    from db import get_tag_links
    try:
        doc_links = get_tag_links.__module__  # cek apakah ada
    except Exception:
        doc_links = []
    return templates.TemplateResponse(request, "equipment_360.html", {
        "tag": tag,
        "data": data,
        "title": f"Equipment 360° — {tag}"
    })

@app.get("/api/equipment/search")
async def api_equipment_search(q: str = "", plant: str = ""):
    results = search_equipment(q=q, plant=plant, limit=20)
    return [dict(r) for r in results]

@app.get("/api/equipment/{tag}/360")
async def api_equipment_360(tag: str):
    return get_equipment_360(tag)

@app.get("/api/tag/{tag}/resolve")
async def api_tag_resolve(tag: str):
    from db_equipment import resolve_tag_variants
    canonical = tag.upper()
    variants = resolve_tag_variants(canonical)
    return {"canonical": canonical, "variants": variants, "total_variants": len(variants)}

@app.get("/api/tag/{tag}/summary")
async def api_tag_summary(tag: str):
    return get_equipment_360(tag.upper())

@app.get("/api/catalog/stats")
async def api_catalog_stats():
    return get_catalog_stats()

@app.get("/api/table/{table_name}/quality")
async def api_table_quality(table_name: str):
    tbl_meta = next((t for t in TABLE_CATALOG if t["table"] == table_name), None)
    tag_col = tbl_meta.get("tag_col") if tbl_meta else None
    return get_data_quality(table_name, tag_col)

# ─── NEO4J ROUTES ────────────────────────────────────────────────────────────

from neo4j_sync import get_neo4j_stats, full_sync, ensure_constraints, test_connection

@app.get("/knowledge-graph", response_class=HTMLResponse)
async def page_knowledge_graph(request: Request):
    from neo4j_sync import TABLE_NEO4J_CONFIG, get_coverage_stats
    from db_equipment import TABLE_CATALOG
    stats = get_neo4j_stats()
    try:
        coverage = get_coverage_stats()
        coverage_tables = {r["table"]: r for r in coverage.get("tables", [])}
    except Exception:
        coverage_tables = {}
    # Build sync status — only tables that can be synced to Neo4j (have tag col)
    table_sync_status = []
    for tname, cfg in TABLE_NEO4J_CONFIG.items():
        ct = coverage_tables.get(tname, {})
        table_sync_status.append({
            "table": tname,
            "label": cfg["label"],
            "rel": cfg["rel"],
            "connected": ct.get("connected", 0),
            "synced": ct.get("connected", 0) > 0,
        })
    # Catalog-only tables — exist in TABLE_CATALOG but not synced as Neo4j relations
    # Exclude: master_data_equipment (IS the Equipment node), doc_registry (Document node)
    neo4j_tables_set = set(TABLE_NEO4J_CONFIG.keys())
    exclude = {"master_data_equipment", "doc_registry"}
    catalog_only = [
        t for t in TABLE_CATALOG
        if t["table"] not in neo4j_tables_set and t["table"] not in exclude
    ]
    return templates.TemplateResponse(request, "knowledge_graph.html", {
        "stats": stats,
        "neo4j_tables": sorted(TABLE_NEO4J_CONFIG.keys()),
        "table_sync_status": table_sync_status,
        "catalog_only_tables": catalog_only,
        "title": "Knowledge Graph"
    })

@app.get("/api/neo4j/stats")
async def api_neo4j_stats():
    return get_neo4j_stats()

@app.post("/api/neo4j/sync")
async def api_neo4j_full_sync(background_tasks: BackgroundTasks):
    if not test_connection():
        raise HTTPException(503, "Neo4j tidak dapat diakses")
    ensure_constraints()
    background_tasks.add_task(_run_full_sync)
    return {"message": "Full sync dimulai di background"}

@app.get("/api/neo4j/sync/status")
async def api_neo4j_sync_status():
    """Cek apakah sync sedang berjalan."""
    return {"running": _sync_running, "last_result": _last_sync_result}

# State sync
_sync_running = False
_last_sync_result = None

def _run_full_sync():
    global _sync_running, _last_sync_result
    _sync_running = True
    try:
        _last_sync_result = full_sync()
    finally:
        _sync_running = False

_table_sync_running = False
_last_table_sync_result = None
_table_sync_current = None

def _run_table_sync(source_table: str = None):
    global _table_sync_running, _last_table_sync_result, _table_sync_current
    _table_sync_running = True
    _table_sync_current = source_table or "all"
    try:
        from neo4j_sync import sync_all_tables
        def _on_progress(tname):
            global _table_sync_current
            _table_sync_current = tname
        _last_table_sync_result = sync_all_tables(source_table=source_table, progress_callback=_on_progress)
    finally:
        _table_sync_running = False
        _table_sync_current = None

@app.get("/api/neo4j/graph")
async def api_neo4j_graph(tag: str = "", depth: int = 1):
    if not tag:
        raise HTTPException(status_code=400, detail="tag parameter required")
    from neo4j_sync import get_graph_for_tag
    return get_graph_for_tag(tag, depth=depth)

@app.get("/api/neo4j/graph/by-table")
async def api_neo4j_graph_by_table(tag: str = ""):
    if not tag:
        raise HTTPException(status_code=400, detail="tag parameter required")
    from neo4j_sync import get_table_graph_for_tag
    return get_table_graph_for_tag(tag)

@app.get("/api/neo4j/tags/search")
async def api_neo4j_tags_search(q: str = "", limit: int = 20):
    """Search tag numbers yang sudah ada di Neo4j Equipment nodes (sudah tersinkron)."""
    if not q or len(q.strip()) < 2:
        return {"tags": []}
    q = q.strip().upper()
    try:
        from neo4j_sync import get_driver
        driver = get_driver()
        if not driver:
            return {"tags": []}
        with driver.session() as session:
            # Prioritaskan starts-with dulu, lalu contains
            rows = session.run(
                """MATCH (e:Equipment)
                   WHERE e.tag_number STARTS WITH $q
                      OR e.tag_number CONTAINS $q
                      OR toLower(e.description) CONTAINS toLower($ql)
                   RETURN e.tag_number AS tag, e.description AS desc,
                     CASE WHEN e.tag_number STARTS WITH $q THEN 0 ELSE 1 END AS rank
                   ORDER BY rank, e.tag_number
                   LIMIT $limit""",
                {"q": q, "ql": q.lower(), "limit": limit}
            ).data()
        driver.close()
        return {"tags": [{"tag": r["tag"], "desc": r["desc"] or ""} for r in rows]}
    except Exception as e:
        return {"tags": [], "error": str(e)}

@app.post("/api/neo4j/sync/tables")
async def api_sync_all_tables(background_tasks: BackgroundTasks, source_table: str = None):
    from neo4j_sync import get_driver, TABLE_NEO4J_CONFIG
    if get_driver() is None:
        raise HTTPException(status_code=503, detail="Neo4j tidak dapat diakses")
    if source_table and source_table not in TABLE_NEO4J_CONFIG:
        raise HTTPException(status_code=400, detail=f"Tabel '{source_table}' tidak ada di konfigurasi Neo4j")
    if _table_sync_running:
        return {"message": "Sync sudah berjalan", "running": True}
    background_tasks.add_task(_run_table_sync, source_table)
    msg = f"Sync tabel '{source_table}' dimulai" if source_table else "Sync semua tabel dimulai"
    return {"message": msg, "running": True}

@app.get("/api/neo4j/sync/tables/status")
async def api_sync_tables_status():
    return {"running": _table_sync_running, "current_table": _table_sync_current, "last_result": _last_table_sync_result}

_domain_rel_running = False
_last_domain_rel_result = None

def _run_domain_relations():
    global _domain_rel_running, _last_domain_rel_result
    _domain_rel_running = True
    try:
        from neo4j_sync import sync_domain_relations
        _last_domain_rel_result = sync_domain_relations()
    finally:
        _domain_rel_running = False

@app.post("/api/neo4j/sync/domain-relations")
async def api_sync_domain_relations(background_tasks: BackgroundTasks):
    from neo4j_sync import get_driver
    if get_driver() is None:
        raise HTTPException(status_code=503, detail="Neo4j tidak dapat diakses")
    background_tasks.add_task(_run_domain_relations)
    return {"message": "Sync domain relations dimulai di background"}

@app.get("/api/neo4j/sync/domain-relations/status")
async def api_domain_relations_status():
    return {"running": _domain_rel_running, "last_result": _last_domain_rel_result}

@app.post("/api/schema/refresh")
async def api_schema_refresh():
    """Force refresh schema cache (Neo4j + PostgreSQL)."""
    try:
        from schema_cache import refresh_all
        refresh_all()
        return {"message": "Schema cache refreshed"}
    except Exception as e:
        return {"error": str(e)}

@app.get("/api/neo4j/coverage")
async def api_neo4j_coverage():
    from neo4j_sync import get_coverage_stats
    return get_coverage_stats()

# ─── CHATBOT ROUTES ──────────────────────────────────────────────────────────

from chatbot import chat as chatbot_chat

@app.get("/chat", response_class=HTMLResponse)
async def page_chat(request: Request):
    return templates.TemplateResponse(request, "chat.html", {
        "title": "AI Assistant"
    })

@app.post("/api/chat")
async def api_chat(request: Request):
    import asyncio
    from concurrent.futures import ThreadPoolExecutor

    body = await request.json()
    message    = body.get("message", "").strip()
    history    = body.get("history", [])
    filters    = body.get("filters", {})
    session_id = body.get("session_id") or None

    if not message:
        raise HTTPException(400, "Message tidak boleh kosong")

    try:
        loop = asyncio.get_event_loop()
        result = await asyncio.wait_for(
            loop.run_in_executor(
                None,
                lambda: chatbot_chat(message, history=history, filters=filters,
                                     session_id=session_id)
            ),
            timeout=120.0
        )
        return result
    except asyncio.TimeoutError:
        return JSONResponse(status_code=200, content={
            "answer": (
                "Maaf, permintaan membutuhkan waktu terlalu lama dan terpaksa dibatalkan.\n\n"
                "Coba sederhanakan pertanyaan atau tanyakan hal yang lebih spesifik."
            ),
            "intent": "error",
            "error": "timeout",
        })
    except Exception as e:
        import traceback, logging
        logging.error(f"[CHAT ERROR] {e}\n{traceback.format_exc()}")
        return JSONResponse(status_code=200, content={
            "answer": f"Maaf, terjadi kesalahan: {str(e)}",
            "intent": "error",
            "error": str(e),
        })

# ─── TAG MAPPING ROUTES ──────────────────────────────────────────────────────

import db_tag_mapping as _db_tm
from tag_resolver import generate_candidates

_mapping_running = False
_last_mapping_result = None
_mapping_current_table = None


def _run_generate_candidates(source_table: str = None):
    global _mapping_running, _last_mapping_result, _mapping_current_table
    _mapping_running = True
    _mapping_current_table = source_table or "all"
    try:
        _last_mapping_result = generate_candidates(source_table=source_table)
    finally:
        _mapping_running = False
        _mapping_current_table = None


@app.get("/tag-mapping", response_class=HTMLResponse)
async def page_tag_mapping(
    request: Request,
    ru: str = "",
    source_table: str = "",
    status: str = "pending",
    page: int = 1,
):
    limit = 20
    groups, total_groups = _db_tm.get_grouped_mappings(
        ru=ru or None,
        source_table=source_table or None,
        status=status or None,
        page=page,
        limit=limit,
    )
    stats = _db_tm.get_mapping_stats()
    ru_list = _db_tm.get_ru_list()
    # All tables from TABLE_CATALOG for generate dropdown (exclude master + doc)
    skip = {"master_data_equipment", "doc_registry"}
    all_tables = sorted([e["table"] for e in TABLE_CATALOG if e["table"] not in skip and e.get("tag_col")])
    # Tables already in tag_mapping for filter dropdown
    table_list = _db_tm.get_source_table_list()
    return templates.TemplateResponse(request, "tag_mapping.html", {
        "groups": groups,
        "total_groups": total_groups,
        "page": page,
        "total_pages": (total_groups + limit - 1) // limit,
        "stats": stats,
        "ru_list": ru_list,
        "table_list": table_list,
        "all_tables": all_tables,
        "filters": {"ru": ru, "source_table": source_table, "status": status},
        "title": "Tag Mapping",
    })


@app.post("/api/tag-mapping/generate")
async def api_generate_mappings(background_tasks: BackgroundTasks, source_table: str = None):
    global _mapping_running
    if _mapping_running:
        return {"message": "Generate sudah berjalan", "running": True}
    background_tasks.add_task(_run_generate_candidates, source_table)
    msg = f"Generate kandidat untuk tabel '{source_table}' dimulai" if source_table else "Generate semua tabel dimulai"
    return {"message": msg, "running": True}


@app.get("/api/tag-mapping/status")
async def api_mapping_status():
    return {
        "running": _mapping_running,
        "current_table": _mapping_current_table,
        "last_result": _last_mapping_result,
    }


@app.post("/api/tag-mapping/{mapping_id}/approve")
async def api_approve(mapping_id: int, request: Request):
    body = {}
    try:
        body = await request.json()
    except Exception:
        pass
    validated_by = body.get("validated_by", "user")
    _db_tm.approve_mapping(mapping_id, validated_by)
    return {"ok": True}


@app.post("/api/tag-mapping/{mapping_id}/reject")
async def api_reject(mapping_id: int, request: Request):
    body = {}
    try:
        body = await request.json()
    except Exception:
        pass
    validated_by = body.get("validated_by", "user")
    _db_tm.reject_mapping(mapping_id, validated_by)
    return {"ok": True}


@app.post("/api/tag-mapping/bulk-approve")
async def api_bulk_approve(request: Request):
    body = await request.json()
    ids = body.get("ids", [])
    validated_by = body.get("validated_by", "user")
    approved = _db_tm.bulk_approve(ids, validated_by)
    return {"approved": approved}

@app.post("/api/neo4j/reset-relations")
async def api_reset_relations():
    from neo4j_sync import reset_all_relations
    result = reset_all_relations()
    return result

@app.post("/api/neo4j/clear-synced-nodes")
async def api_clear_synced_nodes():
    from neo4j_sync import clear_synced_nodes
    result = clear_synced_nodes()
    return result

@app.post("/api/neo4j/full-resync")
async def api_full_resync(background_tasks: BackgroundTasks):
    from neo4j_sync import full_resync
    global _table_sync_running
    if _table_sync_running:
        return {"success": False, "error": "Sync sedang berjalan, tunggu selesai"}
    _table_sync_running = True
    def _run():
        global _table_sync_running, _last_table_sync_result
        try:
            _last_table_sync_result = full_resync()
        finally:
            _table_sync_running = False
    background_tasks.add_task(_run)
    return {"success": True, "message": "Full re-sync dimulai di background"}

@app.post("/api/neo4j/reset-all")
async def api_reset_all_neo4j(background_tasks: BackgroundTasks):
    from neo4j_sync import reset_all_neo4j
    def _run():
        reset_all_neo4j()
    background_tasks.add_task(_run)
    return {"success": True, "message": "Reset semua node dan relasi Neo4j dimulai di background"}

@app.post("/api/neo4j/reset-all-resync")
async def api_reset_all_and_resync(background_tasks: BackgroundTasks):
    from neo4j_sync import reset_all_and_resync
    global _table_sync_running
    if _table_sync_running:
        return {"success": False, "error": "Sync sedang berjalan, tunggu selesai"}
    _table_sync_running = True
    def _run():
        global _table_sync_running, _last_table_sync_result
        try:
            _last_table_sync_result = reset_all_and_resync()
        finally:
            _table_sync_running = False
    background_tasks.add_task(_run)
    return {"success": True, "message": "Reset total + re-sync dimulai di background"}
