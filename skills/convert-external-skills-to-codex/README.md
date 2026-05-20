# convert-external-skills-to-codex

Local Codex skill for auditing and converting third-party skills into safer OpenAI-compatible outputs before reuse or installation.

## Purpose

Use `convert-external-skills-to-codex` when an external skill, prompt pack, or instruction file needs review, narrowing, splitting, or surface adaptation before it becomes:

- a local Codex skill
- a repo-scoped `AGENTS.md` fragment
- a ChatGPT Project package
- a report-only migration audit

For the detailed conversion contract, use `SKILL.md` as the canonical owner.

## How To Run

Invoke the skill with prompts such as:

- `$convert-external-skills-to-codex convert this external skill into a safe local Codex skill`
- `$convert-external-skills-to-codex convert this mixed pack into Codex skills and install them after conversion`
- `$convert-external-skills-to-codex convert this mixed pack into Codex skills under the configured scratch output root without installing them`
- `$convert-external-skills-to-codex audit this prompt pack before installation`
- `$convert-external-skills-to-codex turn this repo guidance into AGENTS.md`

If the command does not say how a `codex-skill` result should be placed, the skill should ask before converting.

## Output Families

The skill supports these primary outputs:

- `chatgpt-project-pack`
- `codex-skill`
- `codex-agents-md`
- `conversion-report-only`

Use `references/openai-surface-guidance.md` when the target surface is unclear.

## Output Placement Modes

This choice applies when the chosen output is `codex-skill`, whether the result is one skill or several.

- `install-after-conversion`: convert the result, then install or refresh it in the normal local destination
- `scratch-folder-without-installation`: convert the result into a dedicated child folder under the configured scratch output root and stop without installing

Scratch-backed placement is resolved from `config/runtime.local.toml`:

- `CODEX_PLAYGROUND_PROJECT_ROOT` takes precedence when the configured path is project-relative
- otherwise use `paths.project_root` when configured
- then resolve `paths.scratch_root`

## Source Of Truth

- Repository source of truth: `skills/convert-external-skills-to-codex/`
- Installed Codex copy: `~/.codex/skills/convert-external-skills-to-codex`

Edit the repository copy first. Treat `config/runtime.local.toml` as the editable local source of truth for scratch-backed output placement.

## Installation

Install or refresh the local Codex copy with:

```bash
bash skills/convert-external-skills-to-codex/install-local.sh
```

The installer copies the skill into `~/.codex/skills/convert-external-skills-to-codex` and links the installed `config/runtime.local.toml` back to the repository copy.

If the current Codex session does not see the updated skill immediately, restart Codex.

## Main Files

- `SKILL.md`: runtime workflow entrypoint
- `config/runtime.example.toml`: example project-root and scratch-output configuration
- `config/runtime.local.toml`: repository-managed local runtime path configuration
- `agents/openai.yaml`: UI-facing skill metadata
- `references/security-audit-checklist.md`: source-risk audit rubric
- `references/openai-surface-guidance.md`: output-family mapping and current OpenAI surface notes
- `references/test-matrix.md`: checker coverage map
- `scripts/check_skill_contract.py`: local mechanical contract checker
- `scripts/check_conversion_fixtures.py`: regression checker for representative converted outputs
- `install-local.sh`: install or refresh the skill into `~/.codex/skills`

## Maintenance

- treat the repository copy as the editable source of truth and the installed copy as a deploy artifact only
- do not patch the installed copy directly unless explicitly requested
- keep workflow and output-contract changes in `SKILL.md`
- keep scratch-backed path defaults in `config/runtime.local.toml`
- run `python3 skills/convert-external-skills-to-codex/scripts/check_skill_contract.py` after contract-facing edits
- run `python3 skills/convert-external-skills-to-codex/scripts/check_conversion_fixtures.py` after output-contract or fixture changes

## Troubleshooting

- If Codex does not see the refreshed skill after reinstall, restart Codex.
- If runtime-path behavior looks wrong, check `config/runtime.local.toml`, `CODEX_PLAYGROUND_PROJECT_ROOT`, and the resolved `paths.scratch_root`.
