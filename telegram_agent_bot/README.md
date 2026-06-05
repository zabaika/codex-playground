# Telegram Agent Bot

Standalone Telegram task agent focused on conversational work:

- `telegram_agent_bridge.py`: Bot API bridge for inbound/outbound Telegram messages
- `telegram_agent_worker.py`: OpenAI-powered agent worker with read-only local tools and public web tools

Project-wide implementation rules and reusable conventions are documented in `../RULEBOOK.md`.
Project-specific bot and maintenance rules live in [./AGENTS.md](./AGENTS.md).

## What it does

- checks bot connectivity with `getMe`
- receives incoming messages with long polling via `getUpdates`
- stores redacted inbound updates in `data/inbox.jsonl`
- stores redacted outbound reply summaries in `data/outbox.jsonl`
- remembers the latest processed update offset in `data/offset.local.json`
- sends outbound messages with `sendMessage`
- runs a conversational task agent from Telegram
- lets the agent inspect allowed local paths and public web pages
- keeps one conversation thread per Telegram chat until reset

## Config

Use [../RULEBOOK.md](../RULEBOOK.md) for repository-wide config, secret, path, and logging conventions. This section documents only `telegram_agent_bot`-specific runtime settings and operator-facing behavior.

Main config groups:

- `[telegram]`, `[bridge]`: Telegram delivery, access control, default command, and reply chunking
- `[agent]`: OpenAI model and tool/output guardrails
- `[agent_prompts]`: worker system instructions
- `[secrets]`: secret references or local secret values

Config shape:

```toml
[telegram]
default_chat_id = "<chat id or empty>"

[bridge]
allowed_chat_ids = "<comma-separated chat ids or empty>"
allowed_user_ids = "<comma-separated Telegram user ids or empty>"
allowed_usernames = "<comma-separated Telegram usernames or empty>"
default_command = "<empty|agent|reset>"
text_chunk_size = "<500..4096>"
agent_stats_row_limit = "<20..2000>"
# worker_path = "/absolute/path/to/telegram_agent_worker.py"

[agent]
model = "<OpenAI model>"
max_tool_rounds = "<maximum tool rounds in one run>"
web_search_limit = "<maximum web hits returned by one search tool call>"
fetch_char_limit = "<maximum fetched page chars kept in one tool call>"
max_local_matches = "<maximum local search hits returned by one search_local_files call>"
max_file_lines = "<maximum numbered lines returned by one read_local_file call>"
max_directory_entries = "<maximum files/folders returned by one list_local_files call>"
max_tool_output_chars = "<maximum chars sent back to OpenAI for one tool result>"
prompt_cache_scope = "<global|chat>"
allowed_roots = [
  ".",
]

[agent_prompts]
system_instructions = """
<task-agent system instructions>
"""

[secrets]
bot_token = "<secret reference or local secret>"
openai_api_key = "<secret reference or local secret>"
```

`default_chat_id` can stay empty until you send at least one message to the bot and discover your chat id through `listen`.

Notes:

- `bridge.allowed_chat_ids` limits who may run `/agent` and `/reset`
- `bridge.allowed_user_ids` and `bridge.allowed_usernames` add per-user protection on top of the chat allowlist; if both are empty, bridge commands are denied
- `bridge.allowed_usernames` may be a comma-separated value or a `keychain://...` reference that resolves to a comma-separated list
- `bridge.default_command = "agent"` means plain text is treated as `/agent ...`; set it empty to require explicit commands
- `bridge.text_chunk_size` controls Telegram reply chunking
- `bridge.agent_stats_row_limit` limits `/agent-stats` to a recent window from `ai_usage_log`
- `agent.allowed_roots` is the allowlist for local file access
- `agent.model` defaults to `gpt-5.4-mini` in the example config
- `agent.max_local_matches`, `agent.max_file_lines`, and `agent.max_directory_entries` control how much local context one tool call may return
- `agent.max_tool_output_chars` is the main payload guardrail for `function_call_output`; keep it conservative to avoid oversized OpenAI requests
- `agent.prompt_cache_scope = "global"` maximizes prompt-cache reuse for one-owner bots; use `"chat"` only if you want separate cache keys per Telegram chat
- the bridge resolves bot/OpenAI secrets once at startup and passes only the minimum required env vars to the worker
- the worker resolves secrets in this order: environment variable, `keychain://...`, `op://...`, plain local value
- the worker logs each OpenAI request round into `data/telegram_agent.sqlite3` inside `ai_usage_log`

### macOS Keychain

Suggested generic-password layout in Keychain:

- service: `telegram-agent-bot`
- accounts:
  - store each secret under its own account name, for example `bot_token`

Then set these refs in `runtime.local.toml`:

```toml
[secrets]
bot_token = "keychain://telegram-agent-bot/bot_token"
openai_api_key = "keychain://telegram-agent-bot/openai_api_key"
```

Before running the scripts:

- store a secret once:
  `security add-generic-password -U -s telegram-agent-bot -a bot_token -w '<token>'`
- verify a ref manually:
  `security find-generic-password -s telegram-agent-bot -a bot_token -w`

## Usage

### Runtime

Telegram bot commands are executed only while the local bridge process is running.
If `telegram_agent_bridge.py listen --run-commands` is not running, the bot can receive messages in Telegram but it will not execute the agent worker.

Bridge commands are accepted with or without the leading `/`.
Only `chat_id` values from `[bridge].allowed_chat_ids` and the configured Telegram user allowlist may run commands.

Bot command quick reference:

- `/help`: show command help
- `/agent <task>`: run the task agent and answer back into Telegram
- `/agent-stats`: show local OpenAI usage and prompt-cache summary from `ai_usage_log`
- `/reset`: clear saved conversation context for the current chat

Start the bridge manually:

```bash
python3 telegram_agent_bot/telegram_agent_bridge.py listen --run-commands
```

Install the macOS background service:

```bash
bash telegram_agent_bot/scripts/install_launch_agent.sh
```

Restart the background service:

```bash
bash telegram_agent_bot/scripts/restart_launch_agent.sh
```

Service update rule:

- rerun `install_launch_agent.sh` after code, config, or prompt changes
- use `restart_launch_agent.sh` only when the installed code and config are already up to date

Daemon logs:

- `telegram_agent_bot/data/launchd/bridge.startup.log`
- `telegram_agent_bot/data/launchd/bridge.stdout.log`
- `telegram_agent_bot/data/launchd/bridge.stderr.log`

Shared command rules:

- regular non-command chat text does not trigger execution unless `bridge.default_command` routes it automatically
- `/agent` keeps one OpenAI response thread per Telegram chat in `data/agent_sessions.local.json`
- `/agent` may inspect only `agent.allowed_roots`
- `/agent-stats` is handled locally by the bridge and does not spend OpenAI tokens
- outbound Telegram replies are written to `data/outbox.jsonl` as redacted summaries

Example prompts:

```text
/agent find the OCR handling in this project and briefly explain the architecture
/agent check the latest OpenAI news today and give me 5 bullet points with links
/reset
```
