CREATE TABLE IF NOT EXISTS tag_mapping (
    id SERIAL PRIMARY KEY,
    tag_canonical VARCHAR(100) NOT NULL,  -- from master_data_equipment.equipment
    tag_variant VARCHAR(100) NOT NULL,
    source_table VARCHAR(100) NOT NULL,
    confidence NUMERIC(4,3) NOT NULL DEFAULT 1.0,
    match_method VARCHAR(50) NOT NULL DEFAULT 'exact',  -- exact, exact_suffix, fuzzy_token, fuzzy_levenshtein, manual
    validated_by VARCHAR(100),
    validated_at TIMESTAMP,
    status VARCHAR(20) NOT NULL DEFAULT 'approved',  -- approved, pending, rejected
    created_at TIMESTAMP DEFAULT NOW(),
    UNIQUE(tag_variant, source_table)
);

CREATE INDEX IF NOT EXISTS idx_tag_mapping_variant ON tag_mapping(tag_variant);
CREATE INDEX IF NOT EXISTS idx_tag_mapping_canonical ON tag_mapping(tag_canonical);
CREATE INDEX IF NOT EXISTS idx_tag_mapping_status ON tag_mapping(status);
CREATE INDEX IF NOT EXISTS idx_tag_mapping_ru_status ON tag_mapping(source_table, status);
