# AGENTS

This file is the project-specific guide for coding agents working in `telegram_agent_bot`.

For repository-wide engineering and safety conventions, see:
- [../RULEBOOK.md](../RULEBOOK.md)

## Purpose

`telegram_agent_bot` is a standalone Telegram project for one conversational task bot.
It must stay operationally separate from `telegram_connector`.

## Architecture

- Keep a split architecture:
  - `telegram_agent_bridge.py`: Telegram Bot API long-polling bridge
  - `telegram_agent_worker.py`: agent worker invoked as a subprocess
- Do not merge this project back into `telegram_connector`.
- Reuse patterns from neighboring projects only when they do not create shared runtime state or shared daemon flows.

## Behavior Rules

These are strict product rules for the bot and must stay aligned with code, config examples, and tests:

- Reply in Russian by default.
- Use English only for terminology, API names, product names, or other proper nouns when needed.
- If additional folder access, credentials, tokens, or permissions are needed, ask in Telegram first.
- When new secrets are needed, prefer 1Password references and follow the same secret-resolution order as `telegram_connector`.
- If file creation is needed, ask where to place files and propose sensible candidate paths.
- If functionality is missing, first look for existing installable skills or ready integrations before inventing a new custom skill from scratch.
- Accept commands only from the configured owner user.
- Default plain-text execution must be explicit and config-driven via `bridge.default_command`.
- If `bridge.default_command` is enabled, keep it limited to safe conversational commands such as `agent`.

## Config Rules

- Keep committed examples in `config/runtime.example.toml`.
- Keep machine-specific values only in `config/runtime.local.toml`.
- Do not commit machine-specific absolute paths.
- `agent.allowed_roots` belongs in local config when it points outside the project.
- Owner restrictions belong in bridge config:
  - `allowed_chat_ids`
  - `allowed_user_ids`
  - `allowed_usernames`

## Security Rules

- Resolve bridge secrets once during daemon startup when feasible.
- Pass only the minimum required secret env vars to the worker subprocess.
- Never print secrets into logs, Telegram replies, or test fixtures.
- Keep runtime data inside `data/`.
- Store only redacted update summaries in `data/inbox.jsonl`.

Daemon checklist before finishing changes:

- `cmd_listen` or the main daemon entrypoint must build one startup runtime bundle before entering the update loop.
- Secret resolution must not happen inside per-update handlers once the daemon is running.
- Worker subprocesses must receive only an allowlisted secret env subset, not the full parent environment.
- Tests should cover startup secret resolution and should fail if `op read` is triggered again for every handled command.
- Any standalone worker secret resolution must stay separate from the long-running bridge path and must not be used as an excuse to skip daemon startup caching.

## Telegram Formatting Rules

- Use one Telegram formatting mode consistently: HTML.
- Set `parse_mode` explicitly in bridge replies instead of relying on Telegram plain text defaults.
- Escape or sanitize user-generated and model-generated text before sending it with HTML formatting.
- Keep Telegram-specific presentation cleanup in bridge post-processing code where possible.
- Prefer simple stable formatting:
  - short sections
  - flat bullets
  - no tables
  - no reliance on prompts alone for message readability

## Testing

Preferred test command:

```bash
python3 -m pytest telegram_agent_bot/tests -q
```

Run targeted tests while iterating, then rerun the full `telegram_agent_bot/tests` suite before finishing changes.
