# Vault Conventions

This file is the single source of truth for the final note contract used by `article-to-obsidian-kb`.
Canonical section-heading strings live in [config/note_schema.yaml](../config/note_schema.yaml). Treat those `headings.*` values as schema labels, not editable prose.

## Scope

- These conventions apply to every saved note regardless of whether the source was processed through the engineering analysis path or the general analysis path.
- Resolve note roots from `config/runtime.local.toml`, using `config/runtime.example.toml` as the canonical reference for key names and defaults.
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

- For single-source source-derived notes, set `date` to the publication year of that source.
- For multi-source source-derived notes, set `date` to the year of the newest source that materially contributes to the saved note.
- Do not use `date` as a last-edited marker for the agent run itself; chronology of later reinforcement belongs in schema-defined dated sections such as `headings.evidence` or `headings.additional_insights`.

### Concept notes

```yaml
---
title: <concept name>
source:
  - <url>
type: concept
tags:
  - <tag>
---
```

Frontmatter is part of the note schema, not optional decoration.

- After any late manual rewrite, merge, or structural cleanup, run one full-note compliance check instead of validating only the field or section you just touched.
- If you updated an already existing note, run that compliance check against the final merged note as a whole, not only against the newly appended fragment.
- Treat touched legacy notes as upgrade candidates: once a note is open for editing, old violations in untouched sections should be cleaned instead of being grandfathered in.
- Re-check frontmatter, links, required closing section, spacing, emphasis, and section shape together on the final note.
- Treat “I only changed one small thing” as a common source of regressions in the rest of the note.
- After that first full-note compliance check, run one more full regression sweep with the same coverage again.
- For pre-existing notes, that second sweep must still target the whole saved note, not the latest delta.
- The second sweep is not a narrower validator; it repeats the same final note contract for reliability after the compliance fixes themselves.
- Do not stop after frontmatter, links, or language are repaired once; re-check the whole note again because late fixes often regress another already-fixed rule.
- Validate required frontmatter fields after the final body rewrite, not only after the first draft.
- When a source-derived note is manually reworked, merged with an older note, or restructured late in the run, re-check that `title`, `source`, `type`, `tags`, and `date` still exist and still match the saved note.
- When a new canonical source-derived note absorbs, renames, or replaces an older source-derived note, carry forward the older note's surviving frontmatter provenance into the new one instead of resetting it from scratch.
- Merge the absorbed note's `source` values into the new note's `source` list and keep any still-valid canonical tags from the absorbed note rather than silently dropping them.
- In `concept` notes, `source` is allowed when the concept note preserves useful provenance from an originating article, transcript, migrated legacy note, or later reinforcing source.
- When a concept note is manually reworked, re-check that `title`, `type: concept`, and `tags` are still present, and preserve `source` when the note already has it.
- When migrating or restructuring any legacy note into the current format, preserve surviving `source` provenance regardless of note type; do not drop `source` just because the note became `concept`, `lessons`, `general`, or `operating-model`.
- When a touched legacy note already contains a valid `source` field, carry it forward into the rewritten frontmatter instead of silently removing it during normalization.
- Update-time decisions about whether a new reinforcing source should mutate frontmatter `source` are owned by [update-patterns.md](update-patterns.md).
- Do not save a note with partial frontmatter just because the body already looks finished.

## Tags

- Generate tags only from the source or concept context.
- Keep all tags strictly in English.
- Use ASCII lowercase kebab-case for new tags unless an established existing vault tag already uses another English form.
- Prefer short, searchable tags.
- Avoid template tags such as `concept`, `engineering`, `article`.
- Avoid Cyrillic tags and mixed Russian-English tag variants.
- Avoid synonyms and near-duplicates.
- Normalize terminology when multiple variants exist.
- Deduplicate tags against the existing vault before saving the note.
- Translate Russian candidate tags into English before the vault deduplication check.
- Treat indexed tag discovery as the canonical vault check path when `kb-index` is healthy:
  - use exact tag lookup first
  - then prefix or nearby-tag inspection when exact lookup is inconclusive
  - do not fall back to `rg` over the vault for tag reuse discovery when the index already exposes the same information
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
- Treat `career`, `job-search`, and `hiring` as three narrow semantics, not as interchangeable umbrella tags:
  - `career` = long-horizon professional trajectory, growth, grade logic, role value, and career decisions outside one active search cycle
  - `job-search` = active candidate-side search funnel, including applications, networking for a live search, resume, interview preparation, offer handling, and search-process risks
  - `hiring` = employer-side hiring logic, evaluation mechanics, hiring bar, interview design, staffing signals, and demand-side labor-market filters
- Do not assign more than one of `career`, `job-search`, and `hiring` by default.
- Allow `career + hiring` only when the note genuinely combines long-term role-value logic with employer-side hiring thresholds or market filters.
- Allow `hiring + job-search` only when the note genuinely bridges employer-side evaluation mechanics and candidate-side preparation for that same mechanism.
- Do not use `career + job-search`; if both seem plausible, choose the narrower dominant meaning instead of keeping both.
- Never assign `career + hiring + job-search` to one note.
- When one of these tags is proposed, compare the note against nearby vault notes already using the same family and keep the narrowest accurate tag set instead of inheriting a broad umbrella label.
- Treat `ai`, `ai-adoption`, `ai-tools`, `ai-agents`, `ai-governance`, and `prompts` as a constrained AI tag family rather than as interchangeable labels.
- Treat `ai` as an over-broad umbrella member of that family, not as a default frontmatter tag.
- Do not use `ai` at all; replace it with one or more narrower AI-family tags.
- Default to the narrowest single AI-family tag.
- Allow multiple narrower AI-family tags only when each one adds an independently useful retrieval angle.
- Prefer:
  - `ai-adoption` only for organizational rollout, operating model, enablement, and transition of real work onto AI at team or company level
  - `ai-tools` for models, tooling, inference stacks, RAG, embeddings, model comparison, and tool-level usage patterns
  - `ai-agents` for agentic workflows, delegation, multi-step autonomous execution, and long-context agent operating patterns
  - `ai-governance` for trust, provenance, verification, human-in-the-loop control, usage guardrails, and accountability around AI outputs or agent behavior
  - `prompts` for prompt collections, prompt design, prompt patterns, and prompt-centric how-to notes
- If several of those AI tags seem plausible, choose the narrowest combination that still reflects independent retrieval value instead of falling back to `ai`.
- Before saving any note with an AI-related tag, re-check whether `ai` can be removed entirely in favor of one or more existing narrower AI tags and whether `ai-adoption` is still truly about rollout or operating-model change rather than a broader AI theme.
- Treat `workflow` as a restricted-use tag rather than as a generic process umbrella.
- Keep `workflow` only when the note is mainly about sequence of work, handoff chain, operating flow, task progression, or an end-to-end operational pipeline.
- Do not use `workflow` for notes whose sharper retrieval axis is already captured by tags such as `organization`, `project-management`, `process-improvement`, `decision-making`, `productivity`, `learning`, `prompts`, or broad `ai-adoption`.
- Do not use `management` at all; replace it with narrower existing tags or with a stable narrower tag admitted through the new-tag gate.
- Treat any tag that starts collecting notes from several different retrieval intents as suspicious; narrow its meaning, convert it into a constrained family, or split off one stable narrower tag before it becomes a new umbrella default.
- Keep note tags sparse: use `1-3` tags per note and never more than `3`.
- Default to the smallest tag set that still captures the note's independent retrieval value.
- Prefer `1-2` tags when they already describe the note precisely enough.
- Add a third tag only when it contributes a clearly separate retrieval angle rather than just making the tag set feel more complete.
- Before creating a new tag, compare the candidate against the nearest 3-5 existing vault tags or constrained-family members and try to reuse those first.
- Create a new tag only when no existing canonical tag or constrained-family member is close enough in meaning and the new tag represents a durable retrieval axis likely to be reused across multiple future notes.
- If one or two existing tags describe the note accurately enough, prefer that combination over inventing a new tag.

## Writing Rules

- Write in Russian.
- Keep key technical terms in English when the English form is the standard term.
- Decide whether to keep an English term by semantic class, not by visual appearance.
- Keep canonical engineering terms, stable role labels, named entities, and canonical `wikilink` titles in English when that is their standard recognizable form.
- Translate generic organizational shorthand, evaluative prose labels, and non-canonical English business language when a direct Russian formulation is clearer.
- Translate ordinary management, business, and product vocabulary into Russian when a natural Russian equivalent exists.
- Do not leave random English nouns in the prose just because they appeared in the source article.
- On first mention, use the Russian form and add the English term in parentheses only when the English wording materially helps recognition or search.
- Use only information supported by the source.
- Keep the structure compact, but do not over-compress the content.
- The final markdown must be save-ready and must not require an extra cleanup pass for spacing, headings, or section hygiene.
- Prefer concrete behavioral requirements over vague words like `append`, `clean up`, `improve`, or `normalize` when the workflow depends on one exact operation.
- Prefer literal, operational wording over metaphorical or fashionable jargon in note prose.
- If a term does not name a concrete role, artifact, step, criterion, mechanism, or constraint, rewrite it into one that does.
- Avoid vague metaphorical or fashionable stand-ins when a direct formulation names the real role, action, decision, mechanism, or constraint more clearly.
- When a rule can be interpreted in more than one plausible way, spell out the intended insertion point, ordering, and stopping condition instead of relying on implication.
- Apply the source-supported practicality gate below for concrete mechanisms, examples, practical anchors, checklist materialization, and source-bounded stopping behavior.
- When a note cites multiple related percentages, rates, or metric values, make the basis explicit if it differs between them.
- Name the cadence, denominator, comparison group, or before/after baseline directly instead of leaving the reader to infer what each number is measuring.
- Do not place two nearby percentages in one sentence if the note does not say whether they mean weekly vs daily usage, share of engineers vs share of all users, or current value vs change over time.
- When the source came through a source-analysis reference, treat that extraction as a working scaffold only and rewrite the final note into a native Obsidian structure instead of preserving the extractor headings literally.
- For every note type, make sections and bullets additive: do not repeat the same recommendation, example, claim, mechanism, or definition under multiple headings unless the source truly requires cross-reference.
- Every next block or bullet must add new knowledge instead of duplicating, inverting, or paraphrasing the previous one.
- Prefer one stronger section over two overlapping ones.
- For source-derived notes, use a content-first body with earned sections rather than filling a fixed template mechanically.
- Treat `3-5` body sections as the normal target for a strong source-derived note; go beyond that only when the source clearly contains multiple distinct layers that would otherwise be lost.
- For source-derived notes, prefer topic-first prose over source-reporting prose.
- The note should read as a note about the mechanism, risk, operating model, recommendation, or concept itself, not as a note about the source artifact.
- Rewrite source-container or source-actor phrasing into direct subject prose whenever the source identity is not materially needed for interpretation.
- Keep source attribution when it materially changes interpretation, scope, provenance, or trustworthiness of a concrete claim, number, or observation.
- This is especially important for the intro and other stable body sections; provenance-oriented sections such as dated evidence logs remain exempt from this de-meta preference.
- Before saving any non-`concept` note, check that each section has a distinct informational role; if two sections mostly carry the same signal, merge them or delete the weaker one.
- Do not create a section only because the schema supports that section family; include it only when the source gives enough independent material for it.
- Use schema-owned section families when they fit the source cleanly:
  - `headings.key_theses`
  - `headings.practice`
  - `headings.key_lessons`
  - `headings.adoption_scope`
  - `headings.platform_systems`
  - `headings.workflows`
  - `headings.metrics_effect`
  - `headings.tools_frameworks`
  - `headings.pitfalls`
  - `headings.apply_immediately`
- Treat those schema-owned families as optional building blocks rather than as a required checklist for every note type.
- Keep section-family roles distinct:
  - `headings.key_theses` carries the main ideas, distinctions, and decision logic
  - `headings.practice` turns those ideas into actions, heuristics, examples, or checklists
  - `headings.key_lessons` is allowed only when it adds a higher-level synthesis beyond neighboring applied or thesis sections
  - `headings.adoption_scope` captures rollout motion, spread, user segmentation, and uptake patterns
  - `headings.platform_systems` captures architecture, internal tools, integration surfaces, and owned technical layers
  - `headings.workflows` captures loops, handoffs, sequences, and operating cycles
  - `headings.metrics_effect` captures concrete numbers, before/after signals, impact, and trade-offs
- If a note would mostly restate the same material under both `headings.practice` and `headings.key_lessons`, keep only the stronger section and drop the other one.
- Run a cross-section dedup pass before finalizing source-derived notes:
  - `headings.key_theses` should explain principles, risks, distinctions, and cause-and-effect logic
  - `headings.practice` should contain actions, documents, criteria, algorithms, and source-supported operational choices
  - nested checklist or algorithm blocks under `headings.practice` should be short final checks or route-selection steps, not a second explanation of the surrounding practice bullets
  - `headings.pitfalls` should name failure modes, false shortcuts, and consequences, not repeat practice bullets in negative form
  - `headings.evidence` should record the dated source of the signal, not retell the stable-body conclusions
- When a nested checklist or algorithm block is warranted, every bullet in that block must start with a short bold lead-in in the form `- **Short label.** Action or criterion.`
- If a nested checklist or algorithm repeats the surrounding practice section, keep the concise check in the nested block and move the explanation, caveat, or example into the surrounding practice bullet.
- In `lessons` notes, merge overlapping lessons instead of keeping two nearby principles with different wording.
- In `lessons` notes, do not let the lessons collapse into headline-only bullets.
- Each lesson must contain not only the reusable principle or recommendation itself, but also at least one short explanatory sentence that makes the lesson understandable without relying on the surrounding sections.
- That second sentence should usually do at least one of these:
  - explain the mechanism
  - name the practical implication
  - clarify the trade-off
  - show why the lesson matters operationally
- If a lesson can be read as only a bold claim with no second-step explanation, treat it as under-materialized and expand it before saving.
- In `operating-model` notes, make each section cover a distinct part of how the organization or system works.
- In `concept` notes, keep the definition compact and avoid restating it in later sections; later additions should extend the note with evidence, observed practices, or adjacent insight.
- Treat `compact` as the default concept-note shape: one tight definition, then the schema-defined `headings.additional_insights`, then the schema-defined `headings.related_notes`.
- Switch a concept note to an `expanded` shape only when the compact form would leave a real comprehension gap.
- Prefer `expanded` concept notes for comparative concepts, easy-to-confuse neighboring concepts, abbreviations or shorthand metric names, and concepts whose recommendation would remain unclear without one more explanatory layer.
- In an `expanded` concept note, add only one or two short clarifying sections that answer the missing question directly, using schema-owned concept clarifier headings such as:
  - `headings.concept_compare`
  - `headings.concept_when_useful`
  - `headings.concept_metric_noise`
- If one concept is expanded mostly to contrast it with a nearby concept, make sure the neighboring concept note also exposes that distinction at least briefly instead of leaving the asymmetry invisible from one side.
- When a source only reinforces an existing concept, update the existing concept note instead of creating a near-duplicate concept file.
- Before creating a new concept file, run a canonical concept check against the existing vault and reuse the canonical note when the meaning already exists.
- When indexed retrieval is configured, run that canonical concept check through the shared `kb-index` shortlist first and read only the strongest returned notes before deciding that the concept is new.
- Do not let a nicer title, a translation, or a local wording variant justify a duplicate concept node when the durable idea is already present.
- Apply the source-supported practicality gate below for `general` note examples, practice sections, and checklist decisions.
- Treat schema-defined dated log sections such as `headings.additional_insights`, `headings.evidence`, and `headings.observed_practices` as chronological append-only logs by default.
- Keep dated bullets in ascending chronological order unless the user explicitly asks for latest-first ordering.
- Insert a new dated bullet after the last existing dated bullet in that section and before the next heading or end-of-file.
- Do not prepend a new dated bullet to the top of a log section unless latest-first ordering was explicitly requested.
- In multi-source source-derived notes, make every bullet under `headings.evidence` explicitly dated.
- Use `YYYY-MM-DD` when the source date is known precisely and `YYYY-MM` when only the month is reliable.
- Treat this as mandatory whenever the note frontmatter contains two or more `source` entries and the note keeps a `headings.evidence` section.
- Add `headings.tools_frameworks` only when it contributes a clearly separate layer of knowledge beyond `headings.practice`.
- Add `headings.pitfalls` only when the source discusses distinct mistakes with their own consequences; do not use it for negative rewrites of recommendations.
- Omit `headings.apply_immediately` when it would only repeat the same actions from `headings.practice`.
- Use bold only for key terms, short labels, or the leading clause of a bullet.
- Use enough bold emphasis that dense sections remain easy to scan, but do not bold entire list items.
- After a late manual rewrite or merge, re-check bold emphasis explicitly; it often disappears when frontmatter, links, or structure are fixed in a second pass.
- Treat lost emphasis as a formatting regression, not as an acceptable cleanup side effect.
- Do not repeat `title` or `source` from frontmatter inside the body.
- Make every saved note read as a standalone knowledge object, not as a diary of how it was produced.
- When a source-derived note introduces a new abbreviation or compressed label that is not already common and obvious inside the note, expand it at first mention in that note body.
- Do not force expansion for every abbreviation in the vault; this rule is for new abbreviations being introduced to the current note, especially when the short form would otherwise be unclear on first read.
- Do not keep process-language in the prose such as `в этом выпуске`, `во втором видео`, `исходная заметка`, `старый материал`, or similar assembly comments when the sentence can be rewritten as direct knowledge.
- In the main note body, prefer idea-first sentences over source-first sentences.
- Treat `source-scaffolding` as phrasing where the source, speaker, author, article, podcast, or similar material is doing the talking for the idea even though the sentence is really expressing reusable knowledge.
- If the sentence still makes full sense after replacing the source with the claim itself, rewrite it as standalone knowledge.
- This includes phrases such as `источник показывает`, `автор выделяет`, or `в транскрипте` when they can be rewritten as direct knowledge.
- Do not replace source-first phrasing with empty discourse placeholders such as `здесь`, `тут`, `в этом`, or similar stand-ins when they no longer point to a real object; rewrite the sentence so the real subject is named directly.
- Keep provenance in frontmatter and use source mentions in the body only when the source itself is a useful case, scenario, or comparison rather than a process footnote.
- Do not apply that source-scaffolding cleanup to schema-defined dated log sections such as `headings.evidence`, `headings.additional_insights`, and `headings.observed_practices`; in those sections, references to `статья`, `подкаст`, `выпуск`, or similar source-type labels are useful provenance and should normally stay.
- Before finalizing a source-derived note, apply the source-supported practicality gate below and check whether the draft has flattened the source into generic statements.
- Keep `headings.key_theses`, `headings.practice`, and `headings.pitfalls` functionally distinct.
- `headings.key_theses` should carry the main ideas, distinctions, and decision logic.
- `headings.practice` should turn those ideas into actions, heuristics, checklists, or examples.
- `headings.pitfalls` should focus on failure modes, misleading shortcuts, and concrete ways the approach breaks in practice.
- `headings.key_lessons` should appear only when it compresses several earlier sections into a stronger synthesis; it should not act as a second copy of `headings.practice` or `headings.key_theses`.
- Do not restate the same point across these sections with only a polarity flip or minor rewording.
- If an anti-pattern is only the inverse of an earlier recommendation, either remove it or rewrite it to include a specific mechanism, risk, tradeoff, or consequence that is not already stated above.
- This pass is especially important for transcripts and long-form spoken sources, where the most useful specificity is often distributed across the material rather than concentrated in the opening section.
- Keep role names and artifact names semantically distinct in Russian when the English source uses nearby terms that would otherwise collapse into one word.
- In particular, reserve `продукт` for the software product, product work, or product-side concerns, and spell the role out explicitly as `Product Manager` when that exact role is meant.
- Do not use `продукт` or `продукты` as a shorthand for `Product Manager` inside note prose, because that blurs role boundaries and makes responsibility lines harder to read.
- Do not rewrite several concrete observations into one abstract sentence if that would hide how the system actually operates.
- For source-derived notes, prefer short sections with 2-4 bullets when the source provides multiple concrete points for the same topic.
- Prefer Russian lesson headings and Russian section bullets unless the English wording is the canonical name of a framework, metric, tool, or code-level term.

## Source-Supported Practicality Gate

Run this pass before final body-normalization for source-derived notes and before final compliance for applied concept notes.

This gate has two obligations in order:

1. First, aggressively extract and preserve every practical signal that the source actually provides.
2. Only after that, relax the output shape when the source does not contain enough supported practical material.

Do not classify a source as `low` practical-signal until the whole source has been checked for:
- concrete actions
- concrete mechanisms such as team scope, ownership, partner functions, named metrics, prioritization logic, platform details, or major constraints
- workflows or sequences
- examples or mini-cases
- numbers, thresholds, or rates
- tools, methods, or named practices
- constraints, trade-offs, and failure modes
- decision criteria
- before/after transitions
- diagnostic signs
- implementation details

Classify the source practical-signal level after that extraction check:
- `high`: the source contains multiple concrete actions, workflows, examples, numbers, cases, tools, constraints, or decision rules.
- `medium`: the source contains some actionable heuristics, examples, or criteria, but not enough for full checklists or step-by-step material.
- `low`: the source remains mostly conceptual, theoretical, reflective, or explanatory after the extraction check.

For `high` practical-signal sources:
- The strict practical-materialization expectation applies.
- Preserve the strongest concrete anchors.
- For each major point in `headings.key_theses`, `headings.practice`, `headings.pitfalls`, or a similar stable section, attach at least one source-supported concrete anchor when one exists.
- Prefer actionable sections, diagnostics, examples, and checklists when they reflect real source structure.
- A practical note should answer what to do, when to do it, and how to recognize the situation.
- Do not downgrade a high-signal source to a conceptual note just because extraction requires more work.

For `medium` practical-signal sources:
- Preserve all non-redundant practical anchors that materially improve actionability.
- If an example is the shortest path to making a recommendation, claim, or anti-pattern understandable, keep a compact version of that example in the note.
- Add lightweight practical material such as criteria, heuristics, compact examples, or short diagnostic lists.
- Do not force a full checklist if the source does not provide a real decision structure.
- If a recommendation is inferred rather than directly stated, keep it conservative and source-bounded.

For `low` practical-signal sources:
- Relax only after the extraction check finds insufficient practical material.
- Do not invent checklists, examples, or step-by-step instructions.
- Keep the note conceptual, but make the concept boundary, distinction, importance, and connections clear.

For every practical-signal level:
- Preserve concrete anchors that change how the reader would act, decide, estimate, diagnose, or scope the problem.
- Do not add decorative detail that does not improve practical understanding.

For `general` notes:
- Fold source-supported examples and cases into the relevant recommendation inside `headings.practice` instead of giving them a standalone dump section.
- Do not strip useful examples out of `headings.key_theses` or `headings.practice` if that would leave only abstract restatements.
- Do not let `headings.practice` restate the same source case that already sits in `headings.key_theses`; if the case is needed in both places, keep the concrete example in the stronger section and rewrite the other bullet so it adds a new action or a different implication.
- After restoring examples, explicitly compare `headings.key_theses` and `headings.practice` for near-duplicate bullets or repeated mini-cases.

For `operating-model` notes:
- Practicality means preserving how the system works: org boundaries, ownership, workflows, tools, metrics, constraints, handoffs, rollout mechanics, and trade-offs.
- Do not convert operating detail into generic recommendations.
- Add checklists only when the source itself provides decision criteria, implementation steps, or rollout mechanics.
- If the source explains a system but not a playbook, keep it as operating-model description rather than forcing `headings.practice`.

For `lessons` notes:
- Each lesson must include a mechanism, trade-off, implication, or concrete source anchor.
- A lesson may remain principle-oriented when the source itself is principle-oriented.
- Do not force implementation checklists unless the source contains repeatable steps or decision criteria.
- If a lesson is abstract, strengthen it with why it matters, where it applies, or where it fails, not with invented action steps.

For concept notes:
- Classify the concept as `applied` or `explanatory`.
- `applied` concepts name a method, planning move, diagnostic pattern, metric, workflow technique, or failure mode. They should usually include criteria, diagnostics, contrast, or good/bad examples when supported by the source.
- `explanatory` concepts mainly name an idea, distinction, theory, or mental model. They may remain compact if the source does not provide practical use cases.
- Do not expand an explanatory concept just to satisfy the practicality gate.
- Before creating a concept, ask whether it remains useful as a standalone knowledge object if the source-derived note disappeared. If not, keep it inside the source note.

For checklist materialization:
- For any non-`concept` note that has `headings.practice` or another clearly applied section, do not add checklists by default.
- Add a checklist only when the source contains operational decision structure that would lose value if flattened into prose.
- A checklist is warranted when at least one of these is true:
  - the source gives 3 or more concrete decision criteria
  - the source compares practical alternatives such as `A vs B`, `buy vs build`, `RAG vs fine-tuning`, or `local vs cloud`
  - the source describes an ordered sequence of actions
  - the source gives applied gates or constraints such as latency, cost, privacy, capacity, reliability, review burden, or support requirements
- When a checklist is added to `headings.practice` or an analogous applied section:
  - place it inside that section or immediately below it
  - use a short heading like `### Чеклист ...`
  - size the checklist by the amount of real decision structure in the source
  - make each item one complete and important action, criterion, or diagnostic question
  - start every item with a short bold lead-in in the form `- **Short label.** Action or criterion.`
  - preserve decision logic and specificity instead of replacing concrete trade-offs with generic advice
  - avoid duplication between the checklist and the surrounding applied prose; keep framing, edge cases, and advice that do not fit checklist form, but do not restate the same actions or criteria in both places
  - split into multiple short checklists if one list starts mixing different decision surfaces
- Do not add a checklist when it would only restate the same applied advice already present in prose, or when the source offers general principles without a clear decision surface.

If the gate cannot produce more practical material without unsupported invention:
- stop rewriting for that missing material
- keep the note honest and source-bounded
- do not run another rewrite loop for the same gap

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
- Do not rename, translate, or remove a wikilink just because the note title contains English terms; note titles are canonical graph identifiers and may legitimately be mixed-language.
- Do not translate, rename, or stylistically rewrite schema-defined section headings in individual notes. If a canonical heading should change, update [config/note_schema.yaml](../config/note_schema.yaml), the checker, tests, and affected notes together as one explicit schema migration.
- Link concept notes by title only, even though they live in `Ideas/Concepts`.
- When the body text explicitly mentions another existing note or concept as a knowledge reference, turn that mention into a wikilink instead of leaving it as plain text.
- Prefer inline wikilinks at the point of mention, not only in `headings.related_notes`.
- Treat inline wikilinks as the primary graph edges.
- Do not mechanically repeat the same note in `headings.related_notes` when it was already linked in the body.
- Use the closing section for net-new navigation links, not as a duplicate dump of all inline references.
- Before creating or keeping `headings.related_notes`, first try to place strong related-note links inline where the stable body already uses the related concept, method, metric, risk, or source-derived topic. Use aliases when the visible phrase should stay grammatical or shorter than the target title. Link only natural, substantive mentions; do not force links into generic words or loosely thematic phrases.

### Inline Wikilink Audit

- Run this pass after the de-meta pass and before closing-section deduplication.
- Source-derived notes must link important existing notes at the point where the idea is used in the stable body.
- The audit set includes:
  - concepts created in the current run
  - existing concepts updated in the current run
  - high-confidence existing concepts or source-derived notes that are explicitly used in the body
  - existing metric, framework, method, or operating-model notes whose titles are mentioned in the body
- If an audit-set term appears in stable body sections, convert the first substantive mention into an inline wikilink.
- Use Obsidian aliases when the visible wording should stay lowercase, inflected, abbreviated, or otherwise different from the note title.
- Do not use inline code to preserve a concept title when a wikilink is the correct graph edge.
- Links in schema-defined dated provenance sections such as `headings.evidence`, `headings.additional_insights`, and `headings.observed_practices` do not satisfy the body-link requirement.
- After adding inline links, re-run closing-section deduplication and remove links from `headings.related_notes` when they are now already linked in the body.

## Required Closing Section

- End the note with `headings.related_notes` only when at least one net-new navigation link remains after final deduplication.
- Closing links are secondary navigation. They must not be used as a substitute for inline links where a concept is actually used in the body.
- Remove the closing heading entirely when deduplication leaves it empty.
- Add 3-10 wikilinks when that many net-new relevant notes exist.
- Prefer links to touched concept notes, source-derived notes, and the closest existing concepts in the vault.
- When indexed retrieval is configured, discover those closest existing notes through the shared `kb-index` shortlist first instead of scanning the vault broadly.
- Remove from the closing section any note that was already linked inline in the body, unless there is a deliberate reason to highlight that one hub note twice.
- Do not add weak or merely thematic filler links just to satisfy a target count.
