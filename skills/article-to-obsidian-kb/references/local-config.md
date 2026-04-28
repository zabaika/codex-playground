# Local Config

## Files

- Commit `config/runtime.example.toml`.
- Keep real machine-specific values in `config/runtime.local.toml`.
- Never commit `runtime.local.toml`.

## Required Keys

```toml
[note_roots]
article = "/absolute/path/to/article-notes"
concept = "/absolute/path/to/concept-notes"
```

- `note_roots.article`: search and save root for `lessons`, `general`, and `operating-model` notes.
- `note_roots.concept`: search and save root for `concept` notes.

## Optional Keys

- `[paths]`: extra local paths when the workflow needs them later.
- `paths.scratch_root`: project-root-relative scratch directory for temporary or staged files. Prefer `scratch/article-to-obsidian-kb`.
- `paths.kb_index_config`: path to `kb-index` `runtime.local.toml` when the workflow should prefer indexed retrieval over broad direct vault search.
  Prefer a project-root-relative path such as `tools/kb-index/config/runtime.local.toml`; keep an absolute path only as a machine-local fallback.
  The skill should inherit retrieval defaults, including shortlist size, from that external `kb-index` config instead of duplicating them locally.
- `[secrets]`: tokens, passwords, or other credentials that must stay local.

## Usage Rules

- Resolve note roots from `<skill-dir>/config/runtime.local.toml`, where `<skill-dir>` is the directory that contains the active `SKILL.md`.
- Resolve `paths.scratch_root` from the same file when present.
- Resolve `paths.kb_index_config` from the same file when present.
- Prefer project-root-relative or skill-root-relative values for `paths.kb_index_config` over hard-coded home-directory paths.
- Prefer project-root-relative scratch paths such as `scratch/article-to-obsidian-kb` over repo-local `tmp/` folders or machine-specific absolute temp paths.
- If `paths.scratch_root` is absent, default to `scratch/article-to-obsidian-kb`.
- If `paths.kb_index_config` is present and points to a working `kb-index` config, prefer `search_kb` retrieval before any broad direct file search through the vault.
- Let `kb-index` own retrieval defaults such as shortlist size. Use `search_kb --limit ...` only when the current source needs an explicit broader or narrower pass than the shared default.
- Keep all temporary and staging artifacts for this skill under the resolved scratch root so cleanup can happen by clearing `scratch/`.
- Do not resolve `runtime.local.toml` relative to the current working directory unless that is also the skill directory.
- If the local config file exists next to the skill and contains both note roots, reuse it without asking the user to repeat those paths.
- Keep tracked docs generic; reference config keys instead of hard-coded absolute paths.
- If a needed path or secret is absent, ask the user instead of inventing one.
