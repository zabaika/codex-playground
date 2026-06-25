# AGENTS

This file is a repo-specific quickstart for coding agents working in `codex-playground`.

Use it as an entry guide. For repository-wide engineering policy, safety rules, and storage conventions, see:
- [RULEBOOK.md](./RULEBOOK.md)

## Repo Layout

- [README.md](./README.md): top-level project index
- [RULEBOOK.md](./RULEBOOK.md): global engineering and safety rules
- [common](./common): repository-wide shared runtime helpers and shared config bundles
- [telegram_connector](./telegram_connector): Telegram ingestion, OCR, digest, and bot bridge project
- [telegram_agent_bot](./telegram_agent_bot): standalone Telegram task agent project
- [telegram_shared](./telegram_shared): shared infrastructure primitives for Telegram projects
- [skills](./skills): local skills and related documentation
- [plugins](./plugins): local Codex plugin sources and related documentation
- [tools/job-search-system](./tools/job-search-system): local job-search workflow system with API-lite, CLI, skills, and canonical artifact/runtime docs
- [tools/kb-index](./tools/kb-index): local retrieval and indexing tool for the Obsidian knowledge base
- [tools/book-search](./tools/book-search): local Litres and Bookmate lookup helpers for reading-list link checks, with saved API JSON as the Bookmate browser fallback
- [tools/document-converter](./tools/document-converter): local one-shot document converter and search-index builder that keeps source inputs read-only

Keep this block current as the repository evolves:
- when a new major program, service, tool, or top-level workflow becomes a meaningful part of the repository, add it here
- when a listed project is removed, renamed, or stops being a meaningful navigation anchor, update this block in the same change

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

When working in `tools/book-search`, prefer these sources in this order:
- [tools/book-search/README.md](./tools/book-search/README.md) for operator behavior, provider limits, command surface, and the no-cookie Bookmate fallback through saved API JSON
- [RULEBOOK.md](./RULEBOOK.md) for cross-project rules

When working in `tools/job-search-system`, prefer these sources in this order:
- [tools/job-search-system/docs/stage3-backlog.md](./tools/job-search-system/docs/stage3-backlog.md) for remaining work and the required Stage 3 implementation loop
- [tools/job-search-system/docs/capability-coverage.md](./tools/job-search-system/docs/capability-coverage.md) for plan-vs-implementation status of each capability
- `Job/job-search-skills/00-10` in the Obsidian knowledge base for canonical design intent, rollout, policies, UI architecture, and testing expectations
- [tools/job-search-system/docs/user-guide.md](./tools/job-search-system/docs/user-guide.md) for operator-facing workflow and artifact layout
- in that user guide, treat the documented operator commands such as `doctor.sh`, `start-api.sh`, `import-vacancies.sh`, `import-linkedin-text.sh`, `import-hh-ru-text.sh`, `import-vacancy-text.sh`, `daily-routine.sh`, `rename-artifacts.sh`, and `cleanup-artifacts.sh` as the canonical shell entrypoints instead of rediscovering `scripts/operator/` ad hoc
- [RULEBOOK.md](./RULEBOOK.md) for cross-project rules
- tests in [tools/job-search-system/tests](./tools/job-search-system/tests) for executable expectations

When working in `tools/document-converter`, prefer these sources in this order:
- [tools/document-converter/README.md](./tools/document-converter/README.md) for operator behavior and command surface
- [RULEBOOK.md](./RULEBOOK.md) for cross-project rules
- the generated `conversion-report.json` and `conversion-summary.md` in the destination tree for the latest run status

For `tools/job-search-system` Stage 3 work, always use this loop:
- pick the next item from `stage3-backlog.md`
- find the matching row in `capability-coverage.md`
- open the referenced `Job/job-search-skills/00-10` source docs
- implement through command/query/API boundaries, never direct SQLite writes from UI, skills, or AI
- add or update tests
- update `capability-coverage.md`, remove or narrow the completed item in `stage3-backlog.md`, and update `Job/job-search-skills/10-job-search-implementation-journal.md` in the same change
- treat `stage3-backlog.md` as a temporary work plan for unresolved work only, not as an archive of completed implementation

When working in `common`, prefer these sources in this order:
- [common/README.md](./common/README.md) for the intended reuse boundary and current shared modules
- [RULEBOOK.md](./RULEBOOK.md) for cross-project runtime, database, deployment, and non-daemon TTL rules
- dependent project tests for executable expectations when `common` changes behavior used by those projects

When working in `skills`, prefer these sources in this order:
- [skills/README.md](./skills/README.md) for the skill catalog, installation pattern, and local conventions
- the target skill's local docs such as `README.md`, `AGENTS.md`, and `SKILL.md` inside its folder for behavior and maintenance rules
- [RULEBOOK.md](./RULEBOOK.md) for cross-project rules
- any skill-local scripts or tests for executable expectations

When working in `plugins`, prefer these sources in this order:
- [plugins/README.md](./plugins/README.md) for the plugin catalog and installation intent
- the target plugin's local docs such as `README.md`, `AGENTS.md`, and bundled skill docs for behavior and maintenance rules
- [RULEBOOK.md](./RULEBOOK.md) for cross-project rules
- any plugin-local scripts or tests for executable expectations

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
- for standalone repository-managed local skills, the minimum expected repository shape is `SKILL.md`, `README.md`, and `install-local.sh`
- for tightly-coupled skill packs over one local system, `SKILL.md`, `install-local.sh`, and skill-local references are acceptable when the shared owner docs clearly live in that system's documentation
- use a skill-local `README.md` as the operator-facing summary for purpose, source-of-truth, installation, runtime behavior, and main files unless a tightly-coupled skill pack intentionally delegates that role to shared owner docs
- keep routine skill maintenance notes in the local `README.md` by default; add a skill-local `AGENTS.md` only when the skill needs a separate future-agent maintenance contract beyond `SKILL.md`, `README.md`, and `RULEBOOK.md`
- when a skill has a `references/` directory, document those reference files in the local `README.md` with one short explanation per file or per clearly grouped subset
- keep reference-file links and explanations in the skill-local `README.md`, not in root catalog documents such as `skills/README.md`
- add audit, security-review, or third-party review documents for a skill only when its provenance or risk profile justifies them
- when a local skill needs operator-facing maintenance docs, keep them in the repository copy and reinstall them together with the rest of the skill so the installed copy stays in sync
- before finishing behavior changes, update docs in the same change and verify the relevant tests pass

## Coding Guardrails

- **Think Before Coding.** Do not assume silently. State assumptions, surface ambiguity, and ask when the task is unclear.
- **Simplicity First.** Write the minimum code that solves the requested problem. Do not add speculative flexibility, abstractions, or side features.
- **Surgical Changes.** Touch only code that directly serves the request. Do not refactor, reformat, or clean adjacent code unless your own change made it necessary.
- **Goal-Driven Execution.** Turn the task into verifiable success criteria, state a short plan for multi-step work, and verify the result before finishing.
