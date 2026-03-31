# Vault Conventions

## Scope

- These conventions apply to every saved note regardless of whether the source was processed through the engineering analysis path or the general analysis path.
- Resolve note roots from `config/runtime.local.toml` via [local-config.md](local-config.md).
- Store source-derived notes in `note_roots.article`.
- Store concept notes in `note_roots.concept`.
- Treat the filename as identical to the note `title`.

## Note Types

- `lessons`: source-derived note with 6-10 reusable lessons, principles, or practical guidelines.
- `general`: source-derived note for general-route materials that have durable signal but do not naturally fit `lessons` or `operating-model`.
- `operating-model`: source-derived note describing how a company or system actually works.
- `concept`: reusable concept note that can connect multiple sources.

## Placement Rules

- Save `lessons` notes in `note_roots.article`.
- Save `general` notes in `note_roots.article`.
- Save `operating-model` notes in `note_roots.article`.
- Save `concept` notes in `note_roots.concept`.
- Search both locations before deciding that a note is new.

## Title And Filename Rules

### Source-derived notes

- Use inverted pyramid naming:
  - `<main topic> - <context>`
- Put the main topic first, then the company, system, speaker, or source context.
- Make the title readable outside the source context and searchable in Obsidian.
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

### Lessons, general, and operating-model notes

```yaml
---
title: <title>
source:
  - <url>
type: <lessons | general | operating-model>
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

Frontmatter is part of the note schema, not optional decoration.

- After any late manual rewrite, merge, or structural cleanup, run one full-note compliance check instead of validating only the field or section you just touched.
- Re-check frontmatter, links, required closing section, spacing, emphasis, and section shape together on the final note.
- Treat “I only changed one small thing” as a common source of regressions in the rest of the note.
- After that first full-note compliance check, run one more full regression sweep with the same coverage again.
- The second sweep is not a narrower validator; it repeats the same final note contract for reliability after the compliance fixes themselves.
- Do not stop after frontmatter, links, or language are repaired once; re-check the whole note again because late fixes often regress another already-fixed rule.
- Validate required frontmatter fields after the final body rewrite, not only after the first draft.
- When a source-derived note is manually reworked, merged with an older note, or restructured late in the run, re-check that `title`, `source`, `type`, `tags`, and `date` still exist and still match the saved note.
- When a concept note is manually reworked, re-check that `title`, `type: concept`, and `tags` are still present.
- Do not save a note with partial frontmatter just because the body already looks finished.

## Tags

- Generate tags only from the source or concept context.
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
- Treat the creation of a brand-new tag as a last resort.
- Create a new tag only when you are fully confident that no existing vault tag is close enough in meaning and the new tag is clearly necessary for future retrieval.
- If there is any material doubt, prefer the nearest existing canonical vault tag instead of introducing a new one.
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
- Preserve concrete examples, mini-cases, numbers, and before/after transitions when they materially improve understanding of the idea instead of merely decorating it.
- If an example is the shortest path to making a recommendation, claim, or anti-pattern understandable, keep a compact version of that example in the note.
- When the source came through a source-analysis reference, treat that extraction as a working scaffold only and rewrite the final note into a native Obsidian structure instead of preserving the extractor headings literally.
- For every note type, make sections and bullets additive: do not repeat the same recommendation, example, claim, mechanism, or definition under multiple headings unless the source truly requires cross-reference.
- Every next block or bullet must add new knowledge instead of duplicating, inverting, or paraphrasing the previous one.
- Prefer one stronger section over two overlapping ones.
- In `lessons` notes, merge overlapping lessons instead of keeping two nearby principles with different wording.
- In `operating-model` notes, make each section cover a distinct part of how the organization or system works.
- In `concept` notes, keep the definition compact and avoid restating it in later sections; later additions should extend the note with evidence, observed practices, or adjacent insight.
- Treat `compact` as the default concept-note shape: one tight definition, `## Additional insights`, and `# Связанные заметки`.
- Switch a concept note to an `expanded` shape only when the compact form would leave a real comprehension gap.
- Prefer `expanded` concept notes for comparative concepts, easy-to-confuse neighboring concepts, abbreviations or shorthand metric names, and concepts whose recommendation would remain unclear without one more explanatory layer.
- In an `expanded` concept note, add only one or two short clarifying sections that answer the missing question directly, for example `## Чем отличается`, `## Когда полезен`, or `## Почему метрика шумная`.
- If one concept is expanded mostly to contrast it with a nearby concept, make sure the neighboring concept note also exposes that distinction at least briefly instead of leaving the asymmetry invisible from one side.
- When a source only reinforces an existing concept, update the existing concept note instead of creating a near-duplicate concept file.
- Before creating a new concept file, run a canonical concept check against the existing vault and reuse the canonical note when the meaning already exists.
- Do not let a nicer title, a translation, or a local wording variant justify a duplicate concept node when the durable idea is already present.
- In `general` notes, fold examples and cases into the relevant recommendation inside `## Практика` instead of giving them a standalone section.
- In `general` notes, do not strip useful examples out of `## Ключевые тезисы` or `## Практика` if that would leave only abstract restatements.
- Add `## Инструменты и фреймворки` only when it contributes a clearly separate layer of knowledge beyond `## Практика`.
- Add `## Подводные камни и антипаттерны` only when the source discusses distinct mistakes with their own consequences; do not use it for negative rewrites of recommendations.
- Omit `## Что можно применить сразу` when it would only repeat the same actions from `## Практика`.
- Use bold only for key terms, short labels, or the leading clause of a bullet.
- Use enough bold emphasis that dense sections remain easy to scan, but do not bold entire list items.
- After a late manual rewrite or merge, re-check bold emphasis explicitly; it often disappears when frontmatter, links, or structure are fixed in a second pass.
- Treat lost emphasis as a formatting regression, not as an acceptable cleanup side effect.
- Do not repeat `title` or `source` from frontmatter inside the body.
- Do not rewrite several concrete observations into one abstract sentence if that would hide how the system actually operates.
- For source-derived notes, prefer short sections with 2-4 bullets when the source provides multiple concrete points for the same topic.
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
- When the body text explicitly mentions another existing note or concept as a knowledge reference, turn that mention into a wikilink instead of leaving it as plain text.
- Prefer inline wikilinks at the point of mention, not only in `# Связанные заметки`.

## Required Closing Section

- End every note with `# Связанные заметки`.
- Add 5-10 wikilinks when that many relevant notes exist.
- Prefer links to touched concept notes, source-derived notes, and the closest existing concepts in the vault.
