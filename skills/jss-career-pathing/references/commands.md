# Career Pathing Commands

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
curl -sS -X POST "$JSS_API_URL/candidates/career-pathing-lite" \
  -H 'Content-Type: application/json' \
  -d '{"candidate_id":"<candidate_id>","target_roles":["<role-1>","<role-2>"]}'
curl -sS -X POST "$JSS_API_URL/candidates/career-pathing-full" \
  -H 'Content-Type: application/json' \
  -d '{"candidate_id":"<candidate_id>","target_roles":["<role-1>","<role-2>"],"include_kb":true}'
```

## CLI Fallback

```bash
python3 -m job_search.interfaces.cli.candidate_cli --config-path "$CONFIG_PATH" --workspace-path "$WORKSPACE_PATH" active
python3 -m job_search.interfaces.cli.candidate_cli --config-path "$CONFIG_PATH" --workspace-path "$WORKSPACE_PATH" show-profile --candidate-id "<candidate_id>"
python3 -m job_search.interfaces.cli.candidate_cli --config-path "$CONFIG_PATH" --workspace-path "$WORKSPACE_PATH" career-pathing-lite --candidate-id "<candidate_id>" --target-role "<role-1>" --target-role "<role-2>"
python3 -m job_search.interfaces.cli.candidate_cli --config-path "$CONFIG_PATH" --workspace-path "$WORKSPACE_PATH" career-pathing-full --candidate-id "<candidate_id>" --target-role "<role-1>" --target-role "<role-2>"
```

## Notes

- Lite mode registers a markdown artifact and returns its `artifact_id`.
- Full mode registers an advisory markdown artifact and returns its `artifact_id`.
- Full mode does not mutate canonical candidate state automatically.
