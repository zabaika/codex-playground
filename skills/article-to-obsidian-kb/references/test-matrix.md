# Note Contract Test Matrix

This matrix separates mechanically checkable parts of the `article-to-obsidian-kb` contract from mandatory editorial passes.

It does **not** claim to verify:
- whether the source was interpreted deeply enough
- whether the best concepts were chosen
- whether routing was semantically perfect
- whether the summary quality is as strong as a human editor would want

Those remain judgment tasks for the skill itself and for human review.

## How To Use

- When adding a new mechanically checkable rule to the skill, update this matrix in the same change.
- Prefer extending the existing checker and fixtures over inventing one-off ad hoc checks.
- For each new rule family, try to keep both:
  - one broken fixture that should fail
  - one clean fixture that should pass

## Covered Rule Families

| Rule family | Canonical docs | Checker coverage | Broken fixture | Clean fixture |
| --- | --- | --- | --- | --- |
| Frontmatter required fields | `vault-conventions.md` | `frontmatter.missing-*`, `frontmatter.invalid-type` | `after-first-pass-regression.md` | `clean-general-note.md` |
| Tag format and count | `vault-conventions.md` | `frontmatter.invalid-tag*`, `frontmatter.invalid-tag-count` | add when needed | `clean-general-note.md` |
| Required headings and forbidden headings | `vault-conventions.md` | `structure.missing-heading:*`, `structure.forbidden-heading:*`, `structure.duplicate-heading:*` | `after-first-pass-regression.md` | `clean-general-note.md` |
| Schema-owned heading literals | `RULEBOOK.md`, `config/note_schema.yaml` | `audit_schema_heading_literals.py`: `schema-heading-literal:*` | generated in `test_audit_schema_heading_literals.py` | generated in `test_audit_schema_heading_literals.py` |
| Schema-owned note-shape labels | `config/note_schema.yaml`, `structured-note-types.md` | isolated schema overrides drive writer and checker without consumer edits | generated invalid opening, stance, headline and degraded marker | `test_structured_note_writer.py`: overridden heading/labels, unchanged payload |
| Intro before first heading | `vault-conventions.md` | `structure.missing-intro-before-first-heading` | add when needed | `clean-general-note.md` |
| Spacing rules and pipe-table padding | `vault-conventions.md` | `spacing.*`, including `spacing.missing-blank-before-table` and `spacing.missing-blank-after-table`; table exceptions to heading/list spacing; no required blank before ATX headings | `after-first-pass-regression.md`; generated table-padding and heading-like prose cases | `clean-general-note.md`; generated headings directly after prose, lists, headings and closed code fences; optional post-table heading padding and document-boundary cases |
| Evidence link boundaries and optional single-source section | `vault-conventions.md` | `evidence.external-link`; scheme-relative targets included; checks stop at the next same-or-higher-level ATX heading with 0–3 leading spaces | generated inline, bare, autolink, scheme-relative, and reference-link cases | generated source-only/source-plus-concept wikilinks, indented section boundaries, local paths, and no-evidence cases |
| Evidence informational value | `vault-conventions.md`, `update-patterns.md` | mandatory semantic pass; no title/author blacklist | editorial F1: bibliography-only entry; F2: weak concept relation | editorial F1/F2: omitted metadata-only section, retained useful dated observation, optional useful concept navigation |
| Forbidden anglicisms and latin residue | `language-normalization.md`, `vault-conventions.md` | `language.forbidden-term:*`, `language.translate-term:*`, `language.translate-phrase:*`, `language.review-term:*`, `language.unexpected-latin:*` | `after-first-pass-regression.md` | `clean-general-note.md` |
| Required wikilinks | `vault-conventions.md` | `links.unlinked-phrase:*` | `after-first-pass-regression.md` | `clean-general-note.md` |
| Inline wikilink audit set | `vault-conventions.md`, `language-normalization.md` | `audit_plain_wikilink_mentions.py`: `plain-wikilink-missing:*`, `backticked-concept:*` | generated in `test_audit_plain_wikilink_mentions.py` | generated in `test_audit_plain_wikilink_mentions.py` |
| Inline-link placement before closing links | `vault-conventions.md` | semantic workflow gate only; partial adjacent coverage through inline wikilink audit and closing-link dedup checks | not fully mechanized | not fully mechanized |
| Semantic link coverage, relation quality, alias precision, comparative navigation | `vault-conventions.md`, `update-patterns.md` | mandatory editorial pass; chosen-link checks do not prove discovery coverage or reader benefit | editorial scenarios below | editorial scenarios below |
| Required examples retained | `vault-conventions.md` | `examples.missing:*` | `after-first-pass-regression.md` | `clean-general-note.md` |
| Source-supported practicality gate and coverage reuse | `vault-conventions.md` | mandatory source-to-note review; format and selected literal examples provide partial adjacent checks only | editorial source/draft cases below | editorial preservation, exclusion, and reuse cases below |
| Cross-section dedup and legacy block preservation | `vault-conventions.md`, `update-patterns.md` | semantic workflow gate only; no dedicated checker because meaningful duplication and old-block conflicts require editorial judgment | not fully mechanized | not fully mechanized |
| Knowledge relevance, material qualifications, and concept-insight value | `vault-conventions.md`, `update-patterns.md` | mandatory semantic editorial pass across all sections, including dated provenance logs; existing de-meta checks cover only adjacent residue classes | not fully mechanized | not fully mechanized |
| Bold-leading scanability | `vault-conventions.md` | `emphasis.missing-leading-bold:*` | `after-first-pass-regression.md` | `clean-general-note.md` |
| Partial source-scaffolding residue coverage | `vault-conventions.md` | `prose.source-scaffolding:*` | `source-scaffolding-regression.md` | `clean-general-note.md` |
| Partial de-meta intro/source-reporting coverage | `vault-conventions.md` | `prose.de-meta:*`, plus representative `prose.source-scaffolding:*` hits for source-artifact reporting | generated in `test_note_contract_regression.py` | generated in `test_note_contract_regression.py` |
| Closing related-notes section | `vault-conventions.md` | `closing.*` | `after-first-pass-regression.md`, `empty-related-section.md` | `clean-general-note.md` |
| Links-only closing content | `vault-conventions.md` | `closing.non-link-content`; one bare wikilink per nonblank line, optional alias | generated annotation/prose/list/subheading cases in `test_note_contract_regression.py` | generated bare/aliased-link and absent-section cases in `test_note_contract_regression.py` |
| Chronological dated log sections | `update-patterns.md`, `vault-conventions.md` | `chronology.*` | `dated-log-prepend-regression.md` | `dated-log-clean.md` |
| Source date resolution | `vault-conventions.md`, `update-patterns.md` | editorial source-date-map review; `chronology.*` checks ordering only | save date replaces a known publication date | verified publication date or allowed fallback |
| Evidence-only reinforcement source handling | `update-patterns.md` | mandatory update-contract pass; final-file checker cannot prove before/after update intent | not fully mechanized | not fully mechanized |
| Structured council verdict note shape | `structured-note-types.md`, `vault-conventions.md` | `frontmatter.invalid-council-tags`, `structure.invalid-council-opening`, `structure.unexpected-h1-in-body`, `structure.invalid-council-section-order`, `structure.invalid-related-section-order`, `structure.invalid-advisor-*`, `structure.empty-advisor-response-body`, `structure.invalid-peer-review-heading`, `structure.empty-peer-review-body`, `structure.invalid-degraded-status-marker`, `structure.missing-degraded-status-details` | generated in `test_structured_note_writer.py` | generated by `test_structured_note_writer.py` |

## Gaps To Keep In Mind

- Link checks validate formatting and explicitly required links among already selected targets. They cannot prove that substantial unnamed mechanisms were considered, a relation helps the reader, an alias describes it accurately, or a comparative link has a concrete purpose. Apply the editorial scenarios below; do not substitute wording-matching tests or keyword blacklists for that pass.
- The checker does not prove that the chosen examples are the best examples, only that explicitly required examples were not dropped.
- Date selection requires source evidence and the actual save date; ordered dates alone do not prove correctness. Review unknown article/video dates, month-only precision, a local date change before saving, and a later edit that must not reset a saved fallback.
- The checker receives the note, not the original source. Its optional required-example phrases default to empty and test literal presence only; they cannot discover an omitted mechanism or prove preserved meaning, conditions, or number bases. Apply the canonical practicality gate and editorial cases below; a populated coverage record alone is not proof either.
- The checker does not prove that cross-section deduplication or old-block conflict handling was semantically correct. Those remain mandatory editorial and update-contract passes because the right action may be to preserve, ask, annotate, rewrite, or relocate material depending on user intent.
- The checker does not prove that a concept should exist, only that once a rule requires a link or section shape, the saved note respects it.
- The checker does not decide whether a qualification changes a claim's meaning or whether a dated concept insight adds durable knowledge. Both require the mandatory editorial pass, including in provenance logs. Check removal of processing commentary, preservation of material scope and status, and skipping concept updates that add no knowledge; pattern-based de-meta checks provide only partial adjacent coverage.
- The checker does not replace manual review for routing, semantic deduplication quality, or concept selection.
- The checker does not prove that an existing-note update was evidence-only reinforcement. That decision depends on the before/after update intent and remains a mandatory update-contract pass from `update-patterns.md`.
- The source-scaffolding checker is intentionally partial: it catches a few representative residue classes such as artifact self-reference, source-container phrasing, attribution clauses, and source-actor reporting verbs, but it does not replace the final manual `de-meta` pass from `vault-conventions.md`.
- The de-meta checker is also intentionally partial: it is meant to catch durable source-artifact reporting failures in intros and stable body prose, not to forbid all attribution to real studies, measurements, or external evidence.

## Mandatory Editorial Practicality Scenarios

Use the [canonical coverage procedure](vault-conventions.md#practical-coverage-record) with the sources, draft variants, and change events in [practicality_cases.md](../tests/editorial/practicality_cases.md). These are behavioral evaluation inputs, not automatic semantic unit tests. Record the applied instruction revision, actual retained/revised passages, source-to-note decisions, and any justified exclusions in scratch. Label self-application and independent evaluation accurately.

Exercise both loss detection and allowed reduction, including a complete draft requiring no additions. Check that final whole-note passes reuse the same source review, while meaningful changes invalidate the affected entries. Reuse the small common sources across variants; do not rerun transcription, vault discovery, or a complete external workflow for each local case. Include a staging integration slice from prepared source through coverage and draft to final checks. Distinguish successful mechanical checks from the semantic assessment and report resource changes only when measured.

Use E1 and E2 for qualification cleanup: remove a repeated general warning while retaining its useful example, and preserve conditions that change a claim or number. Reuse D1 for explanatory distinctions. These are semantic decisions within the existing editorial pass, not negative-word blacklists or another full source review.

## Mandatory Editorial Link Scenarios

Apply the changed instructions to representative draft fragments and candidate contents. Record the actual fragment, candidate meaning, reader benefit, and decision in scratch. Label self-application as such; deterministic format tests are not independent semantic evaluation.

- Find a substantial mechanism expressed without the canonical title, including in a table or example. Reuse indexed candidates or make a targeted query; finding a candidate does not require retaining it.
- Keep a strong relation with a vague alias only after clarifying the visible anchor. Reject a weak relation even when a polished alias makes it sound plausible.
- Keep a comparative link only when it supplies a concrete comparison or additional example. Shared domain alone is insufficient; keep rationale in scratch and only the link in the closing block.
- Reject shared vocabulary when the mechanisms differ, such as clustering review comments versus retrieving evidence for generation, or shared context caching versus progressive disclosure or prompt hardening.
- Accept zero new links and no closing section when no strong counterpart exists. Never set an expected insertion count.

For the mechanical boundary, annotation text beside a link or on a separate line must fail. Bare links and optional aliases must pass; removing the whole closing block must also pass unless an explicit caller requirement demands a particular link.

When the caller permits a nonfinal related-notes section through `require_related_section_final=False` or `--allow-related-section-not-final`, stop the links-only check at the next same-level heading. Regression cases in `test_note_contract_regression.py` must accept that following section, still reject annotations and subheadings inside the links section, and preserve default rejection of nonfinal placement.
