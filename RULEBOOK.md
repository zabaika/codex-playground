# Telegram Integration Rulebook

This rulebook captures the operational and security conventions used in this project so they can be reused when building other Telegram-based tools.

## Purpose

Use this document as a default template for local Telegram automations, bots, bridge daemons, and history-ingestion tools.

The goal is to standardize:

- secure handling of secrets
- project-local runtime data and logs
- safe bot command execution
- reproducible local service setup
- test expectations before changes ship

## 1. Architecture Rules

Use a split architecture instead of a single monolith:

- `bot bridge`: receives Telegram commands and sends responses
- `history/data client`: performs actual business operations
- `runtime config`: local-only machine config
- `project data`: SQLite, offsets, exports, media, logs
- `service wrapper`: background daemon runner

Recommended pattern:

- keep Telegram Bot API logic in one script
- keep Telethon or other heavy data logic in a separate script
- let the bridge invoke the data client as a subprocess
- keep command syntax stable between CLI and bot usage

## 2. Config Rules

Tracked config:

- commit `config/runtime.example.toml`
- keep only examples and non-sensitive defaults there

Local config:

- use `config/runtime.local.toml`
- do not commit it
- keep all machine-specific settings there

Rules:

- command-line explicit values override config
- if a command omits a channel, use configured defaults
- if a command includes a channel or channel list, ignore config defaults

Recommended default channels format:

```toml
[channels]
default_list = [
  "@vcnews, vc.ru",
  "@another_channel, Another Channel",
]
```

Interpretation:

- store entries as `"channel, display name"`
- use only the channel reference in code unless display name is explicitly needed

## 3. Secrets Rules

Do not commit secrets in tracked files.

Preferred storage:

- 1Password CLI references via `op://...`

Recommended fields in 1Password:

- `api_id`
- `api_hash`
- `bot_token`
- `phone`
- `user_password`

Preferred resolution order:

1. environment variable
2. `op://...` reference
3. local plaintext fallback in `runtime.local.toml`

Rules:

- never print secret values to stdout/stderr
- never send secret values back to Telegram
- never include secrets in tests
- never include secrets in committed examples
- if a secret was pasted into chat or logs, rotate it

## 4. Path and Filesystem Rules

Do not hardcode machine-specific paths in committed code.

Rules:

- do not commit absolute local paths containing home directories, usernames, workstation names, or other machine-specific identifiers anywhere in the repository
- derive project root from script location or env
- support a project-root override via environment variable
- keep runtime data inside the project unless there is a strong reason not to
- if a daemon deploys code elsewhere, still point config and data back to the project root

Recommended env override:

- `TELEGRAM_CONNECTOR_PROJECT_ROOT`

Use project-local directories:

- `data/telegram_history.sqlite3`
- `data/media/`
- `data/exports/`
- `data/sessions/`
- `data/launchd/`
- `data/inbox.jsonl`
- `data/offset.local.json`

Git rule:

- ignore the whole `data/` directory

## 5. Logging Rules

Logs must stay inside the project, not inside opaque service folders.

Recommended logs:

- `data/launchd/bridge.startup.log`
- `data/launchd/bridge.stdout.log`
- `data/launchd/bridge.stderr.log`

Rules:

- logs must not contain secrets
- logs must not contain absolute private file paths when avoidable
- logs must not contain raw Telegram updates in full
- log command metadata, not full sensitive payloads

For inbox/update storage:

- store only a redacted event summary
- keep `chat_id`, `command`, timestamps, and text length if needed
- avoid storing full message text unless there is a deliberate product need

## 6. Bot Command Rules

The bot bridge should accept:

- `/command ...`
- `command ...`
- `/command@botname ...`

Rules:

- only recognized commands should be normalized from bare text
- regular non-command chat text must not trigger execution
- only allow command execution from whitelisted chats

Recommended commands:

- `help`
- `doctor`
- `state`
- `backfill`
- `tail`
- `update`
- `ocrhistory`
- `exportcsv`
- `ocr`

If command execution is disabled:

- the bot may still receive messages
- but it must not try to run the history client

## 7. Authorization Rules

Support separate auth modes:

- `bot`
- `user`
- `auto`

Rules:

- default auth mode should be explicit in config
- if omitted in bot commands, use the configured default
- for historical channel reads, prefer `user`
- for public-channel service operations, `bot` may still be useful

Remember:

- Bot API and bot-auth are not enough for full history access
- full historical reads of Telegram channels usually require user auth

## 8. Data Ingestion Rules

For message sync:

- `backfill`: load historical data
- `tail`: inspect the latest window of messages
- `update`: fetch only new messages since the latest stored one

Rules:

- never duplicate already stored messages
- when syncing, skip existing messages by primary key
- for `update`, stop at the boundary of already-known history
- keep sync state per channel
- support multiple channels in one run

Recommended database keys:

- messages: `(channel_id, message_id)`
- media assets: `(channel_id, message_id, ordinal)`
- sync state: one row per channel

## 9. Media and OCR Rules

Default behavior:

- do not download media unless explicitly requested
- do not run OCR unless explicitly requested

Semantics:

- `media` = download media only
- `ocr` = download image media and run OCR

Rules:

- OCR should operate only on image media
- if OCR is requested for already-stored messages without local files, allow media refresh without duplicating the message row
- store OCR text separately from the original message text
- store OCR failure state in a sanitized form

## 10. Export Rules

For CSV exports:

- use `;` as the delimiter
- support export by latest `N` messages
- support export by `since`
- make `until` optional

Rules:

- if `since` is set and `until` is omitted, export through the newest stored message
- for multi-channel export, create one CSV per channel
- if export is triggered through the bot, send all generated CSV files back to Telegram

Avoid exporting:

- absolute local file paths
- secrets
- internal debug paths

## 11. Security Hardening Rules

Never send raw subprocess `stdout/stderr` directly to Telegram.

Rules:

- build Telegram bot replies from a whitelist of safe fields
- redact file paths where possible
- redact bot tokens and similar credentials in error output
- sanitize OCR errors before storing or returning them
- sanitize Telegram API errors before returning them to chat

Database safety:

- use parameterized SQL only
- do not interpolate user-provided strings into SQL

Stored data minimization:

- store minimized metadata instead of full raw Telegram payloads when possible
- avoid storing full raw update bodies in logs
- store only what is operationally needed

## 12. Daemon and Service Rules

Use a system-managed background process for reliable command handling.

For macOS:

- use `launchd`
- install via a project script
- restart via a project script

Rules:

- committed plist/templates must not contain machine-specific paths
- templates may contain placeholders
- installer may render runtime-specific paths locally
- service bundle may live outside the repo
- but config and data should still point back to the project

After code changes:

- reinstall or redeploy the service bundle
- then restart or bootstrap the daemon

## 13. Test Rules

Before shipping changes, run automated tests.

Minimum expectations:

- command parser tests
- config parsing tests
- secret resolution tests
- security redaction tests
- CSV export tests
- OCR-related logic tests
- launchd installer/restart script tests
- multi-channel behavior tests

Rules:

- every new command nuance should have a parser test
- every new config nuance should have a config test
- every security hardening rule should have at least one regression test

## 14. Documentation Rules

README must explicitly document:

- how to configure secrets
- how to run the listener
- that bot commands require the listener daemon
- how to install and restart the daemon
- where logs live
- whether `/command`, `command`, and `/command@botname` are supported
- the default channels config format
- the difference between `media` and `ocr`

Documentation safety rules:

- do not use absolute local filesystem paths in committed docs
- do not mention home directory names, machine-specific usernames, or workstation-specific paths in committed docs
- prefer relative paths, generic placeholders, or env variable names in documentation examples

Repository-wide safety rule:

- the same no-machine-specific-path rule applies to code, configs, templates, scripts, tests, examples, and documentation

## 15. Git Hygiene Rules

Do not commit:

- `runtime.local.toml`
- `data/`
- session files
- SQLite databases
- exported CSVs
- downloaded media
- temporary launchd output

Before pushing:

- search for machine-specific paths
- search for usernames or home directories
- search for tokens or passwords
- ensure service templates are generic

## 16. Operational Checklist

When creating a new Telegram-based program, verify:

1. secrets are outside tracked files
2. logs are sanitized
3. bot replies are sanitized
4. SQL is parameterized
5. runtime data stays in project-local `data/`
6. daemon install/restart scripts exist
7. README explains real startup and runtime behavior
8. tests cover parser, config, security, and export behavior
9. explicit command args override config defaults
10. daemon has been redeployed after code changes

## 17. Reuse Guidance

If you bootstrap a new Telegram project from this one, copy these ideas first:

- local `runtime.example.toml` + ignored `runtime.local.toml`
- 1Password secret resolution
- project-root env override
- sanitized bridge responses
- redacted inbox logging
- launchd installer and restart scripts
- whole-`data/` gitignore rule
- parser tests for every user-facing command nuance

This rulebook is intended to be stricter than convenience defaults. If a future project needs to relax a rule, document why.
