CREATE TABLE IF NOT EXISTS approval_records (
    approval_id TEXT PRIMARY KEY,
    candidate_id TEXT NOT NULL REFERENCES candidates(candidate_id) ON DELETE CASCADE,
    approval_type TEXT NOT NULL,
    approval_state TEXT NOT NULL,
    actor TEXT NOT NULL,
    artifact_id TEXT REFERENCES artifacts(artifact_id) ON DELETE SET NULL,
    target_entity_type TEXT,
    target_entity_id TEXT,
    action_type TEXT,
    platform TEXT,
    external_target TEXT,
    reason TEXT,
    notes TEXT,
    idempotency_key TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE(candidate_id, approval_type, idempotency_key)
);

CREATE INDEX IF NOT EXISTS idx_approval_records_candidate_created
ON approval_records(candidate_id, created_at);

CREATE INDEX IF NOT EXISTS idx_approval_records_artifact
ON approval_records(candidate_id, artifact_id, approval_type, approval_state);
