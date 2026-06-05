CREATE TABLE IF NOT EXISTS touchpoints (
    touchpoint_id TEXT PRIMARY KEY,
    candidate_id TEXT NOT NULL REFERENCES candidates(candidate_id) ON DELETE CASCADE,
    canonical_vacancy_id TEXT REFERENCES canonical_vacancies(canonical_vacancy_id) ON DELETE CASCADE,
    application_id TEXT REFERENCES applications(application_id) ON DELETE CASCADE,
    message_artifact_id TEXT REFERENCES artifacts(artifact_id) ON DELETE SET NULL,
    channel TEXT NOT NULL,
    direction TEXT NOT NULL,
    touchpoint_state TEXT NOT NULL,
    contact_name TEXT,
    occurred_at TEXT NOT NULL,
    replied_at TEXT,
    notes TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_touchpoints_candidate_updated
ON touchpoints(candidate_id, updated_at);

CREATE TABLE IF NOT EXISTS follow_up_reminders (
    reminder_id TEXT PRIMARY KEY,
    candidate_id TEXT NOT NULL REFERENCES candidates(candidate_id) ON DELETE CASCADE,
    touchpoint_id TEXT REFERENCES touchpoints(touchpoint_id) ON DELETE CASCADE,
    canonical_vacancy_id TEXT REFERENCES canonical_vacancies(canonical_vacancy_id) ON DELETE CASCADE,
    application_id TEXT REFERENCES applications(application_id) ON DELETE CASCADE,
    reminder_type TEXT NOT NULL,
    due_at TEXT NOT NULL,
    reminder_status TEXT NOT NULL,
    notes TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_follow_up_reminders_candidate_due
ON follow_up_reminders(candidate_id, reminder_status, due_at);
