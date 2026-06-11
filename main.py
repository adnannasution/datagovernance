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
async def page_table_detail(request: Request, table_name: str,
                             search: str = "", page: int = 1):
    tbl_meta = next((t for t in TABLE_CATALOG if t["table"] == table_name), None)
    if not tbl_meta:
        raise HTTPException(404, "Tabel tidak ditemukan")
    limit = 25
    offset = (page - 1) * limit
    cols = get_table_columns(table_name)
    rows, total = get_table_rows(table_name, search=search or None,
                                  limit=limit, offset=offset)
    quality = get_data_quality(table_name, tbl_meta.get("tag_col"))
    return templates.TemplateResponse(request, "table_detail.html", {
        "tbl": tbl_meta,
        "cols": cols,
        "rows": rows,
        "total": total,
        "page": page,
        "total_pages": (total + limit - 1) // limit,
        "search": search,
        "quality": quality,
        "title": tbl_meta["label"]
    })

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
    stats = get_neo4j_stats()
    return templates.TemplateResponse(request, "knowledge_graph.html", {
        "stats": stats,
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

def _run_table_sync():
    global _table_sync_running, _last_table_sync_result
    _table_sync_running = True
    try:
        from neo4j_sync import sync_all_tables
        _last_table_sync_result = sync_all_tables()
    finally:
        _table_sync_running = False

@app.get("/api/neo4j/graph")
async def api_neo4j_graph(tag: str = ""):
    if not tag:
        raise HTTPException(status_code=400, detail="tag parameter required")
    from neo4j_sync import get_graph_for_tag
    return get_graph_for_tag(tag)

@app.post("/api/neo4j/sync/tables")
async def api_sync_all_tables(background_tasks: BackgroundTasks):
    from neo4j_sync import get_driver
    if get_driver() is None:
        raise HTTPException(status_code=503, detail="Neo4j tidak dapat diakses")
    background_tasks.add_task(_run_table_sync)
    return {"message": "Sync semua tabel dimulai di background"}

@app.get("/api/neo4j/sync/tables/status")
async def api_sync_tables_status():
    return {"running": _table_sync_running, "last_result": _last_table_sync_result}

# ─── CHATBOT ROUTES ──────────────────────────────────────────────────────────

from chatbot import chat as chatbot_chat

@app.get("/chat", response_class=HTMLResponse)
async def page_chat(request: Request):
    return templates.TemplateResponse(request, "chat.html", {
        "title": "AI Assistant"
    })

@app.post("/api/chat")
async def api_chat(request: Request):
    body = await request.json()
    message  = body.get("message", "").strip()
    history  = body.get("history", [])
    filters  = body.get("filters", {})

    if not message:
        raise HTTPException(400, "Message tidak boleh kosong")

    result = chatbot_chat(message, history=history, filters=filters)
    return result
