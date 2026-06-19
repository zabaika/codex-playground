# Structured Note Types

This file is the single source of truth for structured note modes handled by `article-to-obsidian-kb`.

## Mode rules

- The default workflow mode of this skill is `source`.
- `source` mode is the canonical path for articles, transcripts, and long-form text that must still go through source analysis, vault search, and `update vs create`.
- `structured` mode must be selected explicitly.
- Do not silently auto-detect `structured` mode from an incoming JSON file or payload-like object.
- If the caller does not explicitly request `structured` mode, keep the skill in `source` mode.

## Structured routing

Within `structured` mode, route by `type`.

Current supported structured types:

- `council-verdict`

## `council-verdict`

Use this type only when an upstream workflow already produced a canonical decision payload and the remaining job is to write a final Obsidian note from that structured artifact.

The current intended producer is `llm-council`.

Rules:

- treat the payload JSON as the canonical source artifact
- treat `llm-council` as the canonical owner of the `council-verdict` payload schema and executable parser/validator
- do not run source-route detection
- do not run source-analysis extraction
- do not run concept extraction
- do not run `update vs create` heuristics across both note roots
- do not invent a second operational artifact parallel to the payload
- do not require or expect `transcript.md`

Instead:

1. validate the payload against the structured type contract
2. resolve the output root for that structured type
3. render the final markdown note from the dedicated template
4. run the canonical note-contract checks
5. save the final note

### Note-shape contract

Treat the following section order as the canonical human-readable structure for the final `council-verdict` note:

1. frontmatter
2. `Разбор решения`
3. optional degraded-run status block
4. `headings.council_question`
5. `headings.council_verdict`
6. `headings.council_advisor_positions`
7. `headings.council_peer_review`
8. optional related-notes block

More specific rules:

- `Разбор решения` must preserve the original user question as a fenced `text` block, not as an inline paragraph.
- Show the degraded-run status block only when `run_status.status = degraded`.
- Keep the verdict section split into these stable subsections:
  - `headings.council_agree`
  - `headings.council_clashes`
  - `headings.council_blind_spots`
  - `headings.council_recommendation`
  - `headings.council_first_step`
- Render advisor entries under `headings.council_advisor_positions` in a stable per-advisor pattern:
  - `### <Advisor Name>`
  - `- **Позиция:** ...`
  - `- **Ключевой вывод:** ...`
  - full advisor response body
- Render peer review under `headings.council_peer_review` as one block per reviewer in stable order.
- Do not add an extra H1 heading in the note body when the title already exists in frontmatter.
- Use the payload JSON path as the canonical `source` for this note.

### Template boundary

- Keep this reference as the canonical owner of the final `council-verdict` note structure.
- Treat the executable template as a mechanical layout file only.
- Do not move note-shape rules, section semantics, or placement semantics into the template as the only owner.

Filename rule:

- keep the frontmatter `title` readable for humans
- derive the actual filename from a filesystem-safe version of that title
- do not force `council-verdict` to obey the stricter `title == filename stem` rule that source-derived and concept notes use when the human-readable title contains filesystem-hostile characters such as `/`

## Placement rules

- `council-verdict` does not use `note_roots.article` or `note_roots.concept` by default.
- Resolve its destination from `structured_note_roots.council_verdict` in local config when no explicit output path was provided by the caller.
- Keep this separation explicit so source-derived notes, concept notes, and structured decision notes do not silently collapse onto one routing rule.
