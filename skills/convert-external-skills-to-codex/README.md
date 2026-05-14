# convert-external-skills-to-codex

Local Codex skill for auditing and converting third-party skills into safer OpenAI-compatible forms before installation or reuse.

## Purpose

Use `convert-external-skills-to-codex` when a source skill, prompt pack, or instruction file must be narrowed, renamed, split, or rewritten before it becomes a local Codex skill, repo-scoped `AGENTS.md`, or a clean ChatGPT Project package.

This skill does four things:

- audits the source before conversion
- selects a clean output family instead of producing a hybrid artifact
- preserves source value by default and transforms only by exception
- emits a sidecar report for deletions, substantial adaptations, and follow-up risk

For the detailed conversion contract, use `SKILL.md` as the canonical owner.

## How To Run

Invoke the skill from Codex with prompts such as:

- `$convert-external-skills-to-codex convert this external skill into a safe local Codex skill`
- `$convert-external-skills-to-codex audit this prompt pack before installation`
- `$convert-external-skills-to-codex turn this repo guidance into AGENTS.md`

If the task depends on the latest OpenAI operational behavior, use the system skill `$openai-docs` in the same session before finalizing the conversion.

## Output Families

The skill supports these primary outputs:

- `chatgpt-project-pack`
- `codex-skill`
- `codex-agents-md`
- `conversion-report-only`

`chatgpt-project-pack` is the default family for large instruction-rich sources. It contains:

- `full handbook`
- `compact runtime`
- `conversion report`
- optional or default-companion `examples-pack` when the source has a heavy examples layer

For output-family distinctions and current OpenAI surface constraints, use `references/openai-surface-guidance.md`.

## ChatGPT Project Setup

When the chosen output is `chatgpt-project-pack`, wire it into ChatGPT Projects like this:

- upload the `full handbook` to the project files
- upload the `examples-pack` too when one was generated
- paste the `compact runtime` into project instructions
- keep the `conversion report` as a sidecar for review and maintenance; it does not need to stay active inside the project

Use the layers this way:

- `full handbook`: primary reference for module behavior, routing, and richer guidance
- `examples-pack`: reference for worked examples, quality baselines, comparison patterns, and longer specimens
- `compact runtime`: always-active behavior layer that should point the assistant back to the handbook and examples when more detail is needed

If an `examples-pack` exists, do not leave it outside the project and assume the runtime alone is enough. The runtime should stay short; the examples file carries concrete reference material that should remain available as project knowledge.

## Source Of Truth

- Repository source of truth: `skills/convert-external-skills-to-codex/`
- Installed Codex copy: `~/.codex/skills/convert-external-skills-to-codex`

Edit the repository copy first. Reinstall into `~/.codex/skills` after changes.

## Installation

Install or refresh the local Codex copy with:

```bash
bash skills/convert-external-skills-to-codex/install-local.sh
```

This copies the repository skill into `~/.codex/skills/convert-external-skills-to-codex`.

If the current Codex session does not see the updated skill immediately, restart Codex.

## Main Files

- `SKILL.md`: runtime workflow entrypoint
- `agents/openai.yaml`: UI-facing skill metadata
- `references/security-audit-checklist.md`: source-risk audit rubric for blockers, severity, permissions, naming, and mixed-pack checks
- `references/openai-surface-guidance.md`: output-family mapping and current OpenAI operational notes
- `references/test-matrix.md`: checker coverage map and known non-goals
- `scripts/check_skill_contract.py`: local mechanical contract checker
- `scripts/check_conversion_fixtures.py`: regression checker for representative converted outputs
- `tests/fixtures/`: representative saved outputs used by the fixture checker
- `tests/test_conversion_fixture_regression.py`: unittest wrapper for the fixture checker
- `install-local.sh`: install or refresh the skill into `~/.codex/skills`

## Fixture Naming

Keep fixture file names long enough to stay distinguishable in editor navigation and global search.

- In each fixture root, prefer artifact names derived from the fixture folder name, such as `ai-prompting-handbook.md` or `prompt-debugger-conversion-report.md`.
- In `references/`, use equally distinguishable names when multiple fixtures contain the same kind of artifact, such as `prompt-debugger-worked-example.md`.
- Treat `tests/fixtures/manifest.json` as the naming contract for the fixture checker. If a fixture file is renamed, update the manifest and any in-file links in the same change.

## Maintenance

- treat the repository copy as the editable source of truth and the installed copy as a deploy artifact only
- do not patch the installed copy directly unless the user explicitly asks for an emergency local-only fix
- run `python3 skills/convert-external-skills-to-codex/scripts/check_skill_contract.py` after contract-facing edits
- run `python3 skills/convert-external-skills-to-codex/scripts/check_conversion_fixtures.py` after output-contract or fixture changes
- run `python3 ~/.codex/skills/.system/skill-creator/scripts/quick_validate.py skills/convert-external-skills-to-codex` after structural edits
- when OpenAI operational assumptions change, refresh the guidance in `references/openai-surface-guidance.md` with official docs instead of scattering partial updates into multiple files
- when the conversion contract changes, prefer updating sequencing, relocation, disclosure, and package-validation rules in `SKILL.md` instead of drifting them into `security-audit-checklist.md`

## Troubleshooting

- If Codex does not see the refreshed skill after reinstall, restart Codex.
- If `quick_validate.py` fails because the system `skill-creator` environment is missing `PyYAML`, treat that as tooling-environment drift rather than a reason to edit the installed skill directly. Perform the structural check you can, then fix the shared validator separately.
