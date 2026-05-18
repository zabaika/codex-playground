# Skills

Local Codex skills that extend workspace-specific workflows.

## Skill Conventions

Use [RULEBOOK.md](../RULEBOOK.md) as the repository-wide policy baseline when creating or updating any local skill.

For local skills in this repository:

- keep one source of truth for each operational fact such as routing decisions, engine selection, chosen files, and resolved config
- if a skill is an orchestration layer over sibling skills, reuse upstream structured output, persisted metadata, or canonical logs instead of reconstructing the same facts locally
- do not add convenience placeholders, stub engine names, or alternate debug schemas when the real value can be recovered from the underlying skill with reasonable effort
- if both human-readable diagnostics and structured diagnostics are needed, make one representation canonical and keep the other clearly subordinate instead of letting them diverge
- when introducing a new reusable pattern here, prefer codifying it in [RULEBOOK.md](../RULEBOOK.md) so future skills inherit it by default

## Catalog

Root skill docs in this folder should act as a catalog and navigation layer only. Do not duplicate behavior, contracts, prompts, or maintenance details that already live in the target skill's local docs.

### article-to-obsidian-kb

- Converts long-form sources into linked Obsidian notes and updates matching notes instead of creating duplicates.
- Entry: [SKILL.md](./article-to-obsidian-kb/SKILL.md)
- Local docs: [README.md](./article-to-obsidian-kb/README.md)
- Install: [install-local.sh](./article-to-obsidian-kb/install-local.sh)

### convert-external-skills-to-codex

- Audits third-party skills and converts them into a safer Codex skill, `AGENTS.md`, ChatGPT instructions, or a report-only migration plan.
- Entry: [SKILL.md](./convert-external-skills-to-codex/SKILL.md)
- Local docs: [README.md](./convert-external-skills-to-codex/README.md)
- Install: [install-local.sh](./convert-external-skills-to-codex/install-local.sh)

### llm-council

- Runs a structured multi-advisor decision review and produces one canonical verdict payload for downstream note writing.
- Entry: [SKILL.md](./llm-council/SKILL.md)
- Local docs: [README.md](./llm-council/README.md)
- Install: [install-local.sh](./llm-council/install-local.sh)

### video-to-obsidian-kb

- Turns a YouTube or Vimeo URL into linked Obsidian notes by fetching a local transcript first and then reusing the shared note workflow.
- Entry: [SKILL.md](./video-to-obsidian-kb/SKILL.md)
- Local docs: [README.md](./video-to-obsidian-kb/README.md)
- Install: [install-local.sh](./video-to-obsidian-kb/install-local.sh)

### video-transcribe-skill

- Fetches YouTube or Vimeo subtitles or transcripts locally through a fail-closed transcript pipeline with explicit fallback behavior.
- Entry: [SKILL.md](./video-transcribe-skill/SKILL.md)
- Install: [install-local.sh](./video-transcribe-skill/install-local.sh)
- Local docs: [README.md](./video-transcribe-skill/README.md)

## Notes

- Skills in this folder are workspace extensions rather than standalone applications.
- Add new skill links here when new skill folders are added.
- Keep root-level skill catalog text brief and link outward to skill-local docs instead of restating them.
- Keep reference-file links and short explanations inside each skill's local `README.md`, not in this root catalog.
- Third-party skills should be reviewed and, when needed, narrowed before installation into `~/.codex/skills`.
