# Rulebook

This rulebook captures the operational and security conventions used in this project so they can be reused when building other tools.

## Purpose

Use this document as the repository-wide default for local software, automations, skills, daemons, schedulers, and other reusable engineering workflows.

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

### Source of Truth

- put a rule in only one primary place unless a short pointer is needed elsewhere
- prefer links to duplication when a project follows a repository-wide rule
- for each major rule family, keep exactly one canonical documentation owner and make every other file point to it instead of restating the same contract in full
- for any operational fact such as route decisions, chosen engines, selected inputs, or resolved config, define one canonical producer and treat every other layer as a consumer of that fact
- when a workflow produces both a machine-readable payload and one or more human-readable derived artifacts, designate exactly one of them as the canonical source of truth and treat the others as derived views rather than parallel primary artifacts

### Wrapper and Orchestration Inheritance

- if a wrapper or orchestration layer needs to expose upstream metadata, pass through or parse the canonical upstream artifact instead of reconstructing it with local placeholders or guessed values
- if an orchestration or producer workflow already has a shared downstream writer for user-facing notes or documents, keep the producer responsible only for orchestration and structured payloads; do not let it own a second local copy of the final note contract
- for user-facing output contracts such as final summaries, created-vs-updated reports, or section ordering, keep one canonical definition in the deepest owning workflow and make wrappers inherit it instead of restating the same format in multiple places
- if a wrapper delegates note creation, document rendering, or another structured-output workflow to a deeper canonical owner, delegate the final write path as well instead of reusing only a local formatting helper from that owner while bypassing its route resolution, save logic, or final contract checks
- apply the same inheritance rule to final validation layers: if a wrapper delegates note creation, document rendering, or another structured-output workflow to a deeper canonical owner, the wrapper must inherit that owner's final compliance passes and quality gates instead of silently shortening the validation path

### Contract and Documentation Discipline

- if behavior changes, update code, tests, and the relevant source-of-truth document in the same change
- when a workflow grows beyond a few pages of rules, split it into one thin entrypoint plus canonical reference docs; keep the entrypoint focused on sequencing and keep detailed policy in the deepest owning reference file
- if an entrypoint file still repeats a rule family in short form, keep it as a brief guardrail summary only; the detailed wording, examples, and edge cases must still live in the canonical owner
- when an entrypoint delegates formatting or content rules to a canonical reference document, it must still put every major rule family from that canonical owner onto the mandatory execution path of the workflow; a vague pointer like "apply conventions" is not enough when operators or agents could otherwise skip whole families
- when a workflow relies on structured metadata such as frontmatter, route payloads, manifests, or config blocks, validate that metadata after the final manual rewrite or merge instead of trusting an earlier draft
- when a workflow produces structured documents with formatting and schema rules, run one final holistic compliance pass after any late manual edit or merge instead of validating only the thing that was just changed
- when a workflow also maintains a local contract checker or regression harness for those structured outputs, keep that harness focused on mechanically checkable constraints such as schema, formatting, links, and explicit preservation rules
- when a new mechanically checkable output rule is added to such a workflow, update the checker or regression fixtures in the same change so the documented contract and the executable contract do not drift apart
- when a workflow already has a local contract harness, any change to that workflow's output contract, checker, or contract-facing documentation should trigger the harness before the change is considered complete, even if the edit looks like "docs only"
- when documenting a workflow rule, prefer concrete behavioral requirements over vague verbs such as `append`, `clean up`, `normalize`, `improve`, `update`, or `fix` when more than one exact operation is plausible
- if a rule depends on position, ordering, insertion point, stop condition, or fallback behavior, spell that out explicitly instead of relying on implication or common sense

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
- if runtime behavior is tuned by prompts, thresholds, limits, budgets, sizing formulas, section aliases, or other operator-facing knobs, keep those values in one canonical config or version-controlled bundle instead of hardcoding editable copies in code
- when code still needs a constant because of an external protocol or library boundary, document that upstream constraint next to the constant and keep the effective operator-tunable value separate in config
- do not hide derived runtime behavior behind undocumented formulas or silent clamps; if one config value is expected to be sized from another, put the exact practical formula and the current sample inputs next to the owning config fields
- when several profiles share the same operator-facing knob, keep that knob in one shared config block and let per-profile sections override only the values that truly differ by profile
- for local Codex skills, keep exactly one editable `config/runtime.local.toml` in the repository skill folder
- for repository-managed local Codex skills, keep the repository copy under `skills/<skill-name>/` as the single editable source of truth for code, prompts, references, docs, and install helpers
- install repository-managed local Codex skills into `~/.codex/skills/<skill-name>` through a skill-local `install-local.sh` script rather than by ad hoc manual copying
- treat the installed `~/.codex/skills/<skill-name>` copy as a deploy artifact, not as a second editable workspace
- after changing a repository-managed skill, refresh the installed copy by rerunning its `install-local.sh` script instead of patching files under `~/.codex/skills` directly
- keep the install pattern consistent across local skills: resolve the source directory from the script location, target `${CODEX_HOME:-$HOME/.codex}/skills/<skill-name>`, and make the script safe to rerun for normal refresh flows
- if the skill is also installed under `~/.codex/skills`, the installed copy should point to that same repo file instead of keeping a second divergent local config
- if a skill is only an orchestration layer over other local skills, prefer pointing at the sibling skills' local configs instead of copying the same machine-specific values into another file
- do not modify system-provided Codex skills in place as a normal customization path
- when a system skill is missing a dependency, validation helper, or local convention, solve that through a project-local wrapper, companion skill, additional checker, or shared rulebook guidance instead of patching the system skill itself
- treat edits to system skills as an explicit exception path only when a user directly requests that change and the operational tradeoff is understood
- for repository-managed local skill repository-shape and documentation-ownership rules, follow the canonical repo-level guidance in the repository root [AGENTS.md](./AGENTS.md)

## 3. Secrets Rules

Do not commit secrets in tracked files.

Preferred storage:

- macOS Keychain generic-password references via `keychain://<service>/<account>`
- `op://...` remains acceptable as a legacy fallback, but not as the primary backend for unattended local daemons or scheduled jobs

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

Implementation checklist for daemon code review:

- the daemon entrypoint should construct a startup runtime bundle before the main loop starts
- per-request or per-event handlers should accept the resolved runtime bundle instead of raw config whenever possible
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
- when a workflow intentionally saves primary artifacts into an external knowledge vault, synced directory, or other non-project destination, it may use absolute destination paths in `runtime.local.toml` and generic absolute placeholders in `runtime.example.toml`, but that must be documented as an explicit exception instead of becoming the silent default for unrelated tools
- for temporary and staging artifacts, prefer one shared project-local scratch root such as `scratch/` rather than creating many sibling `tmp/` or per-tool temporary folders across the repository
- when a tool needs its own temporary area, place it under the shared scratch root, for example `scratch/<tool-name>/`, so periodic cleanup can happen by cleaning `scratch/` alone
- for file-producing workflows that save into external, synced, or otherwise race-prone storage, finalize the artifact in staging, perform one destination write, then immediately read the destination back and verify the saved state before continuing; do not burn retries on blind rewrites against an unverified write result
- when a workflow has a canonical index or metadata layer for discovery, use it as the default retrieval path and reserve broad filesystem scans for unavailable, broken, or provably stale index states
- when a workflow separates indexed metadata inspection from full artifact reads, prefer the metadata layer for discovery and narrowing, then open only the shortlisted full artifacts for detailed verification or editing

Recommended env override:

- prefer a project-specific `*_PROJECT_ROOT` variable name
- keep one project-local `data/` root with predictable subpaths for databases, exports, sessions, scheduler logs, and cached inputs

Git rule:

- ignore the whole `data/` directory

## 5. Logging Rules

Logs must stay inside the project, not inside opaque service folders.

Recommended logs:

- `data/launchd/<job>.startup.log`
- `data/launchd/<job>.stdout.log`
- `data/launchd/<job>.stderr.log`
- `data/launchd/<job>.last_attempt.json`

Rules:

- logs must not contain secrets
- logs must not contain absolute private file paths when avoidable
- for scheduled jobs, keep a separate machine-readable last-attempt audit artifact with start time, finish time, status, and concise context; overwrite it on each run instead of accumulating unbounded history there
- logs must not contain raw external-provider updates or full sensitive request payloads
- log command metadata, not full sensitive payloads
- for local tools and skills, prefer one append-only log file per tool unless per-run log separation is operationally necessary
- when a tool supports multiple execution engines or many available variants, log the chosen engine and selected result by default; emit the full variant list only on explicit request or in a dedicated diagnostic mode
- for each logged fact, prefer exactly one canonical log representation; avoid writing both a derived summary block and the original structured payload unless both serve distinct operational needs
- orchestration layers should reuse structured routing, engine, and selection diagnostics from the underlying tool that made the decision instead of inventing a second local schema
- when replaying from cached or preexisting artifacts, recover engine or selection metadata from the original producer's persisted logs or metadata if available; only synthesize fallback values when no canonical source exists, and label such values explicitly as fallback
- do not log convenience placeholders such as `unknown`, `existing-*`, or stub engine names when the real value can be recovered from an upstream source of truth with reasonable effort
- for stored operator-event summaries, keep only the minimum actor, command, timestamp, and payload-size metadata needed for support and auditing unless a stronger product need is documented

## 6. Command Surface Rules

Rules:

- normalize only deliberately supported command invocations
- ambiguous free-form input must not trigger execution
- only allow command execution from explicitly authorized callers or contexts
- if command execution is disabled for a surface, receiving inbound events must not silently trigger the underlying worker path anyway

## 7. Authorization and Access Rules

Rules:

- support separate auth modes when different capabilities or trust boundaries require them
- make the default auth mode explicit in config
- choose the least-privileged mode that can actually perform the requested operation
- document capability differences between auth modes in the project-local operator docs instead of assuming operators will infer them

## 8. Incremental Ingestion Rules

Rules:

- define explicit incremental modes when a workflow supports historical load, latest-window inspection, and fetch-only-new updates
- never duplicate already stored records
- when syncing, skip existing records by primary key and stop cleanly at the known-history boundary where the mode requires it
- keep sync state per source
- support multiple sources in one run when the workflow allows it
- operator-facing defaults may come from config, but explicit runtime overrides must win for that run
- for long-running ingestion, commit database writes in batches instead of one row at a time
- distinguish between a shared run budget and a per-source cap:
  the shared budget limits total work in one run, while the per-source cap limits how much one source may consume before the next source is considered
- when multiple sources are requested, process them in the explicit order given by the operator or config
- if a shared run budget is used, stop the run cleanly when the budget is exhausted rather than overrunning it silently
- keep batch size and total run budget as separate knobs; they solve different problems and should not be conflated

## 9. AI Processing Guidance

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

Rules:

- support explicit time- or count-based export scopes when the source data model makes that meaningful
- if a lower bound is set and no upper bound is provided, document whether export continues through the newest stored record
- when multiple sources are exported, keep source boundaries explicit in the produced artifacts
- deliver generated artifacts through the intended operator surface rather than by exposing local machine paths

Avoid exporting:

- absolute local file paths
- secrets
- internal debug paths

## 11. Security Hardening Rules

Never send raw subprocess `stdout/stderr` directly to an operator-facing surface such as a chat surface, CLI passthrough, webhook response, or UI error panel.

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

Stored data minimization:

- store minimized metadata instead of full raw provider payloads when possible
- avoid storing full raw inbound-event or provider-payload bodies in logs
- store only what is operationally needed

## 12. Daemon and Service Rules

Use a system-managed background process for reliable command handling.

For macOS:

- use `launchd`

Rules:

Deployment shape:

- committed plist/templates must not contain machine-specific paths
- templates may contain placeholders
- installer may render runtime-specific paths locally
- service bundle may live outside the repo
- but config and data should still point back to the project
- prefer one deployment shape:
  - keep the executable runtime bundle in a copied service root such as `~/Library/Application Support/<service_name>`
  - keep canonical config, databases, exports, and logs under `project_root/data/` and `project_root/config/`
  - let the service root execute code, but make it consume project-root data and project-root logs
- when a service root is used, pass the project root explicitly through an environment variable if the launcher needs project-local logs, data, or config-adjacent paths
- treat project-local scheduler logs as canonical operational evidence and service-root-local logs as disposable staging at most
- do not treat service-root-local scheduler logs as canonical if the same workflow already has project-local logs; prefer deleting or ignoring the service-root copies to avoid split-brain debugging

Lifecycle operations:

- provide one canonical install or redeploy script that syncs code, syncs effective local config when needed, regenerates runner scripts and rendered launcher artifacts, and reloads or bootstraps the launcher
- keep restart as a narrower operation than redeploy:
  - restart may reload an already installed plist or process
  - restart must not be assumed to sync fresh code, fresh config, or regenerated launcher artifacts unless it explicitly does so
- after code changes, reinstall or redeploy the service bundle before the narrower restart or bootstrap step

Runner and launcher discipline:

- keep runner scripts thin:
  - resolve the interpreter explicitly
  - set only the minimum required environment variables
  - delegate business behavior to the canonical CLI, module entrypoint, or main script instead of re-encoding defaults in shell
- keep scheduler command lines thin and config-driven; do not duplicate business defaults in multiple shell scripts
- scheduled and background jobs must run with an explicit interpreter/runtime path when runtime dependencies are interpreter-specific
- when moving a workflow under `launchd` or another scheduler, verify the real launcher environment end-to-end instead of assuming the interactive shell environment matches it
- validate the deployed runtime with the actual launcher entrypoint:
  - check the chosen interpreter or binary path
  - check that required dependencies are available in that runtime
  - check that config, secrets, and project-root resolution work from the deployed service bundle

Operational reliability:

- for infrequent scheduled analysis on macOS, prefer a scheduler like `launchd` over introducing a second always-on AI daemon
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

The macOS `launchd` bullets are platform-specific examples. The broader rule is to use a real system scheduler or service manager, keep deployed runtime verification explicit, and treat redeploy and restart as separate lifecycle steps when they are not the same operation.

## 13. Test Rules

Before shipping changes, run automated tests.

Minimum expectations:

- command parser tests
- config parsing tests
- secret resolution tests
- security redaction tests
- output-format tests for every user-facing artifact format the project emits
- derived-artifact tests for extraction, OCR, enrichment, or other secondary content-generation paths when those exist
- runtime lifecycle tests for installer, redeploy, restart, scheduler, or service-manager flows when those exist
- multi-source behavior tests when the project can process more than one source, channel, tenant, or input set in one run

Use the subset that matches the project. For example, a local skill may not need output-format or service-lifecycle tests, while a daemonless automation may not need scheduler or service-manager coverage; but every listed capability that does exist should have corresponding tests.

Rules:

- every new command nuance should have a parser test
- every new config nuance should have a config test
- every security hardening rule should have at least one regression test
- when a project defines a canonical project-local virtualenv for tests, install dependencies and run the suite through that environment instead of the system Python

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

Documentation safety rules:

- do not use absolute local filesystem paths in committed docs
- do not mention home directory names, machine-specific usernames, or workstation-specific paths in committed docs
- prefer relative paths, generic placeholders, or env variable names in documentation examples
- if a project has a documented external-destination exception, keep committed examples generic, for example `/absolute/path/to/...`, and explain that the absolute form is intentional because the real artifact root lives outside the repository

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

Commit message rules:

- use commit subjects that uniquely identify the change within nearby project history
- prefer commit messages that name the actual behavior, subsystem, or contract being changed
- avoid generic subjects such as `fix`, `cleanup`, `updates`, `refine`, `misc`, or similarly low-information summaries

## 16. Operational Checklist

When creating a new local program, skill, automation, or service, verify:

1. secrets are outside tracked files
2. logs are sanitized
3. operator-facing replies or outputs are sanitized
4. SQL is parameterized
5. runtime data stays in project-local `data/`
6. install, redeploy, restart, or rerun scripts exist when the runtime model needs them
7. README explains real startup and runtime behavior
8. tests cover parser, config, security, and export behavior
9. explicit command args override config defaults
10. the real deployed or scheduled runtime path has been reinstalled, redeployed, or otherwise refreshed after code changes

This rulebook is intended to be stricter than convenience defaults. If a future project needs to relax a rule, document why.
