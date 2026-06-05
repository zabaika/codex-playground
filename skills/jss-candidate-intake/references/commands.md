# Candidate Intake Commands

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

`API-lite` should remain on `127.0.0.1` or `localhost` by default. Non-loopback bind is intentionally treated as unsafe.

Use API-lite when available:

```bash
curl -sS "$JSS_API_URL/health"
curl -sS "$JSS_API_URL/candidates"
curl -sS "$JSS_API_URL/candidates/active"
curl -sS -X POST "$JSS_API_URL/candidates" -H 'Content-Type: application/json' -d '{"display_name":"<Full Name>"}'
curl -sS -X POST "$JSS_API_URL/candidates/active" -H 'Content-Type: application/json' -d '{"candidate_id":"<candidate_id>"}'
curl -sS -X POST "$JSS_API_URL/candidates/sources/text" -H 'Content-Type: application/json' -d '{"candidate_id":"<candidate_id>","source_kind":"profile","content_text":"<profile text>"}'
curl -sS -X POST "$JSS_API_URL/candidates/drafts" -H 'Content-Type: application/json' -d '{"candidate_id":"<candidate_id>"}'
curl -sS "$JSS_API_URL/candidates/draft-review?candidate_id=<candidate_id>&draft_id=<draft_id>"
curl -sS -X POST "$JSS_API_URL/candidates/confirm-draft" -H 'Content-Type: application/json' -d '{"candidate_id":"<candidate_id>","draft_id":"<draft_id>","accepted_field_values":{}}'
curl -sS "$JSS_API_URL/candidates/profile?candidate_id=<candidate_id>"
```

## Candidate Context

```bash
python3 -m job_search.interfaces.cli.candidate_cli --config-path "$CONFIG_PATH" --workspace-path "$WORKSPACE_PATH" list
python3 -m job_search.interfaces.cli.candidate_cli --config-path "$CONFIG_PATH" --workspace-path "$WORKSPACE_PATH" active
python3 -m job_search.interfaces.cli.candidate_cli --config-path "$CONFIG_PATH" --workspace-path "$WORKSPACE_PATH" create --display-name "<Full Name>"
python3 -m job_search.interfaces.cli.candidate_cli --config-path "$CONFIG_PATH" --workspace-path "$WORKSPACE_PATH" select --candidate-id "<candidate_id>"
```

## Source Ingestion

```bash
python3 -m job_search.interfaces.cli.candidate_cli --config-path "$CONFIG_PATH" --workspace-path "$WORKSPACE_PATH" ingest-file --candidate-id "<candidate_id>" --source-kind resume --file-path "<resume.pdf>"
python3 -m job_search.interfaces.cli.candidate_cli --config-path "$CONFIG_PATH" --workspace-path "$WORKSPACE_PATH" ingest-file --candidate-id "<candidate_id>" --source-kind linkedin --file-path "<linkedin-export.pdf>"
python3 -m job_search.interfaces.cli.candidate_cli --config-path "$CONFIG_PATH" --workspace-path "$WORKSPACE_PATH" ingest-file --candidate-id "<candidate_id>" --source-kind profile --file-path "<search-context.md>"
python3 -m job_search.interfaces.cli.candidate_cli --config-path "$CONFIG_PATH" --workspace-path "$WORKSPACE_PATH" attach-artifact --candidate-id "<candidate_id>" --source-kind resume --existing-artifact-id "<artifact_id>"
```

## Draft Flow

```bash
python3 -m job_search.interfaces.cli.candidate_cli --config-path "$CONFIG_PATH" --workspace-path "$WORKSPACE_PATH" generate-draft --candidate-id "<candidate_id>"
python3 -m job_search.interfaces.cli.candidate_cli --config-path "$CONFIG_PATH" --workspace-path "$WORKSPACE_PATH" build-ai-extraction-request --candidate-id "<candidate_id>"
python3 -m job_search.interfaces.cli.candidate_cli --config-path "$CONFIG_PATH" --workspace-path "$WORKSPACE_PATH" import-ai-draft --candidate-id "<candidate_id>" --payload-file "<ai-response.json>"
python3 -m job_search.interfaces.cli.candidate_cli --config-path "$CONFIG_PATH" --workspace-path "$WORKSPACE_PATH" show-latest-draft --candidate-id "<candidate_id>"
python3 -m job_search.interfaces.cli.candidate_cli --config-path "$CONFIG_PATH" --workspace-path "$WORKSPACE_PATH" show-draft-review --candidate-id "<candidate_id>" --draft-id "<draft_id>"
```

## Confirm Flow

```bash
python3 -m job_search.interfaces.cli.candidate_cli --config-path "$CONFIG_PATH" --workspace-path "$WORKSPACE_PATH" confirm-draft --candidate-id "<candidate_id>" --draft-id "<draft_id>"
python3 -m job_search.interfaces.cli.candidate_cli --config-path "$CONFIG_PATH" --workspace-path "$WORKSPACE_PATH" confirm-draft --candidate-id "<candidate_id>" --draft-id "<draft_id>" --accepted-field "current_title=VP Engineering"
python3 -m job_search.interfaces.cli.candidate_cli --config-path "$CONFIG_PATH" --workspace-path "$WORKSPACE_PATH" show-profile --candidate-id "<candidate_id>"
```

## Notes

- `source_kind` values normally used by this skill: `resume`, `linkedin`, `profile`.
- File-based source ingestion should use CLI by default; API file ingestion is disabled unless `api_allow_local_file_sources` is explicitly enabled in runtime config.
- PDF ingestion requires `pdftotext` on the local machine.
- `build-ai-extraction-request` does not call a model. It returns the safe request payload for this skill's AI reasoning.
- `import-ai-draft` persists only a draft after validation. It does not confirm canonical profile fields.
