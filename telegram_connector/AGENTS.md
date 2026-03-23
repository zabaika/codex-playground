# AGENTS

This file is the project-specific guide for coding agents working in `telegram_connector`.

For repository-wide engineering and safety conventions, see:
- [../RULEBOOK.md](../RULEBOOK.md)

For repo-level orientation, see:
- [../AGENTS.md](../AGENTS.md)

## Purpose

`telegram_connector` is the standalone Telegram history, OCR, export, and digest project.
It must stay operationally separate from `telegram_agent_bot`.

## Architecture

- Keep a split architecture:
  - `telegram_connector.py`: Bot API bridge and command surface
  - `telegram_history_client.py`: Telethon ingestion, OCR, export, and SQLite system of record
  - `telegram_digest.py`: digest orchestration and Telegram delivery
- Keep `telegram_shared` limited to infrastructure primitives.
- Do not move digest, history, OCR, export, or Telethon business logic into `telegram_shared`.

## Source Of Truth

When working in `telegram_connector`, prefer these sources in this order:

- [README.md](./README.md) for current user-facing behavior and command surface
- [../RULEBOOK.md](../RULEBOOK.md) for cross-project rules
- tests in [tests](./tests) for executable expectations

If README, code, and tests disagree, update them together.

## Project Rules

- Keep bot UX and CLI internals clearly separated.
- Preserve config-driven defaults for digest, sync, OCR, and export flows.
- When changing bridge behavior, check both direct CLI usage and Telegram command mapping.
- Treat SQLite history as the system of record; digest and other analysis flows work over stored data.
- Keep `/agent-stats` local to the bridge and bounded to a recent-window query.
- Keep digest HTML formatting owned by `telegram_digest.py`; the bridge must not reformat already-prepared HTML payloads.

## Security And Runtime Rules

- Never commit machine-specific paths, usernames, or plaintext secrets.
- `runtime.local.toml` is local-only and must stay ignored.
- Runtime data belongs under `data/`.
- Bridge and digest logs must not leak secrets or full raw updates.
- Bridge secret resolution should happen at daemon startup, not on every handled update.
- Child subprocesses must receive only the allowlisted env subset they need.

## Testing

Preferred test command:

```bash
python3 -m pytest telegram_connector/tests -q
```

Before finishing changes:

- rerun the full `telegram_connector/tests` suite
- update README when command behavior changes
- update installer scripts when bridge startup flow changes
