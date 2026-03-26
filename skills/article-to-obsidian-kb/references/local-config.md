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
- `[secrets]`: tokens, passwords, or other credentials that must stay local.

## Usage Rules

- Resolve note roots from `<skill-dir>/config/runtime.local.toml`, where `<skill-dir>` is the directory that contains the active `SKILL.md`.
- Resolve `paths.scratch_root` from the same file when present.
- Prefer project-root-relative scratch paths such as `scratch/article-to-obsidian-kb` over repo-local `tmp/` folders or machine-specific absolute temp paths.
- If `paths.scratch_root` is absent, default to `scratch/article-to-obsidian-kb`.
- Keep all temporary and staging artifacts for this skill under the resolved scratch root so cleanup can happen by clearing `scratch/`.
- Do not resolve `runtime.local.toml` relative to the current working directory unless that is also the skill directory.
- If the local config file exists next to the skill and contains both note roots, reuse it without asking the user to repeat those paths.
- Keep tracked docs generic; reference config keys instead of hard-coded absolute paths.
- If a needed path or secret is absent, ask the user instead of inventing one.
