# Codex Playground

Workspace for local experiments, tools, and reusable operating conventions.

## Projects

- [telegram_connector](./telegram_connector/README.md)  
  Local Telegram toolkit with a bot bridge, channel history ingestion via Telethon, SQLite storage, OCR support, CSV export, and a launchd-backed daemon flow.

- [telegram_agent_bot](./telegram_agent_bot/README.md)  
  Standalone Telegram task agent with its own bridge, OpenAI-backed worker, local read-only tools, public web search/fetch, and separate daemon flow.

- [telegram_shared](./telegram_shared/)  
  Shared infrastructure primitives reused by the Telegram projects: config loading, secret resolution, bridge env helpers, Bot API helpers, formatting, redaction, and OpenAI usage/stats utilities.

- [skills](./skills/README.md)  
  Local Codex skill collection for workspace-specific workflows, including Obsidian knowledge-base generation from engineering articles.

- [tools/kb-index](./tools/kb-index/README.md)  
  Local retrieval and indexing tool for the Obsidian knowledge base, built around SQLite/FTS note search and launchd-backed scheduled refresh.

- [plugins](./plugins/README.md)  
  Local Codex plugin sources that package reusable skills and, when needed, broader Codex integrations.

## Shared Docs

- [RULEBOOK.md](./RULEBOOK.md)  
  Cross-project rules for secrets, runtime safety, logging, daemon behavior, database conventions, and repository hygiene.

## Notes

- Some top-level folders such as `scratch/` and `.codex-tmp/` are workspace support areas rather than standalone projects.
- Add new project links here when a top-level folder gains its own `README.md`.
