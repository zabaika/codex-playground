# Telegram Connector

Telegram toolkit with three local components:

- `telegram_bridge.py`: minimal Bot API bridge for inbound/outbound bot messages
- `telegram_history_client.py`: channel history ingester based on Telethon + SQLite + Tesseract
- `telegram_digest.py`: config-driven digest orchestration and Telegram delivery

Project-wide implementation rules and reusable conventions are documented in `../RULEBOOK.md`.
Project-specific coding guidance is documented in [./AGENTS.md](./AGENTS.md).

## What it does

- checks bot connectivity with `getMe`
- receives incoming messages with long polling via `getUpdates`
- stores redacted inbound update summaries in `data/inbox.jsonl`
- remembers the latest processed update offset in `data/offset.local.json`
- sends outbound messages with `sendMessage`
- ingests public and private channel history through a Telegram user session
- stores normalized channel/message/media rows in SQLite
- downloads channel media locally
- runs OCR for downloaded images with Tesseract
- exports saved channel history to CSV
- builds config-driven OpenAI digests over stored channel history
- delivers per-channel digest summaries to Telegram
- records AI usage for digest and related analysis runs in SQLite
- exposes a local `/top-models` bridge command backed by the configured external ranking API

## Config

Use [../RULEBOOK.md](../RULEBOOK.md) for repository-wide config, secret, path, and logging conventions. This section documents only `telegram_connector`-specific runtime settings and operator-facing behavior.

Canonical config files:

- tracked config shape and documented defaults: [config/runtime.example.toml](./config/runtime.example.toml)
- local machine-specific runtime values: `config/runtime.local.toml`
- version-controlled digest prompt bundle referenced from runtime config: [config/digest_prompts.toml](./config/digest_prompts.toml)

Main config groups:

- `[telethon]`, `[auth]`, `[channels]`: Telegram account, auth mode, and default channel selection
- `[processing]`, `[ocr]`: shared analysis defaults, including model and OCR behavior
- `[digest]`, `[digest_ai]`, `[digest_limits.*]`, `[digest_prompts]`: digest windows, AI batching, sync limits, and prompt bundle
- `[sync]`: non-digest sync limits and in-memory staging before short SQLite flush transactions
- `[bridge]`: bot access control, reply chunking, `/agent-stats`, and `/top-models`
- `[paths]`, `[secrets]`: local runtime paths and secret references

`default_chat_id` can stay empty until you send at least one message to the bot and discover your chat id through `listen`.

### History client and auth

- `telethon.api_id` and `secrets.api_hash` come from [my.telegram.org](https://my.telegram.org)
- prefer Keychain references like `keychain://telegram-connector/bot_token` for `telethon.phone` and `secrets.*`
- `telethon.phone` is the phone number of the Telegram user account that reads channel history
- `secrets.user_password` is optional and only needed when Telegram account 2FA is enabled
- for private channels, that user account must already be a member of the channel
- `telethon.user_session_name` and `telethon.bot_session_name` keep separate Telethon sessions
- `auth.default_mode = "user"` makes user-auth the default when a command does not specify auth explicitly
- you can still force `bot` or `auto` per command when needed

### Default channels

- `channels.default_list` is optional and stores default channels in the format `"channel, display name"`
- public channels can be listed as `@username`
- private groups and channels without a public username can be listed by their Bot API-style numeric id, for example `-1001449711572, Private Group`
- if a command does not include `--channel` or `/... @channel`, the history client uses `channels.default_list`
- any explicit channel or comma-separated channel list overrides the config list

### Processing and bridge defaults

- `processing.model` is the default OpenAI model used by digest and other analysis commands
- `processing.ocr` controls whether processing flows should download image media and run OCR by default
- `bridge.allowed_chat_ids` restricts which Telegram chats may invoke bridge commands; if it is empty, the bridge falls back to `telegram.default_chat_id` when that value is set
- `bridge.allowed_user_ids` and `bridge.allowed_usernames` further restrict which senders may trigger bot commands; `@name` and `name` are treated the same
- `bridge.text_chunk_size` controls how long Telegram text replies may grow before the bot splits them into multiple messages
- `bridge.agent_stats_row_limit` limits `/agent-stats` to a recent window from `ai_usage_log`
- `bridge.top_models_*` controls the external ranking source, timeout, cache TTL, and default result limit for `/top-models`

### Digest defaults

- `digest.separator_text` optionally appends the configured divider twice to the end of each digest message; leave it empty to disable the separator
- `digest.run_total_timeout_seconds` is the hard wall-clock TTL for the whole one-shot digest run; it is enforced outside Python through the shared TTL runner
- `digest.termination_grace_seconds` is the grace period between `SIGTERM` and `SIGKILL` when the hard digest TTL expires
- `digest.since` and `digest.until` define the default analysis window; `yesterday` is the recommended morning default
- `digest.until` uses the same aliases as `since`, but date-only values are expanded to the end of that UTC day
- `digest.min_messages_for_ai` sets the per-channel minimum required for OpenAI analysis; below that threshold digest still syncs messages but sends only a short Telegram note without AI processing
- supported date aliases for `since` / `until` include `today` and `-Nd`
- the same alias logic now applies consistently to `sync`, `export-csv`, `ocr-pending`, and `digest`
- `digest.mark_read` enables the existing mark-as-read mechanism for digest prep-sync; it only has effect in `user` auth mode
- `digest.sync_total_timeout_seconds` is a soft in-process timeout for the prep-sync phase; it does not replace the outer hard process TTL
- `[digest_prompts].file` points to the version-controlled TOML bundle that stores the AI system instructions and per-channel prompt templates; relative paths are resolved from the config directory that contains `runtime.local.toml`
- `digest` overrides for `channel`, `since`, `until`, and auth mode win over config defaults when you pass them explicitly

### Digest limits and AI batching

- `digest_limits.day`, `digest_limits.week`, and `digest_limits.month` override digest limits automatically based on the chosen date window
- when more than one channel is selected for `digest`, the active digest `sync_limit` is distributed across them inside that run
- `digest_ai.messages_per_ai_pass` is the per-channel message cap for one direct AI pass; if the full rendered window does not fit, the same value becomes the fallback chunk size
- `digest_ai.message_text_max_chars` and `digest_ai.message_ocr_max_chars` cap how much text from one stored row reaches the prompt
- `digest_ai.message_block_max_chars` is the maximum rendered character budget for one AI input block for one channel
- metadata such as `id`, `date`, `sender`, `link`, `forwards`, and `replies` are always included and do not count against text or OCR limits
- current starting point in config uses `digest_ai.messages_per_ai_pass = 500`, `digest_ai.message_text_max_chars = 450`, `digest_ai.message_ocr_max_chars = 300`, and `digest_ai.message_block_max_chars = 100000`

### Sync behavior

- `[sync].batch_size` controls how many prepared message writes are staged in memory before one short SQLite flush transaction
- `[sync].sync_limit` is the shared Telegram download cap for non-digest sync commands across all selected channels in one run
- `digest_limits.*.sync_limit` caps Telegram fetch volume for digest; `[sync].batch_size` only controls local staging and short flush size
- for multi-channel sync, channels are processed strictly in the order you pass them, or in config order when the channel list comes from defaults
- for non-digest sync, both `[sync].sync_limit` and command-level `--limit` apply; `--limit 0` removes only the per-channel cap
- `tail`, `update`, and `backfill` already stream messages from Telegram incrementally through Telethon; `[sync].batch_size` affects local DB flush size, not Telegram API paging

### Digest output and usage logging

- `message_block` includes a direct Telegram link, `message_id`, UTC date, sender display name, sender username, `forwards`, `replies`, text, and OCR text when available
- for private groups and channels without a public username, `channels.username` may stay empty in SQLite; digest links then fall back to `https://t.me/c/<internal_chat_id>/<message_id>`
- AI batching in `digest` is per channel, not cross-channel
- digest delivery is also per channel; a final status message is sent only if one or more channels failed during sync or analysis
- every OpenAI digest call logs usage into SQLite table `ai_usage_log`
- `ai_usage_log` lives inside `data/telegram_history.sqlite3`; it is not a file under `data/launchd`

### macOS Keychain

The project can resolve secret references through macOS Keychain with `security find-generic-password`.

Suggested generic-password layout in Keychain:

- service: `telegram-connector`
- accounts:
  - store each secret under its own account name, for example `bot_token`

Then set these refs in `runtime.local.toml`:

```toml
[telethon]
api_id = "keychain://telegram-connector/api_id"

[secrets]
bot_token = "keychain://telegram-connector/bot_token"
```

Before running the scripts:

- store a secret once:
  `security add-generic-password -U -s telegram-connector -a bot_token -w '<token>'`
- verify a ref manually:
  `security find-generic-password -s telegram-connector -a bot_token -w`

Secrets are resolved in this order:

- environment variable, if set
- `keychain://...` Keychain reference
- `op://...` legacy 1Password reference
- plain local value from `runtime.local.toml`

## Usage

### Runtime

Telegram bot commands are executed only while the local bridge process is running.
If `telegram_bridge.py listen --run-commands` is not running, the bot can receive messages in Telegram but it will not execute history-client commands.

Bridge commands are accepted with or without the leading `/`.
Bot-triggered commands can be restricted by `[bridge].allowed_chat_ids` and, when configured, by `[bridge].allowed_user_ids` or `[bridge].allowed_usernames`.
If auth is omitted in a bot command, `user` is used by default.

Bot command quick reference:

- `/agent-stats`: recent local OpenAI usage and prompt-cache summary
- `/top-models [limit] [debug]`: current top free models from the configured ranking API
- `/backfill [channel] [limit] [since=...] [until=...] [media] [bot|user|auto]`: historical load into SQLite
- `/tail [channel] [limit] [since=...] [until=...] [media|ocr|read] [bot|user|auto]`: latest window sync
- `/update [channel] [limit] [since=...] [until=...] [media|ocr|read] [bot|user|auto]`: only messages newer than saved history
- `/ocrhistory [channel] [limit] [since=...] [until=...] [bot|user|auto]`: tail + media download + OCR
- `/digest [channel] [since=...] [until=...] [today|yesterday|week|month|-Nd] [bot|user|auto]`: sync + AI digest + Telegram delivery
- `/exportcsv [channel] [limit|since=... until=...] [bot|user|auto]`: export saved history to CSV
- `/ocr [limit] [channel] [since=...] [until=...]`: OCR only for already-downloaded pending images

Start the bridge manually:

```bash
python3 telegram_connector/telegram_bridge.py listen --run-commands
```

Install the macOS background service:

```bash
bash telegram_connector/scripts/install_launch_agent.sh
```

This installs both LaunchAgents:

- `com.zabaika.telegram-connector-bridge`
- `com.zabaika.telegram-connector-digest`

Installed launch entrypoints:

- `launchd` starts `scripts/telegram-connector-bridge-launcher`
- `launchd` starts `scripts/telegram-connector-digest-launcher`
- each launcher executes the existing `run_telegram_*.sh` runner through `/bin/bash`
- do not point `ProgramArguments[0]` directly at `run_telegram_*.sh`; macOS has shown intermittent `posix_spawn(... .sh) -> Operation not permitted` failures on the scheduled digest path

Reload the installed bridge LaunchAgent:

```bash
bash telegram_connector/scripts/restart_launch_agent.sh
```

Service update rule:

- rerun `install_launch_agent.sh` after code changes, `telegram_shared` changes, `runtime.local.toml` changes, prompt-bundle changes, or schedule-related config changes such as `digest.time`
- use `restart_launch_agent.sh` only to reload the already installed bridge LaunchAgent when the installed code and config are already up to date
- the installed plist files must keep their first `ProgramArguments` item on the generated launcher executable, not on the shell runner
- the scheduled digest runner uses the shared `common/ttl_runner.py` with two protections:
  - a sidecar `caffeinate -i -w <child_pid>` so a `launchd` start during macOS maintenance wake does not immediately fall back asleep mid-run
  - a hard wall-clock TTL so a stuck Telethon cleanup cannot leave the job in `launchd state=running` forever

Daemon logs:

- `telegram_connector/data/launchd/bridge.startup.log`
- `telegram_connector/data/launchd/bridge.stdout.log`
- `telegram_connector/data/launchd/bridge.stderr.log`
- `telegram_connector/data/launchd/digest.startup.log`
- `telegram_connector/data/launchd/digest.stdout.log`
- `telegram_connector/data/launchd/digest.stderr.log`
- `telegram_connector/data/launchd/digest.last_attempt.json`
  overwritten on every digest run and keeps only the latest launch audit record

### Shared command rules

- channels can be passed explicitly or taken from `[channels].default_list`
- commands that accept channels also accept comma-separated lists like `@vcnews,@another_channel`
- `since` and `until` work for all sync, OCR, and export commands
- `since=2026-03-15` means `2026-03-15T00:00:00+00:00`
- `until=2026-03-16` means `2026-03-16T23:59:59+00:00`
- aliases `week` and `month` mean `7` and `30` days back from today in UTC
- `media` downloads media only
- `ocr` downloads image media and runs OCR
- `read` is strictly optional and marks messages as read only in `user` auth mode
- `read` is tied to the last processed checkpoint, not to newly appeared channel posts

### Command reference

#### `get-me`

Description:
Check that the bot token works and return basic bot metadata.

CLI:

```bash
python3 telegram_connector/telegram_bridge.py get-me
```

Additional notes:

- use this first when validating a new bot token

#### `listen`

Description:
Run long polling for inbound Telegram bot updates.

CLI:

```bash
python3 telegram_connector/telegram_bridge.py listen
```

Operator example:

```bash
python3 telegram_connector/telegram_bridge.py listen --run-commands
```

Additional notes:

- `--run-commands` is required if you want Telegram messages to execute history-client commands
- the launchd service runs this mode automatically after installation

#### `send`

Description:
Send a plain bot message to a Telegram chat.

CLI:

```bash
python3 telegram_connector/telegram_bridge.py send --chat-id 123456789 "hello from Codex"
```

Additional notes:

- if `--chat-id` is omitted, `default_chat_id` is used

#### `doctor`

Description:
Check local runtime configuration and dependency availability.

CLI:

```bash
python3 telegram_connector/telegram_history_client.py doctor
```

Additional notes:

- useful after changing config, Keychain refs, Telethon, or Tesseract setup

#### `init-db`

Description:
Create or migrate the local SQLite database.

CLI:

```bash
python3 telegram_connector/telegram_history_client.py init-db
```

Additional notes:

- safe to rerun after schema changes

#### `inspect-state`

Description:
Show known channels and their sync checkpoints from SQLite.

CLI:

```bash
python3 telegram_connector/telegram_history_client.py inspect-state
```

Additional notes:

- useful for understanding `last_backfill_message_id` and `last_tail_message_id`

#### `digest`

Description:
Run the config-driven digest workflow: sync the selected channels, optionally OCR images, then either summarize each channel in one direct AI pass or fall back to batched per-channel summarization before delivering to `telegram.default_chat_id`.

CLI:

```bash
python3 telegram_connector/telegram_digest.py run
```

Bot:

```text
/digest
```

Operator example:

```bash
python3 telegram_connector/telegram_digest.py run --channel @vcnews,@refugecard --auth-mode user
```

Additional notes:

- `digest` takes model and OCR defaults from `[processing]`, schedule and window defaults from `[digest]`, digest-wide AI pass limits from `[digest_ai]`, profile-based fetch budgets from `[digest_limits.*]`, and the prompt bundle path from `[digest_prompts].file`
- explicit `--channel`, `--since`, `--until`, and `--auth-mode` override config defaults for a single run
- bot command `digest` supports the same override set: `/digest @vcnews since=2026-03-17 until=2026-03-17`
- if a selected channel has fewer messages than `digest.min_messages_for_ai`, digest skips OpenAI for that channel and sends a short “loaded without analysis” note instead
- if the channel window fits the configured `digest_ai.*` budgets, digest uses one direct AI request; otherwise it falls back to chronological per-channel batches and one final summary
- if a channel reaches its effective `sync_limit` during digest prep-sync, the Telegram digest message for that channel ends with an explicit warning that the history window may have been loaded only partially
- digest formatter normalizes close heading variants from the model back into canonical Telegram section names
- output is delivered to `telegram.default_chat_id`, not to an arbitrary invoking chat
- the scheduled LaunchAgent uses `digest.time` from config and writes logs to `telegram_connector/data/launchd/digest.*.log`

#### `sync`

Description:
Unified sync command for `backfill`, `tail`, and `update`.

CLI:

```bash
python3 telegram_connector/telegram_history_client.py sync --mode tail --channel @vcnews --limit 100
```

Bot aliases:

```text
/backfill @vcnews 100
/tail @vcnews 100
/update @vcnews 100
```

Operator examples:

```bash
python3 telegram_connector/telegram_history_client.py sync --mode backfill --channel @vcnews --since 2026-03-15 --until 2026-03-16
python3 telegram_connector/telegram_history_client.py sync --mode tail --channel @vcnews --limit 100 --download-media --ocr --auth-mode user
python3 telegram_connector/telegram_history_client.py sync --mode update --channel @vcnews --limit 0
```

Additional notes:

- `--limit 0` removes the per-channel message cap for `sync`; the run is then constrained only by `[sync].sync_limit`
- `--mode backfill` is the reliable choice for older date ranges that are no longer in the latest channel tail
- if you pass a private group or channel id in Bot API form like `-1001449711572`, `sync`, `digest`, and SQLite filtering normalize it automatically to the stored internal channel id
- `--mode tail` scans the latest window of messages
- `--mode update` stops at the boundary of already saved history and imports only newer messages
- `ocr` implies media download for image files
- `read` in `update` mode marks only the previously processed checkpoint if newer posts appeared after an earlier sync

#### `export-csv`

Description:
Export saved channel history from SQLite into CSV.

CLI:

```bash
python3 telegram_connector/telegram_history_client.py export-csv --channel @vcnews --limit 100
```

Bot:

```text
/exportcsv @vcnews 100
```

Operator example:

```bash
python3 telegram_connector/telegram_history_client.py export-csv --channel @vcnews --since 2026-03-15 --until 2026-03-16
```

Additional notes:

- `until` is optional
- when omitted, export goes through the newest saved message
- multi-channel export creates one CSV per channel
- CSV also includes normalized message-analysis fields from `messages`, including `grouped_id`, `content_hash`, and `imported_at`

#### `fetch-message`

Description:
Fetch one Telegram message by URL, store it in SQLite, and export a Markdown source artifact for downstream note workflows.

CLI:

```bash
python3 telegram_connector/telegram_history_client.py fetch-message --url https://t.me/bezaspera/2833
```

Operator example:

```bash
python3 telegram_connector/telegram_history_client.py fetch-message \
  --url https://t.me/bezaspera/2833 \
  --output-dir "scratch/article-to-obsidian-kb/telegram"
```

Additional notes:

- this command always uses `user` auth because direct message lookup by URL should not depend on bot visibility rules
- the fetched channel and message are always written to the local SQLite history database; there is no opt-out flag
- the exported `.md` file is a staging source artifact: it keeps source metadata and reconstructs Telegram links inline in the message body whenever that is safe
- if a Telegram entity is too broken to inline safely, the exporter keeps the original body text and adds that URL under `Unresolved Links`
- if `--output-dir` is omitted, the file is written under `scratch/article-to-obsidian-kb/telegram/`

#### `ocrhistory`

Description:
Shortcut for history sync with media download and OCR.

Bot:

```text
/ocrhistory @vcnews 50
```

Equivalent CLI:

```bash
python3 telegram_connector/telegram_history_client.py sync --mode tail --channel @vcnews --limit 50 --download-media --ocr --auth-mode user
```

Additional notes:

- this command fetches Telegram history, downloads image media for that window, and then runs OCR on those files

#### `ocr` / `ocr-pending`

Description:
Run OCR only for already downloaded local image files that are still pending OCR.

CLI:

```bash
python3 telegram_connector/telegram_history_client.py ocr-pending --limit 100
```

Bot:

```text
/ocr 100
```

Additional notes:

- this command does not fetch new Telegram messages
- it only processes local files already present in `data/media/`
- bridge command `ocr` is an alias for CLI command `ocr-pending`

## Storage

SQLite database:

- `channels`: known channels and raw metadata
- `messages`: normalized message rows with raw JSON
- `media_assets`: downloaded media files and OCR results
- `sync_state`: per-channel checkpoints for backfill/tail runs
- `ai_usage_log`: OpenAI usage records for digest and related analysis

Local runtime paths:

- `telegram_connector/data/telegram_history.sqlite3`
- `telegram_connector/data/media/`
- `telegram_connector/data/exports/`
- `telegram_connector/data/sessions/`
- `telegram_connector/data/launchd/`

## Dependencies

Required runtime dependencies:

- `Telethon`
- `tesseract`

Python requirements file:

```bash
python3 -m venv .venv-test-gap-detection
.venv-test-gap-detection/bin/python -m pip install -r telegram_connector/requirements.txt
```

The tracked requirements include both runtime dependencies and the local `pytest` dependency used by the regression suite.

## Tests

Preferred local regression run:

```bash
.venv-test-gap-detection/bin/python -m pytest telegram_connector/tests -q
```

Coverage includes:

- bridge command parsing and chat allowlists
- shared Bot API timeout and transport error messaging
- response chunk splitting
- CSV export logic
- SQLite schema initialization and sync-state handling
- local runtime config loading
- auth-mode routing and default `user` auth in bot commands

Fallback runner:

```bash
python3 telegram_connector/run_tests.py
```
