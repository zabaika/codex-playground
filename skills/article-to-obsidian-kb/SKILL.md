---
name: article-to-obsidian-kb
description: Analyze an engineering article from a provided URL and convert it into linked Obsidian knowledge-base notes inside user-configured note roots. Use when Codex needs to search an existing vault for overlapping lessons, operating models, or concepts; update matching notes instead of creating duplicates; create new notes only when the article adds genuinely new knowledge; and maintain wikilink connections between article notes and concept notes.
---

# Article To Obsidian Kb

## Overview

Turn a source article URL into compact, Russian-language Obsidian notes in a user-configured vault. Store article-derived notes and concept notes in the local roots from the runtime config, while preferring updates over duplicates and keeping the knowledge graph connected with wikilinks. Treat `compact` as a formatting rule, not as permission to collapse concrete mechanisms into vague summaries.

## Local Runtime Config

1. Load [references/local-config.md](references/local-config.md) before touching the vault.
2. Resolve the skill directory from the location of this `SKILL.md`.
3. Read `<skill-dir>/config/runtime.local.toml` when it exists.
4. Use `note_roots.article` and `note_roots.concept` from that file for all search and save operations.
5. Do not look for the config relative to the current working directory unless the skill directory itself is the current working directory.
6. If `<skill-dir>/config/runtime.local.toml` exists and contains both required note roots, do not ask the user for those paths again.
7. Never commit machine-specific paths, local roots, passwords, or tokens into `SKILL.md`, references, or tracked config files.
8. If the local config is missing and the roots are not already obvious from the current task, pause and ask the user instead of guessing.

## Workflow

1. Load the local runtime config and resolve the note roots.
2. Read the source article from the provided URL.
   - Prefer the full article transcript or detailed show notes when they are available.
   - Do not draft article-derived notes from a short teaser alone when the page contains more operational detail deeper in the page.
3. Build an internal concept map with:
   - 5-12 engineering concepts
   - 3-6 non-obvious insights
   - likely company or system
   - likely operating model
   - concrete operational details worth preserving, such as team scope, owned systems, partner functions, named metrics, AI rollout mechanics, and build-vs-buy constraints
4. Search the configured note roots before drafting any file:
   - `note_roots.article`
   - `note_roots.concept`
5. Read the most relevant matching notes and decide `update` vs `create`.
6. Draft or update only the necessary notes.
7. Run tag deduplication against the vault before saving:
   - collect the draft note's candidate tags
   - normalize every candidate tag into English before comparing or writing it
   - search the configured note roots for the same tags and for close semantic variants
   - reuse an existing English vault tag when the meaning is the same and the difference is only wording, hyphenation, singular/plural, abbreviation, Russian/English variant, or word order
   - avoid introducing a new tag when a nearby existing concept note or article note already uses the canonical form
8. Re-check final titles, tags, links, and duplicate risk before saving.

## Search Strategy

- Search both configured note roots:
  - `note_roots.article` for article-derived notes
  - `note_roots.concept` for concept notes
- Use both filename and content search with:
  - company name
  - engineering topic
  - likely lesson titles
  - concept names
  - candidate tags
- Treat semantic overlap as a match even when wording differs.
- Before creating any new file, run one more targeted search with the final proposed title and 2-4 core terms.
- Before saving tags, run one more targeted search for each borderline candidate tag and compare it with tags already used by the closest matching notes.

## Decide Which Notes To Touch

### Lessons Note

- Create at most one lessons note per source article.
- Use it only when the article yields 3 or more portable engineering principles.
- Keep 6-10 lessons maximum.
- Each lesson must be a reusable engineering principle plus a short explanation.
- Do not retell the article chronologically.

### Operating Model Note

- Create or update one note when the article explains how a company or system actually works.
- Cover only real operating details such as team structure, platform architecture, tooling, processes, metrics, workflow, or infrastructure.
- Preserve concrete operating detail when the source gives it, including org scope, team composition, owned systems, cross-functional partners, named metrics, segmentation logic, triage process, AI rollout mechanics, platform constraints, and build-vs-buy reasoning.
- Do not compress several distinct mechanisms into one generic sentence just to keep the note short.
- Prefer a sectioned structure. When the source supports it, cover:
  - `## Команда и зона ответственности`
  - `## Платформы и системы`
  - `## Метрики`
  - `## Приоритизация`
  - `## Сбор обратной связи`
  - `## Внедрение AI`
  - `## Покупать или строить`
- If a standalone lessons note would mostly repeat the operating model, merge the lessons into the operating-model note and add `## Key lessons`.

### Concept Notes

- Create 3-7 concept notes when the article introduces reusable concepts that can apply outside one company.
- Title each concept note with the concept name itself.
- Never use the company name as a concept title.
- Reuse an existing concept note when the meaning matches, even if the phrasing differs.

## Write Notes

- Resolve the local roots through [references/local-config.md](references/local-config.md) before reading or writing files.
- Follow [references/vault-conventions.md](references/vault-conventions.md) for paths, frontmatter, title rules, tags, and markdown formatting.
- Follow [references/language-normalization.md](references/language-normalization.md) for when to keep English terms and when to translate them into Russian.
- Follow [references/update-patterns.md](references/update-patterns.md) for how to append dated updates to existing notes.
- Keep all prose in Russian.
- Keep technical terms in English only inside Russian sentences.
- Use only information that is directly supported by the source article.
- Keep the markdown compact and ready to save without cleanup.
- Keep enough concrete detail that a reader can recover how the operating model actually works without reopening the source.
- Use selective bold emphasis for key mechanisms, labels, or constraints so dense notes stay scannable, but never bold an entire list item.
- Run a final language-normalization pass before saving:
  - translate non-essential English management and business vocabulary into Russian
  - keep English only for canonical framework names, metric names, tool names, code-level terms, established product/discovery method names, or when the English form is the stable industry term
  - if the English term matters, explain it on first mention and then prefer the Russian form afterward
  - rewrite sentences that stack several untranslated English nouns and become hard to read in Russian
- Run a final tag-normalization pass before saving:
  - keep all frontmatter tags strictly in English
  - avoid Cyrillic tags and mixed Russian-English tag variants
  - deduplicate draft tags against the existing vault
  - prefer the exact canonical English tag already used in overlapping notes
  - collapse near-duplicates before writing frontmatter

## Final Output

- Output only files that were created or updated.
- Do not list unchanged notes.
- If the article adds no new knowledge, say so briefly and mention which existing notes already cover it.
