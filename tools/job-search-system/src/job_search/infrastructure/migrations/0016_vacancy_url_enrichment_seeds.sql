CREATE TABLE IF NOT EXISTS vacancy_url_enrichment_seeds (
    url_seed_id TEXT PRIMARY KEY,
    candidate_id TEXT NOT NULL REFERENCES candidates(candidate_id) ON DELETE CASCADE,
    platform TEXT NOT NULL,
    source_url TEXT NOT NULL,
    source_origin TEXT NOT NULL,
    seed_status TEXT NOT NULL,
    latest_preview_json TEXT,
    imported_canonical_vacancy_id TEXT REFERENCES canonical_vacancies(canonical_vacancy_id) ON DELETE SET NULL,
    imported_source_occurrence_id TEXT REFERENCES source_occurrences(source_occurrence_id) ON DELETE SET NULL,
    notes TEXT,
    idempotency_key TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    imported_at TEXT,
    rejected_at TEXT,
    rejection_reason TEXT,
    UNIQUE(candidate_id, source_url),
    UNIQUE(candidate_id, idempotency_key)
);

CREATE INDEX IF NOT EXISTS idx_vacancy_url_enrichment_seeds_candidate_status
ON vacancy_url_enrichment_seeds(candidate_id, seed_status, updated_at);

CREATE INDEX IF NOT EXISTS idx_vacancy_url_enrichment_seeds_platform
ON vacancy_url_enrichment_seeds(candidate_id, platform);
