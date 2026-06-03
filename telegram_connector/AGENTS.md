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
  - `telegram_bridge.py`: Bot API bridge and command surface
  - `telegram_history_client.py`: Telethon ingestion, OCR, export, and SQLite system of record
  - `telegram_digest.py`: digest orchestration and Telegram delivery
- Keep `telegram_shared` limited to infrastructure primitives.
- Do not move digest, history, OCR, export, or Telethon business logic into `telegram_shared`.

## Source Of Truth

When working in `telegram_connector`, prefer these sources in this order:

- [README.md](./README.md) for current user-facing behavior and command surface
- [AGENTS.md](./AGENTS.md) for project-specific coding boundaries and maintenance rules
- [../RULEBOOK.md](../RULEBOOK.md) for cross-project rules
- tests in [tests](./tests) for executable expectations

If README, code, and tests disagree, update them together.

## Project Rules

- Canonical CLI sync entrypoint is `sync --mode <backfill|tail|update>`.
- Canonical bridge CLI entrypoint is `python3 telegram_connector/telegram_bridge.py listen --run-commands`.
- Canonical digest CLI entrypoint is `python3 telegram_connector/telegram_digest.py run`.
- Keep bot UX and CLI internals clearly separated.
- Preserve config-driven defaults for digest, sync, OCR, and export flows.
- Keep `digest` config-driven and only override `channel`, `since`, `until`, or auth mode explicitly per run.
- Keep digest defaults owned by `[processing]`, `[digest]`, and `[digest_limits.*]`; keep non-digest sync limits under `[sync]`.
- Keep hard runtime ceilings for one-shot digest runs owned by `[digest]`, and do not move them into bridge or daemon-only config.
- When changing bridge behavior, check both direct CLI usage and Telegram command mapping.
- Treat SQLite history as the system of record; digest and other analysis flows work over stored data.
- Keep bridge access-control docs aligned with runtime behavior for `allowed_chat_ids`, `allowed_user_ids`, and `allowed_usernames`.
- Keep `/agent-stats` local to the bridge and bounded to a recent-window query.
- Keep digest HTML formatting owned by `telegram_digest.py`; the bridge must not reformat already-prepared HTML payloads.

## Security And Runtime Rules

- Never commit machine-specific paths, usernames, or plaintext secrets.
- `runtime.local.toml` is local-only and must stay ignored.
- Runtime data belongs under `data/`.
- Treat `messages.text` as a Telegram-specific schema exception: it may remain `""` when Telegram explicitly provides no text body, while other optional string fields should still follow the repository-wide `NULL`-for-missing convention.
- Bridge and digest logs must not leak secrets or full raw updates.
- Bridge secret resolution should happen at daemon startup, not on every handled update.
- Child subprocesses must receive only the allowlisted env subset they need.
- After changing bridge code, `telegram_shared`, or `config/runtime.local.toml`, redeploy the launchd service with `scripts/install_launch_agent.sh`, not only `scripts/restart_launch_agent.sh`.
- `restart_launch_agent.sh` only reloads the already installed plist/service-root copy; it does not recopy fresh code or config into `~/Library/Application Support/telegram_connector_service`.
- Keep LaunchAgent `ProgramArguments[0]` pointed at the generated `telegram-connector-*-launcher` executable. The launcher invokes the shell runner through `/bin/bash`; do not restore direct plist execution of `run_telegram_*.sh`.
- Scheduled `digest` is a one-shot job, not a daemon: keep the outer hard TTL in the shared runner and keep `bridge` free of that TTL mechanism.

## Testing

Preferred test workflow:

```bash
python3 -m venv .venv-test-gap-detection
.venv-test-gap-detection/bin/python -m pip install -r telegram_connector/requirements.txt
.venv-test-gap-detection/bin/python -m pytest telegram_connector/tests -q
```

Before finishing changes:

- rerun the full `telegram_connector/tests` suite
- if you change `telegram_shared/bot_api.py`, still rerun `telegram_connector/tests` because the shared Bot API transport regressions live there
- update README when command behavior changes
- update installer scripts when bridge startup flow changes
