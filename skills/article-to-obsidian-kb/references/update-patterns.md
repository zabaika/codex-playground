# Update Patterns

This file is the single source of truth for how existing notes are updated, merged, appended, and kept chronological.
Canonical section-heading strings live in [config/note_schema.yaml](../config/note_schema.yaml). Use those `headings.*` values as schema labels instead of treating headings as editable prose.

## Decide Update Vs Create

- Update an existing note when the main mechanism, principle, or operating pattern matches semantically.
- Create a new note only when the article introduces a genuinely new lesson cluster, operating model, or concept.
- Re-check the indexed candidate pool immediately before creating a file.
- Re-check both configured note roots through indexed retrieval before creating a concept note.
- Treat indexed note metadata as the canonical first-pass inspection layer before any full file read.
- Only fall back to broad direct vault search when the configured index is unavailable, broken, or clearly stale.
- In that fallback, search only inside the configured note roots rather than broadening to unrelated local directories.

## General Update Rules

- Preserve the existing title unless it is clearly wrong or much less searchable than the new canonical title.
- Preserve useful existing content.
- Preserve surviving frontmatter provenance, especially `source`, when rewriting a note into the current format.
- Do not drop a valid old `source` field just because the note type, title, or section structure changed during normalization.
- Add new information without duplicating prior statements.
- Do not replace specific existing detail with a shorter but more generic rewrite.
- When restructuring a touched note, do not fill every available section family just because the schema supports it.
- Keep only earned sections whose role is materially distinct in the final note.
- If two sections end up carrying the same mechanism, recommendation, or example cluster, merge them or delete the weaker one instead of preserving both for symmetry.
- Prefer one stronger content-first structure over a neater but more repetitive template-shaped rewrite.
- Treat it as a `scope fork` when a local update surfaces unrelated whole-note legacy cleanup that would materially expand the work or force a strategic choice between full cleanup, rollback, or rerouting the new signal.
- At that fork, do not silently choose the path yourself. Pause and ask the user which route they want:
  - full cleanup of the touched note
  - rollback of the new fragment
  - or preserving the new signal elsewhere while leaving the touched note for a separate cleanup pass
- If the user chooses `full cleanup`, first show a brief cleanup plan for that note and get explicit confirmation before rewriting the whole note.
- Append a dated entry that links the touched note to the new article and related concepts.
- Use the current insertion date in `YYYY-MM-DD` format.
- Treat dated update sections as chronological append-only logs by default.
- Keep dated bullets in ascending chronological order: oldest entries first, newest entries last.
- Insert a new dated bullet after the last existing dated bullet in that section, not at the top.
- Do not silently reorder older entries during a normal update unless their current order is already broken.
- If an older source is being backfilled later, insert its bullet by date instead of mechanically pushing it to the end.
- When appending into schema-defined dated sections such as `headings.additional_insights`, `headings.evidence`, or `headings.observed_practices`, place the new bullet immediately before the next heading or the end of the note.

## Dated-Log Reinforcement Updates

- Treat an existing-note update as evidence-only reinforcement when the new source only confirms, clarifies, or adds a dated observation to an existing note through `headings.evidence`, `headings.additional_insights`, or `headings.observed_practices`.
- For evidence-only reinforcement, do not add the new source URL to the existing note's frontmatter `source`.
- Keep the new source URL in the new source-derived note that was created from that material.
- In the existing note's dated log bullet, preserve provenance by linking to that source-derived note. The provenance chain should be: existing note -> dated log bullet -> source-derived note -> source-derived note frontmatter `source`.
- Extend an existing note's frontmatter `source` only when the new source becomes part of that note's primary identity: for example, when the note itself is being rewritten as a source-derived artifact, when it absorbs or replaces another source-derived note, or when a structural merge makes the source materially responsible for the stable body rather than just a later confirmation.

## Lessons Notes

- Keep or improve the existing lesson wording only when the new article adds a clearer mechanism or trade-off.
- If the note has no evidence section, add `headings.evidence`.
- Append one bullet per processed article.
- Keep `headings.evidence` chronological unless the user explicitly asked for latest-first ordering.

Bullet format:

```markdown
- YYYY-MM-DD: подтверждено статьей [[<article note title>]]; связанные концепты: [[Concept A]], [[Concept B]].
```

## General Notes

- Keep the stable summary of the source near the top.
- Append only the new signal that the source adds, rather than rewriting the whole note into a different structure.
- If the note has no evidence section, add `headings.evidence`.
- Append one bullet per processed source.
- Keep `headings.evidence` chronological unless the user explicitly asked for latest-first ordering.

Bullet format:

```markdown
- YYYY-MM-DD: дополнено по материалу [[<general note title>]]; связанные концепты: [[Concept A]], [[Concept B]].
```

## Operating-Model Notes

- Keep the stable description of the operating model near the top.
- Keep concrete operating detail in the stable sections when the source supports it: team scope, owned systems, partner functions, named metrics, prioritization mechanics, AI rollout, and build-vs-buy constraints should survive rewrites.
- When a section contains several distinct observations, keep them as separate bullets instead of merging them into one summary line.
- If the note has no practice log, add `headings.observed_practices`.
- Append one bullet per processed article with the date and links.
- Keep `headings.observed_practices` chronological unless the user explicitly asked for latest-first ordering.

Bullet format:

```markdown
- YYYY-MM-DD: наблюдения из [[<article or operating-model note title>]]; связанные концепты: [[Concept A]], [[Concept B]].
```

## Concept Notes

- Do not rewrite the concept definition unless the existing definition is clearly incomplete or inaccurate.
- `source` is allowed in concept-note frontmatter when it preserves useful provenance from the originating source or a migrated legacy note.
- When updating or normalizing an older concept note, keep surviving `source` values instead of stripping them only because `type: concept` does not require `source`.
- If the note has no insight log, add `headings.additional_insights`.
- Append one dated bullet that references the source-derived note and nearby concepts.
- Keep `headings.additional_insights` chronological unless the user explicitly asked for latest-first ordering.
- Keep concept notes `compact` by default, but promote them to an `expanded` shape when the compact form would still leave the concept easy to confuse, too compressed, or too opaque.
- Use `expanded` especially for contrastive concepts, abbreviation-based metric names, and concepts that need an explicit distinction from a neighboring note.
- In an `expanded` concept note, add only the minimum clarifying section set needed to close the gap.
- If the clarification depends on contrast with another touched concept note, update that neighbor too so the distinction is visible from both directions.

Bullet format:

```markdown
- YYYY-MM-DD: подтверждено в [[<article note title>]]; см. также [[Concept A]], [[Concept B]].
```

## Link Hygiene

- Update `headings.related_notes` after every create or update.
- Prefer reusing already opened notes and the existing indexed shortlist before issuing any extra retrieval pass for related links.
- Build candidate related links through indexed retrieval only when the current candidate pool still does not give enough strong options, and then read only the strongest shortlist that survived scoring.
- Inspect candidate tags, headings, and outgoing links from the index first; do not reopen files only to recover metadata the index already has.
- Remove obvious duplicate wikilinks.
- After inline wikilinks are finalized, remove from `headings.related_notes` any note that is already linked inline in the body.
- If the closing section becomes empty after that deduplication, delete the heading instead of leaving an empty or mechanically duplicated block.
- Prefer the touched notes over loosely related older links when the closing section becomes too long.
- After concept titles are finalized, run one more exact-title sweep through the source-derived note and convert remaining plain-text or inline-code mentions of those touched concepts into wikilinks.
- After the exact-title sweep, run one more semantic-alias sweep for touched concepts whose canonical title differs from the wording that naturally appears in the prose.
- Keep both sweeps constrained to touched concepts and shortlisted indexed candidates; do not trigger a broad vault scan just to hunt for more possible links.
- Use that sweep for source terms, abbreviations, English labels, shortened metric names, or compact phrases that should stay visible in the text but now point at a broader canonical concept title.
- When the visible wording should stay shorter than the canonical title, use an Obsidian alias instead of leaving the mention as plain text.

## Write Path Hygiene

### Pre-Write Approval Pass

- Finalize the note content in staging first and then perform one destination write for the finished artifact.
- Before any destination write, present a diff for every updated destination note and wait for explicit user approval.
- Diffs for newly created notes are optional unless the user explicitly asks to review them too.
- Treat this staged diff review as a required approval gate for the whole run: do not write updated or created notes to the destination vault until the user has approved proceeding.

### Post-Write Verification Pass

- Prefer a single copy or move of the finalized artifact over several incremental destination rewrites.
- Immediately read the destination note back after that write and verify the expected final state before running further checks, searches, or user-facing reporting.
- If the read-back content does not match the intended final artifact, stop and investigate the mismatch before retrying; do not spend repeated attempts on blind rewrites against a stale assumption about what was saved.
