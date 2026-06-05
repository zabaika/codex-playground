CREATE TABLE IF NOT EXISTS quality_gate_runs (
    quality_gate_run_id TEXT PRIMARY KEY,
    gate_name TEXT NOT NULL,
    subject_type TEXT NOT NULL,
    subject_id TEXT NOT NULL,
    candidate_id TEXT REFERENCES candidates(candidate_id) ON DELETE CASCADE,
    status TEXT NOT NULL,
    issues_json TEXT NOT NULL DEFAULT '[]',
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_quality_gate_runs_subject
ON quality_gate_runs(subject_type, subject_id, created_at);
