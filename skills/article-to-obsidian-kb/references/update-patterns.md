# Update Patterns

## Decide Update Vs Create

- Update an existing note when the main mechanism, principle, or operating pattern matches semantically.
- Create a new note only when the article introduces a genuinely new lesson cluster, operating model, or concept.
- Re-check the vault immediately before creating a file.
- Re-check both configured note roots before creating a concept note.

## General Update Rules

- Preserve the existing title unless it is clearly wrong or much less searchable than the new canonical title.
- Preserve useful existing content.
- Add new information without duplicating prior statements.
- Do not replace specific existing detail with a shorter but more generic rewrite.
- Append a dated entry that links the touched note to the new article and related concepts.
- Use the current insertion date in `YYYY-MM-DD` format.

## Lessons Notes

- Keep or improve the existing lesson wording only when the new article adds a clearer mechanism or trade-off.
- If the note has no evidence section, add `## Evidence`.
- Append one bullet per processed article.

Bullet format:

```markdown
- YYYY-MM-DD: подтверждено статьей [[<article note title>]]; связанные концепты: [[Concept A]], [[Concept B]].
```

## General Notes

- Keep the stable summary of the source near the top.
- Append only the new signal that the source adds, rather than rewriting the whole note into a different structure.
- If the note has no evidence section, add `## Evidence`.
- Append one bullet per processed source.

Bullet format:

```markdown
- YYYY-MM-DD: дополнено по материалу [[<general note title>]]; связанные концепты: [[Concept A]], [[Concept B]].
```

## Operating-Model Notes

- Keep the stable description of the operating model near the top.
- Keep concrete operating detail in the stable sections when the source supports it: team scope, owned systems, partner functions, named metrics, prioritization mechanics, AI rollout, and build-vs-buy constraints should survive rewrites.
- When a section contains several distinct observations, keep them as separate bullets instead of merging them into one summary line.
- If the note has no practice log, add `## Observed practices`.
- Append one bullet per processed article with the date and links.

Bullet format:

```markdown
- YYYY-MM-DD: наблюдения из [[<article or operating-model note title>]]; связанные концепты: [[Concept A]], [[Concept B]].
```

## Concept Notes

- Do not rewrite the concept definition unless the existing definition is clearly incomplete or inaccurate.
- If the note has no insight log, add `## Additional insights`.
- Append one dated bullet that references the source-derived note and nearby concepts.
- Keep concept notes `compact` by default, but promote them to an `expanded` shape when the compact form would still leave the concept easy to confuse, too compressed, or too opaque.
- Use `expanded` especially for contrastive concepts, abbreviation-based metric names, and concepts that need an explicit distinction from a neighboring note.
- In an `expanded` concept note, add only the minimum clarifying section set needed to close the gap.
- If the clarification depends on contrast with another touched concept note, update that neighbor too so the distinction is visible from both directions.

Bullet format:

```markdown
- YYYY-MM-DD: подтверждено в [[<article note title>]]; см. также [[Concept A]], [[Concept B]].
```

## Link Hygiene

- Update `# Связанные заметки` after every create or update.
- Remove obvious duplicate wikilinks.
- Prefer the touched notes over loosely related older links when the closing section becomes too long.
- After concept titles are finalized, run one more exact-title sweep through the source-derived note and convert remaining plain-text or inline-code mentions of those touched concepts into wikilinks.
- When the visible wording should stay shorter than the canonical title, use an Obsidian alias instead of leaving the mention as plain text.
