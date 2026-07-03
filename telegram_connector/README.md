# Telegram Connector

Local Telegram toolkit for channel history ingestion, OCR, CSV export, scheduled digests, and a small Telegram Bot API bridge.

## Sources Of Truth

- [../RULEBOOK.md](../RULEBOOK.md): repository-wide runtime, config, install, and service rules
- [./AGENTS.md](./AGENTS.md): project-specific coding and maintenance guidance
- [config/runtime.example.toml](./config/runtime.example.toml): canonical runtime config shape, defaults, comments, and clamped ranges
- [config/digest_prompts.toml](./config/digest_prompts.toml): version-controlled digest prompt bundle
- [../telegram_shared/README.md](../telegram_shared/README.md): shared Telegram helper boundaries and retry semantics
- `--help` on the Python entrypoints: exact CLI syntax

## What It Does

- reads channel history through a Telegram user session with Telethon
- stores channel, message, media, sync, and AI usage data in local SQLite
- downloads media and runs Tesseract OCR for images
- exports saved channel history to CSV
- builds OpenAI digests over stored history and delivers per-channel summaries to Telegram
- runs a Bot API bridge for chat commands such as `/digest`, `/tail`, `/update`, `/exportcsv`, and `/ocr`
- records latest digest launch state in `data/launchd/digest.last_attempt.json`

## Runtime Config

Create `config/runtime.local.toml` from [config/runtime.example.toml](./config/runtime.example.toml). Keep local secrets and machine-specific paths out of git.

The README intentionally does not restate every config key. Use `runtime.example.toml` for the complete schema, comments, and clamped ranges. The operator-facing settings most often changed are:

- `telethon.*`, `auth.default_mode`, and `channels.default_list` for account access and default channel selection
- `bridge.allowed_chat_ids`, `bridge.allowed_user_ids`, and `bridge.allowed_usernames` for bot command access control
- `bridge.worker_process_timeout_seconds` for bridge-launched command lifetime
- `bridge.send_message_retry_attempts` and `bridge.send_message_retry_backoff_seconds` for transient Telegram `sendMessage` failures, including network errors, timeouts, and Telegram HTTP 5xx
- `digest.*`, `digest_ai.*`, and `digest_limits.*` for scheduled digest window, AI batching, sync budget, and process TTL
- `digest_prompts.file` for the prompt bundle used by digest
- `sync.sync_limit` and `sync.batch_size` for non-digest sync runs
- `ocr.*` for OCR defaults and subprocess timeout

`telegram.default_chat_id` can stay empty until you send one message to the bot and discover the chat id through `listen`.

### Secrets

Prefer Keychain references in `runtime.local.toml`, for example:

```toml
[secrets]
bot_token = "keychain://telegram-connector/bot_token"
api_hash = "keychain://telegram-connector/api_hash"
```

Useful Keychain commands:

```bash
security add-generic-password -U -s telegram-connector -a bot_token -w '<token>'
security find-generic-password -s telegram-connector -a bot_token -w
```

## Operation

Start the bridge manually:

```bash
python3 telegram_connector/telegram_bridge.py listen --run-commands
```

Install or refresh the macOS LaunchAgents:

```bash
bash telegram_connector/scripts/install_launch_agent.sh
```

Reload only the already-installed bridge LaunchAgent:

```bash
bash telegram_connector/scripts/restart_launch_agent.sh
```

Use the installer after code changes, `telegram_shared` changes, runtime config changes, prompt-bundle changes, or scheduled digest config changes. Use restart only when installed code and config are already current.

Launchd logs:

- `telegram_connector/data/launchd/bridge.startup.log`
- `telegram_connector/data/launchd/bridge.stdout.log`
- `telegram_connector/data/launchd/bridge.stderr.log`
- `telegram_connector/data/launchd/digest.startup.log`
- `telegram_connector/data/launchd/digest.stdout.log`
- `telegram_connector/data/launchd/digest.stderr.log`
- `telegram_connector/data/launchd/digest.last_attempt.json`

## Commands

Bot command quick reference:

- `/agent-stats`: recent local Digest AI usage and prompt-cache summary
- `/top-models [limit] [debug]`: configured external free-model ranking
- `/backfill [channel] [limit] [since=...] [until=...] [media] [bot|user|auto]`: historical load into SQLite
- `/tail [channel] [limit] [since=...] [until=...] [media|ocr|read] [bot|user|auto]`: latest window sync
- `/update [channel] [limit] [since=...] [until=...] [media|ocr|read] [bot|user|auto]`: import only newer messages
- `/ocrhistory [channel] [limit] [since=...] [until=...] [bot|user|auto]`: tail + media download + OCR
- `/digest [channel] [since=...] [until=...] [today|yesterday|week|month|-Nd] [bot|user|auto]`: sync + AI digest + Telegram delivery
- `/exportcsv [channel] [limit|since=... until=...] [bot|user|auto]`: export saved history to CSV
- `/ocr [limit] [channel] [since=...] [until=...]`: OCR already-downloaded pending images

Common CLI examples:

```bash
python3 telegram_connector/telegram_bridge.py get-me
python3 telegram_connector/telegram_history_client.py doctor
python3 telegram_connector/telegram_history_client.py init-db
python3 telegram_connector/telegram_history_client.py sync --mode tail --channel @vcnews --limit 100
python3 telegram_connector/telegram_digest.py run --channel @vcnews,@refugecard --auth-mode user
python3 telegram_connector/telegram_history_client.py export-csv --channel @vcnews --since 2026-03-15 --until 2026-03-16
python3 telegram_connector/telegram_history_client.py ocr-pending --limit 100
```

Command notes:

- channels can be explicit, comma-separated, or loaded from `[channels].default_list`
- private groups and channels without a public username can use Bot API-style ids such as `-1001449711572`
- `since` and `until` work across sync, digest, OCR, and export commands
- `week`, `month`, `today`, `yesterday`, and `-Nd` aliases are supported where date windows are accepted
- `media` downloads media; `ocr` implies image media download and OCR
- `read` is optional and only has effect in `user` auth mode
- if auth is omitted in a bot command, `user` is used by default

## Digest Behavior

Digest runs are per channel: prep-sync, optional OCR, AI analysis, and Telegram delivery are isolated by channel where possible.

- output is delivered to `telegram.default_chat_id`
- channels below `digest.min_messages_for_ai` send a short loaded-without-analysis note
- channels that fit `digest_ai.*` budgets use one direct OpenAI request; larger windows fall back to chronological batches and a final summary
- hitting an effective digest `sync_limit` is called out in the delivered channel message
- transient delivery failures are recorded as per-channel errors and produce `status=partial`
- permanent delivery failures, such as invalid chat ids or revoked tokens, fail the run and write `status=failed`
- final error summary delivery is best-effort

## Storage

Primary local runtime paths:

- `telegram_connector/data/telegram_history.sqlite3`
- `telegram_connector/data/media/`
- `telegram_connector/data/exports/`
- `telegram_connector/data/sessions/`
- `telegram_connector/data/launchd/`

## Dependencies

Required runtime dependencies:

- `Telethon`
- `tesseract`

Python setup:

```bash
python3 -m venv .venv-test-gap-detection
.venv-test-gap-detection/bin/python -m pip install -r telegram_connector/requirements.txt
```

## Tests

Preferred local regression run:

```bash
.venv-test-gap-detection/bin/python -m pytest telegram_connector/tests -q
```

When changing `telegram_shared`, run the shared suite as well:

```bash
.venv-test-gap-detection/bin/python -m pytest telegram_shared/tests telegram_connector/tests -q
```

Fallback runner:

```bash
python3 telegram_connector/run_tests.py
```
