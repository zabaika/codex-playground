# Telegram Agent Bot

Standalone Telegram task agent focused on conversational work:

- `telegram_agent_bridge.py`: Bot API bridge for inbound/outbound Telegram messages
- `telegram_agent_worker.py`: OpenAI-powered agent worker with read-only local tools and public web tools

Project-wide implementation rules and reusable conventions are documented in `../RULEBOOK.md`.
Project-specific bot and maintenance rules live in [./AGENTS.md](./AGENTS.md).

## What It Does

- checks bot connectivity with `getMe`
- receives incoming messages with long polling via `getUpdates`
- stores redacted inbound updates in `data/inbox.jsonl`
- remembers the latest processed update offset in `data/offset.local.json`
- sends outbound messages with `sendMessage`
- runs a conversational task agent from Telegram
- lets the agent inspect allowed local paths and public web pages
- keeps one conversation thread per Telegram chat until reset

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
- `bridge.allowed_user_ids` and `bridge.allowed_usernames` add per-user protection on top of the chat allowlist
- `bridge.allowed_usernames` may be a comma-separated value or an `op://...` 1Password reference that resolves to a comma-separated list
- `bridge.default_command = "agent"` means plain text is treated as `/agent ...`; set it empty to require explicit commands
- `bridge.text_chunk_size` controls Telegram reply chunking
- `bridge.agent_stats_row_limit` limits `/agent-stats` to the latest N rows from `ai_usage_log`, so the command stays fast as the database grows
- `agent.allowed_roots` is the allowlist for local file access
- `agent.model` defaults to `gpt-5.4-mini` in the example config
- `agent.prompt_cache_scope = "global"` maximizes prompt-cache reuse for one-owner bots; use `"chat"` only if you want separate cache keys per Telegram chat
- the bridge resolves bot/OpenAI secrets once at startup and passes only the minimum required env vars to the worker, matching the neighbor project's secret-handling pattern
- the worker resolves secrets in this order:
  1. environment variable
  2. `op://...` reference
  3. plain local value from `runtime.local.toml`
- the worker logs each OpenAI request round into `data/telegram_agent.sqlite3` inside `ai_usage_log`, including `prompt_cache_key`, prompt hashes, cached input tokens, and prefix overlap with the previous request for the same cache key

### 1Password CLI

Suggested item layout in 1Password:

- vault: `Personal`
- item: `telegram-agent-bot`
- fields:
  - `bot_token`
  - `openai_api_key`
  - later, add extra fields only when the bot explicitly asks for a new secret

Then set these refs in `runtime.local.toml`:

```toml
[secrets]
bot_token = "op://Personal/telegram-agent-bot/bot_token"
openai_api_key = "op://Personal/telegram-agent-bot/openai_api_key"
```

Before running the scripts:

- install 1Password CLI and authenticate `op`
- verify a ref manually: `op read op://Personal/telegram-agent-bot/bot_token`

## Usage

### Runtime

Telegram bot commands are executed only while the local bridge process is running.
If `telegram_agent_bridge.py listen --run-commands` is not running, the bot can receive messages in Telegram but it will not execute the agent worker.

Bridge commands are accepted in these forms:

- `/agent найди обработку OCR`
- `найди обработку OCR`
- `agent найди обработку OCR`
- `/agent@vasiliy_the_best_bot найди обработку OCR`
- `/reset`
- `reset`

Only `chat_id` values from `[bridge].allowed_chat_ids` and the configured Telegram user allowlist may run commands.
The leading `/` is optional for supported commands.

Bot command quick reference:

- `/help`
  show command help
- `/agent <task>`
  run the task agent and answer back into Telegram
- `/agent-stats`
  show local OpenAI usage and prompt-cache summary from `ai_usage_log`
- `/reset`
  clear saved conversation context for the current chat

Bot command notes:

- regular non-command chat text does not trigger execution
- if `bridge.default_command` is configured, plain text is automatically routed to that command
- the default agent instructions force Russian answers and require asking before file creation or access expansion
- `/agent` keeps one OpenAI response thread per Telegram chat in `data/agent_sessions.local.json`
- `/agent` may inspect only `agent.allowed_roots`
- `/agent` uses public web search/fetch plus local read-only tools, then synthesizes the answer with the configured OpenAI model
- `/agent-stats` is handled locally by the bridge and does not spend OpenAI tokens
- `/agent-stats` reports over a bounded recent window, not over the whole table
- the bridge sends Telegram replies with explicit `HTML` parse mode and post-processes text for stable readability

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

Daemon logs:

- `telegram_agent_bot/data/launchd/bridge.startup.log`
- `telegram_agent_bot/data/launchd/bridge.stdout.log`
- `telegram_agent_bot/data/launchd/bridge.stderr.log`

Example prompts:

```text
/agent найди в проекте обработку OCR и коротко объясни архитектуру
/agent проверь последние новости OpenAI за сегодня и дай 5 пунктов с ссылками
/reset
```
