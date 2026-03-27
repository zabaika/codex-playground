# AGENTS

This file is a repo-specific quickstart for coding agents working in `codex-playground`.

Use it as an entry guide. For repository-wide engineering policy, safety rules, and storage conventions, see:
- [RULEBOOK.md](./RULEBOOK.md)

## Repo Layout

- [README.md](./README.md): top-level project index
- [RULEBOOK.md](./RULEBOOK.md): global engineering and safety rules
- [telegram_connector](./telegram_connector): Telegram ingestion, OCR, digest, and bot bridge project
- [telegram_agent_bot](./telegram_agent_bot): standalone Telegram task agent project
- [telegram_shared](./telegram_shared): shared infrastructure primitives for Telegram projects
- [skills](./skills): local skills and related documentation

## Main Working Area

Most active code currently lives in:
- [telegram_connector/README.md](./telegram_connector/README.md)
- [telegram_connector/AGENTS.md](./telegram_connector/AGENTS.md)
- [telegram_connector/telegram_connector.py](./telegram_connector/telegram_connector.py)
- [telegram_connector/telegram_history_client.py](./telegram_connector/telegram_history_client.py)
- [telegram_connector/telegram_digest.py](./telegram_connector/telegram_digest.py)
- [telegram_connector/tests](./telegram_connector/tests)
- [telegram_agent_bot/README.md](./telegram_agent_bot/README.md)
- [telegram_agent_bot/AGENTS.md](./telegram_agent_bot/AGENTS.md)
- [telegram_agent_bot/telegram_agent_bridge.py](./telegram_agent_bot/telegram_agent_bridge.py)
- [telegram_agent_bot/telegram_agent_worker.py](./telegram_agent_bot/telegram_agent_worker.py)
- [telegram_agent_bot/tests](./telegram_agent_bot/tests)
- [telegram_shared](./telegram_shared)

## Source Of Truth

When working in `telegram_connector`, prefer these sources in this order:
- [telegram_connector/README.md](./telegram_connector/README.md) for current user-facing behavior and command surface
- [telegram_connector/AGENTS.md](./telegram_connector/AGENTS.md) for project-specific coding boundaries
- [RULEBOOK.md](./RULEBOOK.md) for cross-project rules
- tests in [telegram_connector/tests](./telegram_connector/tests) for executable expectations

When working in `telegram_agent_bot`, prefer these sources in this order:
- [telegram_agent_bot/README.md](./telegram_agent_bot/README.md) for user-facing behavior and command surface
- [telegram_agent_bot/AGENTS.md](./telegram_agent_bot/AGENTS.md) for project-specific coding boundaries
- [RULEBOOK.md](./RULEBOOK.md) for cross-project rules
- tests in [telegram_agent_bot/tests](./telegram_agent_bot/tests) for executable expectations

If README, code, and tests disagree, update them together rather than fixing only one layer.

Operational guidance:
- for route selection, engine choice, selected inputs, config resolution, and similar runtime facts, keep exactly one canonical producer and let wrapper layers consume that output instead of rebuilding it
- do not add parallel summary formats, convenience placeholders, or local stub values when the real metadata already exists in an upstream tool, structured payload, or canonical log
- when a wrapper replays cached or preexisting artifacts, recover metadata from the original producer's persisted output before introducing any fallback

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
- launchd digest service for scheduled digest runs

If behavior changes in bridge or digest startup flow, update:
- code
- README
- installer scripts
- tests

## Editing Guidance

- Avoid broad refactors unless they simplify both code and command semantics.
- Prefer config-driven defaults over hardcoded runtime values.
- Keep user-facing help and README aligned with parser behavior.
- Avoid "temporary" local shims that duplicate existing behavior unless they clearly reduce complexity and are documented as the new source of truth.
- When adding logging or diagnostics, extend the canonical producer first; only add wrapper-side logging when it carries distinct value and does not duplicate the same fact in a second schema.
- When changing sync or digest behavior, check both:
  - direct CLI usage
  - Telegram bot command mapping

## Commit Discipline

- Keep local config and generated runtime artifacts out of commits.
- Before commit, verify relevant tests pass.
- If behavior changed, update docs in the same change.
