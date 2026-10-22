# AGENTS

This file is the project-specific guide for coding agents working in `telegram_connector`.

For repository-wide engineering and safety conventions, see:
- [../RULEBOOK.md](../RULEBOOK.md)

For repo-level orientation, see:
- [../AGENTS.md](../AGENTS.md)

## Purpose

`telegram_connector` owns Telegram history, OCR, export, digest and bounded channel analytics.
It must stay operationally separate from `telegram_agent_bot`.

## Architecture

- Keep a split architecture:
  - `telegram_bridge.py`: Bot API bridge and command surface
  - `telegram_history_client.py`: Telethon ingestion, OCR, export, and SQLite system of record
  - `telegram_digest.py`: digest orchestration and Telegram delivery
  - `telegram_analysis.py`: analytics admission/control, legacy settings adapter, supervised worker, OCR, artifact and delivery orchestration; stable public CLI and compatibility exports
  - `analysis_settings.py`: analytics policy, prompts, profiles and reviewed model metadata; receives legacy settings from the entrypoint
  - `analysis_evidence.py`: evidence schemas, source snapshots, text/OCR offsets, citation/QA/URL validation
  - `analysis_rendering.py`: pure HTML/Markdown formatting of prepared data
  - `analysis_pipeline.py`: selection/manifest, physical provider requests, extraction/cache, relations, merge and synthesis
  - `analysis_store.py`: SQL, transactions, durable budgets and provenance ledger
- Internal analytics calls and dependency patches use the owning module, including evaluator capture hooks. Compatibility exports are for existing callers, not mutable lookup ownership.
- Extracted analytics modules must not import the entrypoint or bridge. Settings and rendering remain independent of pipeline and persistence. Keep related pipeline algorithms together to avoid scattering state and limit handling; revisit this agreed boundary when responsibilities change.
- Keep `telegram_shared` limited to infrastructure primitives.
- Do not move digest, history, OCR, analytics, export, or Telethon business logic into `telegram_shared`.

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
- Keep analytics commands on one shared workflow; channels remain explicit and linked discussion comments stay outside scope.
- When changing analytics extraction, linking, merge or synthesis, use [analysis_prompts.toml](./config/analysis_prompts.toml) for task semantics and the existing schemas, validators and regression tests for executable contracts; verify required information and source support in the actual final synthesis request, including after context packing or reduction, rather than only in stored intermediate records.
- Run enabled, bounded OCR before freezing a new input manifest; resume must preserve existing manifest digests.
- Keep result provenance and recovery dependencies separate from evictable cross-job reuse; invalidate dependent unsent delivery plans when results change, preserving confirmed/unknown send history.
- Keep public analytics workers and evaluation `run` behind the shared outer hard TTL; private worker entrypoints are supervisor implementation details.

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
