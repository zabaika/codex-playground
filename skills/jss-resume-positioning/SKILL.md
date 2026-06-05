---
name: jss-resume-positioning
description: "Use for job-search resume positioning workflows over the local job-search-system: generate role-based and vacancy-aware markdown resume artifacts, produce positioning briefs, run resume quality gates, create persisted resume roast reports linked to source resume drafts, finalize accepted resume artifacts, and prepare draft-only AI rewrite guidance without directly mutating canonical state."
---

# Job Search Resume Positioning

## Purpose

Create and review resume-positioning artifacts for a confirmed candidate profile through `tools/job-search-system`. Use this skill after candidate intake has created and confirmed enough profile data.

## Hard Rules

- Never write SQLite directly.
- Never edit persisted artifacts manually and then pretend they are registered artifacts.
- Use `generate-resume`, `generate-positioning-brief`, and `check-resume` through the CLI/command layer.
- AI rewrite or improvement suggestions are draft-only unless imported through a future validated artifact command.
- Resume roast reports are persisted only through `roast-resume` / `/candidates/resume-roast`.
- Vacancy-specific resumes are persisted only through `generate-vacancy-resume` / `/vacancies/resume` and finalized through `finalize-vacancy-resume` / `/vacancies/resume-final`.
- Vacancy-specific artifact types are `resume_vacancy` and `resume_vacancy_final`; do not call them rewrite artifacts.
- AI review, rewrite guidance, and semantic critique live in this skill. The backend persists only registered artifacts created through validated commands.
- Do not look for or call a backend AI runtime. Use backend artifacts, quality gates, roast reports, and KB evidence as inputs for skill-side reasoning.
- Do not invent facts, numbers, employers, responsibilities, credentials, or achievements.
- Quality gate passing is not user acceptance and not external-action approval.
- Final resume artifacts require explicit user acceptance. `warn` can be finalized only when the user explicitly accepts warnings; `fail` blocks finalization.

## Runtime

Preferred path is API-lite when it is running locally. Check it first:

```bash
curl -sS "$JSS_API_URL/health"
```

Use `JSS_API_URL`, normally `http://127.0.0.1:8765`. If API-lite is unavailable, fall back to CLI:

```bash
PYTHONPATH="$JSS_ROOT/src:$PLAYGROUND_ROOT" python3 -m job_search.interfaces.cli.candidate_cli \
  --config-path "$JSS_ROOT/config/runtime.local.toml" \
  --workspace-path "$WORKSPACE_PATH" \
  <command>
```

To start API-lite when needed:

```bash
PYTHONPATH="$JSS_ROOT/src:$PLAYGROUND_ROOT" python3 -m job_search.interfaces.api.server \
  --config-path "$JSS_ROOT/config/runtime.local.toml" \
  --workspace-path "$WORKSPACE_PATH" \
  --host 127.0.0.1 \
  --port 8765
```

Resolve `PLAYGROUND_ROOT`, `JSS_ROOT`, and `WORKSPACE_PATH` exactly as in the current job-search smoke context. If the workspace is unclear, ask before running commands.

Detailed command map: [references/commands.md](references/commands.md).

## Workflow

1. Verify candidate context.
   - API: `GET /candidates/active` and `GET /candidates/profile`.
   - CLI fallback: `active` and `show-profile`.
   - If core profile is empty or unconfirmed, route to `$jss-candidate-intake` first.
2. Generate a positioning brief.
   - Optional: search KB evidence first through `GET /candidates/resume-kb-evidence` or CLI `search-resume-kb-evidence`.
   - Treat KB evidence as read-only supporting context, not as confirmed candidate facts.
   - If `candidate_review_suggestions` are returned, present them as questions for the user and route confirmed facts through `$jss-candidate-intake`.
   - Do not put a suggested KB signal into a resume until candidate evidence is confirmed or imported.
   - API: `POST /candidates/positioning-brief`.
   - CLI fallback: `generate-positioning-brief --target-role`.
   - Treat the brief as strategy/review artifact, not as canonical profile truth.
3. Generate a markdown resume artifact.
   - API: `POST /candidates/resume`.
   - CLI fallback: `generate-resume --target-role`.
   - Set language explicitly when needed.
4. Run quality gate.
   - API: `POST /candidates/resume-quality`.
   - CLI fallback: `check-resume --artifact-id`.
   - If the gate fails, explain the blocking issue and do not prepare external use.
5. Create a persisted roast report when requested.
   - API: `POST /candidates/resume-roast`.
   - CLI fallback: `roast-resume --artifact-id`.
   - The report is linked to the source resume through `derived_from_artifact_id`.
   - One resume draft has one roast report; reruns overwrite the same report artifact.
   - Store it under `drafts/resume-roast-report--<role>-for-resume-<resume-short-id>--<artifact-short-id>.md`.
6. Finalize only after explicit user acceptance.
   - API: `POST /candidates/resume-final`.
   - CLI fallback: `finalize-resume --artifact-id`.
   - For `warn`, use `--allow-warnings` only after the user explicitly accepts the warning.
   - Final artifacts stay inside `data/artifacts/.../final/`, not in an external `Job/Resume` export.
7. Create a vacancy-specific resume only when requested for a specific vacancy.
   - API: `POST /vacancies/resume`.
   - CLI fallback: vacancy CLI `generate-vacancy-resume --canonical-vacancy-id`.
   - If a final resume exists for the same role, the backend uses it as source.
   - If no matching final exists, present draft `source_options` and ask the user which source to use, even when there is only one draft.
   - Report the source resume artifact id and type.
   - Reruns overwrite the same `resume_vacancy` for that candidate/vacancy.
   - Treat deterministic output as skeleton plus quality gate / roast report guidance. Skill-side AI may suggest concrete edits, but it must not be presented as persisted until a validated artifact command exists.
8. Finalize vacancy-specific resume only after explicit user acceptance.
   - API: `POST /vacancies/resume-final`.
   - CLI fallback: vacancy CLI `finalize-vacancy-resume --artifact-id`.
   - The final artifact type is `resume_vacancy_final`.
9. Optional AI sidecar review.
   - Read the generated artifact only if needed.
   - Compare the artifact against the confirmed candidate profile, target role, vacancy text when relevant, quality gate output, roast report, and KB evidence if available.
   - Produce suggested edits as review notes unless there is a validated command to persist a new artifact.
   - Separate supported edits, user-confirmation questions, and unsafe claims.
   - Mark any assumed or unsupported claim as unsafe.
10. Report artifact ids and quality status.

## Output Contract

Return:

- candidate id
- target role and language
- positioning artifact id, if generated
- resume artifact id
- roast report artifact id, if generated
- final resume artifact id, if finalized
- vacancy resume artifact id and source resume artifact id, if generated
- quality gate result
- unresolved risks or unsupported claims

Keep outputs short. Do not paste the full resume unless asked.
