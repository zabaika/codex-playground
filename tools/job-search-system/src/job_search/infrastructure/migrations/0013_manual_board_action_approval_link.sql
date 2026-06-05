ALTER TABLE manual_board_actions
ADD COLUMN external_action_approval_id TEXT REFERENCES approval_records(approval_id) ON DELETE SET NULL;
