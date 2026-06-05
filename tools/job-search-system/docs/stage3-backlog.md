# Stage 3 Implementation Plan

This document is the single source of truth for remaining work after the closed Stage 1 and Stage 2 non-UI scope.

Coverage audit lives in `docs/capability-coverage.md`. Every capability from `Job/job-search-skills` must either be implemented in backend/API/CLI, implemented as a skill wrapper, or explicitly deferred here.

This backlog is a temporary work plan for unresolved work only. After a group or item is implemented, tested, and reflected in `docs/capability-coverage.md`, remove the completed item from this file or rewrite it to only the unresolved remainder.

## How To Use This Plan

Use this loop for every Stage 3 change:

1. Pick a numbered group from this plan.
2. Find the corresponding row or rows in `docs/capability-coverage.md`.
3. Open the source documents from `Job/job-search-skills/00-10` referenced by this plan or the coverage row.
4. Implement through the existing command/query/API boundary; UI, skills, and AI must not write to SQLite directly.
5. Add or update tests and smoke/contract coverage for the changed surface.
6. Update `docs/capability-coverage.md`: move the capability from `explicitly deferred to stage3-backlog.md` to the implemented status that matches the actual surface.
7. Remove the completed item from this plan or rewrite it to only the remaining unresolved part.
8. Record the verified implementation fact in `Job/job-search-skills/10-job-search-implementation-journal.md`.

Keep responsibilities separated:

- `Job/job-search-skills/00-10` defines what the system should do and why.
- `docs/capability-coverage.md` tracks whether each planned capability is implemented, wrapped by a skill, or deferred.
- `docs/stage3-backlog.md` tracks only what still needs to be done.

## Scope Rules

- Stage 1 and Stage 2 non-UI are treated as closed unless a regression is found.
- New UI, skills, browser, board, or automation work must use API-lite or new versioned API routes over existing command/query handlers.
- UI, skills, and AI must not write to SQLite directly.
- UI-specific API expansion must come with API contract tests before UI code depends on it.
- External actions remain review-first and require explicit action approval; no hidden automation.

## Implementation Groups

Groups are ordered from lower implementation risk to higher implementation risk. UI is intentionally last.

### 1. Documentation, Contract Hygiene, And Schema Compatibility

Status:

- No remaining backlog items in this group.

### 2. Resume Artifact Quality And Operator Export

Status:

- No remaining backend/API/CLI backlog items in this group.
- Platform/profile variants moved to group `11` because they depend on future board/profile publishing flows.

### 3. Vacancy Intake Adapters Beyond LinkedIn Text

Status:

- No remaining active backlog items in this group.
- Generic copied vacancy text is the fallback for new platforms.
- Add new platform-specific adapters only when a real repeated format appears and generic import is insufficient.

### 4. Candidate Review Backend And Durable Matching Rules

Status:

- No remaining backend/API/CLI backlog items in this group.
- UI-specific candidate review remains covered by group `15`.

### 5. Vacancy Scoring, Queues, Reports, And Follow-Up Policy

Backlog:

- Add scoring calibration for weights and thresholds after enough reviewed vacancy batches exist.
- Add stronger negative-outcome review policy if the current `skip` suppression and `low` downgrade still create noise.
- Add smarter follow-up logic and `close-by-silence` policy after manual flows prove useful.
- Add FX-aware cross-currency compensation comparison only if exact-currency matching is insufficient.
- Add advanced dedupe heuristics only after baseline dedupe failures are observed on real batches.
- Add additional ranking signals only when concrete false positives/false negatives are collected.

Source docs:

- `Job/job-search-skills/01-job-search-scope-and-skills.md`
- `Job/job-search-skills/03-job-search-storage-and-state.md`
- `Job/job-search-skills/04-job-search-artifacts-and-contracts.md`
- `Job/job-search-skills/06-job-search-rollout.md`
- `Job/job-search-skills/08-job-search-testing-observability-and-compatibility.md`

### 6. Approval Policy, Application Payload Gates, And Artifact Lifecycle

Backlog:

- Add stronger artifact versioning, status model, lineage, diffing, and history auditability only when the current artifact registry becomes insufficient.
- Add richer publication/submission traceability only if manual board actions start requiring more detailed external proof.

Source docs:

- `Job/job-search-skills/04-job-search-artifacts-and-contracts.md`
- `Job/job-search-skills/05-job-search-policies-and-guardrails.md`
- `Job/job-search-skills/08-job-search-testing-observability-and-compatibility.md`

### 7. Operational Entities: Interviews, Companies, Contacts, And Salary Bands

Backlog:

- Add richer interview-round fields only after real interview workflows require them, for example evaluation notes, feedback outcome, preparation checklist links, or panel members.
- Add normalized `companies` only when repeated company-level matching, analytics, or board reconciliation require more than `canonical_vacancies.company_name`.
- Add normalized `contacts` only when real recruiter/hiring-manager tracking exceeds touchpoint `contact_name`.
- Add normalized `salary_bands` only when compensation analytics require more than vacancy salary parsing and candidate compensation preferences.
- Keep future CRM additions optional and event-driven; do not create empty company/contact/salary records just because a vacancy exists.

Source docs:

- `Job/job-search-skills/03-job-search-storage-and-state.md`
- `Job/job-search-skills/06-job-search-rollout.md`
- `Job/job-search-skills/07-job-search-ui-architecture.md`

### 8. Observability And Strategy Feedback

Backlog:

- Add deeper channel/market conversion analysis after source/channel taxonomy is stable enough.
- Add richer quality trend analysis over time windows after more quality gate history exists.
- Add signal-based strategy adjustment reports after real batches produce enough false positives, false negatives, submissions, responses, and interviews.
- Add deeper resume/position effectiveness analytics only after more recorded outcomes exist, for example time-window comparison, normalized response rate, and controlled comparison between resume versions.
- Keep future metrics deterministic; AI may summarize already computed signals but must not compute canonical metrics.

Source docs:

- `Job/job-search-skills/08-job-search-testing-observability-and-compatibility.md`
- `Job/job-search-skills/06-job-search-rollout.md`

### 9. Career Pathing Full Mode

Status:

- No remaining backlog items in this group.

### 10. Reconciliation And Board-Side State Sync

Status:

- No remaining backlog items in this group.

### 11. Board And External Automation Decisions

Backlog:

- Decide separately whether authenticated board reading or browser-assisted flows are worth implementing.
- Any future browser-assisted work must explicitly define credentials handling, site terms/rate-limit risks, approval boundaries, audit events, and failure modes before implementation.
- Add supervised publish/update/refresh flows only after the manual board workflow is stable on a target platform.
- Add platform-specific resume/profile variants, headline/summary variants, and market wording harvest as first-class persisted artifacts only when a concrete board/profile publishing flow needs them.
- Add platform-specific operator flows for `hh.ru`, `LinkedIn`, and other boards only one platform at a time.
- Unattended submit/publish/send remains out of scope unless separately approved.

Source docs:

- `Job/job-search-skills/01-job-search-scope-and-skills.md`
- `Job/job-search-skills/04-job-search-artifacts-and-contracts.md`
- `Job/job-search-skills/05-job-search-policies-and-guardrails.md`
- `Job/job-search-skills/06-job-search-rollout.md`
- `Job/job-search-skills/07-job-search-ui-architecture.md`

### 12. Skill-Side AI Reasoning

Status:

- No remaining active backlog items in this group.

### 13. Scheduled Jobs And Shared Runtime Extraction

Status:

- No remaining active backlog items in this group.

### 14. Telegram Companion Surface

Status:

- Deferred until after the first Web UI slice from group `15`.
- Telegram is a lightweight companion/inbox/control surface, not a replacement for Web UI.
- Current decision: do not implement Telegram now; proceed to group `15`.

Backlog:

- Reuse `telegram_agent_bot` as the preferred owner when Telegram work resumes.
- Implement explicit mode switching instead of mixing all inputs in one command surface:
  - `/agent` switches the chat to normal agent mode; plain text goes to the existing agent worker.
  - `/jss` switches the chat to job-search mode; plain text is interpreted only as job-search intent.
  - `/mode` shows the current mode.
  - `/jss_help` shows supported job-search actions.
  - `/jss_confirm <operation_id>` confirms a pending state-changing action.
  - `/jss_cancel <operation_id>` cancels a pending action.
  - `/jss_cancel_all` clears pending job-search operations for the chat/user.
- Keep `telegram_agent_bot` allowlists and access-control model.
- Allow plain text in `/agent` mode for advisory work, but do not let it execute job-search shell commands or mutations directly.
- In `/jss` mode, route only to allowlisted local job-search capabilities:
  - submit URL or copied vacancy text for local intake
  - daily actions
  - read-only reports and metrics
  - workflow state changes through command handlers
- Use the simplest reliable pending confirmation storage, preferably Telegram-local runtime state under `telegram_agent_bot/data/`.
- Keep external actions out of Telegram v1:
  - no submit
  - no send
  - no publish
  - no profile update
  - no browser automation
- Add tests proving Telegram cannot bypass command handlers, approval separation, or external-action guardrails.

Source docs:

- `Job/job-search-skills/01-job-search-scope-and-skills.md`
- `Job/job-search-skills/06-job-search-rollout.md`

### 15. Workflow UI And UI-Specific API Expansion

Why last:

- UI should sit on stable command/query/API contracts, not drive the domain model prematurely.
- Earlier groups define the state, approvals, queues, review surfaces, and traceability the UI needs.
- This is the largest productization step.
- This is now the next implementation focus after deferring Telegram.
- Web UI is the primary operational workspace; Telegram remains a later companion surface.

Capabilities:

- Create a local UI prototype over API-lite; do not call repositories, SQLite, or CLI commands from UI code.
- Use Build Web Apps for the first UI implementation pass.
- Add UI-needed read routes as versioned API endpoints with contract tests before UI code depends on them.
- Start with read/review/approve flows before adding any external-action surfaces.
- Keep the UI local-first and loopback-only unless a separate deployment/security design is approved.

Minimum UI slice:

- queue view for new/review vacancies
- daily action view for follow-ups, review items, interview reminders, and reconciliation items
- quick manual transitions for vacancy workflow stage, vacancy flags, application state, and touchpoint state
- projections/query views for queues and daily actions

Advanced UI slice:

- full vacancy workspace over the state machine
- application board with fast status updates
- touchpoint timeline by vacancy, application, and contact
- reminder center and follow-up planner
- reconciliation review screen for board/internal drift
- interview rounds screen
- filters, bulk actions, and saved views for operational queues
- artifact usage drill-down showing which resume/message artifact was used in which application or touchpoint
- candidate-source conflict review screen
- artifact acceptance and external action approval screens

Source docs:

- `Job/job-search-skills/02-job-search-core-architecture.md`
- `Job/job-search-skills/03-job-search-storage-and-state.md`
- `Job/job-search-skills/04-job-search-artifacts-and-contracts.md`
- `Job/job-search-skills/05-job-search-policies-and-guardrails.md`
- `Job/job-search-skills/06-job-search-rollout.md`
- `Job/job-search-skills/07-job-search-ui-architecture.md`
- `Job/job-search-skills/08-job-search-testing-observability-and-compatibility.md`

## ChatGPT UI Handoff Guidance

ChatGPT UI live/spoken scenarios are deliberately not local skills or backend workflows by default.

Use ChatGPT UI for:

- audio mock interviews
- recruiter screen simulation
- executive interview simulation
- live behavioral drill
- spoken English practice for interviews
- spoken salary negotiation practice

Add repeatable handoff docs only if operator workflow needs stable links between local artifacts and conversation-only practice sessions.

## Not Planned By Default

- Multi-user remote deployment.
- Cloud synchronization.
- Background external sends or submits.
- Direct scraping of authenticated job boards.
- Replacing API-lite with a broader API-first platform before UI needs justify it.
- Unattended submit/publish/send unless separately approved.

## Artifact Retention Guidance

Runtime artifacts under `tools/job-search-system/data/artifacts/` are generated local data and are ignored by git.

Keep only artifacts that are still useful for manual inspection:

- the latest successful smoke run candidate folders with human-readable slugs
- the latest Candidate A and Candidate B `sources/` used for smoke debugging
- the latest generated `drafts/` needed to inspect resume quality warnings or application payload output
- any artifact id that is referenced by the current SQLite database you plan to keep

Use `job-search-system cleanup-artifacts` for repeatable cleanup instead of ad hoc SQL plus manual folder deletion. Run it without `--apply` first, inspect `delete_candidate_ids` and `delete_artifact_folders`, then rerun with `--apply`.

Operator workflow and artifact naming rules live in `docs/user-guide.md`.

Everything else can be deleted as local generated data, especially:

- old UUID-only candidate folders from earlier prototype runs
- duplicate smoke candidate folders from failed or superseded runs
- `.DS_Store` files
- artifacts whose database was already reset or discarded
- drafts that are not needed for current quality-gate/debug inspection
