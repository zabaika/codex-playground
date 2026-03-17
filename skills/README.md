# Skills

Local Codex skills that extend workspace-specific workflows.

## Available Skills

- [article-to-obsidian-kb](./article-to-obsidian-kb/SKILL.md)  
  Converts an engineering article URL into compact Russian-language Obsidian knowledge-base notes, updates overlapping notes instead of creating duplicates, and maintains wikilink connections between article notes and concept notes.

## article-to-obsidian-kb

The skill is designed for a vault-backed knowledge workflow rather than for generic note generation.

What it does:

- reads a source article URL
- builds an internal concept map with engineering concepts, non-obvious insights, operating-model details, and reusable lessons
- searches existing Obsidian note roots before drafting anything
- prefers updating matching notes over creating duplicates
- writes article notes and concept notes in Russian
- keeps technical terms in English only when they are the stable industry form
- normalizes tags into canonical English forms
- preserves concrete operational detail instead of flattening everything into generic summaries

Local runtime behavior:

- loads local vault roots from [config/runtime.local.toml](./article-to-obsidian-kb/config/runtime.local.toml)
- uses separate note roots for article-derived notes and concept notes
- depends on local-only paths and therefore must not commit machine-specific roots or secrets

Supporting references:

- [local-config.md](./article-to-obsidian-kb/references/local-config.md)
- [vault-conventions.md](./article-to-obsidian-kb/references/vault-conventions.md)
- [language-normalization.md](./article-to-obsidian-kb/references/language-normalization.md)
- [update-patterns.md](./article-to-obsidian-kb/references/update-patterns.md)

## Notes

- Skills in this folder are workspace extensions rather than standalone applications.
- Add new skill links here when new skill folders are added.
