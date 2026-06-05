ALTER TABLE applications ADD COLUMN message_artifact_id TEXT REFERENCES artifacts(artifact_id) ON DELETE SET NULL;
