CREATE TABLE IF NOT EXISTS interview_rounds (
    interview_round_id TEXT PRIMARY KEY,
    candidate_id TEXT NOT NULL REFERENCES candidates(candidate_id) ON DELETE CASCADE,
    application_id TEXT NOT NULL REFERENCES applications(application_id) ON DELETE CASCADE,
    canonical_vacancy_id TEXT NOT NULL REFERENCES canonical_vacancies(canonical_vacancy_id) ON DELETE CASCADE,
    round_type TEXT NOT NULL,
    round_state TEXT NOT NULL,
    scheduled_at TEXT,
    completed_at TEXT,
    interviewer_name TEXT,
    notes TEXT,
    idempotency_key TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(candidate_id, idempotency_key)
);

CREATE INDEX IF NOT EXISTS idx_interview_rounds_candidate_scheduled
ON interview_rounds(candidate_id, scheduled_at, updated_at);

CREATE INDEX IF NOT EXISTS idx_interview_rounds_application
ON interview_rounds(candidate_id, application_id);
