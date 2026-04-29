# AGENTS

This file is the project-specific guide for coding agents working in `tools/kb-index`.

Use it together with:
- [README.md](./README.md) for user-facing behavior, command surface, and deployment shape
- [../../RULEBOOK.md](../../RULEBOOK.md) for repository-wide engineering and safety rules

## Scope

`kb-index` is the canonical local retrieval layer for the Obsidian knowledge base.

It owns:
- note indexing
- note-level retrieval
- local SQLite/FTS storage
- scheduled index refresh through `launchd`

It does not own:
- note drafting logic
- article or transcript interpretation
- duplicate note resolution policy inside skills

## Source Of Truth

When working in `tools/kb-index`, prefer these sources in this order:
- [README.md](./README.md) for current behavior and operator commands
- files under [src/kb_index](./src/kb_index) for executable behavior
- tests under [tests](./tests) for regression expectations
- [../../RULEBOOK.md](../../RULEBOOK.md) for cross-project rules

If docs, code, and tests disagree, update them together.

## Runtime Model

- `config/runtime.local.toml` is the single source of truth for runtime behavior.
- Retrieval weights, thresholds, scope, and auto-update settings must come from config, not from hidden code defaults.
- `build_kb_index` and `update_kb_index` are the only canonical index mutation paths.
- Auto-update must call the same canonical `update` path rather than introducing a second indexing flow.

## Launchd Deployment

- The `launchd` agent must not run code directly from `Documents/Playground`.
- Installer-driven deployment copies the runtime layer into:
  - `~/Library/Application Support/kb_index_service`
- The generated service root is the runtime execution surface for scheduled refreshes.
- Canonical operational logs for scheduled runs must stay in:
  - `tools/kb-index/data/launchd`
  not inside the service root.
- If config changes must be picked up by `launchd`, rerun:
  - `install_kb_index_auto_update --config-path ...`

## Commands

Primary commands:
- `build_kb_index`
- `update_kb_index`
- `search_kb`
- `status_kb_index`

Auto-update commands:
- `install_kb_index_auto_update`
- `status_kb_index_auto_update`
- `uninstall_kb_index_auto_update`

## Working Rules

- Keep `kb-index` as the single canonical producer of retrieval results for knowledge-base workflows.
- Do not duplicate retrieval logic in wrapper skills.
- Do not introduce a second config location for runtime behavior.
- Keep search freshness logic split cleanly:
  - scheduled refresh from `launchd`
  - optional one-shot post-write refresh from a caller skill
- When changing retrieval semantics, update docs and tests in the same change.
