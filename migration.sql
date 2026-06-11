-- Enable pgvector
CREATE EXTENSION IF NOT EXISTS vector;

-- Master dokumen
CREATE TABLE IF NOT EXISTS doc_registry (
    id SERIAL PRIMARY KEY,
    judul TEXT NOT NULL,
    tipe_dokumen TEXT NOT NULL,
    ru TEXT,
    nomor_dokumen TEXT,
    deskripsi TEXT,
    file_path TEXT NOT NULL,
    file_name TEXT NOT NULL,
    file_type TEXT NOT NULL,
    file_size_bytes BIGINT,
    total_pages INT DEFAULT 0,
    total_chunks INT DEFAULT 0,
    processing_meta JSONB DEFAULT '{}',
    status TEXT DEFAULT 'pending',
    error_message TEXT,
    uploaded_by TEXT DEFAULT 'system',
    uploaded_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- Chunks teks untuk RAG
CREATE TABLE IF NOT EXISTS doc_chunks (
    id SERIAL PRIMARY KEY,
    doc_id INT REFERENCES doc_registry(id) ON DELETE CASCADE,
    chunk_index INT,
    halaman INT,
    slide_number INT,
    slide_title TEXT,
    sheet_name TEXT,
    content TEXT NOT NULL,
    embedding vector(1536),
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- Index untuk vector similarity search
CREATE INDEX IF NOT EXISTS doc_chunks_embedding_idx
    ON doc_chunks USING ivfflat (embedding vector_cosine_ops)
    WITH (lists = 100);

-- Baris tabel yang diekstrak (structured data)
CREATE TABLE IF NOT EXISTS doc_table_rows (
    id SERIAL PRIMARY KEY,
    doc_id INT REFERENCES doc_registry(id) ON DELETE CASCADE,
    halaman INT,
    sheet_name TEXT,
    row_index INT,
    row_data JSONB NOT NULL,
    tag_number TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- Relasi dokumen <-> tag number
CREATE TABLE IF NOT EXISTS doc_tag_links (
    id SERIAL PRIMARY KEY,
    doc_id INT REFERENCES doc_registry(id) ON DELETE CASCADE,
    tag_number TEXT NOT NULL,
    link_type TEXT DEFAULT 'manual',
    created_at TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(doc_id, tag_number)
);

-- Versioning dokumen
CREATE TABLE IF NOT EXISTS doc_versions (
    id SERIAL PRIMARY KEY,
    doc_id INT REFERENCES doc_registry(id) ON DELETE CASCADE,
    version_number INT NOT NULL,
    file_path TEXT NOT NULL,
    file_name TEXT NOT NULL,
    catatan TEXT,
    uploaded_by TEXT DEFAULT 'system',
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- Index umum
CREATE INDEX IF NOT EXISTS idx_doc_registry_ru ON doc_registry(ru);
CREATE INDEX IF NOT EXISTS idx_doc_registry_status ON doc_registry(status);
CREATE INDEX IF NOT EXISTS idx_doc_registry_tipe ON doc_registry(tipe_dokumen);
CREATE INDEX IF NOT EXISTS idx_doc_chunks_doc_id ON doc_chunks(doc_id);
CREATE INDEX IF NOT EXISTS idx_doc_table_rows_doc_id ON doc_table_rows(doc_id);
CREATE INDEX IF NOT EXISTS idx_doc_table_rows_tag ON doc_table_rows(tag_number);
CREATE INDEX IF NOT EXISTS idx_doc_tag_links_doc_id ON doc_tag_links(doc_id);
CREATE INDEX IF NOT EXISTS idx_doc_tag_links_tag ON doc_tag_links(tag_number);
