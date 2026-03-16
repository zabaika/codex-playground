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

Follow the same local-config pattern as the `article-to-obsidian-kb` skill:

- commit `telegram_connector/config/runtime.example.toml`
- keep only local secret references in `telegram_connector/config/runtime.local.toml`
- never commit `runtime.local.toml`

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

### Bot bridge

Telegram bot commands are executed only while the local bridge process is running.
If `telegram_connector.py listen --run-commands` is not running, the bot can receive messages in Telegram but it will not execute history-client commands.

Bridge commands are accepted in these forms:

- `/update 10`
- `update 10`
- `/update@verter_the_bot 10`

Check connection:

```bash
python3 telegram_connector/telegram_connector.py get-me
```

Listen for inbound messages:

```bash
python3 telegram_connector/telegram_connector.py listen
```

Listen and allow Telegram commands to control the history client:

```bash
python3 telegram_connector/telegram_connector.py listen --run-commands
```

To run the listener as a macOS background service with autostart at login and reboot:

```bash
bash telegram_connector/scripts/install_launch_agent.sh
```

This installs a launchd agent with label `com.zabaika.telegram-connector-bridge` and deploys a runnable bundle into:

- `~/Library/Application Support/telegram_connector_service`

After code changes, rerun the installer script to refresh the deployed service bundle.

To restart the background service manually:

```bash
bash telegram_connector/scripts/restart_launch_agent.sh
```

Daemon logs are written into the project folder:

- `telegram_connector/data/launchd/bridge.startup.log`
- `telegram_connector/data/launchd/bridge.stdout.log`
- `telegram_connector/data/launchd/bridge.stderr.log`

Poll once and exit:

```bash
python3 telegram_connector/telegram_connector.py listen --once
```

Listen and echo messages back:

```bash
python3 telegram_connector/telegram_connector.py listen --echo
```

Send a message:

```bash
python3 telegram_connector/telegram_connector.py send --chat-id 123456789 "hello from Codex"
```

Or after setting `TELEGRAM_DEFAULT_CHAT_ID`:

```bash
python3 telegram_connector/telegram_connector.py send "hello from Codex"
```

Bot command bridge supports:

- `/help`
- `/doctor`
- `/state`
- `/backfill @channel [limit] [media] [bot|user|auto]`
- `/tail @channel [limit] [media|ocr] [bot|user|auto]`
- `/update @channel [limit] [media|ocr] [bot|user|auto]`
- `/ocrhistory @channel [limit] [bot|user|auto]`
- `/exportcsv @channel [limit] [bot|user|auto]`
- `/exportcsv @channel since=YYYY-MM-DD until=YYYY-MM-DD [bot|user|auto]`
- `/ocr [limit]`

Only `chat_id` values from `[bridge].allowed_chat_ids` may run these commands.

If auth is omitted in a bot command, `user` is used by default.
The leading `/` is optional for supported bridge commands.

Flag semantics:

- `media`: download media only
- `ocr`: download image media and run OCR
- `update`: fetch only messages newer than the latest saved one and skip already stored history
- commands that accept `@channel` also accept comma-separated lists like `@vcnews,@another_channel`

### History client

Check local setup:

```bash
python3 telegram_connector/telegram_history_client.py doctor
```

Initialize SQLite:

```bash
python3 telegram_connector/telegram_history_client.py init-db
```

Backfill channel history:

```bash
python3 telegram_connector/telegram_history_client.py backfill --channel @vcnews --limit 1000
```

Backfill multiple channels in one run:

```bash
python3 telegram_connector/telegram_history_client.py backfill --channel @vcnews,@another_channel --limit 1000
```

Fetch the latest 100 messages into the existing history:

```bash
python3 telegram_connector/telegram_history_client.py tail --channel @vcnews --limit 100
```

Tail with media download and OCR:

```bash
python3 telegram_connector/telegram_history_client.py tail --channel @vcnews --limit 100 --download-media --ocr --auth-mode user
```

Download media without OCR:

```bash
python3 telegram_connector/telegram_history_client.py tail --channel @vcnews --limit 100 --download-media --auth-mode user
```

Force a specific auth type:

```bash
python3 telegram_connector/telegram_history_client.py tail --channel @vcnews --limit 100 --auth-mode bot
python3 telegram_connector/telegram_history_client.py backfill --channel https://t.me/+invitehash --limit 100 --auth-mode user
```

Backfill and download media:

```bash
python3 telegram_connector/telegram_history_client.py backfill --channel @vcnews --limit 1000 --download-media
```

Update only with messages newer than the latest saved one:

```bash
python3 telegram_connector/telegram_history_client.py update --channel @vcnews --limit 100
```

Update multiple channels in one run:

```bash
python3 telegram_connector/telegram_history_client.py update --channel @vcnews,@another_channel --limit 100
```

Use the default channels from config:

```bash
python3 telegram_connector/telegram_history_client.py update --limit 100
```

Repeat sync runs skip already saved messages instead of importing duplicates:

- `tail` and `backfill` skip messages that are already present in SQLite
- `update` is optimized to stop at the boundary of already saved history and import only newer messages

Export CSV for the latest saved messages:

```bash
python3 telegram_connector/telegram_history_client.py export-csv --channel @vcnews --limit 100
```

Export CSV for multiple channels:

```bash
python3 telegram_connector/telegram_history_client.py export-csv --channel @vcnews,@another_channel --limit 100
```

Export CSV for the configured default channels:

```bash
python3 telegram_connector/telegram_history_client.py export-csv --limit 100
```

Export CSV for a period:

```bash
python3 telegram_connector/telegram_history_client.py export-csv --channel @vcnews --since 2026-03-15 --until 2026-03-16
```

`--until` is optional. If omitted, export goes through the newest saved message:

```bash
python3 telegram_connector/telegram_history_client.py export-csv --channel @vcnews --since 2026-03-15
```

Run OCR for downloaded images:

```bash
python3 telegram_connector/telegram_history_client.py ocr-pending --limit 100
```

Inspect saved sync checkpoints:

```bash
python3 telegram_connector/telegram_history_client.py inspect-state
```

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
