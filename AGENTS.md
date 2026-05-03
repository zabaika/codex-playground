# AGENTS

This file is a repo-specific quickstart for coding agents working in `codex-playground`.

Use it as an entry guide. For repository-wide engineering policy, safety rules, and storage conventions, see:
- [RULEBOOK.md](./RULEBOOK.md)

## Repo Layout

- [README.md](./README.md): top-level project index
- [RULEBOOK.md](./RULEBOOK.md): global engineering and safety rules
- [telegram_connector](./telegram_connector): Telegram ingestion, OCR, digest, and bot bridge project
- [telegram_agent_bot](./telegram_agent_bot): standalone Telegram task agent project
- [telegram_shared](./telegram_shared): shared infrastructure primitives for Telegram projects
- [skills](./skills): local skills and related documentation
- [tools/kb-index](./tools/kb-index): local retrieval and indexing tool for the Obsidian knowledge base

## Main Working Area

Most active code currently lives in:
- [telegram_connector/README.md](./telegram_connector/README.md)
- [telegram_connector/AGENTS.md](./telegram_connector/AGENTS.md)
- [telegram_connector/telegram_connector.py](./telegram_connector/telegram_connector.py)
- [telegram_connector/telegram_history_client.py](./telegram_connector/telegram_history_client.py)
- [telegram_connector/telegram_digest.py](./telegram_connector/telegram_digest.py)
- [telegram_connector/tests](./telegram_connector/tests)
- [telegram_agent_bot/README.md](./telegram_agent_bot/README.md)
- [telegram_agent_bot/AGENTS.md](./telegram_agent_bot/AGENTS.md)
- [telegram_agent_bot/telegram_agent_bridge.py](./telegram_agent_bot/telegram_agent_bridge.py)
- [telegram_agent_bot/telegram_agent_worker.py](./telegram_agent_bot/telegram_agent_worker.py)
- [telegram_agent_bot/tests](./telegram_agent_bot/tests)
- [telegram_shared](./telegram_shared)

## Source Of Truth

When working in `telegram_connector`, prefer these sources in this order:
- [telegram_connector/README.md](./telegram_connector/README.md) for current user-facing behavior and command surface
- [telegram_connector/AGENTS.md](./telegram_connector/AGENTS.md) for project-specific coding boundaries
- [RULEBOOK.md](./RULEBOOK.md) for cross-project rules
- tests in [telegram_connector/tests](./telegram_connector/tests) for executable expectations

When working in `telegram_agent_bot`, prefer these sources in this order:
- [telegram_agent_bot/README.md](./telegram_agent_bot/README.md) for user-facing behavior and command surface
- [telegram_agent_bot/AGENTS.md](./telegram_agent_bot/AGENTS.md) for project-specific coding boundaries
- [RULEBOOK.md](./RULEBOOK.md) for cross-project rules
- tests in [telegram_agent_bot/tests](./telegram_agent_bot/tests) for executable expectations

When working in `tools/kb-index`, prefer these sources in this order:
- [tools/kb-index/README.md](./tools/kb-index/README.md) for operator behavior and command surface
- [tools/kb-index/AGENTS.md](./tools/kb-index/AGENTS.md) for project-specific runtime and deployment rules
- [RULEBOOK.md](./RULEBOOK.md) for cross-project rules
- tests in [tools/kb-index/tests](./tools/kb-index/tests) for executable expectations

When working in `skills`, prefer these sources in this order:
- [skills/README.md](./skills/README.md) for the skill catalog, installation pattern, and local conventions
- the target skill's local docs such as `README.md`, `AGENTS.md`, and `SKILL.md` inside its folder for behavior and maintenance rules
- [RULEBOOK.md](./RULEBOOK.md) for cross-project rules
- any skill-local scripts or tests for executable expectations

If README, code, and tests disagree, update them together rather than fixing only one layer.

## Repo Rules

- for route selection, engine choice, selected inputs, config resolution, and similar runtime facts, keep exactly one canonical producer and let wrapper layers consume that output instead of rebuilding it
- do not add parallel summary formats, convenience placeholders, or local stub values when the real metadata already exists in an upstream tool, structured payload, or canonical log
- when a wrapper replays cached or preexisting artifacts, recover metadata from the original producer's persisted output before introducing any fallback
- never commit machine-specific paths, usernames, home-directory paths, or local workstation identifiers
- never commit plaintext secrets
- keep local config and generated runtime artifacts out of commits
- when a project has its own `AGENTS.md`, prefer that file for operational details, commands, runtime semantics, and verification steps
- for repository-managed local skills, treat the copy under `skills/<skill-name>/` as the editable source of truth and treat `~/.codex/skills/<skill-name>` as an installed copy refreshed from the repository
- before finishing behavior changes, update docs in the same change and verify the relevant tests pass
