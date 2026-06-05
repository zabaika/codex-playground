# Job Search Playbook Commands

Use these commands from `PLAYGROUND_ROOT`.

## Variables

```bash
PLAYGROUND_ROOT="<playground-root>"
JSS_ROOT="$PLAYGROUND_ROOT/tools/job-search-system"
PYTHONPATH="$JSS_ROOT/src:$PLAYGROUND_ROOT"
CONFIG_PATH="$JSS_ROOT/config/runtime.local.toml"
WORKSPACE_PATH="<workspace.local.toml>"
JSS_API_URL="http://127.0.0.1:8765"
```

## API-lite

```bash
curl -sS "$JSS_API_URL/candidates/active"
curl -sS "$JSS_API_URL/candidates/profile?candidate_id=<candidate_id>"
curl -sS -X POST "$JSS_API_URL/candidates/playbook" \
  -H 'Content-Type: application/json' \
  -d '{"candidate_id":"<candidate_id>"}'
```

## CLI Fallback

```bash
python3 -m job_search.interfaces.cli.candidate_cli --config-path "$CONFIG_PATH" --workspace-path "$WORKSPACE_PATH" active
python3 -m job_search.interfaces.cli.candidate_cli --config-path "$CONFIG_PATH" --workspace-path "$WORKSPACE_PATH" show-profile --candidate-id "<candidate_id>"
python3 -m job_search.interfaces.cli.candidate_cli --config-path "$CONFIG_PATH" --workspace-path "$WORKSPACE_PATH" generate-playbook --candidate-id "<candidate_id>"
```

## Notes

- `generate-playbook` registers a markdown artifact and returns its `artifact_id` and `storage_path`.
- The generated reusable message is a draft. External send/submit approval belongs to board or vacancy workflows.
