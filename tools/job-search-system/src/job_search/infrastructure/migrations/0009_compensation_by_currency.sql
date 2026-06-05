ALTER TABLE candidate_compensation
ADD COLUMN compensation_by_currency_json TEXT NOT NULL DEFAULT '{}';
