# Telegram Companion Contract

This document defines the allowed design for a future Telegram companion surface for `job-search-system`.

Telegram must stay a thin control/review surface over existing `job-search-system` command/query/API contracts. It must not own job-search state, parse vacancy business logic independently, write SQLite directly, or perform external actions without explicit approval.

## Status

- Design contract only.
- No Telegram bridge or daemon is implemented for `job-search-system`.
- Telegram implementation is deferred until after the first Web UI slice.
- Web UI is the primary operational workspace; Telegram is a later lightweight companion surface.
- Current preferred owner for future work is `telegram_agent_bot`.

## Candidate Integration Options

Use this decision order:

1. Reuse `telegram_agent_bot` as the preferred owner.
2. Reuse `telegram_connector` patterns only for Bot API bridge, allowlists, secret handling, and delivery mechanics.
3. Add a separate `job-search-system` Telegram bridge only if `telegram_agent_bot` creates unsafe coupling.

Do not merge `telegram_connector`, `telegram_agent_bot`, or `job-search-system` runtime state.

## Relationship To Web UI

Telegram and Web UI should not be functionally identical.

Web UI is the primary operational workspace for:

- queue views
- candidate-source conflict review
- vacancy workspace
- artifact lineage
- approval screens
- reconciliation-heavy workflows
- application board
- touchpoint timelines
- dashboards and metrics

Telegram is a companion surface for:

- submitting a vacancy URL or copied vacancy text
- showing daily actions
- showing short read-only reports and metrics summaries
- quick workflow state changes with explicit confirmation
- lightweight mobile review when a full workspace is unnecessary

Do not implement Telegram before the first Web UI slice unless a concrete operational need appears that Web UI and skills cannot cover.

## Mode Switching

Future Telegram v1 should use explicit per-chat/user modes:

- `/agent` switches to normal agent mode.
- `/jss` switches to job-search mode.
- `/mode` shows the current mode.
- `/jss_help` shows supported job-search actions.
- `/jss_confirm <operation_id>` confirms a pending state-changing action.
- `/jss_cancel <operation_id>` cancels a pending state-changing action.
- `/jss_cancel_all` clears pending job-search operations for the chat/user.

In `/agent` mode, plain text goes to the existing Telegram agent worker for advisory work. It must not execute job-search shell commands or mutate `job-search-system` state directly.

In `/jss` mode, plain text may be interpreted as job-search intent, but only through a small allowlisted action set. If intent is unclear, the bot must ask for clarification or point to `/jss_help` instead of guessing.

## Allowed Capabilities

Telegram may expose read/review workflows:

- show active candidate summary
- show daily actions
- show pipeline report
- show ranked vacancies
- show material-change review items
- show reconciliation review items
- show pending URL enrichment seeds
- request a skill-side advisory rerank summary
- ask for a draft application payload preview

Telegram may initiate local internal mutations only with explicit confirmation:

- shortlist a vacancy
- mark vacancy processed
- resolve a reminder
- resolve a reconciliation item
- workflow state changes through existing command handlers

External action approvals are intentionally excluded from Telegram v1.

Telegram must not perform external actions:

- no submit
- no send
- no publish
- no profile update
- no browser automation
- no board-side refresh

If a later version adds external action approvals, it must be separately approved and must identify candidate, platform, action type, artifact, target vacancy/application, and exact external target.

## Pending Confirmation Model

Any Telegram-triggered mutation must use a two-step confirmation:

1. `prepare`: resolve candidate, command, target entity, expected state change, and risk summary.
2. `confirm`: execute the deterministic command only if the confirmation references the prepared operation id.

Pending confirmations must expire quickly and be scoped to:

- chat id
- user id or username
- candidate id
- command name
- target entity id
- prepared payload hash

Expired or mismatched confirmations must be rejected.

## Runtime State

Runtime state belongs in the Telegram project data directory, not in `job-search-system` canonical tables, except for mutations that go through existing command handlers.

Allowed Telegram-local state:

- update offset
- redacted inbound/outbound summaries
- pending confirmation records
- short-lived conversation context

Forbidden Telegram-local state:

- duplicate vacancy lifecycle
- duplicate application state
- duplicate candidate profile facts
- duplicate artifact registry
- hidden board-side status

## Secrets And Access Control

Follow the existing Telegram project conventions:

- keep `runtime.local.toml` untracked
- prefer `keychain://...` secret references
- require chat/user allowlists
- resolve bridge secrets at startup when possible
- pass only an allowlisted secret environment to child workers
- never print secrets or raw tokens in logs, replies, tests, or docs

## Output Rules

Telegram output must be compact and safe:

- Russian by default
- short sections
- flat bullets
- no tables
- no full resumes or full vacancy payloads unless explicitly requested
- redact or omit sensitive paths, emails, phones, and tokens where possible

## Implementation Gate

Before coding a Telegram companion, decide:

- Confirm that the first Web UI slice is already delivered or that Telegram has a concrete operational need that cannot wait.
- Which exact commands are exposed in v1.
- Where pending confirmations are stored.
- How `/agent` and `/jss` modes are stored and reset.
- Which tests prove that Telegram cannot bypass command handlers or approval boundaries.
