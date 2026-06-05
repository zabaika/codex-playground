---
name: jss-career-pathing
description: "Use for job-search career pathing workflows over the local job-search-system: run career-pathing-lite or career-pathing-full through API-lite or CLI fallback, compare target roles, flag title-inflation risk, select a primary target role, analyze capability gaps, T-shape branches, professional brand plan, and trajectory ranking."
---

# JSS Career Pathing

## Purpose

Run career-pathing workflows for a confirmed candidate profile. Lite mode selects a practical primary target role. Full mode creates a persisted advisory report over confirmed candidate evidence, local vacancy data, and optional KB context without mutating canonical candidate state.

## Hard Rules

- Never write SQLite directly.
- Use API-lite when available; use CLI fallback only through `candidate_cli`.
- Lite and full modes may create persisted markdown artifacts through command handlers.
- Full mode is advisory and must not change canonical candidate state automatically.
- Do not invent candidate facts, credentials, compensation, geography constraints, or market evidence.
- If the candidate profile is incomplete, route to `$jss-candidate-intake` first.

## Runtime

Preferred API check:

```bash
curl -sS "$JSS_API_URL/health"
```

Use `JSS_API_URL`, normally `http://127.0.0.1:8765`. CLI fallback:

```bash
PYTHONPATH="$JSS_ROOT/src:$PLAYGROUND_ROOT" python3 -m job_search.interfaces.cli.candidate_cli \
  --config-path "$JSS_ROOT/config/runtime.local.toml" \
  --workspace-path "$WORKSPACE_PATH" \
  <command>
```

Detailed command map: [references/commands.md](references/commands.md).

## Lite Workflow

1. Verify candidate context.
   - API: `GET /candidates/active` and `GET /candidates/profile`.
   - CLI fallback: `active` and `show-profile`.
2. Collect or infer 2-5 target roles from candidate targets or user input.
3. Run career-pathing-lite.
   - API: `POST /candidates/career-pathing-lite`.
   - CLI fallback: `career-pathing-lite`.
4. Review:
   - realistic roles
   - stretch roles
   - title inflation risks
   - primary target role
5. If the user accepts a target-role change, route the profile update through candidate commands, not direct edits.

## Full Mode

Full mode is backed by `tools/job-search-system` and creates a `career_pathing_full` markdown artifact. It analyzes:

- broader role universe
- capability gaps
- T-shape development branches
- professional brand plan
- career trajectory comparison

Full mode must clearly say that no canonical candidate state was changed. If the report proposes missing evidence, route that evidence through `$jss-candidate-intake` before using it as a candidate fact.

KB context is optional. Use it only when the runtime config contains a working `[integrations].kb_index_config_path`; otherwise full mode must continue without KB and report KB as unavailable instead of blocking the workflow.

Multiple career paths for one candidate are supported as parallel advisory trajectories:

- pass several `target_roles` into one full report
- or create several separate `career_pathing_full` artifacts for different role sets
- do not create or imply a mutable `career_path` lifecycle unless the backend adds that entity later

Preferred API:

```bash
curl -sS -X POST "$JSS_API_URL/candidates/career-pathing-full" \
  -H 'Content-Type: application/json' \
  -d '{"candidate_id":"<candidate_id>","target_roles":["<role-1>","<role-2>"],"include_kb":true}'
```

CLI fallback:

```bash
PYTHONPATH="$JSS_ROOT/src:$PLAYGROUND_ROOT" python3 -m job_search.interfaces.cli.candidate_cli \
  --config-path "$JSS_ROOT/config/runtime.local.toml" \
  --workspace-path "$WORKSPACE_PATH" \
  career-pathing-full --candidate-id "<candidate_id>" --target-role "<role-1>" --target-role "<role-2>"
```

## Output Contract

Return:

- candidate id
- mode: `lite` or `full`
- target roles reviewed
- artifact id and storage path for persisted modes
- primary target role recommendation
- realistic/stretch split
- capability gaps and trajectory ranking for full mode
- unresolved profile or market assumptions
