# Rulebook

This rulebook captures the operational and security conventions used in this project so they can be reused when building other tools.

## Purpose

Use this document as a default template for local software, automations, skills, bots, bridge daemons, schedulers, and ingestion tools.

The goal is to standardize:

- secure handling of secrets
- project-local runtime data and logs
- safe command and operator-surface execution
- reproducible local service setup
- test expectations before changes ship

## Document Roles

Keep documentation split by responsibility:

- `RULEBOOK.md`: cross-project engineering policy, security rules, daemon/runtime conventions, shared architecture guidance
- `<project>/AGENTS.md`: project-specific coding-agent contract, boundaries, and local checklists
- `<project>/README.md`: operator-facing setup, config, commands, runtime behavior, and troubleshooting

Rules:

- put a rule in only one primary place unless a short pointer is needed elsewhere
- prefer links to duplication when a project follows a repository-wide rule
- if behavior changes, update code, tests, and the relevant source-of-truth document in the same change
- for any operational fact such as route decisions, chosen engines, selected inputs, or resolved config, define one canonical producer and treat every other layer as a consumer of that fact
- do not create parallel summaries, shadow metadata, or alternate debug formats when the canonical producer already emits the needed information
- if a wrapper or orchestration layer needs to expose upstream metadata, pass through or parse the canonical upstream artifact instead of reconstructing it with local placeholders or guessed values
- for user-facing output contracts such as final summaries, created-vs-updated reports, or section ordering, keep one canonical definition in the deepest owning workflow and make wrappers inherit it instead of restating the same format in multiple places
- apply the same inheritance rule to final validation layers: if a wrapper delegates note creation, document rendering, or another structured-output workflow to a deeper canonical owner, the wrapper must inherit that owner's final compliance passes and quality gates instead of silently shortening the validation path
- when a workflow grows beyond a few pages of rules, split it into one thin entrypoint plus canonical reference docs; keep the entrypoint focused on sequencing and keep detailed policy in the deepest owning reference file
- for each major rule family such as formatting, tag policy, update semantics, or test coverage, keep exactly one canonical documentation owner and make every other file point to it instead of restating the same contract in full
- if an entrypoint file still repeats a rule family in short form, keep it as a brief guardrail summary only; the detailed wording, examples, and edge cases must still live in the canonical owner
- when an entrypoint delegates formatting or content rules to a canonical reference document, it must still put every major rule family from that canonical owner onto the mandatory execution path of the workflow; a vague pointer like "apply conventions" is not enough when operators or agents could otherwise skip whole families such as links, closing-section hygiene, tag families, or language cleanup
- when creating links between knowledge objects, prefer topical identity over lexical similarity; a shared author, podcast series, brand shell, or similar title is not enough to establish a durable relation on its own
- when creating top-level knowledge objects, do not promote a narrow source-local decision filter or one-off heuristic into its own durable node unless it is likely to be reused across multiple future notes; if its best role is to sharpen one recommendation inside one source note, keep it there
- when deciding whether to create a top-level knowledge object, prefer keeping material inside the source-derived note if it mainly restates that note's own thesis, reads like a detachable subsection, or would realistically have no meaningful backlinks beyond the current source and a couple of sibling nodes from the same run
- when a workflow relies on structured metadata such as frontmatter, route payloads, manifests, or config blocks, validate that metadata after the final manual rewrite or merge instead of trusting an earlier draft; a finished-looking body does not make partial metadata acceptable
- when a new canonical document absorbs, renames, or replaces an older one, preserve the older document's surviving provenance in the new structured metadata instead of dropping source references, tags, or other still-valid context during the merge
- when a workflow produces structured documents with formatting and schema rules, run one final holistic compliance pass after any late manual edit or merge instead of validating only the one thing that was just changed; re-check the whole document contract in its final form because small late fixes often regress unrelated rules
- when those structured-document workflows are especially prone to late manual edits, formatting repairs, link cleanup, or language rewrites, add a second final regression sweep with the same coverage as the first compliance pass so the document survives two identical whole-note checks before it is considered done
- when such a workflow updates an already existing structured document, apply both final whole-note passes to the fully merged saved artifact rather than only to the appended delta; touching a legacy document is an opportunity to bring the whole document up to the current contract instead of preserving stale violations outside the latest edit
- when documenting a workflow rule, prefer concrete behavioral requirements over vague verbs such as `append`, `clean up`, `normalize`, `improve`, `update`, or `fix` when more than one exact operation is plausible
- if a rule depends on position, ordering, insertion point, stop condition, or fallback behavior, spell that out explicitly instead of relying on implication or common sense
- when a workflow also maintains a local contract checker or regression harness for those structured outputs, keep that harness focused on mechanically checkable constraints such as schema, formatting, links, and explicit preservation rules; do not pretend it can prove the full semantic quality of AI interpretation
- when a new mechanically checkable output rule is added to such a workflow, update the checker or regression fixtures in the same change so the documented contract and the executable contract do not drift apart
- when a workflow already has a local contract harness, any change to that workflow's output contract, checker, or contract-facing documentation should trigger the harness before the change is considered complete, even if the edit looks like "docs only"
- when a workflow saves structured knowledge notes, the final prose should read as standalone knowledge rather than as commentary on source order, draft history, or merge mechanics; keep provenance in structured metadata and only mention the source in the body when it is itself a useful case or comparison

The rulebook is intentionally broader than Telegram projects. When a rule names Telegram, bot commands, channels, Telethon, or Bot API specifics, treat that as a domain-specific specialization of the broader engineering rule rather than as the only supported scope.

## 1. Architecture Rules

Use a split architecture instead of a single monolith:

- `interaction bridge`: receives operator input from chat, CLI, webhook, UI, or another thin interface layer and returns results
- `worker/data client`: performs actual business operations
- `runtime config`: local-only machine config
- `project data`: SQLite, offsets, exports, media, logs
- `service wrapper`: background daemon runner

Recommended pattern:

- keep transport- or surface-specific API logic in one script or module
- keep heavy business, data, or history logic in a separate script or module
- let the thin interface layer invoke the worker/data client through a stable contract
- keep command syntax stable across the user-facing surfaces that intentionally expose the same action
- separate data ingestion from downstream enrichment, AI analysis, export, or delivery whenever possible
- treat persisted raw data as the system of record and run expensive analysis as a second stage over stored data
- if multiple sources are processed in one run, keep source-level work units isolated so failures and summaries can be reported per source
- when outputs are independently useful, prefer progressive delivery per source over waiting for one final all-or-nothing payload

Telegram-specific example:

- keep Telegram Bot API logic in one script
- keep Telethon or other heavy Telegram data logic in a separate script

## 2. Config Rules

Tracked config:

- commit `config/runtime.example.toml`
- keep only examples and non-sensitive defaults there

Local config:

- use `config/runtime.local.toml`
- do not commit it
- keep all machine-specific settings there

Rules:

- command-line explicit values override config
- if an operator omits an input target, source, profile, or scope selector, use configured defaults
- if an operator explicitly provides a target, source, profile, or scope selector, ignore conflicting config defaults for that run
- for local Codex skills, keep exactly one editable `config/runtime.local.toml` in the repository skill folder
- if the skill is also installed under `~/.codex/skills`, the installed copy should point to that same repo file instead of keeping a second divergent local config
- if a skill is only an orchestration layer over other local skills, prefer pointing at the sibling skills' local configs instead of copying the same machine-specific values into another file

Telegram-specific example:

- if a command omits a channel, use configured defaults
- if a command includes a channel or channel list, ignore config defaults

Telegram-specific default channels format:

```toml
[channels]
default_list = [
  "@vcnews, vc.ru",
  "@another_channel, Another Channel",
]
```

Interpretation:

- store entries as `"channel, display name"`
- use only the channel reference in code unless display name is explicitly needed

## 3. Secrets Rules

Do not commit secrets in tracked files.

Preferred storage:

- macOS Keychain generic-password references via `keychain://<service>/<account>`
- `op://...` remains acceptable as a legacy fallback, but not as the primary backend for unattended local daemons or scheduled jobs

Suggested Keychain accounts:

- `api_id`
- `api_hash`
- `bot_token`
- `phone`
- `user_password`

Preferred resolution order:

1. environment variable
2. `keychain://...` reference
3. `op://...` reference
4. local plaintext fallback in `runtime.local.toml`

Rules:

- never print secret values to stdout/stderr
- never send secret values back to any operator-facing surface
- never include secrets in tests
- never include secrets in committed examples
- if a secret was pasted into chat or logs, rotate it
- never serialize the full runtime config when it contains secret-backed values
- never dump process environment for debugging in production-like flows

Daemon secret-handling rules:

- a long-running daemon should resolve required secrets once during process startup, not on every handled command
- after startup, keep resolved secrets only in process memory
- do not persist resolved secrets back to files, temp files, sqlite, json logs, or launchd logs
- if the daemon invokes child processes, pass only the minimum required secrets through environment variables at spawn time
- do not forward the entire parent environment to child processes unless there is a strong reason and an explicit allowlist
- secret env vars should be treated as ephemeral runtime transport, not as durable storage

Recommended daemon pattern:

1. read config
2. resolve secrets once
3. keep them in an in-memory cache owned by the main daemon process
4. reuse cached secrets from the in-memory runtime bundle instead of re-resolving them on each handled action
5. pass only the minimum secret subset to child workers through an allowlisted env

Telegram-specific example:

- reuse the cached bot token for Telegram polling and replies

Implementation checklist for daemon code review:

- the daemon entrypoint should construct a startup runtime bundle before the main loop starts
- per-update handlers should accept the resolved runtime bundle instead of raw config whenever possible
- `op read` or equivalent secret-backend calls should not appear on the hot path for each handled message
- tests should exercise the listener path and verify that multiple handled commands do not trigger repeated secret resolution
- standalone one-shot worker CLIs may resolve their own secrets, but that must remain separate from the long-running daemon path
- if launchd runs from a copied service root, changes to code, shared modules, or `runtime.local.toml` must be applied with the install/redeploy script, not only with a restart script
- a restart script may only reload the already installed plist and running copy; it should not be assumed to sync fresh code into the service root
- if schedule-bearing launcher config changes, such as LaunchAgent timing or generated plist fields, rerun the install/redeploy path so launcher artifacts are regenerated; restart alone is not enough unless it explicitly rebuilds them

Tradeoff guidance:

- resolving secrets once in the daemon and keeping them in memory is usually a better balance than calling the secret backend on every request
- this is still safer than storing plaintext secrets in tracked or local runtime files
- for local macOS daemons and scheduled jobs, Keychain-backed runtime resolution is the preferred default because it avoids GUI re-authorization prompts
- jobs started from `launchd` or another non-interactive scheduler should use a secret backend that can resolve without GUI prompts
- if 1Password CLI prompts make scheduled jobs unreliable, migrate that runtime path to Keychain or another non-interactive local secret source

## 4. Path and Filesystem Rules

Do not hardcode machine-specific paths in committed code.

Rules:

- do not commit absolute local paths containing home directories, usernames, workstation names, or other machine-specific identifiers anywhere in the repository
- derive project root from script location or env
- support a project-root override via environment variable
- keep runtime data inside the project unless there is a strong reason not to
- if a daemon deploys code elsewhere, still point config and data back to the project root
- for local tools and skills, prefer project-root-relative config paths such as `scratch/` or `data/` instead of absolute home-directory paths
- for temporary and staging artifacts, prefer one shared project-local scratch root such as `scratch/` rather than creating many sibling `tmp/` or per-tool temporary folders across the repository
- when a tool needs its own temporary area, place it under the shared scratch root, for example `scratch/<tool-name>/`, so periodic cleanup can happen by cleaning `scratch/` alone

For knowledge-base notes, keep section design additive rather than repetitive:

- each next section should add net-new knowledge instead of duplicating, inverting, or paraphrasing the previous section
- if two sections would carry the same material, keep the stronger section and remove the weaker one
- examples and cases should usually live next to the recommendation, lesson, or claim they support instead of being copied into a second standalone section
- after restoring examples or late-editing a structured note, explicitly re-check section boundaries; preserving a useful example is not a reason to repeat the same example or claim across multiple sections
- optional sections should be omitted when they do not add a distinct layer of value
- tags should describe a specific retrieval axis rather than a broad topic "in general"
- when an existing narrower tag already fits, prefer it over a broader umbrella tag
- if a broad tag keeps covering several different retrieval intents, treat it as a candidate for a constrained family or for restricted-use rules rather than continuing to apply it by default
- if a tag starts collecting notes from several different retrieval intents, stop treating it as a harmless default: narrow its meaning, move it into a constrained family, or split off one stable narrower tag before the umbrella meaning spreads further
- when a workflow already has constrained tag families or restricted-use tags, reapply those family rules during the final save pass instead of letting late edits reintroduce umbrella tags
- creating a new tag should be harder than reusing an existing one: admit a new tag only when no existing canonical tag or family member is close enough and the new tag is likely to be reused across multiple future notes
- if one or two existing tags already describe the note accurately enough, prefer that combination over inventing a new tag
- keep note tags sparse
- default to the smallest tag set that still captures the note's independent retrieval value
- when creating concepts, taxonomy nodes, or other durable knowledge entities, run a canonical-entity check before creating anything new
- if a nearby existing entity already captures the same meaning, update the canonical existing entity instead of creating a synonym or near-duplicate
- title differences, translations, word-order variants, and small framing changes are not enough to justify a new knowledge node
- links, tags, and later references should point to the canonical existing entity rather than to a local duplicate name
- when a knowledge workflow uses dated append-only log sections, define one canonical ordering policy and one canonical insertion point in the workflow-local contract instead of improvising append behavior during updates
- when one note explicitly mentions another existing note, concept, or durable knowledge node in the prose, write it as a wikilink instead of plain text
- for file-producing workflows that save into external, synced, or otherwise race-prone storage, finalize the artifact in staging, perform one destination write, then immediately read the destination back and verify the saved state before continuing; do not burn retries on blind rewrites against an unverified write result
- when the canonical target title is longer, broader, translated, or otherwise less natural than the wording that appears in the prose, keep the canonical target but link through an alias instead of leaving the shorter wording as plain text
- run this alias-link pass after final create-vs-update decisions so terms like abbreviations, English source labels, shortened metric names, or compact phrases still resolve to the canonical knowledge node
- when a note already links to another knowledge object inline in the body, do not repeat the same link mechanically in a closing related-notes section; keep the closing block for net-new navigation context instead of duplicating already established graph edges
- if the closing related-notes block becomes empty after deduplicating inline links, remove the block entirely instead of leaving an empty heading or padding it with weak filler links
- when working in a knowledge vault, maintain one canonical navigation index as the default entry point for both humans and agents; prefer auto-updating indexes over manually rebuilt note lists whenever the platform supports them
- when answering questions against a knowledge vault that has such an index, consult the canonical index first to scope the search and only then open the relevant notes directly for factual verification instead of relying on the index alone
- when writing Obsidian `query` blocks that reference paths or file names containing spaces, wrap the full value in quotes instead of relying on shell-style escaping so expressions like `path:"Ideas/AI prompts"` and `-file:"Индекс заметок"` stay reliable

Recommended env override:

- prefer a project-specific `*_PROJECT_ROOT` variable name
- Telegram example: `TELEGRAM_CONNECTOR_PROJECT_ROOT`

Use project-local directories:

- `data/telegram_history.sqlite3`
- `data/media/`
- `data/exports/`
- `data/sessions/`
- `data/launchd/`
- `data/inbox.jsonl`
- `data/offset.local.json`

The concrete paths above are Telegram-oriented examples. Preserve the same principle for other projects: one project-local `data/` root with predictable subpaths for databases, exports, sessions, scheduler logs, and cached inputs.

Git rule:

- ignore the whole `data/` directory

## 5. Logging Rules

Logs must stay inside the project, not inside opaque service folders.

Recommended logs:

- `data/launchd/bridge.startup.log`
- `data/launchd/bridge.stdout.log`
- `data/launchd/bridge.stderr.log`

The concrete filenames above are a Telegram service example. Reuse the same pattern for any local daemon, scheduler, skill, or automation: keep logs project-local, predictable, and separate from opaque launcher-owned directories.

Rules:

- logs must not contain secrets
- logs must not contain absolute private file paths when avoidable
- logs must not contain raw external-provider updates or full sensitive request payloads
- log command metadata, not full sensitive payloads
- for local tools and skills, prefer one append-only log file per tool unless per-run log separation is operationally necessary
- when a tool supports multiple execution engines or many available variants, log the chosen engine and selected result by default; emit the full variant list only on explicit request or in a dedicated diagnostic mode
- for each logged fact, prefer exactly one canonical log representation; avoid writing both a derived summary block and the original structured payload unless both serve distinct operational needs
- orchestration layers should reuse structured routing, engine, and selection diagnostics from the underlying tool that made the decision instead of inventing a second local schema
- when replaying from cached or preexisting artifacts, recover engine or selection metadata from the original producer's persisted logs or metadata if available; only synthesize fallback values when no canonical source exists, and label such values explicitly as fallback
- do not log convenience placeholders such as `unknown`, `existing-*`, or stub engine names when the real value can be recovered from an upstream source of truth with reasonable effort

For inbox/update storage:

- store only a redacted event summary
- keep `chat_id`, `command`, timestamps, and text length if needed
- avoid storing full message text unless there is a deliberate product need

These field names are a Telegram example. For other integrations, keep only the minimum operator, command, timestamp, and payload-size metadata needed for support and auditing.

## 6. Bot Command Rules

The bot bridge should accept:

- `/command ...`
- `command ...`
- `/command@botname ...`

Rules:

- only recognized commands should be normalized from bare text
- regular non-command chat text must not trigger execution
- only allow command execution from whitelisted chats

This section is Telegram-specific. Apply the same intent to any other command surface: normalize only deliberately supported invocations, reject ambiguous free-form input, and restrict execution to explicitly authorized callers or contexts.

Recommended commands:

- `help`
- `backfill`
- `tail`
- `update`
- `digest`
- `ocrhistory`
- `exportcsv`
- `ocr`

If command execution is disabled:

- the bot may still receive messages
- but it must not try to run the history client

## 7. Authorization Rules

Support separate auth modes:

- `bot`
- `user`
- `auto`

Rules:

- default auth mode should be explicit in config
- if omitted in bot commands, use the configured default
- for historical channel reads, prefer `user`
- for public-channel service operations, `bot` may still be useful

Remember:

- Bot API and bot-auth are not enough for full history access
- full historical reads of Telegram channels usually require user auth

This section is Telegram-specific. The general rule is to separate auth modes by capability and choose the least-privileged mode that can actually perform the requested operation.

## 8. Data Ingestion Rules

For message sync:

- `backfill`: load historical data
- `tail`: inspect the latest window of messages
- `update`: fetch only new messages since the latest stored one

Rules:

- never duplicate already stored messages
- when syncing, skip existing messages by primary key
- for `update`, stop at the boundary of already-known history
- keep sync state per channel
- support multiple channels in one run
- daily digest-style commands should take defaults from local config and allow explicit manual overrides to win when the operator passes them
- for long-running ingestion, commit database writes in batches instead of one row at a time
- distinguish between a shared run budget and a per-source cap:
  the shared budget limits total work in one run, while the per-source cap limits how much one source may consume before the next source is considered
- when multiple sources are requested, process them in the explicit order given by the operator or config
- if a shared run budget is used, stop the run cleanly when the budget is exhausted rather than overrunning it silently
- keep batch size and total run budget as separate knobs; they solve different problems and should not be conflated

Recommended database keys:

- messages: `(channel_id, message_id)`
- media assets: `(channel_id, message_id, ordinal)`
- sync state: one row per channel

The examples in this section are phrased for Telegram history sync, but the same rules apply to any ingestion pipeline: clear incremental modes, no duplicate persistence, explicit shared-vs-per-source budgets, and predictable source ordering.

## 9. Media and OCR Rules

Default behavior:

- do not download media unless explicitly requested
- do not run OCR unless explicitly requested

Semantics:

- `media` = download media only
- `ocr` = download image media and run OCR

Rules:

- OCR should operate only on image media
- if OCR is requested for already-stored messages without local files, allow media refresh without duplicating the message row
- store OCR text separately from the original message text
- store OCR failure state in a sanitized form

AI processing guidance:

- prefer hierarchical batching for large inputs:
  summarize smaller batches first, then summarize the batch summaries
- do not mix unrelated sources inside one AI batch if source-level context matters
- include lightweight provenance in prompts when it improves quality, such as source name, sender identity, timestamp, and message id
- if per-source AI results are independently readable, emit them progressively and send a final status summary only when something failed or was skipped
- structure prompts for cache-friendly reuse:
  keep the longest stable instruction and format prefix identical across repeated calls, and append variable payload later
- avoid putting highly variable metadata at the very start of a prompt when prompt caching matters
- treat token usage as an observable production metric:
  log input tokens, cached input tokens, output tokens, total tokens, latency, model, stage, and status for every AI call
- optimize batch sizes using measured token usage and latency from real runs, not only record counts
- when batching large inputs, preserve quality by keeping the batch format and rubric stable while varying only the content payload

This section also applies to any workflow that downloads attachments, extracts text, or runs AI enrichment over stored artifacts.

## 10. Export Rules

For CSV exports:

- use `;` as the delimiter
- support export by latest `N` messages
- support export by `since`
- make `until` optional

Rules:

- if `since` is set and `until` is omitted, export through the newest stored message
- for multi-channel export, create one CSV per channel
- if export is triggered through the bot, send all generated CSV files back to Telegram

Avoid exporting:

- absolute local file paths
- secrets
- internal debug paths

If the export target is not Telegram, preserve the same rule: generated artifacts should be delivered through the intended operator channel, not by leaking local machine paths.

## 11. Security Hardening Rules

Never send raw subprocess `stdout/stderr` directly to an operator-facing surface such as Telegram, CLI passthrough, webhook response, or UI error panel.

Rules:

- build user-facing replies from a whitelist of safe fields
- redact file paths where possible
- redact bot tokens and similar credentials in error output
- sanitize OCR errors before storing or returning them
- sanitize transport- or API-specific errors before returning them to an operator surface
- when transport formatting matters, set the formatting mode explicitly instead of relying on plain-text rendering
- prefer one formatting mode per program path, usually HTML or MarkdownV2, and use it consistently
- escape or sanitize user- and model-generated text before wrapping it in transport-specific formatting markup
- do not rely on prompts alone for transport readability; apply post-processing when message structure must be stable
- keep transport-specific presentation rules in post-processing code when possible, and keep prompts focused on semantic structure
- if a reply mixes generated prose and structured blocks, enforce spacing, headings, and list density in code rather than expecting the model to reproduce them exactly

Database safety:

- use parameterized SQL only
- do not interpolate user-provided strings into SQL

Nullability convention:

- for optional database fields, store missing values as `NULL`, not as empty strings `""`
- apply this consistently to ids, usernames, display names, signatures, local paths, mime types, OCR text, and similar optional string fields
- only use empty strings when an empty string is a deliberate business value, not a placeholder for absence

Explicit exception:

- `messages.text` may be stored as `""` when Telegram explicitly provides no text body for the message
- this is intentional because `""` means “the message has no text”, while `NULL` would suggest “text was not loaded or is unknown”
- keeping `messages.text` non-null also simplifies exports, filtering, length checks, and downstream text-processing code

Telegram-specific note:

- the `messages.text = ""` exception is specific to Telegram ingestion semantics and should not be copied blindly into unrelated schemas

Stored data minimization:

- store minimized metadata instead of full raw provider payloads when possible
- avoid storing full raw update bodies in logs
- store only what is operationally needed

## 12. Daemon and Service Rules

Use a system-managed background process for reliable command handling.

For macOS:

- use `launchd`
- install via a project script
- restart via a project script

Rules:

- committed plist/templates must not contain machine-specific paths
- templates may contain placeholders
- installer may render runtime-specific paths locally
- service bundle may live outside the repo
- but config and data should still point back to the project
- for once-per-day AI analysis on macOS, prefer a scheduler like `launchd` over introducing a second always-on AI daemon
- keep scheduler command lines thin and config-driven; do not duplicate business defaults in multiple shell scripts
- scheduled and background jobs must run with an explicit interpreter/runtime path when runtime dependencies are interpreter-specific
- when moving a workflow under `launchd` or another scheduler, verify the real launcher environment end-to-end instead of assuming the interactive shell environment matches it
- validate the deployed runtime with the actual launcher entrypoint:
  - check the chosen interpreter or binary path
  - check that required dependencies are available in that runtime
  - check that config, secrets, and project-root resolution work from the deployed service bundle
- for long-polling listeners, scheduled jobs, and external API integrations, treat transient network failures as an operational condition, not an automatic process-fatal error
- if a timeout or short-lived transport error is safe to retry, prefer bounded retry with a small backoff for one-shot external calls
- for interactive or scheduled external API calls, 2-3 retry attempts is a good default starting point for timeout and other short-lived transport failures
- if a timeout happens inside a long-running listener loop, prefer logging and continuing the loop over exiting the whole daemon
- reserve process-fatal exits for persistent misconfiguration, invalid credentials, schema problems, or non-retryable API failures
- before considering a scheduler migration complete, perform at least one real trial run through the scheduler itself and confirm:
  - startup log exists
  - stdout/stderr are sane
  - exit code is successful
  - the expected side effect of the job actually happened
- for machines that may sleep, validate both the scheduler path and the wake/resume behavior separately; a healthy job definition is not enough if the host never wakes in time

After code changes:

- reinstall or redeploy the service bundle
- then restart or bootstrap the daemon

The macOS `launchd` bullets are platform-specific examples. The broader rule is to use a real system scheduler or service manager, keep deployed runtime verification explicit, and treat redeploy and restart as separate lifecycle steps when they are not the same operation.

## 13. Test Rules

Before shipping changes, run automated tests.

Minimum expectations:

- command parser tests
- config parsing tests
- secret resolution tests
- security redaction tests
- CSV export tests
- OCR-related logic tests
- launchd installer/restart script tests
- multi-channel behavior tests

Use the subset that matches the project. For example, a local skill may not need CSV export tests, while a daemonless automation may not need service-manager tests; but every listed capability that does exist should have corresponding tests.

Rules:

- every new command nuance should have a parser test
- every new config nuance should have a config test
- every security hardening rule should have at least one regression test

## 14. Documentation Rules

README must explicitly document:

- how to configure secrets
- how to run the main entrypoint or service
- whether a listener, daemon, scheduler, or one-shot runner is required
- how to install, redeploy, restart, or rerun the relevant runtime path
- where logs live
- the supported invocation forms on the exposed operator surface
- the default target or source config format when the project has one
- the differences between adjacent modes that could be confused by operators

Telegram-specific additions:

- that bot commands require the listener daemon
- whether `/command`, `command`, and `/command@botname` are supported
- the default channels config format
- the difference between `media` and `ocr`

Documentation safety rules:

- do not use absolute local filesystem paths in committed docs
- do not mention home directory names, machine-specific usernames, or workstation-specific paths in committed docs
- prefer relative paths, generic placeholders, or env variable names in documentation examples

Repository-wide safety rule:

- the same no-machine-specific-path rule applies to code, configs, templates, scripts, tests, examples, and documentation

## 15. Git Hygiene Rules

Do not commit:

- `runtime.local.toml`
- `data/`
- `scratch/`
- session files
- SQLite databases
- exported CSVs
- downloaded media
- temporary launchd output

Before pushing:

- search for machine-specific paths
- search for usernames or home directories
- search for tokens or passwords
- ensure service templates are generic

## 16. Operational Checklist

When creating a new local program, skill, automation, or service, verify:

1. secrets are outside tracked files
2. logs are sanitized
3. operator-facing replies or outputs are sanitized
4. SQL is parameterized
5. runtime data stays in project-local `data/`
6. service-manager or rerun scripts exist when the runtime model needs them
7. README explains real startup and runtime behavior
8. tests cover parser, config, security, and export behavior
9. explicit command args override config defaults
10. the real deployed or scheduled runtime path has been refreshed after code changes

Telegram-specific additions:

- bot replies are sanitized
- daemon install/restart scripts exist
- the daemon has been redeployed after code changes

## 17. Reuse Guidance

If you bootstrap a new project, skill, or automation from this repository, copy these ideas first:

- local `runtime.example.toml` + ignored `runtime.local.toml`
- Keychain-backed secret resolution
- project-root env override
- sanitized operator-surface responses
- redacted inbound-event logging
- system-scheduler or service-manager install and restart scripts when the runtime model needs them
- whole-`data/` gitignore rule
- parser tests for every user-facing command nuance

Telegram-specific examples:

- sanitized bridge responses
- redacted inbox logging
- launchd installer and restart scripts

This rulebook is intended to be stricter than convenience defaults. If a future project needs to relax a rule, document why.
