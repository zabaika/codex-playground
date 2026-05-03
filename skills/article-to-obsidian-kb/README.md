# article-to-obsidian-kb

Local Codex skill for turning long-form sources into linked Obsidian knowledge-base notes.

## Purpose

Use `article-to-obsidian-kb` when a source article, transcript, or long-form text needs to be converted into compact Russian-language Obsidian notes rather than summarized ad hoc in chat.

The skill:

- reads a source URL or provided long-form text
- routes the source through an engineering or general analysis path
- searches existing note roots before drafting anything
- prefers updating matching notes over creating duplicates
- writes source-derived notes and concept notes with shared title, tag, link, and update rules

## Source Of Truth

- Repository source of truth: `skills/article-to-obsidian-kb/`
- Installed Codex copy: `~/.codex/skills/article-to-obsidian-kb`

Edit the repository copy first. Reinstall into `~/.codex/skills` after changes.

## Installation

Install or refresh the local Codex copy with:

```bash
bash skills/article-to-obsidian-kb/install-local.sh
```

## Local Runtime Behavior

- loads vault roots from `config/runtime.local.toml`
- uses separate note roots for source-derived notes and concept notes
- resolves staging and temporary files through `paths.scratch_root`, defaulting to `scratch/article-to-obsidian-kb`
- uses `paths.kb_index_config` as the canonical entry point to `kb-index` when configured
- treats the repository copy of `config/runtime.local.toml` as the single editable local config

## Main Files

- `SKILL.md`: runtime workflow entrypoint
- `scripts/check_note_contract.py`: mechanical note-contract checker
- `scripts/detect_source_route.py`: source routing helper

## References

- `references/local-config.md`: explains how the skill resolves local vault roots, scratch paths, and `kb-index` config
- `references/vault-conventions.md`: defines the canonical final note contract for frontmatter, titles, tags, links, spacing, and closing sections
- `references/update-patterns.md`: defines update-vs-create behavior, merge rules, chronology, and post-write verification
- `references/source-analysis-engineering.md`: defines the extraction path for engineering-heavy sources
- `references/source-analysis-general.md`: defines the extraction path for general sources
- `references/language-normalization.md`: defines language cleanup and normalization rules for final note text
- `references/test-matrix.md`: explains which note-contract rule families are expected to stay mechanically test-covered

## Notes

- Keep this README operator-facing and brief.
- Keep detailed workflow and note-contract rules in `SKILL.md` and canonical reference files.
