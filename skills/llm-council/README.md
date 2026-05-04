# llm-council

Local Codex skill for running a structured multi-agent decision review.

## Purpose

Use `llm-council` when one answer is not enough and the user wants a decision pressure-tested from conflicting angles before acting.

The skill:

- frames one neutral decision brief
- runs five advisor perspectives in parallel
- anonymizes the responses for peer review
- synthesizes one final verdict with a clear recommendation and first step
- saves a canonical payload JSON
- delegates final verdict-note writing to `article-to-obsidian-kb` structured mode
- treats the saved `council-payload-...json` as the single canonical run artifact and uses it as the source for all downstream artifacts

This skill is for judgment-heavy questions, not for factual lookups or routine implementation work.

## How To Run

Invoke the skill from Codex with prompts such as:

- `$llm-council проанализируй это решение`
- `Council this decision`
- `Pressure-test this plan`

Use it as a one-shot decision workflow inside a Codex session. It is not a daemon, listener, or scheduled background tool.

## Source Of Truth

- Repository source of truth: `skills/llm-council/`
- Installed Codex copy: `~/.codex/skills/llm-council`

Edit the repository copy first. Reinstall into `~/.codex/skills` after changes.

## Installation

Install or refresh the local Codex copy with:

```bash
bash skills/llm-council/install-local.sh
```

The installer copies the skill into `~/.codex/skills/llm-council`, but it does not keep a second writable runtime config there. Instead it links `~/.codex/skills/llm-council/config/runtime.local.toml` back to the repository copy so the repository remains the single editable config source of truth.

If the current Codex session does not see the updated skill immediately, restart Codex.

## Artifacts

The skill writes one canonical artifact from `config/runtime.local.toml`:

- `paths.temp_root/council-payload-YYYYMMDD-HHMMSS.json`

The final verdict note is no longer written by `llm-council` directly. It is written downstream by `article-to-obsidian-kb` in explicit `structured` mode with `type=council-verdict`.

There is no separate persistent transcript artifact in the normal pipeline. The default audit trail is the saved `council-payload-YYYYMMDD-HHMMSS.json`.

## Local Runtime Behavior

- loads the default artifact destination from `config/runtime.local.toml`
- uses `paths.temp_root` as the canonical default directory for council payload files
- enforces canonical payload writes to stay under the configured `paths.temp_root`
- resolves reviewer batch size from `phases.reviewer_count`, with `3` as the built-in default
- resolves a soft payload cleanup stage from `payload_cleanup.enabled`, with `true` as the built-in default
- applies that cleanup before persisting the canonical `council-payload-...json`, not only during downstream note generation
- resolves advisor, reviewer, and chairman model settings from the same local config
- keeps the advisor, reviewer, and chairman output language aligned with the dominant language of the original user question
- treats `run_status=full` as valid only when the payload contains exactly 5 completed advisor responses and a non-empty peer-review list
- delegates final verdict-note writing to `article-to-obsidian-kb` structured mode, which owns verdict placement and final note contract
- treats the repository copy of `config/runtime.local.toml` as the single editable local config
- expects the payload directory to exist already and does not treat directory creation as a normal per-run step

## Main Files

- `SKILL.md`: runtime workflow entrypoint for the skill
- `config/runtime.example.toml`: template for local payload-path, model, and cleanup configuration
- `agents/openai.yaml`: UI-facing skill metadata
- `scripts/render_common.py`: shared normalization and path-validation helpers for council payload handling
- `scripts/council_payload_schema.py`: canonical executable parser/validator for `council-verdict` payloads, reused by downstream consumers
- `scripts/prepare_canonical_payload.py`: writes an already-normalized canonical `council-payload-...json`
- `scripts/render_council_report.py`: adapter that converts a council payload into `article-to-obsidian-kb` structured-note writing
- `install-local.sh`: install or refresh the skill into `~/.codex/skills`

## References

- `references/role-prompts.md`: defines advisor, reviewer, chairman, and shared role-output formatting rules
- `references/payload-contract.md`: defines the canonical council payload JSON and its text-format rules

## Maintenance

- Treat `skills/llm-council/` in this repository as the editable source of truth.
- Treat `~/.codex/skills/llm-council` as an installed copy only.
- Treat `~/.codex/skills/llm-council/config/runtime.local.toml` as a symlinked consumer of the repository config, not as a second editable config.
- Treat `config/runtime.example.toml` as the canonical place for documented config keys, defaults, and inline operator notes about local runtime behavior.
- Do not patch the installed copy directly unless the user explicitly asks for an emergency local-only fix.
- Run the local regression harness with `python3 -m unittest discover -s skills/llm-council/tests -q` after renderer or contract changes.
- Prefer changing canonical reference files over scattering prompt or formatting tweaks into several places.
- Keep workflow and output-contract rules in their canonical owners:
  - orchestration and payload rules in `SKILL.md`
  - advisor/reviewer/chairman prompt rules in `references/role-prompts.md`
  - payload JSON shape and payload text-format rules in `references/payload-contract.md`
  - final verdict-note format and placement rules in `article-to-obsidian-kb`
- After contract or adapter changes, run the unittest harness, then run a small payload smoke test through the downstream structured writer path.
- If the skill grows new reusable operational patterns, prefer codifying them in `RULEBOOK.md`.
