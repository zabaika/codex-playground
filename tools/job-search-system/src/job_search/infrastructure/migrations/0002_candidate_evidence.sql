CREATE TABLE IF NOT EXISTS candidate_experience_entries (
    experience_entry_id TEXT PRIMARY KEY,
    candidate_id TEXT NOT NULL REFERENCES candidates(candidate_id) ON DELETE CASCADE,
    company_name TEXT NOT NULL,
    role_title TEXT NOT NULL,
    start_date TEXT,
    end_date TEXT,
    is_current INTEGER NOT NULL DEFAULT 0,
    location TEXT,
    company_context_text TEXT,
    company_industry TEXT,
    org_scale_hint TEXT,
    domain_context_json TEXT NOT NULL DEFAULT '[]',
    source_artifact_id TEXT REFERENCES artifacts(artifact_id) ON DELETE SET NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS candidate_achievement_evidence (
    achievement_evidence_id TEXT PRIMARY KEY,
    candidate_id TEXT NOT NULL REFERENCES candidates(candidate_id) ON DELETE CASCADE,
    experience_entry_id TEXT REFERENCES candidate_experience_entries(experience_entry_id) ON DELETE SET NULL,
    source_artifact_id TEXT REFERENCES artifacts(artifact_id) ON DELETE SET NULL,
    achievement_text TEXT NOT NULL,
    metric_name TEXT,
    metric_value REAL,
    metric_unit TEXT,
    metric_period TEXT,
    confidence_status TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS candidate_education_entries (
    education_entry_id TEXT PRIMARY KEY,
    candidate_id TEXT NOT NULL REFERENCES candidates(candidate_id) ON DELETE CASCADE,
    institution_name TEXT NOT NULL,
    degree TEXT,
    faculty TEXT,
    specialization TEXT,
    start_year INTEGER,
    end_year INTEGER,
    source_artifact_id TEXT REFERENCES artifacts(artifact_id) ON DELETE SET NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS candidate_skill_signals (
    skill_signal_id TEXT PRIMARY KEY,
    candidate_id TEXT NOT NULL REFERENCES candidates(candidate_id) ON DELETE CASCADE,
    skill_name TEXT NOT NULL,
    skill_group TEXT,
    context TEXT,
    source_artifact_id TEXT REFERENCES artifacts(artifact_id) ON DELETE SET NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS candidate_certifications (
    certification_id TEXT PRIMARY KEY,
    candidate_id TEXT NOT NULL REFERENCES candidates(candidate_id) ON DELETE CASCADE,
    certification_name TEXT NOT NULL,
    issuer TEXT,
    issued_at TEXT,
    expires_at TEXT,
    source_artifact_id TEXT REFERENCES artifacts(artifact_id) ON DELETE SET NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS candidate_publications (
    publication_id TEXT PRIMARY KEY,
    candidate_id TEXT NOT NULL REFERENCES candidates(candidate_id) ON DELETE CASCADE,
    title TEXT NOT NULL,
    publication_type TEXT,
    publication_url TEXT,
    published_at TEXT,
    source_artifact_id TEXT REFERENCES artifacts(artifact_id) ON DELETE SET NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS candidate_recommendations (
    recommendation_id TEXT PRIMARY KEY,
    candidate_id TEXT NOT NULL REFERENCES candidates(candidate_id) ON DELETE CASCADE,
    recommender_name TEXT NOT NULL,
    recommender_role TEXT,
    recommender_company TEXT,
    contact_hint TEXT,
    recommendation_text TEXT,
    source_artifact_id TEXT REFERENCES artifacts(artifact_id) ON DELETE SET NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS candidate_awards (
    award_id TEXT PRIMARY KEY,
    candidate_id TEXT NOT NULL REFERENCES candidates(candidate_id) ON DELETE CASCADE,
    award_name TEXT NOT NULL,
    awarder TEXT,
    awarded_at TEXT,
    source_artifact_id TEXT REFERENCES artifacts(artifact_id) ON DELETE SET NULL,
    created_at TEXT NOT NULL
);
