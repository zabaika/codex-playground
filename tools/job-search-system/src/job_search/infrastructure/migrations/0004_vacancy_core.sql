CREATE TABLE IF NOT EXISTS canonical_vacancies (
    canonical_vacancy_id TEXT PRIMARY KEY,
    candidate_id TEXT NOT NULL REFERENCES candidates(candidate_id) ON DELETE CASCADE,
    company_name TEXT NOT NULL,
    role_title TEXT NOT NULL,
    location_text TEXT,
    normalized_company_name TEXT NOT NULL,
    normalized_role_title TEXT NOT NULL,
    normalized_location_text TEXT,
    dedupe_key TEXT NOT NULL,
    workflow_stage TEXT NOT NULL,
    processed INTEGER NOT NULL DEFAULT 0,
    hidden INTEGER NOT NULL DEFAULT 0,
    blacklisted INTEGER NOT NULL DEFAULT 0,
    material_change_detected INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(candidate_id, dedupe_key)
);

CREATE INDEX IF NOT EXISTS idx_canonical_vacancies_stage
ON canonical_vacancies(workflow_stage, processed, hidden, blacklisted);

CREATE INDEX IF NOT EXISTS idx_canonical_vacancies_candidate_updated
ON canonical_vacancies(candidate_id, updated_at);

CREATE TABLE IF NOT EXISTS source_occurrences (
    source_occurrence_id TEXT PRIMARY KEY,
    candidate_id TEXT NOT NULL REFERENCES candidates(candidate_id) ON DELETE CASCADE,
    canonical_vacancy_id TEXT NOT NULL REFERENCES canonical_vacancies(canonical_vacancy_id) ON DELETE CASCADE,
    source_kind TEXT NOT NULL,
    source_url TEXT,
    external_vacancy_id TEXT,
    source_title TEXT NOT NULL,
    source_company_name TEXT NOT NULL,
    source_location_text TEXT,
    content_hash TEXT NOT NULL,
    raw_payload_json TEXT NOT NULL,
    imported_at TEXT NOT NULL,
    last_seen_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_source_occurrences_vacancy
ON source_occurrences(canonical_vacancy_id, imported_at);

CREATE INDEX IF NOT EXISTS idx_source_occurrences_candidate_imported
ON source_occurrences(candidate_id, imported_at);

CREATE TABLE IF NOT EXISTS applications (
    application_id TEXT PRIMARY KEY,
    candidate_id TEXT NOT NULL REFERENCES candidates(candidate_id) ON DELETE CASCADE,
    canonical_vacancy_id TEXT NOT NULL REFERENCES canonical_vacancies(canonical_vacancy_id) ON DELETE CASCADE,
    application_state TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(candidate_id, canonical_vacancy_id)
);

CREATE INDEX IF NOT EXISTS idx_applications_candidate_updated
ON applications(candidate_id, updated_at);
