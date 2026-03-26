---
name: article-to-obsidian-kb
description: Analyze an article, transcript, or long-form source from a provided URL or text input and convert it into linked Obsidian knowledge-base notes inside user-configured note roots. Use when Codex needs to route a source through an engineering-specific or general analysis pass, search an existing vault for overlapping lessons, operating models, or concepts, update matching notes instead of creating duplicates, and maintain wikilink connections between source-derived notes and concept notes.
---

# Article To Obsidian Kb

## Overview

Turn a source article, transcript, or other long-form text into compact, Russian-language Obsidian notes in a user-configured vault. Store source-derived notes and concept notes in the local roots from the runtime config, while preferring updates over duplicates and keeping the knowledge graph connected with wikilinks. Treat `compact` as a formatting rule, not as permission to collapse concrete mechanisms into vague summaries.

## Local Runtime Config

1. Load [references/local-config.md](references/local-config.md) before touching the vault.
2. Resolve the skill directory from the location of this `SKILL.md`.
3. Read `<skill-dir>/config/runtime.local.toml` when it exists.
4. Treat that repo copy as the single editable local config. If an installed Codex copy exists under `~/.codex/skills`, it should point to the same file rather than keeping a second divergent copy.
5. Use `note_roots.article` and `note_roots.concept` from that file for all search and save operations.
6. Do not look for the config relative to the current working directory unless the skill directory itself is the current working directory.
7. If `<skill-dir>/config/runtime.local.toml` exists and contains both required note roots, do not ask the user for those paths again.
8. Never commit machine-specific paths, local roots, passwords, or tokens into `SKILL.md`, references, or tracked config files.
9. If the local config is missing and the roots are not already obvious from the current task, pause and ask the user instead of guessing.

## Workflow

1. Load the local runtime config and resolve the note roots.
2. Read the source from the provided URL or supplied text.
   - Prefer the full article body, transcript, or detailed show notes when they are available.
   - Do not draft source-derived notes from a short teaser alone when the page contains more operational detail deeper in the page.
3. Route the source before extracting notes.
   - When a local source file already exists, prefer the local helper first:

```bash
python3 scripts/detect_source_route.py --source-file "[SOURCE_FILE]"
```

   - As soon as the route is chosen, print it to the screen in this exact format:
     - `Route used: engineering`
     - or `Route used: general`
   - Immediately print one short reason line after that:
     - `Route reason: ...`
   - Load [references/source-analysis-engineering.md](references/source-analysis-engineering.md) when the source is primarily about an engineering organization, platform, delivery system, developer workflow, company operating model, or other material that can plausibly produce `operating-model` notes or engineering-heavy `lessons`.
   - Load [references/source-analysis-general.md](references/source-analysis-general.md) when the source is primarily broad expert content, business analysis, management thinking, career advice, productivity discussion, or another article or transcript that does not naturally map to an engineering operating model.
   - If the source is ambiguous, choose the closer path and do not load both references unless the source genuinely mixes both modes.
4. Build an internal extraction from the chosen analysis path with:
   - 5-12 reusable concepts, ideas, methods, or mechanisms
   - 3-6 non-obvious insights
   - likely company, system, speaker context, or domain
   - whether a real operating model is present
   - concrete details worth preserving, such as team scope, owned systems, partner functions, named metrics, examples, AI rollout mechanics, build-vs-buy constraints, recommendations, and anti-patterns
5. Search the configured note roots before drafting any file:
   - `note_roots.article`
   - `note_roots.concept`
6. Read the most relevant matching notes and decide `update` vs `create`.
7. Draft or update only the necessary notes.
   - Treat the analysis reference as an internal extraction step, not as the final Obsidian format.
   - Map the extracted signal into vault note types instead of copying the analysis headings verbatim.
   - Keep one shared set of vault rules for every source regardless of the chosen route: search the vault first, deduplicate tags against existing notes, apply the same title rules, apply the same language-normalization rules, remove unnecessary anglicisms, and reuse or update existing notes when the meaning already exists.
8. Run tag deduplication against the vault before saving:
   - collect the draft note's candidate tags
   - normalize every candidate tag into English before comparing or writing it
   - search the configured note roots for the same tags and for close semantic variants
   - reuse an existing English vault tag when the meaning is the same and the difference is only wording, hyphenation, singular/plural, abbreviation, Russian/English variant, or word order
   - avoid introducing a new tag when a nearby existing concept note or source-derived note already uses the canonical form
   - treat creation of a brand-new tag as a last resort
   - create a new tag only when you are fully confident that no existing vault tag matches the meaning closely enough and that the new tag is clearly necessary for future retrieval
   - if you are not fully confident, choose the closest existing canonical vault tag instead of inventing a new one
9. Re-check final titles, tags, links, and duplicate risk before saving.

## Content Routing

- Choose the engineering path when the source contains at least two of the following:
  - a concrete company or system context
  - real team, platform, process, metric, tooling, or infrastructure details
  - portable engineering lessons
  - reusable concepts for engineering or platform work
- Choose the general transcript path when the source is mostly:
  - expert commentary or teaching
  - business, management, productivity, career, or communication advice
  - examples, cases, and recommendations without a concrete operating model
  - broad analysis where the most useful outputs are concepts, action points, anti-patterns, or lessons rather than an engineering system description
- Routing changes only the extraction method. It does not change the Obsidian rules for title formation, English-only tags, language cleanup, vault search, deduplication, note updates, or final markdown structure.
- Do not force an `operating-model` note when the source does not actually explain how a company or system works.
- Do not mirror the general-analysis sections one to one in the saved note. Use them to decide whether the vault needs:
  - a `lessons` note
  - a `general` note
  - one or more `concept` notes
  - updates to existing notes only
- If the source has signal but not enough reusable knowledge for a new note, update nearby existing notes and stop there.

## Search Strategy

- Search both configured note roots:
  - `note_roots.article` for source-derived notes
  - `note_roots.concept` for concept notes
- Use both filename and content search with:
  - company name
  - main topic
  - likely lesson titles
  - concept names
  - candidate tags
- Treat semantic overlap as a match even when wording differs.
- Before creating any new file, run one more targeted search with the final proposed title and 2-4 core terms.
- Before saving tags, run one more targeted search for each borderline candidate tag and compare it with tags already used by the closest matching notes.

## Decide Which Notes To Touch

### Lessons Note

- Create at most one lessons note per source.
- Use it only when the source yields 3 or more portable principles, practices, or recommendations that are reusable in other contexts.
- Keep 6-10 lessons maximum.
- Each lesson must be a reusable principle or practical guideline plus a short explanation.
- Do not retell the source chronologically.
- Title the note with the substantive topic and context, not with the literal word `Lessons`.
- Treat `type: lessons` in frontmatter as the place that encodes the note class, so the title should not repeat it unless the source itself uses `Lessons` as a canonical name.

### Operating Model Note

- Create or update one note only when the source explains how a company or system actually works.
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

### General Note

- Create at most one `general` note per source.
- Use it only when the chosen route is `general` and the source has useful signal but does not naturally fit a `lessons` note or an `operating-model` note.
- A `general` note may capture a structured high-signal digest of the source, including:
  - main thesis
  - key ideas
  - practical recommendations
  - cases or examples
  - frameworks or tools
  - anti-patterns
  - immediately applicable takeaways
- Do not mirror empty sections just because the general analysis reference listed them.
- Keep only the sections that are truly supported by the source.
- Title the note with the main topic and context, using the same inverted-pyramid rule as other source-derived notes.

### Concept Notes

- Create 3-7 concept notes when the source introduces reusable concepts that can apply outside one company, one video, or one article.
- Title each concept note with the concept name itself.
- Never use the company name as a concept title.
- Reuse an existing concept note when the meaning matches, even if the phrasing differs.

## Write Notes

- Resolve the local roots through [references/local-config.md](references/local-config.md) before reading or writing files.
- Follow [references/vault-conventions.md](references/vault-conventions.md) for paths, frontmatter, title rules, tags, and markdown formatting.
- Follow [references/language-normalization.md](references/language-normalization.md) for when to keep English terms and when to translate them into Russian.
- Follow [references/update-patterns.md](references/update-patterns.md) for how to append dated updates to existing notes.
- Use the chosen analysis reference only to extract signal from the source:
  - [references/source-analysis-engineering.md](references/source-analysis-engineering.md)
  - [references/source-analysis-general.md](references/source-analysis-general.md)
- Keep all prose in Russian.
- Keep technical terms in English only inside Russian sentences.
- Use only information that is directly supported by the source.
- Keep the markdown compact and ready to save without cleanup.
- Keep enough concrete detail that a reader can recover how the operating model actually works without reopening the source.
- Use selective bold emphasis for key mechanisms, labels, or constraints so dense notes stay scannable, but never bold an entire list item.
- Run a final language-normalization pass before saving:
  - translate non-essential English management and business vocabulary into Russian
  - keep English only for canonical framework names, metric names, tool names, code-level terms, established product/discovery method names, or when the English form is the stable industry term
  - if the English term matters, explain it on first mention and then prefer the Russian form afterward
  - rewrite sentences that stack several untranslated English nouns and become hard to read in Russian
  - aggressively translate finance, labor-market, and business-operation nouns such as `output`, `white-collar`, `in-house`, `headcount`, `recurring revenue`, and similar phrases when a natural Russian equivalent exists
- Run a final tag-normalization pass before saving:
  - keep all frontmatter tags strictly in English
  - avoid Cyrillic tags and mixed Russian-English tag variants
  - deduplicate draft tags against the existing vault
  - prefer the exact canonical English tag already used in overlapping notes
  - collapse near-duplicates before writing frontmatter

## Final Output

- Before listing touched files, report the chosen route in one short line:
  - `Route used: engineering`
  - or `Route used: general`
- Add one short reason sentence after the route so the user can understand why that path was chosen.
- Do not write the route or the reason into any Obsidian note.
- Output only files that were created or updated.
- Do not list unchanged notes.
- If the source adds no new knowledge, say so briefly and mention which existing notes already cover it.
