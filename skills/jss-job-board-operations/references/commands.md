# Job Board Operations Commands

Use these commands from `PLAYGROUND_ROOT`.

## Variables

```bash
PLAYGROUND_ROOT="${PLAYGROUND_ROOT:-$HOME/Documents/Playground}"
JSS_ROOT="$PLAYGROUND_ROOT/tools/job-search-system"
PYTHONPATH="$JSS_ROOT/src:$PLAYGROUND_ROOT"
CONFIG_PATH="$JSS_ROOT/config/runtime.local.toml"
WORKSPACE_PATH="<workspace.local.toml>"
JSS_API_URL="http://127.0.0.1:8765"
```

## API-lite

```bash
curl -sS "$JSS_API_URL/vacancies/board-checklist?candidate_id=<candidate_id>&platform=linkedin"
curl -sS "$JSS_API_URL/vacancies/board-checklist?candidate_id=<candidate_id>&platform=linkedin&canonical_vacancy_id=<canonical_vacancy_id>"
curl -sS -X POST "$JSS_API_URL/vacancies/url-seeds" -H 'Content-Type: application/json' -d '{"candidate_id":"<candidate_id>","source_url":"<job_url>","platform":"linkedin","idempotency_key":"<stable_seed_key>"}'
curl -sS -X POST "$JSS_API_URL/vacancies/url-seeds/preview" -H 'Content-Type: application/json' -d '{"candidate_id":"<candidate_id>","url_seed_id":"<url_seed_id>","content_text":"<manually copied vacancy page text>"}'
curl -sS -X POST "$JSS_API_URL/vacancies/url-seeds/confirm" -H 'Content-Type: application/json' -d '{"candidate_id":"<candidate_id>","url_seed_id":"<url_seed_id>"}'
curl -sS "$JSS_API_URL/vacancies/url-seeds?candidate_id=<candidate_id>&seed_status=pending"
curl -sS -X POST "$JSS_API_URL/vacancies/artifact-acceptance" -H 'Content-Type: application/json' -d '{"candidate_id":"<candidate_id>","artifact_id":"<artifact_id>","approval_state":"accepted","idempotency_key":"<stable_acceptance_key>"}'
curl -sS -X POST "$JSS_API_URL/vacancies/external-action-approval" -H 'Content-Type: application/json' -d '{"candidate_id":"<candidate_id>","platform":"linkedin","action_type":"application_submitted","canonical_vacancy_id":"<canonical_vacancy_id>","application_id":"<application_id>","artifact_id":"<artifact_id>","external_target":"<job_url>","idempotency_key":"<stable_approval_key>"}'
curl -sS -X POST "$JSS_API_URL/vacancies/board-actions" -H 'Content-Type: application/json' -d '{"candidate_id":"<candidate_id>","platform":"linkedin","action_type":"application_submitted","canonical_vacancy_id":"<canonical_vacancy_id>","application_id":"<application_id>","artifact_id":"<artifact_id>","external_target":"<job_url>","occurred_at":"<iso_datetime>","idempotency_key":"<stable_key>","external_action_approval_id":"<approval_id>"}'
curl -sS "$JSS_API_URL/vacancies/board-actions?candidate_id=<candidate_id>&platform=linkedin"
curl -sS "$JSS_API_URL/vacancies/reconciliation?candidate_id=<candidate_id>&review_status=open"
curl -sS -X POST "$JSS_API_URL/vacancies/reconciliation/resolve" -H 'Content-Type: application/json' -d '{"candidate_id":"<candidate_id>","reconciliation_item_id":"<reconciliation_item_id>","review_status":"resolved","resolution_notes":"<notes>"}'
curl -sS "$JSS_API_URL/vacancies/approvals?candidate_id=<candidate_id>&approval_type=external_action_approval"
```

## CLI Fallback

```bash
python3 -m job_search.interfaces.cli.vacancy_cli --config-path "$CONFIG_PATH" --workspace-path "$WORKSPACE_PATH" board-checklist --candidate-id "<candidate_id>" --platform linkedin
python3 -m job_search.interfaces.cli.vacancy_cli --config-path "$CONFIG_PATH" --workspace-path "$WORKSPACE_PATH" board-checklist --candidate-id "<candidate_id>" --platform linkedin --canonical-vacancy-id "<canonical_vacancy_id>"
python3 -m job_search.interfaces.cli.vacancy_cli --config-path "$CONFIG_PATH" --workspace-path "$WORKSPACE_PATH" create-url-seed --candidate-id "<candidate_id>" --source-url "<job_url>" --platform linkedin --idempotency-key "<stable_seed_key>"
python3 -m job_search.interfaces.cli.vacancy_cli --config-path "$CONFIG_PATH" --workspace-path "$WORKSPACE_PATH" preview-url-seed --candidate-id "<candidate_id>" --url-seed-id "<url_seed_id>" --content-path "<copied-vacancy-page.md>"
python3 -m job_search.interfaces.cli.vacancy_cli --config-path "$CONFIG_PATH" --workspace-path "$WORKSPACE_PATH" confirm-url-seed-import --candidate-id "<candidate_id>" --url-seed-id "<url_seed_id>"
python3 -m job_search.interfaces.cli.vacancy_cli --config-path "$CONFIG_PATH" --workspace-path "$WORKSPACE_PATH" list-url-seeds --candidate-id "<candidate_id>" --seed-status pending
python3 -m job_search.interfaces.cli.vacancy_cli --config-path "$CONFIG_PATH" --workspace-path "$WORKSPACE_PATH" record-artifact-acceptance --candidate-id "<candidate_id>" --artifact-id "<artifact_id>" --idempotency-key "<stable_acceptance_key>"
python3 -m job_search.interfaces.cli.vacancy_cli --config-path "$CONFIG_PATH" --workspace-path "$WORKSPACE_PATH" record-external-action-approval --candidate-id "<candidate_id>" --platform linkedin --action-type application_submitted --canonical-vacancy-id "<canonical_vacancy_id>" --application-id "<application_id>" --artifact-id "<artifact_id>" --external-target "<job_url>" --idempotency-key "<stable_approval_key>"
python3 -m job_search.interfaces.cli.vacancy_cli --config-path "$CONFIG_PATH" --workspace-path "$WORKSPACE_PATH" record-board-action --candidate-id "<candidate_id>" --platform linkedin --action-type application_submitted --canonical-vacancy-id "<canonical_vacancy_id>" --application-id "<application_id>" --artifact-id "<artifact_id>" --external-target "<job_url>" --occurred-at "<iso_datetime>" --idempotency-key "<stable_key>" --external-action-approval-id "<approval_id>"
python3 -m job_search.interfaces.cli.vacancy_cli --config-path "$CONFIG_PATH" --workspace-path "$WORKSPACE_PATH" list-board-actions --candidate-id "<candidate_id>" --platform linkedin
python3 -m job_search.interfaces.cli.vacancy_cli --config-path "$CONFIG_PATH" --workspace-path "$WORKSPACE_PATH" list-reconciliation --candidate-id "<candidate_id>" --review-status open
python3 -m job_search.interfaces.cli.vacancy_cli --config-path "$CONFIG_PATH" --workspace-path "$WORKSPACE_PATH" resolve-reconciliation --candidate-id "<candidate_id>" --reconciliation-item-id "<reconciliation_item_id>" --review-status resolved --resolution-notes "<notes>"
python3 -m job_search.interfaces.cli.vacancy_cli --config-path "$CONFIG_PATH" --workspace-path "$WORKSPACE_PATH" list-approvals --candidate-id "<candidate_id>" --approval-type external_action_approval
```

## Action Types

- `saved_search_configured`
- `vacancy_opened`
- `application_submitted`
- `message_sent`
- `profile_updated`
- `visibility_checked`
- `vacancy_hidden`
- `manual_note`

`application_submitted`, `message_sent`, and `profile_updated` require `artifact_id` and `external_action_approval_id` when logged as `completed`.

## Reconciliation Outcomes

- `auto_accept`: external/manual signal has enough internal context and no review is required.
- `record_only`: signal is informational and does not need lifecycle mutation.
- `needs_review`: operator review is required before any internal state change.
- `reject_as_invalid`: signal is not a valid drift event, for example a planned action that has not happened yet.

Reconciliation items never create a second lifecycle. If internal state must change, run the explicit vacancy/application command after review.
