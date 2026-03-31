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
6. Resolve `paths.scratch_root` from that file when it exists. If it is missing, default to `scratch/article-to-obsidian-kb` relative to the current project root.
7. If temporary or staging files are needed at any point in the workflow, write them only under `paths.scratch_root`.
8. Never create repo-local temporary folders under `tmp/` for this skill. Keep temporary artifacts consolidated under `scratch/` so they are easy to inspect and clean.
9. Do not look for the config relative to the current working directory unless the skill directory itself is the current working directory.
10. If `<skill-dir>/config/runtime.local.toml` exists and contains both required note roots, do not ask the user for those paths again.
11. Never commit machine-specific paths, local roots, passwords, or tokens into `SKILL.md`, references, or tracked config files.
12. If the local config is missing and the roots are not already obvious from the current task, pause and ask the user instead of guessing.

## Workflow

1. Load the local runtime config and resolve the note roots.
   - Resolve the scratch staging root too.
   - If `paths.scratch_root` is absent, use `scratch/article-to-obsidian-kb`.
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
   - useful concrete examples, mini-cases, scenarios, before/after transitions, or worked illustrations from the source
   - for each such example, whether it materially improves understanding of a claim, recommendation, anti-pattern, or metric and therefore should survive into the saved note
5. Search the configured note roots before drafting any file:
   - `note_roots.article`
   - `note_roots.concept`
6. Read the most relevant matching notes and decide `update` vs `create`.
7. Draft or update only the necessary notes.
   - Treat the analysis reference as an internal extraction step, not as the final Obsidian format.
   - Map the extracted signal into vault note types instead of copying the analysis headings verbatim.
   - Keep one shared set of vault rules for every source regardless of the chosen route: search the vault first, deduplicate tags against existing notes, apply the same title rules, apply the same language-normalization rules, remove unnecessary anglicisms, and reuse or update existing notes when the meaning already exists.
   - If you need intermediate files, previews, or staged markdown, store them only under the resolved scratch root and never under repo-local `tmp/`.
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
10. When you add or tighten a mechanically checkable note-contract rule in this skill, update the local contract harness in the same change.
   - This applies to rules about frontmatter, tags, headings, spacing, closing sections, wikilinks, language cleanup, emphasis, or preservation of explicitly required examples.
   - Update at least one of:
     - the checker under `scripts/`
     - a broken regression fixture
     - a clean passing fixture
     - a `unittest` that proves the new rule is enforced
   - Do not treat semantic source understanding as testable by this harness. The harness exists to protect deterministic output constraints after note drafting, not to prove that every future source was interpreted perfectly.
11. When this skill's contract layer changes, run the local note-contract tests before finishing the change.
   - Treat changes to any of these files as a required test trigger:
     - `SKILL.md`
     - `references/vault-conventions.md`
     - `references/language-normalization.md`
     - `references/update-patterns.md`
     - `references/test-matrix.md`
     - files under `tests/`
     - the checker under `scripts/`
   - The current required command is:

```bash
python3 -m unittest skills/article-to-obsidian-kb/tests/test_note_contract_regression.py -q
```

   - Do not skip this test run just because the change is "only documentation" if that documentation changes the executable note contract.

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
- Before creating any concept note, run a canonical entity check:
  - compare the candidate against existing concept notes by meaning, not just by title
  - treat translations, word-order changes, narrower phrasing, and cosmetic title improvements as possible duplicates
  - if an existing concept already captures the same durable idea, update that canonical note instead of creating a new file
  - link the source-derived notes, related concepts, and tags to the canonical concept title rather than to a local synonym
 - After drafting the candidate concept list but before filling any concept note, run one more concept-validity pass:
 - drop or merge candidates that are really examples, brands, sources, sections of the article, or one-off formulations rather than durable reusable concepts
 - drop or merge candidates that only restate another candidate from the same run at a different level of specificity
 - prefer the more universal concept node over a brand-specific or source-specific wording
 - only keep candidates that still look worth linking from multiple future notes, not just from the current article
  - drop candidates that are only narrow decision filters for one local scenario in the current source, such as a one-off hiring heuristic, one choice criterion, or one risk check whose best home is a bullet inside the source-derived note
  - if a candidate mainly sharpens one recommendation inside one article and is not likely to earn independent reuse across multiple future notes, merge it back into the source-derived note instead of promoting it to a top-level concept
  - if a candidate mostly restates one pillar of the current source-derived note's own thesis, one subsection that would naturally live inside that note, or one supporting argument that is not yet useful outside this source, keep it embedded in the source-derived note instead of splitting it out
  - if the candidate's only realistic backlinks for now would be the current source-derived note and one or two sibling candidates from the same run, treat that as evidence against promotion unless the concept is clearly durable outside this source
- Before saving `# Связанные заметки`, run one more related-link validation pass:
  - do not add a related note only because the source comes from the same podcast, channel, author, series, or brand shell
  - treat lexical overlap, similar titles, or shared career/AI/startup framing as insufficient on their own
  - add a related source-derived note only when at least two durable topical anchors actually match, such as the same market, the same decision context, the same mechanism, or the same operating constraint
  - if the candidate note comes from a different source id or a different episode, verify that the dominant topic still overlaps before linking it
  - if the candidate would send the reader into a different task or scenario than the current source, drop the link even when some vocabulary overlaps
  - prefer topical identity over surface similarity and treat `same series != same knowledge node` as a hard rule

## Decide Which Notes To Touch

### Lessons Note

- Create at most one lessons note per source.
- Use it only when the source yields 3 or more portable principles, practices, or recommendations that are reusable in other contexts.
- Keep 6-10 lessons maximum.
- Each lesson must be a reusable principle or practical guideline plus a short explanation.
- Do not retell the source chronologically.
- Keep the lessons mutually non-overlapping: each lesson should add a distinct principle or mechanism instead of restating another lesson with slightly different wording.
- If two lesson bullets would mostly say the same thing, merge them into one stronger lesson.
- Title the note with the substantive topic and context, not with the literal word `Lessons`.
- Treat `type: lessons` in frontmatter as the place that encodes the note class, so the title should not repeat it unless the source itself uses `Lessons` as a canonical name.

### Operating Model Note

- Create or update one note only when the source explains how a company or system actually works.
- Cover only real operating details such as team structure, platform architecture, tooling, processes, metrics, workflow, or infrastructure.
- Preserve concrete operating detail when the source gives it, including org scope, team composition, owned systems, cross-functional partners, named metrics, segmentation logic, triage process, AI rollout mechanics, platform constraints, and build-vs-buy reasoning.
- Do not compress several distinct mechanisms into one generic sentence just to keep the note short.
- Keep operating-model sections additive: each section should cover a different part of how the system works instead of repeating the same mechanism under new headings.
- If two operating-model sections would describe the same operating behavior, merge or delete the weaker section.
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
- Prefer `general` over `lessons` when the source keeps substantial value in practical recommendations, supporting cases, tool context, or anti-patterns that would be lost if everything were collapsed into portable principles.
- Use `lessons` only when the source can mostly be reduced to transferable principles and little signal would be lost by omitting separate practice-oriented structure.
- A `general` note may capture a structured high-signal digest of the source, including:
  - main thesis
  - key ideas
  - practical recommendations with attached examples
  - optional frameworks or tools
  - optional anti-patterns
  - immediately applicable takeaways
- Do not mirror empty sections just because the general analysis reference listed them.
- Keep only the sections that are truly supported by the source.
- Keep the sections semantically distinct instead of repeating the same points under new headings.
- Every next block must add net-new knowledge instead of duplicating, inverting, or paraphrasing the previous blocks.
- If two sections would carry the same material, keep the stronger section and delete the weaker one.
- If the source includes concrete examples, scenarios, or mini-cases that materially clarify an idea, recommendation, anti-pattern, or metric, keep at least one such example attached to the relevant point instead of flattening the note into abstract statements only.
- Do not drop a concrete example when removing it would make the point harder to understand, easier to misread, or less actionable.
- Prefer embedding a compact example directly under the relevant thesis or recommendation instead of collecting examples in a detached dump section.
- Use this split when several blocks are present:
  - the first paragraph explains the source and its main problem instead of using a separate `## Суть` heading
  - `## Ключевые тезисы` captures the core ideas and claims, not action steps, but may keep a compact source example when that example is the shortest path to understanding the claim
  - `## Практика` captures reusable action steps and attaches the relevant examples, scenarios, numbers, or illustrations directly to the recommendation they support
  - `## Инструменты и фреймворки` appears only when at least two named tools, methods, or frameworks are independently useful and the block adds clear new information beyond `## Практика`
  - `## Подводные камни и антипаттерны` appears only when the source discusses at least three distinct mistakes or false approaches with their own consequences, not just the inverse wording of `## Практика`
  - `## Что можно применить сразу` captures a short prioritized starter subset and should be omitted if it would just restate `## Практика`
- Title the note with the main topic and context, using the same inverted-pyramid rule as other source-derived notes.

### Concept Notes

- Create 3-7 concept notes when the source introduces reusable concepts that can apply outside one company, one video, or one article.
- Title each concept note with the concept name itself.
- Never use the company name as a concept title.
- Reuse an existing concept note when the meaning matches, even if the phrasing differs.
- Before filling concept notes, re-check the candidate set and make sure each remaining item is truly a reusable concept rather than a branded example, a source-specific phrase, or a detail that belongs only inside the source-derived note.
- Do not promote a source-local decision filter to a concept note unless it is likely to become a reusable node across multiple future notes; if it mainly serves one recommendation in one source, keep it inside the source-derived note.
- Do not turn a strong subsection of the current source-derived note into a top-level concept note unless it has a cleaner reusable boundary than the source note itself and can plausibly attract independent future reuse.
- A good concept note should still feel meaningful if the current source-derived note disappeared; if it mostly reads like a detached paragraph from that one source, keep it embedded there instead.
- Keep the concept definition tight and additive: define the concept once, then let later sections add observations or evidence rather than restating the definition in new words.
- Use two concept-note shapes:
  - `compact` is the default: one tight definition, then `## Additional insights`, then `# Связанные заметки`
  - `expanded` is required when the reader would likely misunderstand the concept without one more explanatory layer
- Promote a concept note from `compact` to `expanded` when at least one of these is true:
  - the concept is primarily comparative or contrastive, such as `X vs Y`
  - the concept is easy to confuse with a nearby existing concept in the same run or the same note cluster
  - the concept is named by an abbreviation, shorthand metric, or compressed label that hides the mechanism
  - the source relies on the concept for a recommendation, but the default one-paragraph definition would not make the recommendation understandable
- In an `expanded` concept note, add only one or two short sections that resolve the ambiguity directly, such as `## Чем отличается`, `## Когда полезен`, `## Почему метрика шумная`, or another equally specific heading.
- Do not expand every concept note by default; use `expanded` only to remove a real comprehension gap.
- When one concept note is expanded mainly to distinguish it from a nearby concept, update the neighboring concept note enough that the distinction is visible from both sides, even if the neighbor remains compact.
- If a source only reinforces an existing concept, prefer updating the existing note instead of creating a near-duplicate concept note.
- Do not let title generation hide a semantic duplicate. A cleaner title is still a duplicate if the underlying concept is the same.

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
- Run one final note-compliance pass before saving any touched note:
  - treat this as a holistic re-check of the entire note, not as a narrow validator for only one recent edit
  - if the note was manually rewritten, merged, structurally reorganized, or otherwise changed late in the run, re-run all relevant note rules after that late edit
  - re-check frontmatter, title consistency, note type, tags, required closing section, wikilinks, language normalization, spacing, bold emphasis, and section-shape rules together
  - do not assume that a late fix for one thing, such as links or frontmatter, preserved the rest of the note formatting
  - do not mark a note complete until it passes this full-note compliance pass in its final saved form
- Run one final regression-sweep pass immediately after the note-compliance pass:
  - treat this as a second large pass with the same coverage as the note-compliance pass, not as a smaller spot check
  - re-run the full final-note checklist again after all late edits, merges, link fixes, language cleanup, and formatting cleanup are done
  - verify the same contract again: frontmatter, title consistency, note type, tags, required closing section, wikilinks, language normalization, spacing, bold emphasis, section-shape rules, and preservation of concrete examples
  - use this second pass specifically to catch regressions introduced by the first compliance fixes themselves, such as restored links that break scanability, translated phrases that drop aliases, or frontmatter repairs that disturb section layout
  - the two passes should be identical in coverage; the second exists for reliability, not because it checks a narrower subset
  - do not mark a note complete until it survives both the note-compliance pass and the regression-sweep pass in its final saved form
- Run a final frontmatter-validation pass before saving:
  - treat frontmatter as required structured metadata, not as optional decoration
  - for every source-derived note, verify that `title`, `source`, `type`, `tags`, and `date` are all present and match the final note state
  - for every concept note, verify that `title`, `type: concept`, and `tags` are present
  - if a note was manually rewritten, merged, or heavily restructured late in the run, re-validate frontmatter after that rewrite instead of trusting the earlier draft
  - do not consider a note complete until its frontmatter passes this check
- Apply the additive-structure rule to every note type:
  - each next section or bullet should add net-new knowledge
  - do not duplicate, invert, or paraphrase the previous section or bullet just to fill structure
  - if two sections or bullets overlap heavily, keep the stronger one and remove or merge the weaker one
- Run a final link-normalization pass before saving:
  - whenever the prose explicitly mentions another existing note, concept, or durable knowledge node, convert that mention into an Obsidian wikilink
  - reuse the exact canonical existing title in the wikilink
  - do not leave plain-text mentions of an existing note when the mention is actually a reference to that note
  - run this pass after the final concept create-or-update decisions are complete, not before
  - for each concept note touched in the current run, do one exact-title sweep through the source-derived note body and replace remaining plain-text or inline-code mentions with wikilinks
  - after the exact-title sweep, run one semantic-alias sweep for each touched concept note whose canonical title is broader, longer, translated, or more explicit than the wording used in the source-derived note
  - build a small alias map from the actual source wording and the final canonical concept title, especially for abbreviations, English source terms, shortened metric names, and compact phrases like `AI evaluation`, `LOC`, `PR throughput`, or `AI-assisted`
  - when that shorter wording clearly refers to the touched concept, replace it with a wikilink that keeps the canonical target and preserves the source wording through an alias
  - if the prose needs a shorter visible label, keep the canonical target and use an alias rather than leaving the mention unlinked
- Run a final related-links dedup pass before saving:
  - treat inline wikilinks in the body as the primary knowledge links
  - remove from `# Связанные заметки` every note that is already mentioned as a wikilink in the body
  - keep in `# Связанные заметки` only net-new navigation links that broaden the reader's path through the cluster
  - if the closing block becomes shorter after deduplication, prefer a shorter non-duplicative block over a longer repetitive one
  - if deduplication removes every useful net-new navigation link, delete the `# Связанные заметки` heading entirely instead of leaving an empty block
  - do not add weak or filler links just to keep the closing block non-empty
  - allow a rare exception only when one especially important hub note needs to be highlighted both inline and in the closing block on purpose
- Run a final language-normalization pass before saving:
  - translate non-essential English management and business vocabulary into Russian
  - keep English only for canonical framework names, metric names, tool names, code-level terms, established product/discovery method names, or when the English form is the stable industry term
  - if the English term matters, explain it on first mention and then prefer the Russian form afterward
  - rewrite sentences that stack several untranslated English nouns and become hard to read in Russian
  - aggressively translate finance, labor-market, and business-operation nouns such as `output`, `white-collar`, `in-house`, `headcount`, `recurring revenue`, and similar phrases when a natural Russian equivalent exists
- Run a final scannability-emphasis pass before saving:
  - after the last manual rewrite, merge, or structural cleanup, re-check that the note still has enough bold emphasis to remain easy to scan
  - restore bold on key mechanisms, labels, contrasts, or the leading clause of dense bullets when that emphasis was lost during rewriting
  - in dense `general`, `lessons`, and `operating-model` notes, prefer bolded leading clauses for high-signal bullets instead of leaving long uniform text blocks
  - do not bold entire list items, whole sentences, or random quoted words just to increase visual weight
  - if a rewrite changed structure from bullets to paragraphs or vice versa, re-balance emphasis for the final structure rather than inheriting the old pattern mechanically
- Run a final example-retention pass before saving:
  - check whether the source contained concrete examples, scenarios, mini-cases, numbers, or worked transitions that materially clarified the main ideas
  - verify that those examples were either preserved in the note or consciously dropped only because they were redundant, trivial, or pure source-local noise
  - if removing an example made a recommendation, anti-pattern, or thesis more abstract, more vague, or harder to operationalize, restore a compact version of that example
  - prefer one strong clarifying example per dense idea over several abstract bullets with no grounding
- Run a final tag-normalization pass before saving:
  - keep all frontmatter tags strictly in English
  - avoid Cyrillic tags and mixed Russian-English tag variants
  - deduplicate draft tags against the existing vault
  - prefer the exact canonical English tag already used in overlapping notes
  - collapse near-duplicates before writing frontmatter
- Run a final output-synthesis pass before replying:
  - build an explicit touched-file ledger from the actual side effects of the run, not from memory
  - keep separate buckets for newly created source-derived notes, newly created concept notes, updated source-derived notes, and updated concept notes
  - derive the final user-facing report only from that ledger
  - if a wrapper skill invoked this workflow, the wrapper must still reuse this skill's final-output contract instead of improvising its own block structure
  - if a file was not actually created or updated in this run, it must not appear in the final report

## Final Output

- Before listing touched files, report the chosen route in one short line:
  - `Route used: engineering`
  - or `Route used: general`
- Add one short reason sentence after the route so the user can understand why that path was chosen.
- Do not write the route or the reason into any Obsidian note.
- Structure the final response in separate blocks in this order:
  - `Созданы` for all new source-derived notes
  - `Новые концепты` for all new concept notes
  - `Обновлены` for updated source-derived notes and updated concept notes
- In `Созданы` and `Новые концепты`, give each file a one-line explanation of what it is about.
- In `Обновлены`, group updated files together and briefly say what changed or what new signal was appended.
- Output only files that were created or updated.
- Do not list unchanged notes.
- If the source adds no new knowledge, say so briefly and mention which existing notes already cover it.
