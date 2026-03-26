# Telegram Connector

Telegram toolkit with two local clients:

- `telegram_connector.py`: minimal Bot API bridge for inbound/outbound bot messages
- `telegram_history_client.py`: channel history ingester based on Telethon + SQLite + Tesseract

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
- can build and deliver a config-driven daily AI digest to Telegram

## Config

Use [../RULEBOOK.md](../RULEBOOK.md) as the source of truth for repository-wide conventions:

- local-vs-committed config layout
- secret handling
- runtime file placement
- logging and storage safety rules

Config shape:

```toml
[telegram]
default_chat_id = "<chat id or empty>"

[telethon]
user_session_name = "<user session name>"
bot_session_name = "<bot session name>"
api_id = "<api id or secret reference>"
phone = "<phone or secret reference>"

[auth]
default_mode = "<user|bot|auto>"
public_channel_mode = "<user|bot>"
private_channel_mode = "<user|bot>"

[channels]
default_list = [
  "<channel>, <display name>",
]

[processing]
model = "<OpenAI model>"
ocr = "<true|false>"

[digest]
time = "<HH:MM>"
since = "<today|yesterday|week|month|-Nd|YYYY-MM-DD>"
until = "<today|yesterday|week|month|-Nd|YYYY-MM-DD>"
sync_mode = "<backfill|update|tail>"
mark_read = "<true|false>"

[digest_limits.day]
sync_limit = "<messages per digest run for a 1-day window>"
ai_batch_size = "<AI messages per summarization chunk for a 1-day window>"

[digest_limits.week]
sync_limit = "<messages per digest run for a 7-day window>"
ai_batch_size = "<AI messages per summarization chunk for a 7-day window>"

[digest_limits.month]
sync_limit = "<messages per digest run for a 30-day window>"
ai_batch_size = "<AI messages per summarization chunk for a 30-day window>"

[sync]
sync_limit = "<messages per non-digest sync run, 0 disables the shared cap>"
backfill_limit = "<default per-channel limit for sync --mode backfill>"
tail_limit = "<default per-channel limit for sync --mode tail>"
update_limit = "<default per-channel limit for sync --mode update>"
batch_size = "<shared SQLite commit batch size for all sync flows>"

[digest_prompts]
system_instructions = "<system instructions>"
batch_digest_template = """
<batch prompt template with placeholders>
"""

final_digest_template = """
<final prompt template with placeholders>
"""

[paths]
# history_db = "/absolute/path/to/telegram_history.sqlite3"
# media_root = "/absolute/path/to/telegram_media"
# tesseract_binary = "/opt/homebrew/bin/tesseract"

[bridge]
allowed_chat_ids = "<comma-separated chat ids or empty>"
text_chunk_size = "<500..4096>"
agent_stats_row_limit = "<20..2000>"

[secrets]
bot_token = "<secret reference or local secret>"
api_hash = "<secret reference or local secret>"
user_password = "<secret reference or local secret>"
openai_api_key = "<secret reference or local secret>"
```

`default_chat_id` can stay empty until you send at least one message to the bot and discover your chat id through `listen`.

For the history client:

- `telethon.api_id` and `secrets.api_hash` come from [my.telegram.org](https://my.telegram.org)
- `telethon.api_id`, `telethon.phone` and `secrets.*` can be plain local values, but the preferred mode is a Keychain reference like `keychain://telegram-connector/bot_token`
- `telethon.phone` is the phone number of the Telegram user account that will read channel history; it can also be a Keychain reference
- `secrets.user_password` is optional and only needed when Telegram account 2FA is enabled
- for private channels, that user account must already be a member of the channel
- `telethon.user_session_name` and `telethon.bot_session_name` keep separate Telethon sessions
- `auth.default_mode = "user"` makes user-auth the default when a command does not specify auth explicitly
- you can still force `bot` or `auto` per command when needed
- `channels.default_list` is optional and stores default channels in the format `"channel, display name"`
- public channels can be listed as `@username`
- private groups and channels without a public username can be listed by their Bot API-style numeric id, for example `-1001449711572, Private Group`
- if a command does not include `--channel` or `/... @channel`, the history client uses `channels.default_list`
- if a command explicitly includes one channel or a comma-separated channel list, that explicit value overrides the config list
- `[processing]` stores cross-cutting defaults shared by analysis flows
- `processing.model` is the default OpenAI model used by digest and other analysis commands
- `processing.ocr` controls whether processing flows should download image media and run OCR by default
- `bridge.text_chunk_size` controls how long Telegram text replies may grow before the bot splits them into multiple messages
- `bridge.agent_stats_row_limit` limits `/agent-stats` to the latest N rows from `ai_usage_log`, so the command stays fast as the database grows
- `[digest]` stores default daily-digest behavior
- `digest.separator_text` optionally appends the configured divider twice to the end of each digest message; leave it empty to disable the separator
- `digest.since` and `digest.until` define the default analysis window; `yesterday` is the recommended morning default
- `digest.until` uses the same aliases as `since`, but date-only values are expanded to the end of that UTC day
- `digest.min_messages_for_ai` sets the per-channel minimum required for OpenAI analysis; below that threshold digest still syncs messages but sends only a short Telegram note without AI processing
- supported date aliases for `since` / `until`: `today`, `yesterday`, `week`, `month`, `-Nd`
- `-Nd` means “N days back from the current UTC date”, so with current UTC date `2026-03-23`, `-3d` resolves to `2026-03-20`
- the same alias logic now applies consistently to `sync`, `export-csv`, `ocr-pending`, and `digest`
- `digest.mark_read` enables the existing mark-as-read mechanism for digest prep-sync; it only has effect in `user` auth mode
- `[digest_prompts]` stores the AI system instructions and the per-channel prompt template
- `digest_limits.day`, `digest_limits.week`, and `digest_limits.month` override digest limits automatically based on the chosen date window
- `digest_limits.day` applies to any one-day window, including `today`, `yesterday`, or an explicit single date like `since=2026-03-18 until=2026-03-18`
- when more than one channel is selected for `digest`, the active digest `sync_limit` is distributed across them inside that run so that every selected channel gets a share of the budget
- `digest_limits.*.ai_batch_size` is the AI summarization chunk size used for hierarchical digest generation
- `[sync].batch_size` is the shared SQLite commit batch size for all sync flows, including digest-prep sync
- `[sync].sync_limit` is the shared Telegram download cap for non-digest sync commands; it is applied across all selected channels in one run
- `digest_limits.*.sync_limit` and `[sync].batch_size` do different jobs:
  `digest_limits.*.sync_limit` caps how many Telegram messages a digest run may fetch for the chosen window profile, while `[sync].batch_size` only controls how many downloaded rows are written before the next SQLite commit
- for multi-channel sync, channels are processed strictly in the order you pass them, or in config order when the channel list comes from defaults
- `[sync].sync_limit` and a command-level `--limit` both apply for non-digest sync:
  channels are processed in the order you requested, each channel may use up to its own command `--limit`, and the whole run stops once the shared `[sync].sync_limit` budget is exhausted
- for non-digest sync, `--limit 0` removes the per-channel cap entirely; in that case only the shared `[sync].sync_limit` still limits the overall run
- `batch_digest_template` supports `{channel_name}`, `{since}`, `{until}`, `{batch_index}`, `{message_count}`, `{message_block}`, and `{previous_batch_summary}`
- `final_digest_template` supports `{channel_name}`, `{since}`, `{until}`, `{message_count}`, `{batch_count}`, and `{batch_summary_block}`
- `digest` overrides for `channel`, `since`, `until`, and auth mode win over config defaults when you pass them explicitly
- `message_block` already includes a direct Telegram message link, `message_id`, UTC date, sender display name, sender username, `forwards`, `replies`, text, and OCR text when available
- for private groups and channels without a public username, `channels.username` may stay empty in SQLite; this is expected and not an error
- when `channels.username` is empty, digest links fall back to the private-message format `https://t.me/c/<internal_chat_id>/<message_id>`
- both batch and final digest prompts should require the same first-line convention, currently `Главные темы дня: ...`, to reduce model drift between intermediate and final summaries
- the digest prompts are expected to output `Наиболее популярное` as direct Telegram message links with short human-readable titles, using `forwards` and `replies` as popularity hints
- `Связки вопрос-ответ/развитие темы` stays optional and should only appear when it adds structure beyond the main topics and popularity block; when used, each item should also reference a direct message link
- prompt templates should avoid leaking internal processing words like `батч` into the user-facing digest text
- Telegram-specific spacing and block formatting are enforced by digest post-processing, so prompt templates only need to describe the semantic structure of the answer
- `tail`, `update`, and `backfill` already stream messages from Telegram incrementally through Telethon; `[sync].batch_size` affects local DB commit batching, not the underlying Telegram API paging
- AI batching in `digest` is done per channel, not across channels: each selected channel gets its own batch chain, intermediate summaries, and final channel summary
- digest delivery is also per channel: as soon as one channel summary is ready, it is sent to Telegram as a separate message
- a final digest status message is sent only if one or more channels failed during sync or analysis
- digest prompts are structured so that the stable instruction/rubric prefix comes before the variable batch payload, which helps OpenAI input caching hit more often across repeated calls
- `digest_prompts.shared_prompt_prefix` is prepended to both intermediate and final prompts so the cache-friendly prefix stays aligned across the whole digest pipeline
- every OpenAI digest call logs usage into SQLite table `ai_usage_log`, including input tokens, cached input tokens, output tokens, total tokens, latency, stage, status, `response_id`, `prompt_cache_key`, and shared-prefix diagnostics

### macOS Keychain

The project can resolve secret references through macOS Keychain with `security find-generic-password`.

Suggested generic-password layout in Keychain:

- service: `telegram-connector`
- accounts:
  - `api_id`
  - `bot_token`
  - `api_hash`
  - `phone`
  - `user_password`
  - `openai_api_key`
  - `allowed_users`

Then set these refs in `runtime.local.toml`:

```toml
[telethon]
api_id = "keychain://telegram-connector/api_id"
phone = "keychain://telegram-connector/phone"

[secrets]
bot_token = "keychain://telegram-connector/bot_token"
api_hash = "keychain://telegram-connector/api_hash"
user_password = "keychain://telegram-connector/user_password"
openai_api_key = "keychain://telegram-connector/openai_api_key"
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
If `telegram_connector.py listen --run-commands` is not running, the bot can receive messages in Telegram but it will not execute history-client commands.

Bridge commands are accepted in these forms:

- `/update 10`
- `update 10`
- `/update@verter_the_bot 10`
- `/agent-stats`

Only `chat_id` values from `[bridge].allowed_chat_ids` may run bot-triggered commands.
If auth is omitted in a bot command, `user` is used by default.
The leading `/` is optional for supported bridge commands.

Bot command quick reference:

- `/agent-stats`
  show local OpenAI usage and prompt-cache summary from recent digest runs
- `/backfill [channel] [limit] [since=...] [until=...] [media] [bot|user|auto]`
  historical load into SQLite
- `/tail [channel] [limit] [since=...] [until=...] [media|ocr|read] [bot|user|auto]`
  latest window sync
- `/update [channel] [limit] [since=...] [until=...] [media|ocr|read] [bot|user|auto]`
  only messages newer than saved history
- `/ocrhistory [channel] [limit] [since=...] [until=...] [bot|user|auto]`
  tail + media download + OCR
- `/digest [channel] [since=...] [until=...] [today|yesterday|week|month|-Nd] [bot|user|auto]`
  sync + AI digest + Telegram delivery using config defaults
- `/exportcsv [channel] [limit|since=... until=...] [bot|user|auto]`
  export saved history to CSV
- `/ocr [limit] [channel] [since=...] [until=...]`
  OCR only for already-downloaded pending images

Bot command notes:

- `channel` may be omitted to use default channels from config
- `channel` may be a comma-separated list
- auth defaults to `user`
- `since` means start of UTC day, `until` means end of UTC day for date-only values
- supported date aliases: `today`, `yesterday`, `week`, `month`, `-Nd`
- `/digest` keeps processing defaults, sync behavior, batch size, prompts, and schedule in config; bot parameters only override `channel`, `since`, `until`, and auth mode
- `/digest -3d` is a shorthand for a one-day digest window with `since=-3d` and `until=-3d`
- `/agent-stats` is handled locally by the bridge and scans only the latest configured `ai_usage_log` rows

Start the bridge manually:

```bash
python3 telegram_connector/telegram_connector.py listen --run-commands
```

Install the macOS background service:

```bash
bash telegram_connector/scripts/install_launch_agent.sh
```

This installs both LaunchAgents:

- `com.zabaika.telegram-connector-bridge`
- `com.zabaika.telegram-connector-digest`

Restart the background service:

```bash
bash telegram_connector/scripts/restart_launch_agent.sh
```

Daemon logs:

- `telegram_connector/data/launchd/bridge.startup.log`
- `telegram_connector/data/launchd/bridge.stdout.log`
- `telegram_connector/data/launchd/bridge.stderr.log`
- `telegram_connector/data/launchd/digest.startup.log`
- `telegram_connector/data/launchd/digest.stdout.log`
- `telegram_connector/data/launchd/digest.stderr.log`

### Shared command rules

- channels can be passed explicitly or taken from `[channels].default_list`
- commands that accept channels also accept comma-separated lists like `@vcnews,@another_channel`
- `since` and `until` work for all sync, OCR, and export commands
- `since=2026-03-15` means `2026-03-15T00:00:00+00:00`
- `until=2026-03-16` means `2026-03-16T23:59:59+00:00`
- aliases `week` and `month` mean `7` and `30` days back from today in UTC
- `digest` uses `[digest]` defaults for its window and sync settings, and `[processing]` defaults for model/OCR, whenever you omit overrides
- digest analysis is hierarchical: overlapping message batches are summarized first, then a final digest is built from the batch summaries
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
python3 telegram_connector/telegram_connector.py get-me
```

Additional notes:

- use this first when validating a new bot token

#### `listen`

Description:
Run long polling for inbound Telegram bot updates.

CLI:

```bash
python3 telegram_connector/telegram_connector.py listen
```

Examples:

```bash
python3 telegram_connector/telegram_connector.py listen --once
python3 telegram_connector/telegram_connector.py listen --echo
python3 telegram_connector/telegram_connector.py listen --run-commands
```

Additional notes:

- `--run-commands` is required if you want Telegram messages to execute history-client commands
- the launchd service runs this mode automatically after installation

#### `send`

Description:
Send a plain bot message to a Telegram chat.

CLI:

```bash
python3 telegram_connector/telegram_connector.py send --chat-id 123456789 "hello from Codex"
```

Examples:

```bash
python3 telegram_connector/telegram_connector.py send "hello from Codex"
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
Run the config-driven morning workflow: sync the selected channels, optionally OCR images, summarize messages in overlapping AI batches, then build one final per-channel digest and deliver it to `telegram.default_chat_id`.

CLI:

```bash
python3 telegram_connector/telegram_digest.py run
```

Bot:

```text
/digest
```

Examples:

```bash
python3 telegram_connector/telegram_digest.py run --channel @vcnews
python3 telegram_connector/telegram_digest.py run --since 2026-03-17 --until 2026-03-17
python3 telegram_connector/telegram_digest.py run --since week
python3 telegram_connector/telegram_digest.py run --channel @vcnews,@refugecard --auth-mode user
```

Additional notes:

- `digest` takes model and OCR defaults from `[processing]`, schedule and window defaults from `[digest]`, profile-based fetch and AI chunk limits from `[digest_limits.*]`, and prompt templates from `[digest_prompts]`
- explicit `--channel`, `--since`, `--until`, and `--auth-mode` override config defaults for a single run
- bot command `digest` supports the same override set: `/digest @vcnews since=2026-03-17 until=2026-03-17`
- if a selected channel has fewer messages than `digest.min_messages_for_ai`, digest skips OpenAI for that channel and sends a short “loaded without analysis” note instead
- sender display names and usernames are included in the AI input to preserve question/answer context in user discussions
- batches keep chronological order and use a small automatic overlap between neighboring batches to reduce context loss at boundaries
- final quality is usually better than a single huge prompt on long periods because the model sees ordered local context first and only then performs a second-pass synthesis
- output is delivered to `telegram.default_chat_id`, not to an arbitrary invoking chat
- the scheduled LaunchAgent uses `digest.time` from config and writes logs to `telegram_connector/data/launchd/digest.*.log`
- if you change `digest.time` or other schedule-related config, rerun `install_launch_agent.sh` so the LaunchAgent plist is regenerated with the new time

#### `sync`

Description:
Unified sync command for `backfill`, `tail`, and `update` modes.

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

Examples:

```bash
python3 telegram_connector/telegram_history_client.py sync --mode backfill --channel @vcnews --since 2026-03-15 --until 2026-03-16
python3 telegram_connector/telegram_history_client.py sync --mode backfill --channel @vcnews,@another_channel --limit 100
python3 telegram_connector/telegram_history_client.py sync --mode backfill --channel @vcnews --limit 100 --download-media
python3 telegram_connector/telegram_history_client.py sync --mode backfill --channel https://t.me/+invitehash --limit 100 --auth-mode user
python3 telegram_connector/telegram_history_client.py sync --mode backfill --channel @vcnews --limit 0 --since 2026-03-15 --until 2026-03-16
python3 telegram_connector/telegram_history_client.py sync --mode tail --channel @vcnews --limit 100 --since 2026-03-15
python3 telegram_connector/telegram_history_client.py sync --mode tail --channel @vcnews --limit 100 --download-media --ocr --auth-mode user
python3 telegram_connector/telegram_history_client.py sync --mode tail --channel @vcnews --limit 100 --download-media --auth-mode user
python3 telegram_connector/telegram_history_client.py sync --mode tail --channel @vcnews --limit 100 --mark-read --auth-mode user
python3 telegram_connector/telegram_history_client.py sync --mode tail --channel @vcnews --limit 100 --since 2026-03-15 --until 2026-03-16 --download-media --ocr --auth-mode user
python3 telegram_connector/telegram_history_client.py sync --mode tail --channel @vcnews --limit 100 --auth-mode bot
python3 telegram_connector/telegram_history_client.py sync --mode update --channel @vcnews --limit 100 --since 2026-03-15 --until 2026-03-16
python3 telegram_connector/telegram_history_client.py sync --mode update --channel @vcnews --limit 100 --mark-read --auth-mode user
python3 telegram_connector/telegram_history_client.py sync --mode update --channel @vcnews,@another_channel --limit 100
python3 telegram_connector/telegram_history_client.py sync --mode update --limit 100
```

Additional notes:

- `--limit 0` removes the per-channel message cap for `sync`; the run is then constrained only by `[sync].sync_limit`

- `--mode backfill` is the reliable choice for older date ranges that are no longer in the latest channel tail
- if you pass a private group or channel id in Bot API form like `-1001449711572`, `sync`, `digest`, and SQLite filtering normalize it automatically to the stored internal channel id
- `--mode tail` scans the latest window of messages
- `--mode update` stops at the boundary of already saved history and imports only newer messages
- `ocr` implies media download for image files
- `read` checks the current Telegram read boundary first and avoids redundant acknowledge calls
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

Examples:

```bash
python3 telegram_connector/telegram_history_client.py export-csv --channel @vcnews,@another_channel --limit 100
python3 telegram_connector/telegram_history_client.py export-csv --limit 100
python3 telegram_connector/telegram_history_client.py export-csv --channel @vcnews --since 2026-03-15 --until 2026-03-16
python3 telegram_connector/telegram_history_client.py export-csv --channel @vcnews --since 2026-03-15
```

Additional notes:

- `until` is optional
- when omitted, export goes through the newest saved message
- multi-channel export creates one CSV per channel
- CSV now also includes additional normalized message-analysis fields from `messages`, including `grouped_id`, `content_hash`, and `imported_at`

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

Examples:

```text
/ocrhistory @vcnews since=2026-03-15 until=2026-03-16
```

Additional notes:

- this command fetches Telegram history first
- then it downloads image media for that sync window
- then it runs OCR on those downloaded images

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

Examples:

```bash
python3 telegram_connector/telegram_history_client.py ocr-pending --channel @vcnews --since 2026-03-15 --until 2026-03-16 --limit 100
```

```text
/ocr @vcnews 100 since=2026-03-15 until=2026-03-16
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
- `data/exports/`: generated CSV exports

Local data directories:

- `telegram_connector/data/telegram_history.sqlite3`
- `telegram_connector/data/media/`
- `telegram_connector/data/sessions/`

## Dependencies

Required for history ingestion:

- `Telethon`
- `tesseract`

Python requirements file:

```bash
python3 -m pip install -r telegram_connector/requirements.txt
```

## Tests

Run the local regression suite before each code change:

```bash
python3 -m pytest telegram_connector/tests -q
```

Current tests cover:

- Telegram bot command parsing
- chat allowlist parsing
- response chunk splitting
- CSV export logic
- SQLite schema initialization
- per-channel sync state separation
- local runtime config loading
- public/private auth-mode routing
- default `user` auth in bot commands

Fallback runner without pytest-specific discovery:

```bash
python3 telegram_connector/run_tests.py
```
