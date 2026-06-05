---
name: jss-candidate-intake
description: "Use for job-search candidate intake workflows over the local job-search-system: create or select candidates, ingest resume, LinkedIn export, profile text, URLs, or existing artifacts, build AI extraction requests, import validated AI drafts, review conflicts and missing fields, and confirm canonical candidate profile state through command handlers."
---

# Job Search Candidate Intake

## Purpose

Run candidate intake through the local `tools/job-search-system` service layer. This skill is the operator-facing workflow for turning resume, LinkedIn export, profile text, URL content, or existing artifacts into a reviewed candidate profile draft and then a confirmed canonical candidate profile.

## Hard Rules

- Never write SQLite directly.
- Never edit `tools/job-search-system/data/` by hand.
- AI lives in this skill: it may extract or propose a draft from the schema-bound request, but it must not claim canonical state changed.
- Do not look for or call a backend AI runtime. The backend only builds the request contract and validates imported draft JSON.
- Canonical candidate state changes only through `confirm-draft`.
- If `tools/job-search-system` or local config is missing, operate only in draft-only degraded mode and say that nothing was persisted.
- Do not invent candidate facts, metrics, dates, employers, credentials, profile URLs, or work authorizations.
- Treat conflicts and missing fields as review items, not as permission to guess.
- Keep API-lite on loopback only unless the user explicitly accepts an unsafe trusted-network bind.

## Runtime

Preferred path is API-lite when it is running locally. Check it first:

```bash
curl -sS "$JSS_API_URL/health"
```

Use `JSS_API_URL`, normally `http://127.0.0.1:8765`. If API-lite is unavailable, fall back to the canonical CLI through the same command handlers used by Codex glue:

```bash
PYTHONPATH="$JSS_ROOT/src:$PLAYGROUND_ROOT" python3 -m job_search.interfaces.cli.candidate_cli \
  --config-path "$JSS_ROOT/config/runtime.local.toml" \
  --workspace-path "$WORKSPACE_PATH" \
  <command>
```

Before running commands, resolve:

- `PLAYGROUND_ROOT`: local Playground checkout, for example `$HOME/Documents/Playground`
- `JSS_ROOT`: `$PLAYGROUND_ROOT/tools/job-search-system`
- `WORKSPACE_PATH`: smoke kit workspace or another explicit local workspace

To start API-lite when needed:

```bash
PYTHONPATH="$JSS_ROOT/src:$PLAYGROUND_ROOT" python3 -m job_search.interfaces.api.server \
  --config-path "$JSS_ROOT/config/runtime.local.toml" \
  --workspace-path "$WORKSPACE_PATH" \
  --host 127.0.0.1 \
  --port 8765
```

If the user did not provide `WORKSPACE_PATH`, use the active smoke kit only when it is clearly the current task context. Otherwise ask for the workspace path.

Detailed command map: [references/commands.md](references/commands.md).

## Workflow

1. Check whether a candidate already exists.
   - API: `GET /candidates` and `GET /candidates/active`.
   - CLI fallback: `list` and `active`.
   - If the user clearly names a new candidate, create it.
   - If multiple candidates may match, ask before selecting.
2. Select the active candidate.
3. Ingest sources.
   - API: `POST /candidates/sources/text` or `POST /candidates/sources/url`.
   - CLI fallback: `ingest-file`, `ingest-text`, or `ingest-url`.
   - Local file ingestion is disabled over API by default; use CLI for file sources unless the runtime explicitly enables `api_allow_local_file_sources`.
   - Resume files use `source_kind=resume`.
   - LinkedIn export files use `source_kind=linkedin`.
   - Profile/search context uses `source_kind=profile`.
   - Existing artifacts: `attach-artifact`.
4. Build an AI extraction request when AI extraction is needed.
   - API: `POST /candidates/ai-extraction-request`.
   - CLI fallback: `build-ai-extraction-request`.
   - Use the returned JSON as the schema-bound prompt input for AI.
   - The AI output must be JSON matching the returned contract.
   - Fill only fields supported by the provided sources. Put uncertain or conflicting facts into the contract's assumptions/conflicts/missing-data surfaces instead of guessing.
   - If the source is too noisy, return a partial draft plus missing fields; do not fabricate a complete profile.
5. Import the AI draft only through `import-ai-draft`.
   - Never bypass validation by writing a draft directly.
   - If validation fails, report the exact field/source-reference error and ask for corrected draft JSON.
6. For deterministic-only intake, run `POST /candidates/drafts` or CLI `generate-draft`.
7. Show the latest draft review surface and summarize:
   - API: `GET /candidates/draft-review`.
   - CLI fallback: `show-draft-review`.
   - confirmed or inferred fields
   - conflicts
   - missing fields
   - source coverage
   - intake quality issues such as duplicate evidence or ambiguous profile URLs
8. Confirm only after explicit user approval.
   - API: `POST /candidates/confirm-draft`.
   - CLI fallback: `confirm-draft`.
   - Use `--accepted-field key=value` only for fields the user explicitly accepted or corrected.
9. After confirmation, run `GET /candidates/profile` or CLI `show-profile` and verify that core fields, languages, external profiles, work authorizations, evidence, targets, compensation, and search preferences look coherent.

## Output Contract

When completing an intake task, report:

- candidate id and active candidate state
- source artifacts ingested or reused
- draft id
- conflicts and missing fields
- whether canonical profile was confirmed
- any degraded-mode limitation

Keep final reports concise. Do not paste full resumes or full profile JSON unless the user asks.
