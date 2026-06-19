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
- when a workflow depends on canonical labels, headings, section names, statuses, field names, or other repeated identifiers, keep those literals in one machine-readable schema, manifest, or config owner and make every other layer reference that owner instead of duplicating the same strings by hand
- when code, tests, templates, or workflow helpers need rendered schema-owned identifiers, resolve them through the owning schema loader, generated constants, or manifest accessors instead of hardcoding the rendered strings outside the owner
- treat local renaming, translation, or stylistic rewriting of schema-owned identifiers as a contract change rather than as ordinary content editing; update the canonical owner first, then update every consumer as one explicit migration
- when a repository-managed tool or skill is installed or copied into a runtime location such as `~/.codex/skills`, treat the repository copy as the only editable owner and the installed/runtime copy as a derived artifact
- do not edit the installed/runtime copy directly; change the repository copy and then refresh the installed copy through the canonical install or sync path

### Wrapper and Orchestration Inheritance

- if a wrapper or orchestration layer needs to expose upstream metadata, pass through or parse the canonical upstream artifact instead of reconstructing it with local placeholders or guessed values
- if an orchestration or producer workflow already has a shared downstream writer for user-facing notes or documents, keep the producer responsible only for orchestration and structured payloads; do not let it own a second local copy of the final note contract
- for user-facing output contracts such as final summaries, created-vs-updated reports, or section ordering, keep one canonical definition in the deepest owning workflow and make wrappers inherit it instead of restating the same format in multiple places
- if a wrapper delegates note creation, document rendering, or another structured-output workflow to a deeper canonical owner, delegate the final write path as well instead of reusing only a local formatting helper from that owner while bypassing its route resolution, save logic, or final contract checks
- apply the same inheritance rule to final validation layers: if a wrapper delegates note creation, document rendering, or another structured-output workflow to a deeper canonical owner, the wrapper must inherit that owner's final compliance passes and quality gates instead of silently shortening the validation path

### Contract and Documentation Discipline

- if behavior changes, update code, tests, and the relevant source-of-truth document in the same change
- when a top-level project, tool, or workflow becomes a meaningful navigation anchor, update both the root `README.md` project index and the root `AGENTS.md` repo layout in the same change
- when a workflow grows beyond a few pages of rules, split it into one thin entrypoint plus canonical reference docs; keep the entrypoint focused on sequencing and keep detailed policy in the deepest owning reference file
- when a stateful workflow grows beyond a small prototype, define a rollout split such as `core`, `optional`, and later stages instead of treating every planned capability as equally urgent
- for the earliest releaseable stage, define one reference end-to-end user journey and treat it as the gate for scope decisions; capabilities that do not improve that journey should not block the first release
- for each major module, skill, or subsystem in that earliest stage, define a concrete minimum definition of done so implementation does not drift into feature-complete ambitions before the short journey works
- if an entrypoint file still repeats a rule family in short form, keep it as a brief guardrail summary only; the detailed wording, examples, and edge cases must still live in the canonical owner
- when an entrypoint delegates formatting or content rules to a canonical reference document, it must still put every major rule family from that canonical owner onto the mandatory execution path of the workflow; a vague pointer like "apply conventions" is not enough when operators or agents could otherwise skip whole families
- when a workflow relies on structured metadata such as frontmatter, route payloads, manifests, or config blocks, validate that metadata after the final manual rewrite or merge instead of trusting an earlier draft
- when a workflow produces structured documents with formatting and schema rules, run one final holistic compliance pass after any late manual edit or merge instead of validating only the thing that was just changed
- when a workflow also maintains a local contract checker or regression harness for those structured outputs, keep that harness focused on mechanically checkable constraints such as schema, formatting, links, and explicit preservation rules
- when a new mechanically checkable output rule is added to such a workflow, update the checker or regression fixtures in the same change so the documented contract and the executable contract do not drift apart
- when a workflow already has a local contract harness, any change to that workflow's output contract, checker, or contract-facing documentation should trigger the harness before the change is considered complete, even if the edit looks like "docs only"
- when documenting a workflow rule, prefer concrete behavioral requirements over vague verbs such as `append`, `clean up`, `normalize`, `improve`, `update`, or `fix` when more than one exact operation is plausible
- if a rule depends on position, ordering, insertion point, stop condition, or fallback behavior, spell that out explicitly instead of relying on implication or common sense
- when a quality rule is primarily semantic or editorial and cannot be exhaustively reduced to deterministic pattern matching, keep the canonical rule in documentation and treat any checker coverage as partial regression protection only
- for such semantic or editorial rules, document the human pass as mandatory, label the checker coverage as partial in the owning test matrix or checker docs, and do not present representative pattern checks as if they fully prove compliance
- if a checker is added for that kind of rule family, prefer a small number of durable failure classes over a long brittle blacklist of recent wording examples
- when changing a shared rule, encode the underlying invariant rather than the last observed example
- prefer a general rule that covers the failure class over a narrow patch tied to specific words, labels, route/type pairs, or one recent incident
- use example-specific wording only when the contract truly depends on canonical fixed identifiers such as schema-owned labels, manifest keys, config fields, or other explicit vocabulary
- when changing the identity, layout, install root, vendor root, runtime path scheme, config location, wrapper relationship, or other structural boundary of a repository-managed tool, audit every path-based dependency and legacy coupling before considering the migration complete
- this migration audit rule applies not only to renames, but also to moves, splits, merges, wrapper extraction, runtime relocation, vendor-directory reshaping, config-root changes, and similar structural refactors
- after such a structural migration, add or update a regression check that the new tool no longer depends on obsolete paths, old install names, legacy sibling layouts, or previously copied runtime roots unless backward compatibility is explicitly documented and tested
- for migrations, conversions, rewrites, or normalization passes over existing material, preserve by default and transform by explicit exception; removal or strong compression must have a stated reason rather than being justified only by neatness or shorter length
- when a workflow deletes content, relocates it, or substantially adapts its meaning or operational role, disclose that change explicitly in the owning report, summary, or migration notes instead of leaving operators to infer it from the final artifact alone
- do not replace concrete useful material such as examples, templates, checklists, structured specimens, or other directly usable blocks with a more abstract summary unless a clearly identifiable functional equivalent survives in the same workflow
- when de-branding, anonymizing, or generalizing source-specific material, preserve any underlying quality baseline, contrast pattern, or validation value that the original example provided; remove incidental identity, not the practical standard it was teaching
- when one workflow produces multiple artifacts with different operational roles, prefer cleanly separated artifacts over hybrids, and make each artifact responsible for one role rather than blending several incompatible responsibilities into one file
- before compressing or deleting a useful block because it feels too heavy in its current location, first test whether it belongs under a different canonical owner; relocate to the correct owner before choosing lossier transformations
- for complex multi-file or multi-output systems, maintain a regression harness for mechanically checkable contract rules and update that harness in the same change when the output contract evolves

### Agent Coding Discipline

- treat coding work as a constrained execution task, not as permission to improvise product decisions, architecture changes, or cleanup outside the user request

#### 1. Think Before Coding

**Don't assume. Don't hide confusion. Surface tradeoffs.**

- before implementing, state assumptions explicitly when they affect behavior, scope, or risk
- if more than one plausible interpretation exists, surface the alternatives instead of silently choosing one
- if a simpler approach exists, say so and push back when the requested direction is overbuilt
- if a key requirement is unclear, stop and name the exact confusion instead of filling the gap with guessed intent

#### 2. Simplicity First

**Minimum code that solves the problem. Nothing speculative.**

- do not add features beyond what was requested
- do not introduce abstractions for single-use code
- do not add flexibility, configurability, or extension points that were not requested
- do not add defensive handling for scenarios that are impossible or unsupported in the current system
- if the implementation is materially longer or more layered than necessary, rewrite it smaller before calling it done
- use the senior-engineer test: if a strong reviewer would call the solution overcomplicated, simplify it

#### 3. Surgical Changes

**Touch only what you must. Clean up only your own mess.**

- when editing existing code, change only lines that trace directly to the user request
- do not opportunistically improve adjacent code, comments, naming, or formatting
- do not refactor unrelated code just because you noticed a better shape
- match the existing local style unless the user asked for a broader cleanup
- if you notice unrelated dead code or debt, mention it separately; do not remove it unasked
- remove imports, variables, functions, or files only when your own change made them unused
- do not treat pre-existing dead code as part of your cleanup budget unless the user explicitly asked for it
- when applying manual file edits, follow Patch Hygiene: keep patches small, refresh context for dirty files, and verify the diff before stacking more edits

#### 3a. Patch Hygiene

**Small patches, fresh context, immediate verification.**

- prefer one `apply_patch` call per file unless a single atomic change genuinely requires multiple files
- do not patch multiple dirty or staged files in one `apply_patch` block
- before editing a dirty, staged, or recently generated file, reread the exact target fragment that will be changed
- keep patch context minimal but unique: use the nearest stable surrounding lines, not an entire section or large block
- when a change needs several hunks in the same file, split them if each hunk can be verified independently
- after a patch to a sensitive, dirty, staged, generated, or contract-bearing file, inspect that file's local diff before stacking more edits on top
- if a patch fails to apply, do not retry the same broad patch; narrow it to one file and one hunk, refresh the surrounding context, then retry
- use mechanical rewrite tools only for intentionally broad, repetitive edits; before doing so, count matches, run the rewrite once, and inspect the resulting diff
- this section governs manual patch edits; whole-file artifact promotion from staging or scratch may use the workflow's canonical one-write promotion path followed by read-back and contract verification

#### 4. Goal-Driven Execution

**Define success criteria. Loop until verified.**

- translate vague tasks into verifiable outcomes before implementation
- prefer checks that can prove the change, such as a reproducing test, a contract check, or an explicit observable behavior change
- for multi-step tasks, state a short plan where each step names its verification check
- do not stop at `implemented`; stop only when the success criteria have been verified or a concrete blocker is surfaced
- prefer strong, local success criteria over broad phrases like `make it work`, because weak criteria force repeated clarification and invite silent drift

#### 5. External OS Services and Signing Agents

**Treat service installation and external signing as system mutations.**

- run installers or reinstallers that mutate macOS `launchd` state outside the sandbox with explicit approval
- run Git commit or amend operations that require external signing agents outside the sandbox with explicit approval
- do not fall back to unsigned commits unless the user explicitly approves that security downgrade
- when SSH commit signing is used through 1Password, run signing with access to the 1Password SSH agent socket and verify the result before reporting success
- do not treat sandbox failures from `launchctl` as application failures; rerun the canonical installer outside the sandbox before debugging service code
- after code, shared runtime, copied service-root files, or runtime config changes, prefer the canonical installer over a restart-only script
- restart-only scripts are appropriate only when the installed service root already contains the intended code and config
- for macOS LaunchAgents, prefer a stable executable launcher wrapper as `ProgramArguments[0]`; let that wrapper invoke shell runners through `/bin/bash`

#### 6. Runtime Limits and Operator-Visible Guardrails

**Make behavioral limits visible to operators.**

- any timeout, row limit, token/output cap, batch size, retry count, cache TTL, or worker lifetime that affects operator-visible behavior must be present in runtime config or documented as a protocol/API invariant
- config examples must include a nearby comment explaining what the limit bounds and whether it applies per item, per request, per command, or to the whole process
- if code clamps a config value to a range, the nearby config comment must state that range with `Values are clamped to ...`
- avoid hidden fallback constants for business behavior; use code defaults only as compatibility fallbacks for missing older configs

Examples:

- `add validation` -> `write tests for invalid inputs, then make them pass`
- `fix the bug` -> `write a test that reproduces it, then make it pass`
- `refactor X` -> `ensure relevant tests pass before and after`

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

Use the architecture rules in this order:

1. define the runtime structure
2. define the command boundary and mutation ownership
3. define the workflow entity/state model
4. define how thin surfaces behave, including degraded mode
5. define approval, audit, idempotency, and automation constraints
6. only then harden database runtime behavior

### Core Runtime Structure

- when a local workflow grows into a stateful system with persistent data, AI-assisted steps, multiple interfaces, or a future UI/API, prefer a service-first modular monolith before splitting into separate services
- use one canonical runtime project that separates at least:
  - `domain`: entities, invariants, policies, lifecycle vocabulary
  - `application`: commands, queries, handlers, use cases
  - `infrastructure`: repositories, migrations, storage adapters, projections, parsers
  - `interfaces`: CLI, chat glue, skill glue, webhook, future API, future UI backend
  - `ai`: optional model-facing adapters or engines with structured input/output contracts
- if the earliest stage does not justify a dedicated `ai/` layer yet, keep AI calls isolated behind application-level adapters that can later move into `ai/` without changing the command/query model
- keep storage-oriented subsystems inside `infrastructure`; do not let a database helper, repository package, or storage bundle become the public API of the whole system
- if a system exposes a named storage-oriented subsystem, treat it as persistence infrastructure only; canonical business mutations must still enter through application command handlers

### Shared Runtime Extraction Rules

- if the same low-level runtime mechanism starts to repeat across multiple top-level projects, make `common/` its canonical owner instead of keeping project-local copies
- move only domain-agnostic runtime primitives into `common/`; do not use `common/` as a dumping ground for shared business logic
- when a helper moves into `common/`, treat changes to it as compatibility-sensitive for every dependent project
- if project-local code needs different business behavior, keep that behavior in the project and layer it on top of `common/` rather than turning `common/` into a product-specific module

### Command Boundary and Mutation Ownership

- if a system combines persistent workflow state, AI surfaces, CLI, and UI, keep the canonical business logic and state transitions in one deterministic application/service layer
- do not let AI mutate canonical workflow state directly; AI may propose, generate, summarize, classify, or advise, but state changes must still flow through deterministic handlers
- do not let UI or other interface layers write directly to the database; every mutation must go through command handlers in the canonical service layer
- do not let skills, wrappers, or interface adapters own canonical operational logic when a shared service layer exists; they should orchestrate or adapt that logic, not redefine it
- route all state-changing operations through command handlers and all queue/view construction through query or projection handlers instead of ad hoc SQL from multiple surfaces

### Workflow State and Entity Model

- when a workflow tracks business progress over time, define one canonical entity vocabulary before adding UI views, agent wrappers, reports, or automations on top of it
- distinguish primary business entities from their occurrences, events, or derived views; do not collapse canonical records, source-specific sightings, reminders, audit entries, and projections into one overloaded table or object
- keep workflow stages separate from orthogonal flags such as hidden, processed, archived, blocked, or materially_changed; do not overload one status field to mean both progression and side-condition overlays
- if several related state machines exist, such as item workflow, external action progress, human touchpoints, or review flows, keep them explicitly separate unless they truly share the same lifecycle semantics
- do not require empty placeholder operational records just to satisfy a theoretical schema; create touchpoints, reminders, interviews, reconciliation items, or similar records only when the corresponding real-world event or intent exists
- when a system suppresses rediscovery, deduplicates history, or routes changed records into review buckets, encode that as canonical business logic in one owning layer instead of reimplementing it independently in UI filters, agent prompts, or report scripts
- when a new surface needs a queue, dashboard, or operator list, build it as a projection over canonical entities and states rather than as a second mutable state store

### Surface Independence and Degraded Mode

- atomicity for a skill, wrapper, CLI, or interface surface means a self-contained operator scenario, not an independent copy of shared domain logic
- if a shared service layer exists, do not reimplement separate local versions of profile schemas, lifecycle states, approval semantics, artifact identity, storage assumptions, or quality gate contracts in each surface
- when a surface must still be usable without the shared backend, define an explicit degraded mode instead of pretending the full system contract still holds
- degraded mode may generate drafts, analyses, checklists, or recommendations, but it must not falsely claim persistence, lifecycle mutation, durable artifact identity, history-aware dedupe, or reconciliation guarantees that require the missing backend
- when running in degraded mode, make that limitation visible in the output contract instead of silently returning a draft that looks like a committed system fact

### Approval Separation

- treat content acceptance, quality validation, and permission to perform an external action as three separate contracts even if they often appear in the same workflow
- `artifact acceptance` means the operator accepts the content or draft as acceptable
- `quality gate result` means the system considers the artifact structurally and semantically safe enough for its intended class of use
- `external action approval` means the operator authorizes a concrete external mutation such as send, publish, submit, refresh, hide, or update
- do not let one of these confirmations silently stand in for the others
- when a workflow can affect outside systems or public-facing outputs, require the exact combination of approvals that matches that action rather than relying on one overloaded `approved` flag

### Audit, Idempotency, and Automation Safety

- design state-changing commands to be idempotent where practical; when true idempotence is not possible, require an explicit idempotency key or a detectable duplicate policy
- a retried command must not create a second business fact just because an agent, scheduler, wrapper, or operator repeated the same request
- if a repeated command cannot be auto-resolved safely, return a review outcome instead of silently creating duplicates
- perform validation, invariant checks, state mutation, audit-event creation, and any required usage-event creation as one logical mutation unit
- do not commit a state change without its corresponding audit record
- do not emit an audit record that claims a state change which was not committed
- if a workflow creates reminders, schedules, queues, or follow-up items, do not imply background execution that the system does not actually perform
- reminders, scheduled items, and daily action lists may surface required work, but they must not be presented as completed sends, submits, publishes, or other external actions unless that action was explicitly executed and recorded

### Database Runtime and Transaction Rules

- when multiple local tools use the same database engine, prefer one shared repository-wide helper module for engine setup, connection defaults, and transaction helpers instead of duplicating low-level connection code in each project
- keep operator-tunable database runtime settings such as lock wait limits, busy timeouts, retry ceilings, transaction modes, or durability knobs in config or a version-controlled shared config bundle rather than hardcoding them in many call sites
- favor safe-by-default connection behavior for long-running or scheduled jobs; if an engine supports autocommit plus explicit transactions, prefer that over long implicit write transactions
- keep write transactions short and explicit
- do not hold a write transaction open across network calls, `await` boundaries, subprocess execution, OCR, user interaction, or long filesystem scans
- if a logical write unit spans multiple statements, wrap only that database-only unit in an explicit transaction helper and ensure rollback happens on failure
- if a workflow performs slow external work before writing, stage that work in memory or temporary files first and enter the write transaction only for the final database mutation step
- bound lock waits with engine-appropriate settings such as busy timeouts or lock wait limits so competing runs fail clearly instead of hanging indefinitely on the database layer
- close database connections in `finally` blocks or equivalent structured cleanup paths
- if a scheduled or one-shot process can block future runs by holding a database lock, design its TTL and its transaction scope together; process-level timeouts do not replace transaction discipline
- if a runtime is deployed into a copied service root, deploy the shared database helper and its shared config bundle with that runtime so installed code and repository code do not diverge in lock behavior

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
- all operator-tunable runtime ceilings such as total run timeouts, retry budgets, batch sizes, and output limits must live in config with explicit units
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
- if a repository-managed tool or skill depends on third-party code that has been security-audited or otherwise explicitly approved, treat the audited vendored copy and its exact approved version set as the only normal install source
- do not silently fetch, upgrade, or replace such audited dependencies from the internet during ordinary install, bootstrap, repair, or refresh flows
- any change to a vendored dependency version, wheel set, source bundle, plugin, helper binary, provider implementation, or other audited third-party runtime input must be treated as an explicit re-audit event rather than as routine maintenance
- when a dependency is intentionally pinned because only a reviewed version is trusted, commit the audited inputs needed for reproduction and make the install path consume only those tracked audited artifacts by default
- a repository-managed tool, skill, or local service must not depend on hidden runtime state that exists only in a previously installed copy, old deployment directory, manual operator setup, or other non-repository location
- every required runtime asset must be either tracked in the repository as an audited input or bootstrapped deterministically from tracked audited inputs during install
- track audited third-party inputs such as reviewed source bundles, reviewed wheel sets, reviewed plugins, reviewed provider code, and pinned lockfiles as repository assets when they are required for trusted offline or deterministic install
- do not treat generated runtime outputs such as `venv/` directories, transient caches, installed package trees, compiled bundles, or one-off operator-built environments as the canonical source of truth
- if a generated runtime output is required for execution, it must be reproducible from the tracked audited inputs through a documented bootstrap step
- if a local skill or service requires vendored runtimes, wheels, plugins, helper binaries, models, or provider assets, its install script must validate their presence and bootstrap the runnable local runtime explicitly
- a successful install must leave the tool runnable without requiring legacy sibling installs, manual copying from older paths, or undocumented post-install repair steps
- install scripts must fail clearly when required audited inputs are missing instead of silently producing a partially installed runtime
- if a tool depends on vendored third-party runtime assets, include regression coverage for the runtime contract, not only for business logic
- such runtime-contract coverage should verify the required vendor layout, successful local bootstrap from tracked audited artifacts, basic import or execution viability of the bootstrapped runtime, and absence of unintended coupling to obsolete install paths or legacy tool identities
- when a refactor changes install layout, runtime roots, or vendored dependency handling, update those runtime-contract tests in the same change
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
- when a knowledge workflow produces an evaluative or rewritten artifact from a note corpus, keep retrieval and narrowing as an explicit intermediate step before the final rewrite; do not jump from a broad corpus query straight to a polished output without a documented evidence-shortlisting pass
- when indexed retrieval and final narrative output are separated, treat the retrieved notes, snippets, or shortlisted artifacts as the evidence set for the rewrite and keep any broader filesystem exploration as an explicit fallback path rather than a silent parallel source

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
- for non-daemon jobs, last-attempt audit logs must include the configured total timeout, current phase, and terminal status on timeout
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
- access-control helpers for command execution must fail closed when the required allowlist or authorization config is empty
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
- for advisory, evaluative, or rewrite workflows such as resume reviews, career guidance, strategic assessments, or profile packaging, never invent missing facts, metrics, budgets, role scope, dates, or outcomes to make the output read better
- when an advisory workflow needs a stronger claim than the provided evidence supports, downgrade the claim, label it explicitly as inference, or convert it into a follow-up question instead of synthesizing a confident-looking fact
- in evaluative rewrites over human-provided career or profile material, keep a visible separation between confirmed facts, inferences, and requested follow-up data so a polished narrative cannot silently overwrite uncertainty

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
- do not accept SQL identifiers such as table or column names as free-form strings; use fixed constants or a narrow allowlist when dynamic identifiers are truly required

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
- if a non-daemon scheduled job needs helper processes such as wake or idle inhibitors, launch them as children or sidecars tied to the real worker lifetime rather than as detached background processes
- if a runner introduces helper processes around a one-shot job, the runner must own their cleanup path as part of normal exit and timeout exit

Operational reliability:

- for infrequent scheduled analysis on macOS, prefer a scheduler like `launchd` over introducing a second always-on AI daemon
- for long-polling listeners, scheduled jobs, and external API integrations, treat transient network failures as an operational condition, not an automatic process-fatal error
- if a timeout or short-lived transport error is safe to retry, prefer bounded retry with a small backoff for one-shot external calls
- for interactive or scheduled external API calls, 2-3 retry attempts is a good default starting point for timeout and other short-lived transport failures
- if a timeout happens inside a long-running listener loop, prefer logging and continuing the loop over exiting the whole daemon
- reserve process-fatal exits for persistent misconfiguration, invalid credentials, schema problems, or non-retryable API failures
- processes that are not intended to behave as daemons must have an explicit wall-clock TTL
- every one-shot CLI, scheduled job, migration, sync, export, digest, batch worker, or other non-daemon process must define a maximum allowed runtime
- the timeout must be a wall-clock timeout for the whole run, not only per-request or per-step timeouts
- keep the timeout value in config, not hardcoded in code, unless an external tool forces a fixed limit
- use consistent units for config timeouts across the repository; prefer `*_timeout_seconds`
- soft in-process timeouts, coroutine cancellation, library-level request timeouts, or retry limits do not by themselves count as sufficient TTL enforcement for non-daemon jobs
- enforce the hard TTL at the outermost process boundary so a stuck cleanup path, pending async task, or library-level cancellation failure cannot leave the job alive indefinitely
- when a process exceeds its TTL, terminate it as failed instead of letting it remain stuck in a `running` state indefinitely
- a timed-out run must write a final machine-readable status such as `failed` or `timed_out` before exit whenever possible
- timeout expiry must be visible in project-local logs and last-attempt audit artifacts
- non-daemon jobs must not rely on operator wake-up, manual restart, or host sleep/resume behavior as a substitute for bounded runtime
- if a non-daemon job can block future scheduled runs while still marked as active, enforcing TTL is mandatory, not optional
- if a workflow intentionally runs without a TTL, document why it is a daemon and what health or liveness mechanism replaces the TTL
- prefer one total run timeout first; add per-step timeouts only when they solve a distinct operational problem
- keep retry budgets bounded inside the total TTL; retries must not extend runtime without limit
- if the process invokes child processes or external tools, ensure the parent timeout also causes the child work to stop
- if a timed-out run may have spawned children, terminate the whole process group or equivalent owned runtime subtree rather than signalling only one PID
- design timeout cleanup so helper processes, open lock holders, and other child runtime state cannot survive the parent timeout and block the next scheduled run
- if graceful shutdown is possible, log timeout context such as current phase, current source, and elapsed time before exit
- prefer one stable and distinguishable exit code for forced timeout so scheduler and operator logs can tell timeout apart from ordinary application failure
- for scheduled jobs, record the configured timeout and the last known phase in `data/launchd/<job>.last_attempt.json`
- after a forced timeout, verify that the scheduler no longer considers the job running and that the next launch can start a fresh process without manual cleanup
- before considering a scheduler migration complete, perform at least one real trial run through the scheduler itself and confirm:
  - startup log exists
  - stdout/stderr are sane
  - exit code is successful
  - the expected side effect of the job actually happened
- for non-daemon jobs with hard TTL, also perform at least one forced-timeout trial and confirm:
  - terminal status is `failed` or `timed_out`
  - no stale process or lock holder remains
  - the next scheduler-triggered run starts cleanly
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
11. every non-daemon run path has an explicit total runtime timeout recorded in config and surfaced in logs or last-attempt audit output

This rulebook is intended to be stricter than convenience defaults. If a future project needs to relax a rule, document why.
