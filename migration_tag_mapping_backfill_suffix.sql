-- One-off backfill: auto-approve existing pending mappings where the variant
-- only differs from the canonical tag by a trailing numeric suffix like /00, /01.
-- Run manually once after deploying the exact_suffix matching logic in tag_resolver.py.
UPDATE tag_mapping
SET status = 'approved',
    match_method = 'exact_suffix',
    confidence = 1.0
WHERE tag_canonical = regexp_replace(tag_variant, '/\d+$', '')
  AND tag_variant != tag_canonical
  AND status = 'pending';
