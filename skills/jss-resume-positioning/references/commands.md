# Resume Positioning Commands

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
curl -sS "$JSS_API_URL/candidates/active"
curl -sS "$JSS_API_URL/candidates/profile?candidate_id=<candidate_id>"
curl -sS "$JSS_API_URL/candidates/resume-kb-evidence?candidate_id=<candidate_id>&target_role=<role>&query=<query>&limit=5"
curl -sS -X POST "$JSS_API_URL/candidates/positioning-brief" -H 'Content-Type: application/json' -d '{"candidate_id":"<candidate_id>","target_role":"<role>","language":"en"}'
curl -sS -X POST "$JSS_API_URL/candidates/resume" -H 'Content-Type: application/json' -d '{"candidate_id":"<candidate_id>","target_role":"<role>","language":"en"}'
curl -sS -X POST "$JSS_API_URL/candidates/resume-quality" -H 'Content-Type: application/json' -d '{"artifact_id":"<artifact_id>"}'
curl -sS -X POST "$JSS_API_URL/candidates/resume-roast" -H 'Content-Type: application/json' -d '{"artifact_id":"<artifact_id>","target_role":"<role>"}'
curl -sS -X POST "$JSS_API_URL/candidates/resume-final" -H 'Content-Type: application/json' -d '{"artifact_id":"<artifact_id>","allow_warnings":false}'
curl -sS -X POST "$JSS_API_URL/vacancies/resume" -H 'Content-Type: application/json' -d '{"candidate_id":"<candidate_id>","canonical_vacancy_id":"<canonical_vacancy_id>","language":"en"}'
curl -sS -X POST "$JSS_API_URL/vacancies/resume-final" -H 'Content-Type: application/json' -d '{"candidate_id":"<candidate_id>","artifact_id":"<resume_vacancy_artifact_id>","allow_warnings":false}'
```

## Candidate Context

```bash
python3 -m job_search.interfaces.cli.candidate_cli --config-path "$CONFIG_PATH" --workspace-path "$WORKSPACE_PATH" active
python3 -m job_search.interfaces.cli.candidate_cli --config-path "$CONFIG_PATH" --workspace-path "$WORKSPACE_PATH" show-profile --candidate-id "<candidate_id>"
```

## Artifacts

```bash
python3 -m job_search.interfaces.cli.candidate_cli --config-path "$CONFIG_PATH" --workspace-path "$WORKSPACE_PATH" generate-positioning-brief --candidate-id "<candidate_id>" --target-role "<role>" --language en
python3 -m job_search.interfaces.cli.candidate_cli --config-path "$CONFIG_PATH" --workspace-path "$WORKSPACE_PATH" search-resume-kb-evidence --candidate-id "<candidate_id>" --target-role "<role>" --query "<query>" --limit 5
python3 -m job_search.interfaces.cli.candidate_cli --config-path "$CONFIG_PATH" --workspace-path "$WORKSPACE_PATH" generate-resume --candidate-id "<candidate_id>" --target-role "<role>" --language en
python3 -m job_search.interfaces.cli.candidate_cli --config-path "$CONFIG_PATH" --workspace-path "$WORKSPACE_PATH" check-resume --artifact-id "<artifact_id>"
python3 -m job_search.interfaces.cli.candidate_cli --config-path "$CONFIG_PATH" --workspace-path "$WORKSPACE_PATH" roast-resume --artifact-id "<artifact_id>" --target-role "<role>"
python3 -m job_search.interfaces.cli.candidate_cli --config-path "$CONFIG_PATH" --workspace-path "$WORKSPACE_PATH" finalize-resume --artifact-id "<artifact_id>"
python3 -m job_search.interfaces.cli.candidate_cli --config-path "$CONFIG_PATH" --workspace-path "$WORKSPACE_PATH" finalize-resume --artifact-id "<artifact_id>" --allow-warnings
python3 -m job_search.interfaces.cli.vacancy_cli --config-path "$CONFIG_PATH" --workspace-path "$WORKSPACE_PATH" generate-vacancy-resume --candidate-id "<candidate_id>" --canonical-vacancy-id "<canonical_vacancy_id>" --language en
python3 -m job_search.interfaces.cli.vacancy_cli --config-path "$CONFIG_PATH" --workspace-path "$WORKSPACE_PATH" finalize-vacancy-resume --candidate-id "<candidate_id>" --artifact-id "<resume_vacancy_artifact_id>"
```

## Notes

- `generate-resume` registers a markdown artifact and returns its `artifact_id`.
- `search-resume-kb-evidence` is read-only and returns `unavailable` if `kb_index_config_path` is not configured.
- `check-resume` checks an existing artifact id.
- `roast-resume` creates or overwrites one `resume_roast_report` derived from the source resume artifact.
- `finalize-resume` creates a derived `resume_markdown_final` artifact under `final/`; use `--allow-warnings` only after explicit user acceptance.
- `generate-vacancy-resume` creates or overwrites one `resume_vacancy` per candidate/vacancy and reports the source resume artifact used.
- `finalize-vacancy-resume` creates or overwrites `resume_vacancy_final` under `final/`.
- AI rewrite guidance is skill-side review output. It is not a persisted artifact unless a validated command creates or finalizes that artifact.
- If a user asks for a rewrite that changes facts, stop and route the claim through candidate intake/evidence first.
