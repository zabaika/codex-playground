ALTER TABLE candidate_search_preferences
ADD COLUMN company_avoid_list_json TEXT NOT NULL DEFAULT '[]';

ALTER TABLE candidate_search_preferences
ADD COLUMN company_priorities_json TEXT NOT NULL DEFAULT '[]';

ALTER TABLE candidate_search_preferences
ADD COLUMN hybrid_policy TEXT;
