CREATE TABLE IF NOT EXISTS candidates (
    candidate_id TEXT PRIMARY KEY,
    display_name TEXT NOT NULL,
    status TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS candidate_profiles (
    candidate_id TEXT PRIMARY KEY REFERENCES candidates(candidate_id) ON DELETE CASCADE,
    full_name TEXT,
    primary_email TEXT,
    primary_phone TEXT,
    current_location TEXT,
    current_title TEXT,
    summary_text TEXT,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS candidate_external_profiles (
    external_profile_id TEXT PRIMARY KEY,
    candidate_id TEXT NOT NULL REFERENCES candidates(candidate_id) ON DELETE CASCADE,
    platform TEXT NOT NULL,
    profile_url TEXT NOT NULL,
    handle_or_slug TEXT,
    is_primary INTEGER NOT NULL DEFAULT 0,
    visibility_status TEXT,
    last_checked_at TEXT,
    last_source_artifact_id TEXT,
    created_at TEXT NOT NULL,
    UNIQUE(candidate_id, platform, profile_url)
);

CREATE TABLE IF NOT EXISTS candidate_work_authorizations (
    work_authorization_id TEXT PRIMARY KEY,
    candidate_id TEXT NOT NULL REFERENCES candidates(candidate_id) ON DELETE CASCADE,
    country_or_region TEXT NOT NULL,
    authorization_status TEXT NOT NULL,
    authorization_basis TEXT,
    valid_until TEXT,
    is_primary INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS candidate_language_proficiencies (
    language_proficiency_id TEXT PRIMARY KEY,
    candidate_id TEXT NOT NULL REFERENCES candidates(candidate_id) ON DELETE CASCADE,
    language_name TEXT NOT NULL,
    proficiency_level TEXT,
    is_primary INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    UNIQUE(candidate_id, language_name)
);

CREATE TABLE IF NOT EXISTS candidate_targets (
    candidate_id TEXT PRIMARY KEY REFERENCES candidates(candidate_id) ON DELETE CASCADE,
    target_roles_json TEXT NOT NULL DEFAULT '[]',
    target_markets_json TEXT NOT NULL DEFAULT '[]',
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS candidate_compensation (
    candidate_id TEXT PRIMARY KEY REFERENCES candidates(candidate_id) ON DELETE CASCADE,
    salary_floor INTEGER,
    salary_target INTEGER,
    salary_aspiration INTEGER,
    currency TEXT,
    compensation_notes TEXT,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS candidate_platform_preferences (
    candidate_id TEXT PRIMARY KEY REFERENCES candidates(candidate_id) ON DELETE CASCADE,
    linkedin_enabled INTEGER NOT NULL DEFAULT 1,
    hh_enabled INTEGER NOT NULL DEFAULT 1,
    other_platforms_json TEXT NOT NULL DEFAULT '[]',
    public_profile_preference TEXT,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS candidate_search_preferences (
    candidate_id TEXT PRIMARY KEY REFERENCES candidates(candidate_id) ON DELETE CASCADE,
    base_location TEXT,
    target_geographies_json TEXT NOT NULL DEFAULT '[]',
    remote_preference TEXT,
    relocation_preference TEXT,
    travel_preference TEXT,
    commute_preference TEXT,
    employment_type_preferences_json TEXT NOT NULL DEFAULT '[]',
    work_model_preferences_json TEXT NOT NULL DEFAULT '[]',
    confidential_search INTEGER NOT NULL DEFAULT 0,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS artifacts (
    artifact_id TEXT PRIMARY KEY,
    artifact_type TEXT NOT NULL,
    candidate_id TEXT REFERENCES candidates(candidate_id) ON DELETE CASCADE,
    version INTEGER NOT NULL DEFAULT 1,
    derived_from_artifact_id TEXT,
    storage_path TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    language TEXT,
    notes TEXT,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_artifacts_candidate_type ON artifacts(candidate_id, artifact_type);

CREATE TABLE IF NOT EXISTS candidate_sources (
    candidate_source_id TEXT PRIMARY KEY,
    candidate_id TEXT NOT NULL REFERENCES candidates(candidate_id) ON DELETE CASCADE,
    artifact_id TEXT NOT NULL REFERENCES artifacts(artifact_id) ON DELETE CASCADE,
    source_kind TEXT NOT NULL,
    source_origin TEXT NOT NULL,
    external_profile_id TEXT REFERENCES candidate_external_profiles(external_profile_id) ON DELETE SET NULL,
    imported_at TEXT NOT NULL,
    notes TEXT,
    UNIQUE(candidate_id, artifact_id)
);

CREATE TABLE IF NOT EXISTS candidate_profile_drafts (
    candidate_profile_draft_id TEXT PRIMARY KEY,
    candidate_id TEXT NOT NULL REFERENCES candidates(candidate_id) ON DELETE CASCADE,
    draft_artifact_id TEXT REFERENCES artifacts(artifact_id) ON DELETE SET NULL,
    source_set_id TEXT NOT NULL,
    draft_payload_json TEXT NOT NULL,
    field_conflicts_json TEXT NOT NULL,
    field_evidence_json TEXT NOT NULL,
    missing_fields_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS candidate_profile_snapshots (
    candidate_profile_snapshot_id TEXT PRIMARY KEY,
    candidate_id TEXT NOT NULL REFERENCES candidates(candidate_id) ON DELETE CASCADE,
    source_draft_id TEXT NOT NULL REFERENCES candidate_profile_drafts(candidate_profile_draft_id) ON DELETE CASCADE,
    snapshot_payload_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS audit_events (
    audit_event_id TEXT PRIMARY KEY,
    command_name TEXT NOT NULL,
    actor TEXT NOT NULL,
    entity_type TEXT NOT NULL,
    entity_id TEXT NOT NULL,
    previous_state_json TEXT,
    new_state_json TEXT,
    reason TEXT,
    source TEXT,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_audit_events_entity ON audit_events(entity_type, entity_id, created_at);
