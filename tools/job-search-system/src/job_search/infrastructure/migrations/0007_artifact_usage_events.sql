CREATE TABLE IF NOT EXISTS artifact_usage_events (
    artifact_usage_event_id TEXT PRIMARY KEY,
    artifact_id TEXT NOT NULL REFERENCES artifacts(artifact_id) ON DELETE CASCADE,
    candidate_id TEXT REFERENCES candidates(candidate_id) ON DELETE CASCADE,
    usage_type TEXT NOT NULL,
    target_entity_type TEXT,
    target_entity_id TEXT,
    external_target TEXT,
    occurred_at TEXT NOT NULL,
    notes TEXT
);

CREATE INDEX IF NOT EXISTS idx_artifact_usage_events_artifact
ON artifact_usage_events(artifact_id, occurred_at);
