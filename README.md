# Codex Playground

Workspace for local Codex-oriented projects, tools, plugins, skills, and shared operating conventions.

## Projects

- [common](./common/README.md)  
  Repository-wide shared runtime helpers and config bundles used across multiple local tools and services, currently including the shared SQLite access layer and the shared hard-TTL runner for one-shot non-daemon jobs.

- [telegram_connector](./telegram_connector/README.md)  
  Local Telegram toolkit with a bot bridge, channel history ingestion via Telethon, SQLite storage, OCR support, CSV export, and a launchd-backed daemon flow.

- [telegram_agent_bot](./telegram_agent_bot/README.md)  
  Standalone Telegram task agent with its own bridge, OpenAI-backed worker, local read-only tools, public web search/fetch, and separate daemon flow.

- [telegram_shared](./telegram_shared/)  
  Shared infrastructure primitives reused by the Telegram projects: config loading, secret resolution, bridge env helpers, Bot API helpers, formatting, redaction, and OpenAI usage/stats utilities.

- [skills](./skills/README.md)  
  Local Codex skill collection for workspace-specific workflows, including Obsidian knowledge-base generation, YouTube transcript-to-notes conversion, local transcript extraction, and multi-agent decision review via `llm-council`.

- [tools/kb-index](./tools/kb-index/README.md)  
  Local retrieval and indexing tool for the Obsidian knowledge base, built around SQLite/FTS note search and launchd-backed scheduled refresh.

- [plugins](./plugins/README.md)  
  Local Codex plugin sources that package reusable skills and, when needed, broader Codex integrations such as `codex-token-monitor`.

## Shared Docs

- [RULEBOOK.md](./RULEBOOK.md)  
  Cross-project rules for secrets, runtime safety, logging, daemon behavior, database conventions, and repository hygiene.

- [AGENTS.md](./AGENTS.md)  
  Repo-level quickstart for coding agents, including navigation anchors and source-of-truth order by project area.

## Notes

- Some top-level folders such as `scratch/` and `.codex-tmp/` are workspace support areas rather than standalone projects.
- `Ideas/` is a workspace knowledge area rather than a standalone application or package.
- Add new project links here when a top-level folder gains its own `README.md`.
