# Skill Contract Test Matrix

This matrix tracks only the parts of the `convert-external-skills-to-codex` package that are mechanically checkable.

It does **not** claim to verify:

- whether a converted skill is semantically strong
- whether the best output family was chosen for a real source
- whether a generated handbook or skill preserves the best possible examples
- whether change disclosure is intellectually honest in edge cases that require judgment

Those remain judgment tasks for the skill itself and for human review.

## How To Use

- When adding a new mechanically checkable rule family to this skill package, update this matrix in the same change.
- Prefer extending the existing checker over inventing one-off shell checks.
- Keep the checker focused on structure, ownership boundaries, required anchors, and package-shape constraints.

## Covered Rule Families

| Rule family | Canonical docs | Checker coverage |
| --- | --- | --- |
| Required package files exist | `SKILL.md`, `README.md` | `package.missing-file:*` |
| SKILL frontmatter basics | `SKILL.md` | `frontmatter.missing:*`, `frontmatter.invalid-allowed-tools` |
| SKILL entrypoint sections exist | `SKILL.md` | `skill.missing-section:*` |
| README stays operator-facing and points to canonical owner | `README.md`, `SKILL.md` | `readme.missing-section:*`, `readme.missing-canonical-owner-pointer` |
| `security-audit-checklist.md` owns only audit/triage | `security-audit-checklist.md` | `security-audit.missing-anchor:*`, `security-audit.forbidden-restatement:*` |
| `openai-surface-guidance.md` owns only surface selection and current OpenAI constraints | `openai-surface-guidance.md` | `surface-guidance.missing-anchor:*`, `surface-guidance.missing-doc-link:*` |
| UI metadata exists | `agents/openai.yaml` | `openai-yaml.missing-interface`, `openai-yaml.missing-field:*` |
| Install script targets the deployed skill path | `install-local.sh` | `install.missing-target-path`, `install.missing-copy-step` |
| Real `chatgpt-project-pack` fixtures keep package separation | `SKILL.md` output contract | `*.missing:handbook/runtime/examples/report`, `*.handbook.missing-anchor:*`, `*.runtime.missing-anchor:*`, `*.runtime.missing-handbook-pointer`, `*.report.missing-anchor:*` |
| Real fixtures stay free of vendor residue | `SKILL.md`, `openai-surface-guidance.md` | `*.vendor-residue:*` |
| Real `codex-skill` fixtures keep narrow skill shape and surviving references | `SKILL.md` output contract | `*.skill.missing-anchor:*`, `*.skill.missing-reference-link:*`, `*.reference.too-thin:*`, `*.report.invalid-family` |

## Gaps To Keep In Mind

- The checker does not validate generated conversion outputs.
- The checker validates representative saved outputs, but it does not prove that every future conversion will be equally strong semantically.
- The checker does not prove that references are concise enough, only that ownership boundaries are still explicit.
- The checker does not replace official `skill-creator` validation.
