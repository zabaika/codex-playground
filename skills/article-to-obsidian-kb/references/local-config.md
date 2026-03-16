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

- Resolve note roots from `runtime.local.toml` before searching the vault.
- Keep tracked docs generic; reference config keys instead of hard-coded absolute paths.
- If a needed path or secret is absent, ask the user instead of inventing one.
