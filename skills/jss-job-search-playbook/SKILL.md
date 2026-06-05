---
name: jss-job-search-playbook
description: "Use for job-search playbook workflows over the local job-search-system: generate search strategy, saved-search design pack, reusable outreach message, compensation framework, and interview-prep artifacts through API-lite or CLI fallback without direct database writes."
---

# JSS Job Search Playbook

## Purpose

Generate the operator playbook for a confirmed candidate profile through `tools/job-search-system`. This skill owns search strategy, saved-search design, reusable outreach text, compensation framing, and lightweight interview-prep artifacts.

## Hard Rules

- Never write SQLite directly.
- Use API-lite when available; use CLI fallback only through `candidate_cli`.
- Do not invent candidate facts, market claims, compensation numbers, or credentials.
- Treat generated playbook artifacts as guidance, not as approval for external sends or submissions.
- If `tools/job-search-system` is unavailable, operate only in draft-only degraded mode and say that nothing was persisted.

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

## Workflow

1. Verify candidate context.
   - API: `GET /candidates/active` and `GET /candidates/profile`.
   - CLI fallback: `active` and `show-profile`.
2. Confirm that target role, markets, search preferences, and compensation are present enough for a useful playbook.
3. Generate the playbook.
   - API: `POST /candidates/playbook`.
   - CLI fallback: `generate-playbook`.
4. Review the returned artifact path and playbook sections:
   - search strategy
   - saved-search design pack
   - reusable message artifact
   - compensation framework
   - interview artifacts
5. If the playbook reveals missing candidate facts, route back to `$jss-candidate-intake` instead of patching profile state here.

## Output Contract

Return:

- candidate id
- playbook artifact id and storage path
- primary role
- saved-search names
- compensation readiness note
- unresolved missing data

Do not paste the full playbook unless asked.
