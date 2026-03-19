# AGENTS

This file is a repo-specific quickstart for coding agents working in `codex-playground`.

Use it as an entry guide. For repository-wide engineering policy, safety rules, and storage conventions, see:
- [RULEBOOK.md](./RULEBOOK.md)

## Repo Layout

- [README.md](/Users/andrejzabaev/Documents/Playground/README.md): top-level project index
- [README.md](./README.md): top-level project index
- [RULEBOOK.md](./RULEBOOK.md): global engineering and safety rules
- [telegram_connector](./telegram_connector): Telegram ingestion, OCR, digest, and bot bridge project
- [skills](./skills): local skills and related documentation

## Main Working Area

Most active code currently lives in:
- [telegram_connector/README.md](./telegram_connector/README.md)
- [telegram_connector/telegram_connector.py](./telegram_connector/telegram_connector.py)
- [telegram_connector/telegram_history_client.py](./telegram_connector/telegram_history_client.py)
- [telegram_connector/telegram_digest.py](./telegram_connector/telegram_digest.py)
- [telegram_connector/tests](./telegram_connector/tests)

## Source Of Truth

When working in `telegram_connector`, prefer these sources in this order:
- [telegram_connector/README.md](./telegram_connector/README.md) for current user-facing behavior and command surface
- [RULEBOOK.md](./RULEBOOK.md) for cross-project rules
- tests in [telegram_connector/tests](./telegram_connector/tests) for executable expectations

If README, code, and tests disagree, update them together rather than fixing only one layer.

## Runtime And Secrets

- Never commit machine-specific paths, usernames, home-directory paths, or local workstation identifiers.
- Never commit plaintext secrets.
- `runtime.local.toml` is local-only and intentionally ignored.
- Committed config examples belong in:
  - [telegram_connector/config/runtime.example.toml](./telegram_connector/config/runtime.example.toml)
- Project runtime data belongs under:
  - [telegram_connector/data](./telegram_connector/data)

## Telegram Connector Notes

- Canonical CLI sync entrypoint is `sync --mode <backfill|tail|update>`.
- Bot aliases may expose a friendlier surface than the CLI; keep bot UX and CLI internals clearly separated.
- `digest` is config-driven:
  - model and OCR defaults come from `[processing]`
  - schedule/window defaults come from `[digest]`
  - profile-specific digest limits come from `[digest_limits.*]`
- Non-digest sync limits live under `[sync]`.

## Testing

Preferred test command:

```bash
python3 -m pytest telegram_connector/tests -q
```

Use narrower test targets while iterating, then run the full suite before committing changes in `telegram_connector`.

## Background Processes

There are two operational paths to keep in mind:
- launchd bridge service for Telegram bot command handling
- crontab entry for scheduled digest runs

If behavior changes in bridge or digest startup flow, update:
- code
- README
- installer scripts
- tests

## Editing Guidance

- Avoid broad refactors unless they simplify both code and command semantics.
- Prefer config-driven defaults over hardcoded runtime values.
- Keep user-facing help and README aligned with parser behavior.
- When changing sync or digest behavior, check both:
  - direct CLI usage
  - Telegram bot command mapping

## Commit Discipline

- Keep local config and generated runtime artifacts out of commits.
- Before commit, verify relevant tests pass.
- If behavior changed, update docs in the same change.
