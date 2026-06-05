---
name: jss-vacancy-pipeline
description: "Use for job-search vacancy pipeline workflows over the local job-search-system: import structured or raw user-provided vacancy batches, normalize, dedupe, score and rank vacancies, shortlist, create application drafts, prepare vacancy-specific resume artifacts when needed, prepare review-first application payloads, log interview rounds, touchpoints and reminders, inspect daily actions, and generate pipeline reports without direct database writes."
---

# JSS Vacancy Pipeline

## Purpose

Run the vacancy processing and application-prep workflow through `tools/job-search-system`. Use this skill after candidate intake and at least one resume-positioning pass exist for the active candidate.

## Hard Rules

- Never write SQLite directly.
- Never mutate lifecycle state from AI text or by editing files.
- AI rerank, semantic extraction, summaries, hints, and next-best-action suggestions live in this skill as advisory sidecars over canonical API/CLI results.
- Do not look for or call a backend AI runtime. The backend provides deterministic rank/report/application payloads; this skill may critique or refine them for review only.
- Use API-lite when available, otherwise use `vacancy_cli` fallback for import, ranking, shortlist, processed state, application drafts, payloads, interview rounds, touchpoints, reminders, daily actions, and reports.
- Ranking/scoring is a query/projection step; it must not be described as a lifecycle mutation.
- Application payloads are review-first. Do not claim anything was submitted, sent, or published.
- Vacancy-specific resumes are optional. Create `resume_vacancy` only when requested for a concrete vacancy, and finalize as `resume_vacancy_final` only after explicit acceptance.
- Quality gate result, user content acceptance, and external action approval are separate.
- LinkedIn-specific intake is Stage 2 and must use `import-linkedin-text`; it accepts copied job pages, LinkedIn job-alert/recommended-jobs email text, markdown search-results cards with job URLs, and manually prepared CSV-like rows. Do not imply that LinkedIn has a native CSV vacancy export. Do not use browser automation or generic URL ingestion for LinkedIn job pages.
- hh.ru-specific intake must use `import-hh-ru-text`; it accepts copied single vacancy pages with a URL header and search-results markdown cards with vacancy URLs, and does not perform browser automation.
- URL-only saved job links must use the URL enrichment seed flow: `create-url-seed`, `preview-url-seed`, then `confirm-url-seed-import`. Do not describe URL-only seeds as imported vacancies until confirm succeeds.

## Runtime

Preferred path is API-lite when it is running locally. Check it first:

```bash
curl -sS "$JSS_API_URL/health"
```

Use `JSS_API_URL`, normally `http://127.0.0.1:8765`. If API-lite is unavailable, fall back to CLI:

```bash
PYTHONPATH="$JSS_ROOT/src:$PLAYGROUND_ROOT" python3 -m job_search.interfaces.cli.vacancy_cli \
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

Resolve `PLAYGROUND_ROOT`, `JSS_ROOT`, and `WORKSPACE_PATH` from the current smoke/workspace context. If no active candidate is selected, route to `$jss-candidate-intake` first.

Detailed command map: [references/commands.md](references/commands.md).

## Workflow

1. Verify candidate context and profile readiness.
   - API: `GET /candidates/active` and `GET /candidates/profile`.
   - CLI fallback: candidate CLI `active` and `show-profile`.
   - If there is no confirmed profile, use `$jss-candidate-intake`.
2. Import vacancy batch.
   - API: `POST /vacancies/import-json`.
   - CLI fallback: `import-json --items-path`.
   - Input should be structured JSON, not raw browser automation.
3. Import generic copied vacancy text when explicitly provided.
   - API: `POST /vacancies/import-text`.
   - CLI fallback: `import-text --content-path` or `--content-text`.
   - Text must contain enough title/company information; URL-only input must not be described as imported.
4. Store URL-only saved job links as enrichment seeds when explicitly provided.
   - API: `POST /vacancies/url-seeds`.
   - CLI fallback: `create-url-seed`.
   - A seed is not a vacancy and must not appear in ranking/shortlist before confirm.
5. Preview a URL seed only with manually provided page text.
   - API: `POST /vacancies/url-seeds/preview`.
   - CLI fallback: `preview-url-seed --content-path` or `--content-text`.
   - Preview must yield exactly one vacancy before confirm import.
6. Confirm URL seed import only after reviewing the preview.
   - API: `POST /vacancies/url-seeds/confirm`.
   - CLI fallback: `confirm-url-seed-import`.
7. Import LinkedIn copied/search/email text when explicitly provided.
   - API: `POST /vacancies/import-linkedin-text`.
   - CLI fallback: `import-linkedin-text --content-path` or `--content-text`.
   - Raw copied page text without URL is importable when title/company/location can be extracted, but must return a warning because there is no stable `external_vacancy_id`.
   - URL-only input without title/company must use URL enrichment seed flow.
8. Import hh.ru copied vacancy/search-results text when explicitly provided.
   - API: `POST /vacancies/import-hh-ru-text`.
   - CLI fallback: `import-hh-ru-text --content-path` or `--content-text`.
   - Single vacancy page text must include the vacancy URL at the top.
   - Search-result cards must contain vacancy URL, title, employer and enough location/context to import.
9. Rank vacancies.
   - API: `GET /vacancies/rank`.
   - CLI fallback: `rank`.
   - Review `fit_label`, score reasons, structured `scoring_breakdown`, matched signals, missing signals, dealbreakers, and `needs_review`.
10. Optional AI advisory rerank.
   - Use only after deterministic ranking exists.
   - Review the top relevant vacancies plus any `needs_review` items for semantic fit, noisy text, hidden seniority mismatch, compensation ambiguity, company fit, and missing evidence.
   - Output `advisory_rerank`, `semantic_findings`, `questions_for_user`, and `suggested_next_actions`.
   - Do not change ranking scores, workflow stage, shortlist, processed state, or application state from AI output.
   - If AI finds a parser/import issue, route it through normal import, URL seed preview, or manual review rather than editing SQLite.
11. Shortlist only explicit choices.
   - API: `POST /vacancies/shortlist`.
   - CLI fallback: `shortlist`.
   - Do not auto-shortlist just because score is high unless the user asked for that policy.
12. Create application draft for selected vacancy.
   - API: `POST /vacancies/application-draft`.
   - CLI fallback: `create-application-draft`.
13. Prepare application payload.
   - API: `POST /vacancies/application-payload`.
   - CLI fallback: `prepare-application-payload`.
   - Confirm resume artifact, message artifact, separate quality statuses, combined `application_payload_quality_gate`, and application id.
14. Optional: create a vacancy-specific resume for a priority vacancy.
   - API: `POST /vacancies/resume`.
   - CLI fallback: `generate-vacancy-resume`.
   - Use only when the user wants a tailored CV for that vacancy.
   - If no matching final resume exists, present draft `source_options` and ask the user to choose, even when there is only one draft.
   - Always report the source resume artifact used.
   - Reruns overwrite the same `resume_vacancy` for that candidate/vacancy.
   - Treat deterministic output as skeleton plus quality gate / roast report guidance. Skill-side AI may suggest edits, but persistence still requires a validated artifact command.
15. Optional: finalize the accepted vacancy-specific resume.
   - API: `POST /vacancies/resume-final`.
   - CLI fallback: `finalize-vacancy-resume`.
   - `warn` requires explicit warning acceptance; `fail` blocks.
16. Log touchpoint/reminder only if a real touchpoint exists or is planned.
   - API: `POST /vacancies/touchpoints`.
   - CLI fallback: `create-touchpoint`.
   - Use reminder resolution only after actual completion.
17. Optional: log interview rounds only when an interview is scheduled, planned, completed, or cancelled.
   - API: `POST /vacancies/interview-rounds`, `POST /vacancies/interview-rounds/state`, `GET /vacancies/interview-rounds`.
   - CLI fallback: `create-interview-round`, `update-interview-round`, `list-interview-rounds`.
   - Interview rounds are separate operational records; they do not replace application state.
18. Mark processed only after review decision.
   - API: `POST /vacancies/processed`.
   - CLI fallback: `mark-processed`.
   - Material changes should route to review, not back to `new`.
19. Generate report.
   - API: `GET /vacancies/daily-actions`, `GET /vacancies/material-change-review`, and `GET /vacancies/pipeline-report`.
   - CLI fallback: `daily-actions`, `material-change-review`, and `pipeline-report`.
   - Strategy metrics: `GET /system/strategy-report` or system CLI `strategy-report`.
   - Use strategy report for deterministic funnel, role/company/source conversion, resume effectiveness, and position effectiveness.
20. For manual job-board logging after external operator actions, route to `$jss-job-board-operations`.
   - Completed submit/send/profile-update actions require explicit external action approval; quality gates and content acceptance are not enough.

## Output Contract

Return:

- candidate id
- imported count and dedupe/material-change summary
- ranked shortlist candidates with reasons
- AI advisory rerank or semantic findings, if requested
- selected vacancy id
- application id and artifact ids, if created
- vacancy-specific resume artifact id and source resume artifact id, if created
- interview round ids, if created
- touchpoint/reminder ids, if created
- processed/material-change status
- next manual review actions

Do not paste full vacancy payloads unless asked.
