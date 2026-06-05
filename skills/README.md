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

### jss-candidate-intake

- Creates/selects candidates, prepares AI extraction requests, imports validated profile drafts, reviews conflicts, and confirms canonical candidate profiles through `tools/job-search-system`.
- Entry: [SKILL.md](./jss-candidate-intake/SKILL.md)
- Commands: [references/commands.md](./jss-candidate-intake/references/commands.md)
- Install: [install-local.sh](./jss-candidate-intake/install-local.sh)

### jss-career-pathing

- Runs career-pathing lite/full workflows: role comparison, title-inflation risk, primary target role, capability gaps, brand plan, and trajectory ranking.
- Entry: [SKILL.md](./jss-career-pathing/SKILL.md)
- Commands: [references/commands.md](./jss-career-pathing/references/commands.md)
- Install: [install-local.sh](./jss-career-pathing/install-local.sh)

### jss-job-board-operations

- Produces manual job-board checklists, saved-search settings, URL enrichment seeds, board action logs, artifact usage records, and reconciliation views without browser automation.
- Entry: [SKILL.md](./jss-job-board-operations/SKILL.md)
- Commands: [references/commands.md](./jss-job-board-operations/references/commands.md)
- Install: [install-local.sh](./jss-job-board-operations/install-local.sh)

### jss-job-search-playbook

- Generates search strategy, saved-search design pack, reusable outreach message, compensation framing, and lightweight interview-prep artifacts.
- Entry: [SKILL.md](./jss-job-search-playbook/SKILL.md)
- Commands: [references/commands.md](./jss-job-search-playbook/references/commands.md)
- Install: [install-local.sh](./jss-job-search-playbook/install-local.sh)

### jss-resume-positioning

- Generates role-based and vacancy-aware resume artifacts, positioning briefs, quality gates, persisted resume roast reports, and final accepted resume artifacts.
- Entry: [SKILL.md](./jss-resume-positioning/SKILL.md)
- Commands: [references/commands.md](./jss-resume-positioning/references/commands.md)
- Install: [install-local.sh](./jss-resume-positioning/install-local.sh)

### jss-vacancy-pipeline

- Imports raw or structured vacancy batches, normalizes/dedupes/scores/ranks vacancies, manages shortlist/processed state, application drafts, payloads, touchpoints, reminders, and reports.
- Entry: [SKILL.md](./jss-vacancy-pipeline/SKILL.md)
- Commands: [references/commands.md](./jss-vacancy-pipeline/references/commands.md)
- Install: [install-local.sh](./jss-vacancy-pipeline/install-local.sh)

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
