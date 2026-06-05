CREATE TABLE IF NOT EXISTS reconciliation_items (
    reconciliation_item_id TEXT PRIMARY KEY,
    candidate_id TEXT NOT NULL REFERENCES candidates(candidate_id) ON DELETE CASCADE,
    board_action_id TEXT REFERENCES manual_board_actions(board_action_id) ON DELETE SET NULL,
    canonical_vacancy_id TEXT REFERENCES canonical_vacancies(canonical_vacancy_id) ON DELETE SET NULL,
    application_id TEXT REFERENCES applications(application_id) ON DELETE SET NULL,
    platform TEXT,
    external_target TEXT,
    drift_type TEXT NOT NULL,
    outcome TEXT NOT NULL,
    review_status TEXT NOT NULL,
    reason TEXT NOT NULL,
    recommended_action TEXT,
    idempotency_key TEXT NOT NULL,
    resolution_notes TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    resolved_at TEXT,
    UNIQUE(candidate_id, idempotency_key)
);

CREATE INDEX IF NOT EXISTS idx_reconciliation_items_candidate_status
ON reconciliation_items(candidate_id, review_status, updated_at);

CREATE INDEX IF NOT EXISTS idx_reconciliation_items_board_action
ON reconciliation_items(candidate_id, board_action_id);
