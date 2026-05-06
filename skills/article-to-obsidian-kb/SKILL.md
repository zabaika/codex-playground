---
name: article-to-obsidian-kb
description: Analyze an article, transcript, or long-form source from a provided URL or text input and convert it into linked Obsidian knowledge-base notes inside user-configured note roots. Use when Codex needs to route a source through an engineering-specific or general analysis pass, search an existing vault for overlapping lessons, operating models, or concepts, update matching notes instead of creating duplicates, and maintain wikilink connections between source-derived notes and concept notes.
---

# Article To Obsidian Kb

## Overview

Turn a source article, transcript, or other long-form text into compact, Russian-language Obsidian notes in a user-configured vault. Store source-derived notes and concept notes in the local roots from the runtime config, while preferring updates over duplicates and keeping the knowledge graph connected with wikilinks. Treat `compact` as a formatting rule, not as permission to collapse concrete mechanisms into vague summaries.

## File Responsibilities

| File | Canonical responsibility |
| --- | --- |
| [SKILL.md](SKILL.md) | Workflow entrypoint: load config, choose route, search vault, decide `update vs create`, apply canonical contracts, run final passes, and format the final user-facing report. |
| [references/vault-conventions.md](references/vault-conventions.md) | Single source of truth for final note contract: frontmatter, titles, tags, language, links, spacing, closing section, and final note-shape rules. |
| [references/update-patterns.md](references/update-patterns.md) | Single source of truth for update behavior: `update vs create`, merge rules, chronology, dated logs, and provenance preservation during rewrites. |
| [references/source-analysis-engineering.md](references/source-analysis-engineering.md) | Extraction contract for engineering-heavy sources. |
| [references/source-analysis-general.md](references/source-analysis-general.md) | Extraction contract for general sources. |
| [references/structured-note-types.md](references/structured-note-types.md) | Canonical contract for explicitly selected structured-note routes and their placement rules. |
| [references/test-matrix.md](references/test-matrix.md) | Documentation for what the harness is expected to catch mechanically and which rule families must stay test-covered. |
| [scripts/check_note_contract.py](scripts/check_note_contract.py) | Executable checker for mechanically verifiable note rules. |
| [scripts/write_structured_note.py](scripts/write_structured_note.py) | Explicit structured-note writer used when the skill is invoked in `structured` mode. |
| [templates/council-verdict.md.tmpl](templates/council-verdict.md.tmpl) | Mechanical render-layout for the `council-verdict` structured note. |
| [tests/test_note_contract_regression.py](tests/test_note_contract_regression.py) | Regression coverage for the executable contract. |

## Local Runtime Config

1. Resolve the skill directory from the location of this `SKILL.md`.
2. Read `<skill-dir>/config/runtime.local.toml` when it exists.
3. Use [config/runtime.example.toml](config/runtime.example.toml) as the canonical reference for config keys, defaults, and local-runtime notes.
4. Treat that repo copy as the single editable local config. If an installed Codex copy exists under `~/.codex/skills`, it should point to the same file rather than keeping a second divergent copy.
5. Use `note_roots.article` and `note_roots.concept` from that file for all `source`-mode search and save operations.
6. When `structured` mode is explicitly selected, resolve the destination for that structured type according to [references/structured-note-types.md](references/structured-note-types.md) instead of reusing the normal source-mode note roots.
7. Resolve `paths.scratch_root` from that file when it exists. If it is missing, default to `<project_root>/scratch/article-to-obsidian-kb`.
8. Resolve `paths.project_root` from that file when it exists. Use it as the canonical base for project-local relative paths such as `paths.scratch_root` and `paths.kb_index_config`. If `CODEX_PLAYGROUND_PROJECT_ROOT` is set, prefer it over the config key.
9. Resolve `paths.kb_index_config` from that file when it exists. Use it as the canonical entry point to `kb-index` for indexed retrieval.
10. If temporary or staging files are needed at any point in the workflow, write them only under `paths.scratch_root`.
11. Never create repo-local temporary folders under `tmp/` for this skill. Keep temporary artifacts consolidated under `scratch/` so they are easy to inspect and clean.
12. Do not look for the config or resolve project-local relative paths relative to the current working directory.
13. If `<skill-dir>/config/runtime.local.toml` exists and contains both required note roots, do not ask the user for those paths again.
14. Never commit machine-specific paths, local roots, passwords, or tokens into `SKILL.md`, references, or tracked config files.
15. If the local config is missing and the roots are not already obvious from the current task, pause and ask the user instead of guessing.

## Workflow

Default mode: `source`.
Use the numbered workflow below for `source` mode unless the caller explicitly selected `structured`.
When `structured` mode is explicitly selected, route by [references/structured-note-types.md](references/structured-note-types.md) and use the dedicated structured-note writer instead of the source-analysis path below.

1. Load the local runtime config and resolve the note roots.
   - Resolve the scratch staging root too.
   - Resolve `paths.kb_index_config` too when it exists.
   - If `paths.scratch_root` is absent, use `<project_root>/scratch/article-to-obsidian-kb`.
   - Resolve `paths.scratch_root` and `paths.kb_index_config` relative to the resolved project root, never relative to the shell cwd.
   - If one of those paths is relative and project root still cannot be resolved, fail fast instead of guessing.
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
5. Search the configured note roots before drafting any file.
   - If `paths.kb_index_config` is present and the index is healthy, use indexed retrieval first through the canonical external CLI.
   - Derive `KB_INDEX_ROOT` as the parent directory of the directory that contains `paths.kb_index_config`.
   - Treat the workflow as three separate access modes:
     - `discovery`: use the index only
     - `metadata inspection`: use the index first
     - `full-content reading`: read files directly only after the shortlist is already chosen
   - When the index is healthy, do not use `rg`, `find`, `fd`, or broad direct filesystem scans for:
     - note discovery
     - known-note lookup
     - related-note discovery
     - concept discovery
     - tag reuse or tag existence checks
   - In the normal path, filesystem access is allowed only for:
     - reading config files
     - opening already shortlisted notes by known `path`
     - final write, merge, and post-write verification steps

```bash
[KB_INDEX_ROOT]/bin/search_kb --config-path "[KB_INDEX_CONFIG]" --json "[QUERY]"
```

   - Treat `search_kb` as a note-level retrieval step. The result is a shortlist of whole notes, not chunk hits.
   - Each result should be interpreted through `path`, `title`, `score`, `tags`, `lead_summary`, `headings`, and `snippet`.
   - Treat those indexed fields as the canonical metadata inspection layer for existing notes while the index is healthy.
   - Do not open files just to inspect note metadata such as `tags`, `title`, `headings`, `note_type`, `links_out`, or `lead_summary` when the same facts are already available from the index.
   - For tag discovery and tag reuse checks, use the canonical tag-discovery CLI instead of `rg`:

```bash
[KB_INDEX_ROOT]/bin/list_kb_tags --config-path "[KB_INDEX_CONFIG]" --json
[KB_INDEX_ROOT]/bin/list_kb_tags --config-path "[KB_INDEX_CONFIG]" --tag "[EXACT_TAG]" --json
[KB_INDEX_ROOT]/bin/list_kb_tags --config-path "[KB_INDEX_CONFIG]" --prefix "[TAG_PREFIX]" --json
```

   - Use `list_kb_tags --tag` when the workflow wants to know whether one exact tag already exists and which notes already use it.
   - Use `list_kb_tags --prefix` when the workflow wants to inspect nearby existing tags before deciding whether a new tag is justified.
   - Use unfiltered `list_kb_tags` only when a broader tag inventory is genuinely needed; do not default to it when an exact or prefix check is enough.
   - Let the index-configured `retrieval.default_limit` control the default shortlist size. Override with `--limit` only when the current source clearly needs a broader pass.
   - When the workflow already knows or strongly suspects the exact note title, prefer an index-backed title lookup instead of a filesystem name scan:

```bash
[KB_INDEX_ROOT]/bin/search_kb --config-path "[KB_INDEX_CONFIG]" --mode title-first --note-type concept --json "[KNOWN_NOTE_TITLE]"
```

   - Build retrieval queries in a minimal pass set instead of relying on one vague search string:
     - source-derived article candidate pass:
       - first query: final proposed article title
       - second query: `main topic + context`
       - optional third query only when the first two passes are weak or divergent:
         - `company/system/person name + mechanism`
         - or `2-4` core terms from the extraction
     - concept candidate pass:
       - first query: exact concept title candidate
       - optional second query only when needed:
         - `concept + context`
         - or alternate Russian/English wording when both are plausible
   - Prefer `2` article queries and `1` concept query by default. Escalate to an extra query only when the current shortlist is weak, noisy, or contradictory.
   - Stop early when one note is clearly dominant and the nearby shortlist is coherent enough to support a confident `update vs create` check.
   - Merge the returned notes into one candidate pool, deduplicate by `path`, and keep the strongest score per note.
   - Use the index to shortlist candidate notes from both `note_roots.article` and `note_roots.concept`.
   - Only after that open the most relevant files directly for full-text verification or merge work.
   - In the normal path, read the full text of only the top `3-5` candidate notes per decision surface:
     - top article-like candidates when deciding whether to update or create the source-derived note
     - top concept-like candidates when deciding whether to update or create each concept note
   - Reuse the same indexed candidate pool for later tag, concept, related-note, and wikilink decisions whenever it already covers the local topic well enough.
   - Prefer index-backed metadata inspection over direct file inspection for those later decisions.
   - If indexed retrieval returns weak or obviously off-topic notes, broaden once with one extra query or a higher `--limit`. Do not jump straight to a full vault scan.
   - If the index is unavailable, broken, or clearly stale, fall back to direct filename/content search only inside `note_roots.article` and `note_roots.concept`.
   - Even in fallback mode, never broaden discovery to unrelated roots or to the entire home directory.
6. Read the most relevant matching notes and decide `update` vs `create`.
   - Treat high-confidence matches as candidates for update, not as automatic updates. Confirm by reading the full note.
   - Bias toward `update` when the existing note matches the same durable topic, mechanism, and context, even if the new source adds better wording or stronger evidence.
   - Bias toward `create` when the overlap is only lexical, when the existing note solves a different task, or when merging would produce a muddy topic boundary.
   - For concept notes, prefer updating an existing reusable node rather than spawning a near-duplicate with slightly different phrasing.
7. Draft or update only the necessary notes.
   - Treat the analysis reference as an internal extraction step, not as the final Obsidian format.
   - Map the extracted signal into vault note types instead of copying the analysis headings verbatim.
   - Apply the canonical final-note contract from [references/vault-conventions.md](references/vault-conventions.md) instead of restating those rules locally.
   - Apply the canonical update contract from [references/update-patterns.md](references/update-patterns.md) whenever an existing note is touched.
   - If you need intermediate files, previews, or staged markdown, store them only under the resolved scratch root and never under repo-local `tmp/`.
8. Resolve source dates before final frontmatter normalization.
   - Build a source-date map for every source that materially contributes to the saved note.
   - For single-source notes, verify the source date before assigning `frontmatter.date`.
   - For multi-source notes, write dated bullets in `## Evidence` first, then derive `frontmatter.date` from the newest dated evidence source instead of setting it from memory.
   - If a source date is unclear, verify it from the source page before saving rather than guessing.
9. Before saving, run the canonical tag pass from [references/vault-conventions.md](references/vault-conventions.md).
10. Re-check final titles, tags, links, duplicate risk, and chronology against the canonical contracts before saving.
   - Re-run the canonical closing-section deduplication pass after inline wikilinks are finalized.
11. After each destination write, run the canonical post-write verification from [references/update-patterns.md](references/update-patterns.md) before continuing with more checks, searches, or reporting.
12. After all destination writes are complete, refresh `kb-index` once when and only when all of these are true:
   - the run actually created or updated at least one note
   - `paths.kb_index_config` is present
   - the run is not a dry-run or analysis-only pass
   - Use the canonical external CLI and run it only once per skill run:

```bash
[KB_INDEX_ROOT]/bin/update_kb_index --config-path "[KB_INDEX_CONFIG]"
```

   - Treat this as a best-effort post-write sync, not as part of note generation itself.
   - If the index refresh fails, do not roll back already-saved notes. Report the failure briefly in the final response and let the scheduled auto-update recover later.
13. When you add or tighten a mechanically checkable note-contract rule in this skill, update the local contract harness in the same change.
   - This applies to rules about frontmatter, tags, headings, spacing, closing sections, wikilinks, language cleanup, emphasis, or preservation of explicitly required examples.
   - Update at least one of:
     - the checker under `scripts/`
     - a broken regression fixture
     - a clean passing fixture
     - a `unittest` that proves the new rule is enforced
   - Do not treat semantic source understanding as testable by this harness. The harness exists to protect deterministic output constraints after note drafting, not to prove that every future source was interpreted perfectly.
14. When this skill's contract layer changes, run the local note-contract tests before finishing the change.
   - Treat changes to any of these files as a required test trigger:
     - `SKILL.md`
     - `config/runtime.example.toml`
     - `references/structured-note-types.md`
     - `references/vault-conventions.md`
     - `references/language-normalization.md`
     - `references/update-patterns.md`
     - `references/test-matrix.md`
     - `templates/council-verdict.md.tmpl`
     - files under `tests/`
     - `scripts/check_note_contract.py`
     - `scripts/write_structured_note.py`
   - The current required command is:

```bash
python3 -m unittest discover -s skills/article-to-obsidian-kb/tests -q
```

   - Do not skip this test run just because the change is "only documentation" if that documentation changes the executable note contract.

## Content Routing

- Treat `source` as the default workflow mode of this skill.
- Switch to `structured` only when the caller explicitly selected that mode.
- In `structured` mode, load [references/structured-note-types.md](references/structured-note-types.md) and route by `type`.
- Do not auto-detect `structured` mode just because an input looks like JSON.

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

- Treat `kb-index` as the canonical retrieval layer for every search-like step in the workflow, not only the first `update vs create` pass.
- Treat `kb-index` as the canonical metadata-inspection layer for existing notes too, as long as the index is healthy.
- Treat `list_kb_tags` as the canonical index-backed path for tag existence checks, tag reuse checks, and nearby-tag discovery.
- Search coverage must still span both configured note roots, but do that through indexed retrieval first, not by broad direct filesystem scanning.
- Treat `kb-index` as the canonical first-pass retrieval layer and direct file reads as the second pass.
- Direct file reads are for full note content only after the shortlist is known, not for vault-wide discovery and not for metadata checks that the index already exposes.
- Keep the detailed retrieval plan, query sequencing, and stop conditions in workflow step `5` above.
- Keep canonical concept-creation, closing-link, and update semantics in:
  - [references/vault-conventions.md](references/vault-conventions.md)
  - [references/update-patterns.md](references/update-patterns.md)
- Reuse the same indexed candidate pool for tag, concept, related-note, and wikilink decisions whenever it already covers the local topic well enough.
- Only broaden retrieval when the current shortlist is weak, noisy, contradictory, or insufficient for a confident decision.
- Do not expand into a broad vault scan unless the index is unavailable, broken, or clearly stale.

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
- Preserve surviving `source` provenance when converting, renaming, or restructuring any existing note into `lessons`; normalization is not a reason to drop old source links.

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
- Preserve surviving `source` provenance when converting, renaming, or restructuring any existing note into `operating-model`; do not reset frontmatter provenance during a structural rewrite.

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
  - for any non-`concept` note with `## Практика` or another clearly applied section, apply the checklist materialization rule from [references/vault-conventions.md](references/vault-conventions.md): materialize checklists only when they preserve real decision structure, and keep them non-duplicative with the surrounding applied prose
- Title the note with the main topic and context, using the same inverted-pyramid rule as other source-derived notes.
- Preserve surviving `source` provenance when converting, renaming, or restructuring any existing note into `general`; keep old source links even when the body and title are substantially rewritten.

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
- `source` is allowed in concept-note frontmatter when it preserves useful provenance from the originating article, transcript, migrated legacy note, or a later reinforcing source.
- When upgrading an older concept note into the current format, keep its surviving `source` field instead of stripping it only because `type: concept` does not require `source`.
- More generally, when any legacy note is migrated into the current note schema, preserve surviving `source` provenance regardless of the final note type.

## Write Notes

- Resolve the local roots through `<skill-dir>/config/runtime.local.toml` before reading or writing files, using [config/runtime.example.toml](config/runtime.example.toml) as the canonical key reference.
- Apply the canonical final-note contract from [references/vault-conventions.md](references/vault-conventions.md).
- Apply the canonical language rules from [references/language-normalization.md](references/language-normalization.md).
- Apply the canonical update contract from [references/update-patterns.md](references/update-patterns.md) whenever an existing note is touched.
- Treat verbs like `append`, `merge`, `normalize`, `clean up`, and `update` as insufficient on their own when the operation has more than one plausible interpretation.
- When the intended behavior depends on exact position or ordering, define the insertion point and order explicitly and follow the stricter rule from the references instead of improvising.
- When migrating a legacy note into the current schema, preserve surviving frontmatter provenance such as `source` across the rewrite regardless of whether the final note is source-derived or `concept`.
- Use the chosen analysis reference only to extract signal from the source:
  - [references/source-analysis-engineering.md](references/source-analysis-engineering.md)
  - [references/source-analysis-general.md](references/source-analysis-general.md)
- Before finalizing the note structure, run a specificity pass on the draft:
  - re-check whether the main lessons or applied sections have been flattened into generic claims during summarization
  - when the source contains concrete anchors such as examples, numbers, pipelines, decision rules, explicit limitations, or concrete failure mechanisms, preserve them in `## Ключевые тезисы`, `## Практика`, `## Подводные камни и антипаттерны`, or a similar section if they materially improve actionability
  - treat this as an early synthesis guardrail, not only as a late cleanup step
- Run one final note-compliance pass before saving any touched note:
  - apply the full final-note contract from [references/vault-conventions.md](references/vault-conventions.md) to the final saved artifact
  - this pass is mandatory for every major rule family in that canonical contract, not just for the family you touched most recently
  - if the note was manually rewritten, merged, or changed late in the run, re-run the full contract after that late edit
  - if you updated an existing note, run this pass against the whole merged note rather than only the latest fragment
  - re-check the main note body for source-scaffolding and rewrite source-first sentences into idea-first knowledge statements whenever meaning is preserved; this includes source-type phrasings such as `в статье`, `в подкасте`, or `в транскрипте`; keep dated provenance sections such as `Evidence`, `Additional insights`, and `Observed practices` exempt from that cleanup
  - if the note is non-`concept` and has `## Практика` or another clearly applied section, re-check the checklist rule from [references/vault-conventions.md](references/vault-conventions.md): checklists should preserve real decision structure, and surrounding applied prose should not duplicate the same actions or criteria
  - re-check whether the main lessons or applied sections have been flattened into generic claims; when the source contains concrete anchors such as examples, numbers, pipelines, decision rules, explicit limitations, or concrete failure mechanisms, preserve them in the note if they materially improve actionability
  - a strong practical note should usually contain at least some source-native specificity, not only abstract restatements
  - re-check role and artifact terminology where the source contains neighboring English terms such as product, product manager, design, or platform; keep the Russian wording semantically separated instead of collapsing distinct roles into one overloaded noun
  - do not mark a note complete until it passes this full-note contract in its final saved form
- Run one final regression-sweep pass immediately after the note-compliance pass:
  - re-run the same full final-note contract a second time with identical coverage after all fixes are done
  - if the note already existed before the current run, apply the second pass to the whole saved note again
  - do not mark a note complete until it survives both whole-note passes
- Run one final update-contract pass for every touched existing note:
  - apply the full update contract from [references/update-patterns.md](references/update-patterns.md) after the note body is already final
  - verify chronology and provenance on the final saved artifact, not on an earlier draft
- Keep these high-risk reminders visible even though the canonical rules live in the reference docs:
  - preserve surviving `source` provenance when rewriting, merging, or renaming notes
  - keep tags within `1-3` total and default to the smallest accurate tag set
  - do not allow `ai` or `management` back into final frontmatter
  - do not let late edits reintroduce known high-risk regressions
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
  - `Новые теги` for genuinely new frontmatter tags introduced in this run
- In `Созданы` and `Новые концепты`, give each file a one-line explanation of what it is about.
- In `Обновлены`, group updated files together and briefly say what changed or what new signal was appended.
- If this run removed, replaced, or materially pruned links from `# Связанные заметки` in any touched note, explicitly mention that in the relevant line under `Обновлены` when the change altered the note's meaningful graph.
- For each such note, state which links were removed or replaced and give one short reason when the change was substantive, for example weaker than new nearby links or deliberate narrowing of the closing section.
- Do not require enumerating removals that happened only because a link was already present inline and the closing section was deduplicated mechanically.
- Do not hide substantive link-pruning side effects behind generic phrases like `cleaned up links` when specific removed links materially changed the note's graph.
- In `Новые теги`, list only tags that did not already exist in the vault before this run and name the created or updated notes where each new tag was introduced.
- If the run did not create any new tags, still include `Новые теги` and say explicitly that no new tags were introduced.
- After `Новые теги`, always add a short final index-sync block when the run attempted the post-write `kb-index` refresh and it succeeded.
- Format that success block as two flat bullets:
  - `индекс был обновлён успешно`
  - `в индекс вошли N изменённые заметки`
- Use the actual `updated_notes_count` returned by the canonical `update_kb_index` command for `N`.
- Treat that `updated_notes_count` as the canonical source of truth for the index-sync report.
- Do not reconcile it against the notes the assistant believes it touched in this run or against recent filesystem mtimes.
- Assume the index may have picked up concurrent or manual vault edits too.
- Investigate a mismatch only when the user explicitly asks for that investigation.
- Do not mention index errors in the normal success case.
- Mention an index-refresh problem only if the post-write sync actually failed, and keep that failure note brief.
- Output only files that were created or updated.
- Do not list unchanged notes.
- If the source adds no new knowledge, say so briefly and mention which existing notes already cover it.
