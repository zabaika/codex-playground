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

Bullet format:

```markdown
- YYYY-MM-DD: подтверждено в [[<article note title>]]; см. также [[Concept A]], [[Concept B]].
```

## Link Hygiene

- Update `# Связанные заметки` after every create or update.
- Remove obvious duplicate wikilinks.
- Prefer the touched notes over loosely related older links when the closing section becomes too long.
