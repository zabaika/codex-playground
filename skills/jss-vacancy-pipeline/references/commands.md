# Vacancy Pipeline Commands

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

Start local API-lite:

```bash
PYTHONPATH="$JSS_ROOT/src:$PLAYGROUND_ROOT" python3 -m job_search.interfaces.api.server --config-path "$CONFIG_PATH" --workspace-path "$WORKSPACE_PATH" --host 127.0.0.1 --port 8765
```

Use API-lite when available:

```bash
curl -sS -X POST "$JSS_API_URL/vacancies/import-json" -H 'Content-Type: application/json' -d '{"candidate_id":"<candidate_id>","source_kind":"manual","items":[{"title":"<title>","company_name":"<company>"}]}'
curl -sS -X POST "$JSS_API_URL/vacancies/import-text" -H 'Content-Type: application/json' -d '{"candidate_id":"<candidate_id>","content_text":"Title: <title>\nCompany: <company>","source_origin":"manual_text"}'
curl -sS -X POST "$JSS_API_URL/vacancies/import-linkedin-text" -H 'Content-Type: application/json' -d '{"candidate_id":"<candidate_id>","content_text":"<copied LinkedIn job text>","source_origin":"manual_page"}'
curl -sS -X POST "$JSS_API_URL/vacancies/import-hh-ru-text" -H 'Content-Type: application/json' -d '{"candidate_id":"<candidate_id>","content_text":"<copied hh.ru vacancy/search-results text>","source_origin":"search_results"}'
curl -sS -X POST "$JSS_API_URL/vacancies/url-seeds" -H 'Content-Type: application/json' -d '{"candidate_id":"<candidate_id>","source_url":"<job_url>","platform":"linkedin","idempotency_key":"<stable_seed_key>"}'
curl -sS -X POST "$JSS_API_URL/vacancies/url-seeds/preview" -H 'Content-Type: application/json' -d '{"candidate_id":"<candidate_id>","url_seed_id":"<url_seed_id>","content_text":"<manually copied vacancy page text>"}'
curl -sS -X POST "$JSS_API_URL/vacancies/url-seeds/confirm" -H 'Content-Type: application/json' -d '{"candidate_id":"<candidate_id>","url_seed_id":"<url_seed_id>"}'
curl -sS "$JSS_API_URL/vacancies/url-seeds?candidate_id=<candidate_id>&seed_status=pending"
curl -sS "$JSS_API_URL/vacancies/rank?candidate_id=<candidate_id>"
curl -sS -X POST "$JSS_API_URL/vacancies/shortlist" -H 'Content-Type: application/json' -d '{"candidate_id":"<candidate_id>","canonical_vacancy_id":"<canonical_vacancy_id>"}'
curl -sS -X POST "$JSS_API_URL/vacancies/application-draft" -H 'Content-Type: application/json' -d '{"candidate_id":"<candidate_id>","canonical_vacancy_id":"<canonical_vacancy_id>","language":"en"}'
curl -sS -X POST "$JSS_API_URL/vacancies/application-payload" -H 'Content-Type: application/json' -d '{"candidate_id":"<candidate_id>","canonical_vacancy_id":"<canonical_vacancy_id>","language":"en"}'
curl -sS -X POST "$JSS_API_URL/vacancies/resume" -H 'Content-Type: application/json' -d '{"candidate_id":"<candidate_id>","canonical_vacancy_id":"<canonical_vacancy_id>","language":"en"}'
curl -sS -X POST "$JSS_API_URL/vacancies/resume-final" -H 'Content-Type: application/json' -d '{"candidate_id":"<candidate_id>","artifact_id":"<resume_vacancy_artifact_id>","allow_warnings":false}'
curl -sS -X POST "$JSS_API_URL/vacancies/interview-rounds" -H 'Content-Type: application/json' -d '{"candidate_id":"<candidate_id>","application_id":"<application_id>","round_type":"technical","scheduled_at":"<iso_datetime>","idempotency_key":"<stable_key>"}'
curl -sS -X POST "$JSS_API_URL/vacancies/interview-rounds/state" -H 'Content-Type: application/json' -d '{"candidate_id":"<candidate_id>","interview_round_id":"<interview_round_id>","round_state":"completed","completed_at":"<iso_datetime>"}'
curl -sS "$JSS_API_URL/vacancies/interview-rounds?candidate_id=<candidate_id>"
curl -sS -X POST "$JSS_API_URL/vacancies/touchpoints" -H 'Content-Type: application/json' -d '{"candidate_id":"<candidate_id>","canonical_vacancy_id":"<canonical_vacancy_id>","follow_up_due_at":"<iso_datetime>"}'
curl -sS "$JSS_API_URL/vacancies/daily-actions?candidate_id=<candidate_id>"
curl -sS "$JSS_API_URL/vacancies/material-change-review?candidate_id=<candidate_id>"
curl -sS "$JSS_API_URL/vacancies/pipeline-report?candidate_id=<candidate_id>"
curl -sS "$JSS_API_URL/system/strategy-report?candidate_id=<candidate_id>"
curl -sS "$JSS_API_URL/vacancies/board-checklist?candidate_id=<candidate_id>&platform=linkedin"
curl -sS "$JSS_API_URL/vacancies/approvals?candidate_id=<candidate_id>"
curl -sS "$JSS_API_URL/vacancies/board-actions?candidate_id=<candidate_id>&platform=linkedin"
curl -sS "$JSS_API_URL/vacancies/reconciliation?candidate_id=<candidate_id>&review_status=open"
```

## Import / Rank / Shortlist

```bash
python3 -m job_search.interfaces.cli.vacancy_cli --config-path "$CONFIG_PATH" --workspace-path "$WORKSPACE_PATH" import-json --candidate-id "<candidate_id>" --source-kind manual --items-path "<vacancies.json>"
python3 -m job_search.interfaces.cli.vacancy_cli --config-path "$CONFIG_PATH" --workspace-path "$WORKSPACE_PATH" import-text --candidate-id "<candidate_id>" --content-path "<vacancy-text.md>" --source-origin manual_text
python3 -m job_search.interfaces.cli.vacancy_cli --config-path "$CONFIG_PATH" --workspace-path "$WORKSPACE_PATH" import-linkedin-text --candidate-id "<candidate_id>" --content-path "<linkedin-job-text.md>" --source-origin manual_page
python3 -m job_search.interfaces.cli.vacancy_cli --config-path "$CONFIG_PATH" --workspace-path "$WORKSPACE_PATH" import-hh-ru-text --candidate-id "<candidate_id>" --content-path "<hh-ru-vacancies.md>" --source-origin search_results
python3 -m job_search.interfaces.cli.vacancy_cli --config-path "$CONFIG_PATH" --workspace-path "$WORKSPACE_PATH" create-url-seed --candidate-id "<candidate_id>" --source-url "<job_url>" --platform linkedin --idempotency-key "<stable_seed_key>"
python3 -m job_search.interfaces.cli.vacancy_cli --config-path "$CONFIG_PATH" --workspace-path "$WORKSPACE_PATH" preview-url-seed --candidate-id "<candidate_id>" --url-seed-id "<url_seed_id>" --content-path "<copied-vacancy-page.md>"
python3 -m job_search.interfaces.cli.vacancy_cli --config-path "$CONFIG_PATH" --workspace-path "$WORKSPACE_PATH" confirm-url-seed-import --candidate-id "<candidate_id>" --url-seed-id "<url_seed_id>"
python3 -m job_search.interfaces.cli.vacancy_cli --config-path "$CONFIG_PATH" --workspace-path "$WORKSPACE_PATH" list-url-seeds --candidate-id "<candidate_id>" --seed-status pending
python3 -m job_search.interfaces.cli.vacancy_cli --config-path "$CONFIG_PATH" --workspace-path "$WORKSPACE_PATH" rank --candidate-id "<candidate_id>"
python3 -m job_search.interfaces.cli.vacancy_cli --config-path "$CONFIG_PATH" --workspace-path "$WORKSPACE_PATH" shortlist --candidate-id "<candidate_id>" --canonical-vacancy-id "<canonical_vacancy_id>"
```

## Application Prep

```bash
python3 -m job_search.interfaces.cli.vacancy_cli --config-path "$CONFIG_PATH" --workspace-path "$WORKSPACE_PATH" create-application-draft --candidate-id "<candidate_id>" --canonical-vacancy-id "<canonical_vacancy_id>"
python3 -m job_search.interfaces.cli.vacancy_cli --config-path "$CONFIG_PATH" --workspace-path "$WORKSPACE_PATH" prepare-application-payload --candidate-id "<candidate_id>" --canonical-vacancy-id "<canonical_vacancy_id>" --language en
python3 -m job_search.interfaces.cli.vacancy_cli --config-path "$CONFIG_PATH" --workspace-path "$WORKSPACE_PATH" generate-vacancy-resume --candidate-id "<candidate_id>" --canonical-vacancy-id "<canonical_vacancy_id>" --language en
python3 -m job_search.interfaces.cli.vacancy_cli --config-path "$CONFIG_PATH" --workspace-path "$WORKSPACE_PATH" finalize-vacancy-resume --candidate-id "<candidate_id>" --artifact-id "<resume_vacancy_artifact_id>"
python3 -m job_search.interfaces.cli.vacancy_cli --config-path "$CONFIG_PATH" --workspace-path "$WORKSPACE_PATH" create-interview-round --candidate-id "<candidate_id>" --application-id "<application_id>" --round-type technical --scheduled-at "<iso_datetime>" --idempotency-key "<stable_key>"
python3 -m job_search.interfaces.cli.vacancy_cli --config-path "$CONFIG_PATH" --workspace-path "$WORKSPACE_PATH" update-interview-round --candidate-id "<candidate_id>" --interview-round-id "<interview_round_id>" --round-state completed --completed-at "<iso_datetime>"
python3 -m job_search.interfaces.cli.vacancy_cli --config-path "$CONFIG_PATH" --workspace-path "$WORKSPACE_PATH" list-interview-rounds --candidate-id "<candidate_id>"
```

## Touchpoints / Reminders

```bash
python3 -m job_search.interfaces.cli.vacancy_cli --config-path "$CONFIG_PATH" --workspace-path "$WORKSPACE_PATH" create-touchpoint --candidate-id "<candidate_id>" --canonical-vacancy-id "<canonical_vacancy_id>" --application-id "<application_id>" --channel email --direction outgoing --notes "<short note>" --message-artifact-id "<artifact_id>" --follow-up-due-at "<iso_datetime>"
python3 -m job_search.interfaces.cli.vacancy_cli --config-path "$CONFIG_PATH" --workspace-path "$WORKSPACE_PATH" daily-actions --candidate-id "<candidate_id>"
python3 -m job_search.interfaces.cli.vacancy_cli --config-path "$CONFIG_PATH" --workspace-path "$WORKSPACE_PATH" material-change-review --candidate-id "<candidate_id>"
python3 -m job_search.interfaces.cli.vacancy_cli --config-path "$CONFIG_PATH" --workspace-path "$WORKSPACE_PATH" resolve-reminder --reminder-id "<reminder_id>"
```

## Processed / Report

```bash
python3 -m job_search.interfaces.cli.vacancy_cli --config-path "$CONFIG_PATH" --workspace-path "$WORKSPACE_PATH" mark-processed --candidate-id "<candidate_id>" --canonical-vacancy-id "<canonical_vacancy_id>"
python3 -m job_search.interfaces.cli.vacancy_cli --config-path "$CONFIG_PATH" --workspace-path "$WORKSPACE_PATH" pipeline-report --candidate-id "<candidate_id>"
python3 -m job_search.interfaces.cli.system_cli --config-path "$CONFIG_PATH" --workspace-path "$WORKSPACE_PATH" strategy-report --candidate-id "<candidate_id>"
```

## Notes

- `rank` is explainable scoring, not mutation. Review `scoring_breakdown` for role, seniority, skill stack, company, location/work-model, compensation and dealbreakers.
- AI rerank is skill-side advisory review over deterministic `rank`, `pipeline-report`, and user-provided vacancy text. It must not mutate scores, shortlist, processed state, application state, or SQLite.
- `shortlist`, `mark-processed`, touchpoint creation, reminder resolution, and application commands are mutations.
- `import-text` is generic user-provided vacancy text intake. URL-only input without title/company is not importable by import commands.
- `create-url-seed` stores URL-only saved jobs as enrichment seeds. Seeds are not vacancies and do not appear in ranking until `preview-url-seed` yields exactly one vacancy and `confirm-url-seed-import` succeeds.
- `import-linkedin-text` is LinkedIn-aware copied/search/email text intake. Raw copied page text without URL can be imported with a missing external id warning; LinkedIn job-alert/recommended-jobs email text and search-results pages are parsed from markdown job cards; manually prepared CSV-like rows are supported, but native LinkedIn CSV vacancy export is not assumed; URL-only input without title/company must use URL seed enrichment flow.
- `import-hh-ru-text` is hh.ru-aware copied vacancy/search-results intake. It parses single vacancy pages with URL headers and search-result cards, preserving vacancy URL, employer, salary/experience/work-model metadata, publication/update date and location when available.
- `prepare-application-payload` creates review-ready artifacts; it does not submit externally.
- `application_payload_quality_gate` combines resume and message quality status for the prepared payload; it is still not external action approval.
- `artifact_acceptance`, `quality_gate_result`, and `external_action_approval` are separate. Completed submit/send/profile-update board actions require explicit `external_action_approval_id`.
- `create-interview-round` creates a real interview operational record and moves the application to `interviewing`; it is separate from touchpoints and reminders.
- `update-interview-round` changes only the interview round state; it does not close the application automatically.
- `strategy-report` is a read-only deterministic metrics projection. It includes funnel, role/company/source conversion, follow-up, quality, board action, resume effectiveness and position effectiveness metrics.
- Open reconciliation items appear in `daily-actions` as `review_reconciliation_item`; resolve them through `$jss-job-board-operations`.
- Resume effectiveness is attributed only through recorded `application_resume_attached` usage events.
- `material-change-review` is the explicit review bucket for processed vacancies that changed after processing; it must not move them back to `new`.
- `generate-vacancy-resume` creates or overwrites one `resume_vacancy` per candidate/vacancy and reports which source resume was used.
- `finalize-vacancy-resume` creates or overwrites `resume_vacancy_final`; use `--allow-warnings` only after explicit warning acceptance.
- Manual job-board actions belong to `$jss-job-board-operations`.
