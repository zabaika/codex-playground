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

- `note_roots.article`: search and save root for `lessons` and `operating-model` notes.
- `note_roots.concept`: search and save root for `concept` notes.

## Optional Keys

- `[paths]`: extra local absolute paths when the workflow needs them later.
- `[secrets]`: tokens, passwords, or other credentials that must stay local.

## Usage Rules

- Resolve note roots from `<skill-dir>/config/runtime.local.toml`, where `<skill-dir>` is the directory that contains the active `SKILL.md`.
- Do not resolve `runtime.local.toml` relative to the current working directory unless that is also the skill directory.
- If the local config file exists next to the skill and contains both note roots, reuse it without asking the user to repeat those paths.
- Keep tracked docs generic; reference config keys instead of hard-coded absolute paths.
- If a needed path or secret is absent, ask the user instead of inventing one.
