# Note Contract Test Matrix

This matrix tracks only the parts of the `article-to-obsidian-kb` contract that are mechanically checkable after drafting.

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
| Frontmatter required fields | `SKILL.md`, `vault-conventions.md` | `frontmatter.missing-*`, `frontmatter.invalid-type` | `after-first-pass-regression.md` | `clean-general-note.md` |
| Tag format and count | `SKILL.md`, `vault-conventions.md` | `frontmatter.invalid-tag*`, `frontmatter.invalid-tag-count` | add when needed | `clean-general-note.md` |
| Required headings and forbidden headings | `SKILL.md`, `vault-conventions.md` | `structure.missing-heading:*`, `structure.forbidden-heading:*`, `structure.duplicate-heading:*` | `after-first-pass-regression.md` | `clean-general-note.md` |
| Intro before first heading | `SKILL.md`, `vault-conventions.md` | `structure.missing-intro-before-first-heading` | add when needed | `clean-general-note.md` |
| Spacing rules | `SKILL.md`, `vault-conventions.md` | `spacing.*` | `after-first-pass-regression.md` | `clean-general-note.md` |
| Forbidden anglicisms and latin residue | `SKILL.md`, `language-normalization.md`, `vault-conventions.md` | `language.forbidden-term:*`, `language.unexpected-latin:*` | `after-first-pass-regression.md` | `clean-general-note.md` |
| Required wikilinks | `SKILL.md`, `vault-conventions.md` | `links.unlinked-phrase:*` | `after-first-pass-regression.md` | `clean-general-note.md` |
| Required examples retained | `SKILL.md`, `vault-conventions.md` | `examples.missing:*` | `after-first-pass-regression.md` | `clean-general-note.md` |
| Bold-leading scanability | `SKILL.md`, `vault-conventions.md` | `emphasis.missing-leading-bold:*` | `after-first-pass-regression.md` | `clean-general-note.md` |
| Closing related-notes section | `SKILL.md`, `vault-conventions.md` | `closing.*` | `after-first-pass-regression.md`, `empty-related-section.md` | `clean-general-note.md` |

## Gaps To Keep In Mind

- The checker does not prove that the chosen examples are the best examples, only that explicitly required examples were not dropped.
- The checker does not prove that a concept should exist, only that once a rule requires a link or section shape, the saved note respects it.
- The checker does not replace manual review for routing, deduplication quality, or concept selection.
