---
name: jss-job-board-operations
description: "Use for job-search job-board operations over the local job-search-system: produce manual platform checklists and saved-search settings, log manual board actions, record artifact usage for external-facing actions, inspect logged board actions and reconciliation items, and keep job-board work in checklist/manual-sync mode without browser automation or direct database writes."
---

# JSS Job Board Operations

## Purpose

Run job-board operations through `tools/job-search-system` after candidate intake and vacancy pipeline context exist. This skill is for supervised/manual-sync work only: platform checklists, saved search settings, URL enrichment seeds, and traceable logging of actions the operator performed manually.

## Hard Rules

- Never write SQLite directly.
- Never use browser automation, credential handling, scraping, or unattended collection.
- Use API-lite when available; use CLI fallback only through `vacancy_cli`.
- URL-only saved job links may be stored as enrichment seeds, but they are not canonical vacancies until supervised preview and confirm import succeed.
- URL seed preview accepts manually provided page text only; do not claim the system retrieved the page.
- A logged board action is a record of a manual operator action, not proof that the system performed an external action.
- Every logged board action may create a reconciliation item for board/internal drift review.
- Reconciliation items do not create a second lifecycle and must not hide internal state changes.
- For completed submit/send/profile-update actions, require both the relevant artifact id and explicit `external_action_approval_id`; do not collapse quality gate, content acceptance, and external action approval.
- Do not create board records “just in case”; log only real planned/completed/manual-review actions.

## Runtime

Preferred API check:

```bash
curl -sS "$JSS_API_URL/health"
```

Use `JSS_API_URL`, normally `http://127.0.0.1:8765`. CLI fallback:

```bash
PYTHONPATH="$JSS_ROOT/src:$PLAYGROUND_ROOT" python3 -m job_search.interfaces.cli.vacancy_cli \
  --config-path "$JSS_ROOT/config/runtime.local.toml" \
  --workspace-path "$WORKSPACE_PATH" \
  <command>
```

Detailed command map: [references/commands.md](references/commands.md).

## Workflow

1. Verify active candidate and vacancy context.
   - API: `GET /candidates/active`, `GET /candidates/profile`, optional `GET /vacancies/show`.
   - CLI fallback: candidate CLI active/profile and vacancy `show`.
2. Generate platform checklist before manual work.
   - API: `GET /vacancies/board-checklist?platform=<platform>`.
   - CLI fallback: `board-checklist --platform <platform>`.
3. For URL-only saved job links, create an enrichment seed.
   - API: `POST /vacancies/url-seeds`.
   - CLI fallback: `create-url-seed`.
4. Add manually copied page text and inspect preview.
   - API: `POST /vacancies/url-seeds/preview`.
   - CLI fallback: `preview-url-seed`.
   - Confirm only if preview yields exactly one vacancy.
5. Confirm import from preview.
   - API: `POST /vacancies/url-seeds/confirm`.
   - CLI fallback: `confirm-url-seed-import`.
6. Perform the platform work manually outside Codex automation.
   - Examples: configure saved search, inspect a vacancy, manually submit, manually send a message, check visibility.
7. For completed submit/send/profile-update actions, record explicit external action approval first.
   - API: `POST /vacancies/external-action-approval`.
   - CLI fallback: `record-external-action-approval`.
   - If the action uses generated content, record artifact acceptance separately with `POST /vacancies/artifact-acceptance` or `record-artifact-acceptance`.
8. Record the manual action.
   - API: `POST /vacancies/board-actions`.
   - CLI fallback: `record-board-action`.
   - Use an idempotency key for actions that may be retried.
9. Inspect logged actions when needed.
   - API: `GET /vacancies/board-actions`.
   - CLI fallback: `list-board-actions`.
10. Inspect reconciliation items when manual/external state may diverge from internal state.
   - API: `GET /vacancies/reconciliation`.
   - CLI fallback: `list-reconciliation`.
   - Open `needs_review` items also appear in daily actions.
11. Resolve reconciliation items only after operator review.
   - API: `POST /vacancies/reconciliation/resolve`.
   - CLI fallback: `resolve-reconciliation`.
   - If internal vacancy/application state should change, use the explicit vacancy/application command after review.

## Output Contract

Return:

- candidate id
- platform
- checklist or saved-search settings, if requested
- URL seed id, preview, and imported vacancy id, if URL enrichment was used
- board action id and reused flag, if logged
- reconciliation item id, outcome, and review status, if created
- approval ids, if external action approval or artifact acceptance was recorded
- linked vacancy/application/artifact ids, if present
- remaining manual next steps

Do not claim that the system submitted, published, sent, refreshed, or hid anything unless the user explicitly says they performed that action and it was logged as a manual action.
