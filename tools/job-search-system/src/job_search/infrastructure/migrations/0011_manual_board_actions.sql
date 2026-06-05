CREATE TABLE IF NOT EXISTS manual_board_actions (
    board_action_id TEXT PRIMARY KEY,
    candidate_id TEXT NOT NULL REFERENCES candidates(candidate_id) ON DELETE CASCADE,
    platform TEXT NOT NULL,
    action_type TEXT NOT NULL,
    action_state TEXT NOT NULL,
    canonical_vacancy_id TEXT REFERENCES canonical_vacancies(canonical_vacancy_id) ON DELETE SET NULL,
    application_id TEXT REFERENCES applications(application_id) ON DELETE SET NULL,
    artifact_id TEXT REFERENCES artifacts(artifact_id) ON DELETE SET NULL,
    external_target TEXT,
    occurred_at TEXT NOT NULL,
    notes TEXT,
    idempotency_key TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(candidate_id, idempotency_key)
);

CREATE INDEX IF NOT EXISTS idx_manual_board_actions_candidate_occurred
ON manual_board_actions(candidate_id, occurred_at);

CREATE INDEX IF NOT EXISTS idx_manual_board_actions_vacancy
ON manual_board_actions(candidate_id, canonical_vacancy_id);
