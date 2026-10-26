# Codex Playground

Workspace for local Codex-oriented projects, tools, plugins, skills, and shared operating conventions.

## Projects

- [common](./common/README.md)  
  Shared atomic UTF-8/JSON writes, SQLite access and config, and hard-TTL process supervision for one-shot jobs.

- [telegram_connector](./telegram_connector/README.md)  
  Telegram history, OCR, CSV export, scheduled digests and bounded `/topics`, `/search`, `/qa` analytics, with a separate bot listener and persistent job recovery.

- [telegram_agent_bot](./telegram_agent_bot/README.md)  
  Standalone Telegram task agent with its own bridge, OpenAI-backed worker, local read-only tools, public web search/fetch, and separate daemon flow.

- [telegram_shared](./telegram_shared/README.md)
  Shared Telegram/OpenAI transport, bounded retries and attempt hooks, config/secrets, bridge env, formatting, redaction and usage telemetry.

- [infoext-monitor](./infoext-monitor/README.md)
  Local macOS monitor for an InfoExt expediente status, using Playwright, local CAPTCHA OCR, atomic state and the existing `telegram_connector` delivery path.

- [skills](./skills/README.md)  
  Local Codex skill collection for workspace-specific workflows, including Obsidian knowledge-base generation, YouTube transcript-to-notes conversion, local transcript extraction, multi-agent decision review via `llm-council`, and the `jss-*` job-search skill pack.

- [tools/job-search-system](./tools/job-search-system/docs/user-guide.md)
  Local job-search workflow system with API-lite, CLI fallback, Codex skills, canonical artifact handling, and Stage 3 planning/coverage docs.

- [tools/kb-index](./tools/kb-index/README.md)  
  Local retrieval and indexing tool for the Obsidian knowledge base, built around SQLite/FTS note search and launchd-backed scheduled refresh.

- [tools/book-search](./tools/book-search/README.md)  
  Local Litres and Bookmate lookup helpers for finding catalogue links from reading-list search queries. Bookmate lookup uses the public search API response shape and supports saved API JSON files as the browser fallback; it does not handle browser cookies.

- [tools/document-converter](./tools/document-converter/README.md)  
  Local one-shot converter and index builder that mirrors a source document tree into readable converted files plus search indexes while preserving source inputs.

- [plugins](./plugins/README.md)  
  Local Codex plugin sources that package reusable skills and, when needed, broader Codex integrations such as `codex-token-monitor`.

## Python dependency follow-ups

The following projects use reviewed temporary distributions. Exact versions,
compatibility ranges, hashes and provenance remain in their owning inputs.
Review official metadata and wheel tags before replacing them for a selected Python.

| Project / environment | Temporary distribution | Replacement and verification |
| --- | --- | --- |
| [video-transcribe-skill](./skills/video-transcribe-skill/README.md), also used by `video-to-obsidian-kb` | Local transcript API wheel with adjusted compatibility metadata; Python sources unchanged | Adopt a compatible official release. Review the archive, manifest, lockfile and hashes, then run offline install/parser/runner tests and reinstall. See the [audit record](./skills/video-transcribe-skill/THIRD_PARTY_AUDIT_YOUTUBE_TRANSCRIPT_API.md) and [manifest](./skills/video-transcribe-skill/config/vendor-manifest.toml). |
| [Shared skill validation](./skills/README.md#skill-validation), `skills/.venv-validation` | Reviewed pure-Python PyYAML wheel with its optional C extension disabled | Adopt an official compatible ARM64 wheel. Review any change to [dependency inputs](./skills/requirements-validation.txt), prepare the environment and rerun skill validation and affected contract tests. |
| [video-transcribe-skill](./skills/video-transcribe-skill/README.md), optional acceleration | Official pure-Python charset-normalizer wheel recorded in the manifest | Functionality already works. If useful, review a compatible official accelerated wheel, update audited archive/hashes and rerun encoding/parser tests before reinstalling. |

Pure-Python wheels preserve functionality; optional native extensions can affect
performance. Never relabel an incompatible wheel or silently replace audited inputs.
Use the [shared runtime upgrade procedure](./common/README.md#python-runtime-upgrades).

## Shared Docs

- [RULEBOOK.md](./RULEBOOK.md)  
  Cross-project rules for secrets, runtime safety, logging, daemon behavior, database conventions, and repository hygiene.

- [CODE_CONVENTIONS.md](./CODE_CONVENTIONS.md)\
  Shared Python coding conventions and function/module size-review signals for new and changed code.

- [AGENTS.md](./AGENTS.md)  
  Repo-level quickstart for coding agents, including navigation anchors and source-of-truth order by project area.

## Notes

- Some top-level folders such as `scratch/` and `.codex-tmp/` are workspace support areas rather than standalone projects.
- `Ideas/` is a workspace knowledge area rather than a standalone application or package.
- Add new project links here when a top-level folder gains its own `README.md` or another canonical operator-facing entry document.
