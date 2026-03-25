# Vault Conventions

## Scope

- Resolve note roots from `config/runtime.local.toml` via [local-config.md](local-config.md).
- Store article-derived notes in `note_roots.article`.
- Store concept notes in `note_roots.concept`.
- Treat the filename as identical to the note `title`.

## Note Types

- `lessons`: article-derived note with 6-10 reusable engineering lessons.
- `operating-model`: article-derived note describing how a company or system actually works.
- `concept`: reusable concept note that can connect multiple articles.

## Placement Rules

- Save `lessons` notes in `note_roots.article`.
- Save `operating-model` notes in `note_roots.article`.
- Save `concept` notes in `note_roots.concept`.
- Search both locations before deciding that a note is new.

## Title And Filename Rules

### Article-derived notes

- Use inverted pyramid naming:
  - `<engineering topic> - <context>`
- Put the main engineering theme first, then the company or system context.
- Make the title readable outside the article context and searchable in Obsidian.
- Avoid titles like `Lessons from X` or `Summary of X`.
- For `type: lessons`, do not include the literal word `Lessons` in the title when the frontmatter already captures the note type.
- Prefer a substantive topical title such as the main risk, model, method, or pattern described by the source.

Examples:

- `Developer Experience - Dropbox`
- `Platform Engineering Operating Model - Spotify`
- `AI Code Review Workflow - ByteDance`
- `Интенсификация работы с AI - Harvard Business Review`

### Concept notes

- Use the concept name itself as the title and filename.
- Keep the concept universal and reusable.
- Do not include the company name unless the concept itself is named that way.

Examples:

- `Data Flywheel`
- `Deployment Frequency`
- `Technical Debt`

## Frontmatter

### Lessons and operating-model notes

```yaml
---
title: <title>
source:
  - <url>
type: <lessons | operating-model>
tags:
  - <tag>
date: <year>
---
```

### Concept notes

```yaml
---
title: <concept name>
type: concept
tags:
  - <tag>
---
```

## Tags

- Generate tags only from the article or concept context.
- Keep 4-8 tags.
- Keep all tags strictly in English.
- Use ASCII lowercase kebab-case for new tags unless an established existing vault tag already uses another English form.
- Prefer short, searchable tags.
- Avoid template tags such as `concept`, `engineering`, `article`.
- Avoid Cyrillic tags and mixed Russian-English tag variants.
- Avoid synonyms and near-duplicates.
- Normalize terminology when multiple variants exist.
- Deduplicate tags against the existing vault before saving the note.
- Translate Russian candidate tags into English before the vault deduplication check.
- Reuse an existing vault tag when the meaning matches and the difference is only:
  - singular vs plural
  - hyphenation or spacing
  - abbreviation vs expanded form
  - Russian vs English wording
  - minor word-order changes
- Prefer the canonical English tag already used by the closest matching notes or concept notes over inventing a new variant.
- If two candidate tags still overlap after the vault check, keep the shorter and more searchable canonical form.

## Writing Rules

- Write in Russian.
- Keep key technical terms in English when the English form is the standard term.
- Translate ordinary management, business, and product vocabulary into Russian when a natural Russian equivalent exists.
- Do not leave random English nouns in the prose just because they appeared in the source article.
- On first mention, use the Russian form and add the English term in parentheses only when the English wording materially helps recognition or search.
- Use only information supported by the source.
- Keep the structure compact, but do not over-compress the content.
- Preserve concrete mechanisms when the source states them explicitly, especially team scope, owned systems, partner teams, named metrics, prioritization logic, AI/platform details, and major constraints.
- Use bold only for key terms, short labels, or the leading clause of a bullet.
- Use enough bold emphasis that dense sections remain easy to scan, but do not bold entire list items.
- Do not repeat `title` or `source` from frontmatter inside the body.
- Do not rewrite several concrete observations into one abstract sentence if that would hide how the system actually operates.
- For article-derived notes, prefer short sections with 2-4 bullets when the source provides multiple concrete points for the same topic.
- Prefer Russian lesson headings and Russian section bullets unless the English wording is the canonical name of a framework, metric, tool, or code-level term.

## Spacing Rules

- Do not leave an empty line after headings.
- Do not leave an empty line before lists.
- Do not leave empty lines between list items.
- Do not use double blank lines.
- Use at most one blank line between major sections.

## Links

- Use Obsidian wikilinks for related notes.
- Keep link labels short and consistent with filenames.
- Reuse the exact existing title when linking to an existing note.
- Link concept notes by title only, even though they live in `Ideas/Concepts`.

## Required Closing Section

- End every note with `# Связанные заметки`.
- Add 5-10 wikilinks when that many relevant notes exist.
- Prefer links to touched concept notes, article-derived notes, and the closest existing concepts in the vault.
