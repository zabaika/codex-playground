# Telegram Connector

Telegram toolkit with two local clients:

- `telegram_connector.py`: minimal Bot API bridge for inbound/outbound bot messages
- `telegram_history_client.py`: channel history ingester based on Telethon + SQLite + Tesseract

Project-wide implementation rules and reusable conventions are documented in `../RULEBOOK.md`.

## What it does

- checks bot connectivity with `getMe`
- receives incoming messages with long polling via `getUpdates`
- stores raw inbound updates in `data/inbox.jsonl`
- remembers the latest processed update offset in `data/offset.local.json`
- sends outbound messages with `sendMessage`
- ingests public and private channel history through a Telegram user session
- stores normalized channel/message/media rows in SQLite
- downloads channel media locally
- runs OCR for downloaded images with Tesseract
- exports saved channel history to CSV

## Config

Use [../RULEBOOK.md](../RULEBOOK.md) as the source of truth for repository-wide conventions:

- local-vs-committed config layout
- secret handling
- runtime file placement
- logging and storage safety rules

Config shape:

```toml
[telegram]
default_chat_id = ""

[telethon]
user_session_name = "telegram_history_user"
bot_session_name = "telegram_history_bot"
api_id = ""
phone = "op://Personal/telegram-connector/phone"

[auth]
default_mode = "user"
public_channel_mode = "bot"
private_channel_mode = "user"

[channels]
default_list = [
  "@vcnews, vc.ru",
]

[paths]
# history_db = "/absolute/path/to/telegram_history.sqlite3"
# media_root = "/absolute/path/to/telegram_media"
# tesseract_binary = "/opt/homebrew/bin/tesseract"

[secrets]
bot_token = "op://Personal/telegram-connector/bot_token"
api_hash = "op://Personal/telegram-connector/api_hash"
user_password = "op://Personal/telegram-connector/user_password"
```

`default_chat_id` can stay empty until you send at least one message to the bot and discover your chat id through `listen`.

For the history client:

- `telethon.api_id` and `secrets.api_hash` come from [my.telegram.org](https://my.telegram.org)
- `telethon.api_id`, `telethon.phone` and `secrets.*` can be plain local values, but the preferred mode is a 1Password CLI reference like `op://Personal/telegram-connector/bot_token`
- `telethon.phone` is the phone number of the Telegram user account that will read channel history; it can also be a 1Password reference
- `secrets.user_password` is optional and only needed when Telegram account 2FA is enabled
- for private channels, that user account must already be a member of the channel
- `telethon.user_session_name` and `telethon.bot_session_name` keep separate Telethon sessions
- `auth.default_mode = "user"` makes user-auth the default when a command does not specify auth explicitly
- you can still force `bot` or `auto` per command when needed
- `channels.default_list` is optional and stores default channels in the format `"channel, display name"`
- if a command does not include `--channel` or `/... @channel`, the history client uses `channels.default_list`
- if a command explicitly includes one channel or a comma-separated channel list, that explicit value overrides the config list

### 1Password CLI

The project can resolve secret references through 1Password CLI with `op read`.

Suggested item layout in 1Password:

- vault: `Personal`
- item: `telegram-connector`
- fields:
  - `api_id`
  - `bot_token`
  - `api_hash`
  - `phone`
  - `user_password`

Then set these refs in `runtime.local.toml`:

```toml
[telethon]
api_id = "op://Personal/telegram-connector/api_id"
phone = "op://Personal/telegram-connector/phone"

[secrets]
bot_token = "op://Personal/telegram-connector/bot_token"
api_hash = "op://Personal/telegram-connector/api_hash"
user_password = "op://Personal/telegram-connector/user_password"
```

Before running the scripts:

- install 1Password CLI and authenticate `op`
- verify a ref manually: `op read op://Personal/telegram-connector/bot_token`

Secrets are resolved in this order:

- environment variable, if set
- `op://...` 1Password reference
- plain local value from `runtime.local.toml`

## Usage

### Runtime

Telegram bot commands are executed only while the local bridge process is running.
If `telegram_connector.py listen --run-commands` is not running, the bot can receive messages in Telegram but it will not execute history-client commands.

Bridge commands are accepted in these forms:

- `/update 10`
- `update 10`
- `/update@verter_the_bot 10`

Only `chat_id` values from `[bridge].allowed_chat_ids` may run bot-triggered commands.
If auth is omitted in a bot command, `user` is used by default.
The leading `/` is optional for supported bridge commands.

Bot command quick reference:

- `/backfill [channel] [limit] [since=...] [until=...] [media] [bot|user|auto]`
  historical load into SQLite
- `/tail [channel] [limit] [since=...] [until=...] [media|ocr|read] [bot|user|auto]`
  latest window sync
- `/update [channel] [limit] [since=...] [until=...] [media|ocr|read] [bot|user|auto]`
  only messages newer than saved history
- `/ocrhistory [channel] [limit] [since=...] [until=...] [bot|user|auto]`
  tail + media download + OCR
- `/exportcsv [channel] [limit|since=... until=...] [bot|user|auto]`
  export saved history to CSV
- `/ocr [limit] [channel] [since=...] [until=...]`
  OCR only for already-downloaded pending images

Bot command notes:

- `channel` may be omitted to use default channels from config
- `channel` may be a comma-separated list
- auth defaults to `user`
- `since` means start of UTC day, `until` means end of UTC day for date-only values

Start the bridge manually:

```bash
python3 telegram_connector/telegram_connector.py listen --run-commands
```

Install the macOS background service:

```bash
bash telegram_connector/scripts/install_launch_agent.sh
```

Restart the background service:

```bash
bash telegram_connector/scripts/restart_launch_agent.sh
```

Daemon logs:

- `telegram_connector/data/launchd/bridge.startup.log`
- `telegram_connector/data/launchd/bridge.stdout.log`
- `telegram_connector/data/launchd/bridge.stderr.log`

### Shared command rules

- channels can be passed explicitly or taken from `[channels].default_list`
- commands that accept channels also accept comma-separated lists like `@vcnews,@another_channel`
- `since` and `until` work for all sync, OCR, and export commands
- `since=2026-03-15` means `2026-03-15T00:00:00+00:00`
- `until=2026-03-16` means `2026-03-16T23:59:59+00:00`
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

- useful after changing config, 1Password refs, Telethon, or Tesseract setup

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

#### `backfill`

Description:
Read historical channel messages into SQLite.

CLI:

```bash
python3 telegram_connector/telegram_history_client.py backfill --channel @vcnews --limit 1000
```

Bot:

```text
/backfill @vcnews 1000
```

Examples:

```bash
python3 telegram_connector/telegram_history_client.py backfill --channel @vcnews --since 2026-03-15 --until 2026-03-16
python3 telegram_connector/telegram_history_client.py backfill --channel @vcnews,@another_channel --limit 1000
python3 telegram_connector/telegram_history_client.py backfill --channel @vcnews --limit 1000 --download-media
python3 telegram_connector/telegram_history_client.py backfill --channel https://t.me/+invitehash --limit 100 --auth-mode user
```

Additional notes:

- skips already saved messages instead of duplicating them
- `--download-media` downloads files but does not run OCR by itself

#### `sync`

Description:
Unified incremental sync command for `tail` and `update` modes.

CLI:

```bash
python3 telegram_connector/telegram_history_client.py sync --mode tail --channel @vcnews --limit 100
```

Bot aliases:

```text
/tail @vcnews 100
/update @vcnews 100
```

Examples:

```bash
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
