# article-to-obsidian-kb

Local Codex skill for turning long-form sources into linked Obsidian knowledge-base notes.

## Purpose

Use `article-to-obsidian-kb` when a source article, transcript, or long-form text needs to be converted into compact Russian-language Obsidian notes rather than summarized ad hoc in chat.

The skill:

- reads a source URL or provided long-form text
- defaults to `source` mode unless a structured mode is explicitly requested
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

Treat the repository copy of `config/runtime.local.toml` as the single editable local config. If an installed Codex copy exists under `~/.codex/skills`, it should point to the same file instead of keeping a second divergent copy.

## Local Runtime Behavior

- loads vault roots from `config/runtime.local.toml`
- keeps `source` as the default workflow mode
- uses separate note roots for source-derived notes and concept notes
- can resolve dedicated structured note roots when `structured` mode is explicitly selected
- resolves staging and temporary files through `paths.scratch_root`, defaulting to `<project_root>/scratch/article-to-obsidian-kb`
- can use `paths.project_root` or `CODEX_PLAYGROUND_PROJECT_ROOT` to keep project-local `source` paths relative even when the skill runs from an installed copy under `~/.codex`
- uses `paths.kb_index_config` as the canonical entry point to `kb-index` when configured
- never resolves project-local relative paths from the shell cwd
- uses `config/runtime.example.toml` as the canonical operator-facing reference for config keys, defaults, and local-runtime notes

## Main Files

- `SKILL.md`: runtime workflow entrypoint
- `config/note_schema.yaml`: single source of truth for canonical section headings used by the note contract
- `scripts/note_schema.py`: shared schema accessor for local Python consumers
- `scripts/check_note_contract.py`: mechanical note-contract checker
- `scripts/detect_source_route.py`: source routing helper
- `scripts/write_structured_note.py`: explicit structured-note writer for non-source payloads such as `council-verdict`
- `templates/council-verdict.md.tmpl`: mechanical render-layout for the `council-verdict` structured note

## References

- `references/structured-note-types.md`: defines explicit structured-note modes and the current `council-verdict` route
- `references/vault-conventions.md`: defines the canonical final note contract for frontmatter, titles, tags, links, spacing, and closing sections
- `references/update-patterns.md`: defines update-vs-create behavior, merge rules, chronology, and post-write verification
- `references/source-analysis-engineering.md`: defines the extraction path for engineering-heavy sources
- `references/source-analysis-general.md`: defines the extraction path for general sources
- `references/language-normalization.md`: defines language cleanup and normalization rules for final note text
- `references/test-matrix.md`: explains which note-contract rule families are expected to stay mechanically test-covered

## Notes

- Keep this README operator-facing and brief.
- Keep detailed workflow and note-contract rules in `SKILL.md` and canonical reference files.
- Treat `config/note_schema.yaml` as the canonical owner for schema-defined headings. Do not translate or locally rename those headings in individual notes; change the schema first and migrate consumers together.
