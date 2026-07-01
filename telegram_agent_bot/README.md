# Telegram Agent Bot

Standalone Telegram task agent with a Bot API bridge and an OpenAI-backed worker for local read-only tools and public web tools.

## Sources Of Truth

- [../RULEBOOK.md](../RULEBOOK.md): repository-wide runtime, config, install, and service rules
- [./AGENTS.md](./AGENTS.md): project-specific bot and maintenance guidance
- [config/runtime.example.toml](./config/runtime.example.toml): canonical runtime config shape, defaults, comments, and clamped ranges
- [../telegram_shared/README.md](../telegram_shared/README.md): shared Telegram helper boundaries and retry semantics
- `--help` on `telegram_agent_bridge.py` and `telegram_agent_worker.py`: exact CLI syntax

## What It Does

- receives Telegram bot messages with long polling
- stores redacted inbound updates in `data/inbox.jsonl`
- stores redacted outbound reply summaries in `data/outbox.jsonl`
- remembers the latest processed update offset in `data/offset.local.json`
- runs `/agent` tasks through the OpenAI worker
- lets the worker inspect only configured local roots and public web pages
- keeps one conversation thread per Telegram chat until `/reset`
- exposes `/agent-stats` from local usage logs without spending OpenAI tokens

## Runtime Config

Create `config/runtime.local.toml` from [config/runtime.example.toml](./config/runtime.example.toml). Keep local secrets and machine-specific paths out of git.

The README intentionally does not restate every config key. Use `runtime.example.toml` for the complete schema, comments, and clamped ranges. The operator-facing settings most often changed are:

- `bridge.allowed_chat_ids`, `bridge.allowed_user_ids`, and `bridge.allowed_usernames` for command access control
- `bridge.default_command` for routing plain text to `/agent`
- `bridge.worker_process_timeout_seconds` for one bridge-launched worker subprocess
- `bridge.send_message_retry_attempts` and `bridge.send_message_retry_backoff_seconds` for transient Telegram `sendMessage` failures, including network errors, timeouts, and Telegram HTTP 5xx
- `agent.model`, `agent.openai_timeout_seconds`, and `agent.max_tool_rounds` for OpenAI worker behavior
- `agent.allowed_roots` for local file access
- `agent.max_local_matches`, `agent.max_file_lines`, `agent.max_directory_entries`, and `agent.max_tool_output_chars` for tool-output guardrails
- `agent.prompt_cache_scope` for global or per-chat prompt-cache keys

`telegram.default_chat_id` can stay empty until you send one message to the bot and discover the chat id through `listen`.

### Secrets

Prefer Keychain references in `runtime.local.toml`, for example:

```toml
[secrets]
bot_token = "keychain://telegram-agent-bot/bot_token"
openai_api_key = "keychain://telegram-agent-bot/openai_api_key"
```

Useful Keychain commands:

```bash
security add-generic-password -U -s telegram-agent-bot -a bot_token -w '<token>'
security find-generic-password -s telegram-agent-bot -a bot_token -w
```

## Operation

Start the bridge manually:

```bash
python3 telegram_agent_bot/telegram_agent_bridge.py listen --run-commands
```

Install or refresh the macOS LaunchAgent:

```bash
bash telegram_agent_bot/scripts/install_launch_agent.sh
```

Reload only the already-installed service:

```bash
bash telegram_agent_bot/scripts/restart_launch_agent.sh
```

Use the installer after code changes, `telegram_shared` changes, runtime config changes, or prompt changes. Use restart only when installed code and config are already current.

Launchd logs:

- `telegram_agent_bot/data/launchd/bridge.startup.log`
- `telegram_agent_bot/data/launchd/bridge.stdout.log`
- `telegram_agent_bot/data/launchd/bridge.stderr.log`

## Commands

Bot command quick reference:

- `/help`: show command help
- `/agent <task>`: run the task agent and answer back into Telegram
- `/agent-stats`: show local OpenAI usage and prompt-cache summary
- `/reset`: clear saved conversation context for the current chat

Command notes:

- commands are accepted with or without the leading `/`
- plain non-command text triggers `/agent` only when `bridge.default_command = "agent"`
- bot commands require an allowed chat and a configured Telegram user allowlist
- `/agent` may inspect only `agent.allowed_roots`
- outbound Telegram replies are written to `data/outbox.jsonl` as redacted summaries

Example prompts:

```text
/agent find the OCR handling in this project and briefly explain the architecture
/agent check the latest OpenAI news today and give me 5 bullet points with links
/reset
```

## Storage

Primary local runtime paths:

- `telegram_agent_bot/data/telegram_agent.sqlite3`
- `telegram_agent_bot/data/agent_sessions.local.json`
- `telegram_agent_bot/data/inbox.jsonl`
- `telegram_agent_bot/data/outbox.jsonl`
- `telegram_agent_bot/data/offset.local.json`
- `telegram_agent_bot/data/launchd/`

## Tests

Preferred local regression run:

```bash
.venv-test-gap-detection/bin/python -m pytest telegram_agent_bot/tests -q
```

When changing `telegram_shared`, run the shared suite as well:

```bash
.venv-test-gap-detection/bin/python -m pytest telegram_shared/tests telegram_agent_bot/tests -q
```
