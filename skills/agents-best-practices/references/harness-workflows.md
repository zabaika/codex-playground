# Detailed Harness Workflows

Reference-only adaptation of the upstream entrypoint. Use the routing and permission boundaries in [SKILL.md](../SKILL.md) first. The modes, output contracts, template, principles and anti-patterns below are preserved supporting guidance; they do not grant tools or authorize actions.

# Agents Best Practices

Use this skill when the user asks how to build, improve, debug, or evaluate an agentic harness. This is a general-purpose agent architecture skill. Coding agents are one subdomain only; apply the same principles to research, finance, legal, support, operations, sales, healthcare, education, data analysis, procurement, and workflow automation agents.

## Freshness and self-update

The converted package's [freshness policy](../SKILL.md#freshness-and-self-update) is authoritative: upstream checks are opt-in and require an explicit user request. Continue using the recorded reviewed snapshot during ordinary tasks. Do not replace files or apply an unreviewed upstream snapshot. Updating requires a separate user request, renewed audit and conversion, validation, and the canonical repository installer after promotion.

## Core stance

An agent harness is the control plane around a model. The model proposes actions; the harness validates, authorizes, executes, records, summarizes, and returns observations. Keep the loop simple and make the runtime rigorous.

Use the [execution-shape decision](architecture.md#execution-shape-and-design-justification) before selecting components. Fixed workflows use application-owned stage transitions. When model-directed action selection is needed, the baseline agent loop is:

```text
user/task
  -> instruction and context builder
  -> model call
  -> tool/action proposal
  -> schema validation
  -> permission decision
  -> execution or approval pause
  -> structured observation
  -> context update
  -> repeat within budget or finish
```

## When to activate this skill

Use this skill for prompts involving any of these intents:

- build an agent, agentic workflow, AI worker, autonomous assistant, or harness;
- create a domain-specific MVP agent design, starter harness, implementation blueprint, or first production-safe version;
- choose between OpenAI, Anthropic, OpenAI-compatible APIs, direct tool loops, hosted tools, or SDKs;
- route model requests within a session, replay instruction/tool configuration changes, or evaluate paid cache warming;
- design tools, permissions, guardrails, approval flows, or sandboxing;
- design agent-rendered interfaces, record provenance, cumulative business limits, or user-memory lifecycles;
- design an agent for a partially known or changing environment using capability discovery, safe probing, runtime binding, schema verification, or drift invalidation;
- reduce code-mode or programmatic-tool latency through speculative execution, partial-program analysis, futures, exact claim semantics, or cancellation of unused work;
- create planning mode, workflow orchestration, goal mode, todo tracking, or long-running task behavior;
- build an always-on agent service or durable session with accepted-input receipts, task ownership, application state, restart recovery, reconnecting observers, or recurring source collection and delivery bookkeeping;
- design collaborating agent teams with distinct approaches, shared work awareness, finding exchange, or evidence-driven reassignment;
- add context compaction, memory, retrieval, scoped instructions, or prompt hierarchies;
- design a recursive language model (RLM), programmable-context runtime, self-refining or continual harness, retained child agents, or executable skills;
- build or install a hardware/embedded agent, flash an agent to a board, preserve device identity during an update, or debug its resource and wake behavior;
- design or debug voice agents, speech pipelines, realtime turn taking, interruptions, playback, or corrections during tool work;
- attach Agent Skills, reusable workflows, MCP servers, external connectors, or tool search;
- design agent communication through public boards with explicit public-audience disclosure, user approval, and outbound-data controls;
- audit an existing agent for reliability, cost, prompt-cache hit rate, safety, latency, or observability;
- improve measured harness or application performance using validated benchmark proxies, production acceptance, and regression ratchets;
- troubleshoot partial streams, stalled turns, restart or cancellation races, duplicate actions, context-accounting errors, or telemetry overload;
- create system prompts or developer instructions for a domain-specific agent;
- make source-of-truth knowledge, validation signals, logs, metrics, or workflow state legible to an agent.

Do not use this skill for ordinary single-turn writing, translation, or Q&A unless the user is asking about the design of an agent that will perform those tasks.

## How to use this skill

First, identify the user's design problem:

1. **Domain**: what work the agent performs.
2. **Autonomy level**: answer-only, draft-only, approval-gated action, or autonomous action within policy.
3. **Risk level**: read-only, internal write, external communication, financial, legal, healthcare, security, destructive, or privileged.
4. **State duration**: single turn, multi-turn session, resumable workflow, or long-running goal.
5. **Tool surface**: internal APIs, hosted tools, MCP/external connectors, browser, sandbox, filesystem, database, communication, or computation.
6. **Validation**: what proves the task is complete.

Apply the [request routing and implementation gate](../SKILL.md#request-routing-and-implementation-gate) before selecting a mode. Load the selected route and the mechanism owners needed for its decisions, not all references or every section of the blueprint. Follow workspace rules for mandatory full instruction reads; never treat truncated output as complete evidence.

## Requirements Refinement Mode

Use this route to improve a specification or terms of reference for an agent or bounded LLM workflow. Diagnose gaps and contradictions in the supplied material first. Inspect a repository only when requested or necessary for correctness; otherwise label reuse and implementation claims as unverified instead of inventing project facts.

Return a compact, actionable deliverable:

1. Diagnosis: the goal, scope, significant ambiguities and conflicting requirements.
2. Proposed specification edits: concrete wording or an amended specification preserving the user's intended product and constraints.
3. Decisions and rationale: choose fixed stages or an agent loop as needed; distinguish observed facts, attributed guidance and proposed assumptions/defaults. Justify additions using the [design criteria](architecture.md#execution-shape-and-design-justification).
4. Verification and implementation plan: acceptance/failure cases, smallest stages, dependencies, and questions only where correctness or authority depends on missing information.

Use only relevant mechanisms; a specification edit does not require a complete harness blueprint, paid probe or runtime scaffold. Present the diagnosis and plan under the entrypoint's approval gate, then wait for explicit approval before implementation. Approval of a previously presented plan is execution authority within that scope, not a request to produce another plan.

## Implementation Plan Structure

Use this contract when the requested deliverable includes substantial multi-step implementation, whether the primary route is design, audit, troubleshooting or requirements refinement. Keep a brief diagnosis or small specification edit compact. Adapt headings and layout to the user's requested format; this contract does not require extra files or a fixed number of steps. Repository rules remain authoritative for approval, coding and TDD policy.

Before designing the sequence, consolidate mandatory requirements, latest user corrections, scope exclusions and open decisions into a compact inventory. Distinguish user decisions, observed project facts and proposed assumptions; resolve superseded requirements explicitly. Reuse the existing requirement-to-check mapping as this inventory when possible rather than creating a parallel ledger.

Start with a short shared contract: the intended user-visible outcome, scope, mandatory constraints, accepted decisions and unresolved assumptions. Identify one owner for shared schemas, interfaces, configuration defaults and repeated execution rules. Put detailed shared contracts in an addressable reference section or the existing project owner. Cards carry critical local conditions and exceptions; shared TDD, regression and progress rules need a precise reference rather than repeated boilerplate. Keep settings excerpts tied to their canonical keys and units; overrides must not silently become different defaults.

Divide milestones into small, ordered steps that can be verified independently. Each step must provide:

- **Outcome and boundary:** the concrete behavior or artifact it delivers and the components/interfaces it changes.
- **Prerequisites:** completed dependencies and the relevant current project state; identify required input-preparation stages, their readiness criteria and error/limit policy using [input readiness and freezing](#input-readiness-and-freezing) when applicable. No completion check may depend on an unimplemented later step. Where an interface intentionally precedes its implementation, state the bounded fixture/injected contract used now and the later production wiring step.
- **Working context:** required inputs, interfaces, settings/defaults and precise references to shared contracts. Include critical step-specific conditions directly, including scope, limits, failure behavior and compatibility requirements.
- **Verification:** behavioral test scenarios, applicable TDD sequence, commands and expected observable results. Identify checks of direct consumers and the applicable full-regression checkpoint under repository policy. Distinguish tests to write from tests already run.
- **Completion and recovery:** the evidence required to close the step and how to continue safely if it stops partway through.

Map each mandatory requirement to an implementation step and a verification check using a compact table, list or identifiers appropriate to the plan's size. Assign cross-cutting requirements an integration check as well as their local checks. This mapping must reveal omissions; it is not satisfied by copying the same generic checklist into every step.

When multiple components must compose, identify the earliest useful end-to-end slice from user input to an observable outcome. Use bounded substitutes for unfinished external or domain stages where appropriate, label those boundaries, and extend the slice as real stages arrive. Avoid postponing all integration until every infrastructure component is complete; a fixture journey does not establish production delivery or model quality.

Before presenting the plan, walk through each step using only the shared contract, that step and its cited owners. Check that the implementer can locate the inputs/interfaces, write a collecting behavioral test, run the stated checks at that point, and know when to stop or resume. Missing information belongs in the step or its canonical owner, not in assumed chat memory. A small step delivers an independently verifiable result; do not impose a fixed number of steps or split mechanically by file/function. Review length for repeated boilerplate, overlapping steps and unused references without removing necessary local conditions.

During approved execution, after loading the applicable mandatory rules, read the shared plan contract, current step and its referenced details, then inspect the current code before editing. Apply the repository's short feedback cycles and validation policy: start with the nearest meaningful test when TDD applies instead of designing every remaining substep first. Split a card only for independently verifiable outcomes, preserving its requirements and necessary shared-contract design. After each completed step or interruption, persist verified progress, decisions, remaining work and the exact next action in the plan or its existing progress owner. Mark planned, in-progress, verified-complete and blocked work distinctly; do not infer completion from code presence. Resume must be possible from this recorded state without relying on chat memory.

When a discovery changes a shared contract, update its canonical owner and affected steps together. At milestone boundaries, verify that their outputs compose; before final completion, close the requirement-to-check mapping. Use the entrypoint's approval gate for any material scope or architecture change.

For restructuring or compression, preserve the requirement inventory and stable identifiers where useful. Compare the before/after meaning: requirements retained or moved, and intentional changes/removals with their authority. Literal/key/anchor checks support this comparison but do not prove semantic equivalence. Keep the preservation assessment in the existing verification record; no separate report format is required.

### Input readiness and freezing

For workflows that freeze analysis inputs, identify preparation stages and readiness criteria from the project's contract for the requested scope, including relevant stored inputs. Readiness is the required output actually present in the next stage's input, not just a successful download, function call or task status; distinguish a valid empty extraction from pending or failed processing. Do not invent prerequisites requiring unrelated pending data, but respect shared resource limits. Configuration does not override mandatory requirements: a disabled optional stage need not block, while a missing or disabled required stage follows the project's stop/degraded policy. Reuse existing settings and limits; errors or exhausted limits must not silently become complete processing.

On resume, preserve the frozen input version rather than silently enriching or replacing an existing manifest/checkpoint. Any rebuild must follow the project's explicit policy and assess affected results under [result dependencies and retention](always-on-agents.md#result-dependencies-and-retention). Verify the handoff using observable consumer input and cover enabled/disabled processing, errors, limits and resume; a call-count check alone cannot prove readiness.

## Final Deliverable Review

After drafting every final skill result, perform a separate review before presenting it. This applies to initial plans, revised plans, specification refinements, diagnoses and implementation reports, including late edits or merges. Review the complete current deliverable using the latest user decisions, not just the diff or the section most recently changed. Use current text already available in context; when file reads are needed, use bounded ranges with complete coverage rather than relying on truncated output. A brief answer needs a brief applicable review, not a new architecture document.

Apply the following checks where the deliverable contains the corresponding material:

1. **Intent and consistency.** Reconcile scope, terminology, interfaces, examples, tables, acceptance criteria and reported outcomes. Follow the latest explicit user corrections. Trace important behavior across its producer, transformations and consumers: an iterator, successful sync, valid schema or prepared answer alone does not establish the whole workflow's claimed property. For transformations or reductions, apply [information preservation and handoffs](workflow-orchestration.md#information-preservation-and-handoffs) using the project's required information and permitted losses.
2. **Obsolete remnants and proportionality.** Remove stale options, fields, components, tests, references and defaults left by removed requirements. Remove orphan prohibitions such as "do not implement X" when they only narrate a discarded design; retain explicit scope exclusions or guardrails that still protect a live requirement. Express the current positive contract instead of accumulating rejected proposals. Consolidate repeated execution boilerplate and overlapping steps under their owners. Keep necessary rationale and supported alternatives, and do not simplify by silently dropping user requirements; for restructuring, compare before/after requirement meaning.
3. **Execution order and coverage.** Verify prerequisites exist before each step, its tests can run at that point, and the dependency graph has no cycles or reliance on future implementation. Walk through step-local inputs and checks as described in the plan-structure contract. Check that an early observable integration slice, delivery and recovery occur in a workable order; map every mandatory condition to a step and verification check. Distinguish building a capability from wiring it into the real user journey, and fixtures from actual integrations. For public capabilities, follow the intended user's path from discovery through required parameters to a supported invocation using the project's designated help and documentation; check example validity without requiring intentionally private entrypoints to be advertised. This review does not authorize external menu registration or publication.
4. **State, boundaries and recovery.** Reconcile when inputs/cutoffs are fixed, which component owns each fact, transaction/acknowledgement order, allowed state transitions, retries and cancellation. Check [input readiness and freezing](#input-readiness-and-freezing), cumulative versus per-attempt budgets and analysis versus delivery outcomes. For non-daemon processes subject to the repository's hard-TTL policy, identify applicable public launch paths, including auxiliary or evaluation routes, and trace each to its outer hard runtime limiter; a protected main worker does not establish coverage of other routes. Reuse existing enforcement rather than requiring another mechanism. Trace checkpoints and dependencies of active work and retained completed/failed results through source changes, restart, cache eviction and retention expiry. Apply [result dependencies and retention](always-on-agents.md#result-dependencies-and-retention) to check dependent-artifact invalidation, actual publication/restoration and preservation of confirmed or uncertain external-effect history.
5. **Data and SQL, when present.** Check identity scope, primary/unique/foreign keys, nullability, enum constraints, ownership, lineage and cascade/delete behavior against consumers. Check transaction scope and indexes for actual read/write paths; include derived-index maintenance, readiness and backfill/rebuild where proposed. Look for expensive per-record full scans and ambiguous identifier mappings. Label a logical schema review separately from executable migration, query-plan and runtime tests.
6. **Configuration, values and feasibility.** Inventory every proposed behavioral knob, including limits mentioned only in prose or steps. Ensure it has one owner, a default or explicit required-input/derived rule, units, scope, validation and override precedence. Check existing reuse options, compatible global/profile caps, token reserves, concurrency and timeout semantics. Explain why each material number is needed and whether it is an external constraint, measured value or proposed starting point; a label alone does not justify an arbitrary value. State the sizing rationale and validation needed for proposed values, without inventing benchmark evidence or constants for a capability not yet verified.
7. **Evidence and presentation.** Keep observed facts, sourced guidance, proposals, assumptions, planned tests and actually executed checks distinct. Check links/anchors, Markdown tables and code blocks, and the saved artifact when applicable. Structural validation does not prove semantic completeness or runtime health. Do not claim a comprehensive review if any relevant part was unread or unavailable.

Fix findings within the authorized artifact scope, then recheck the affected contracts and their consumers. If a correction materially changes the design, perform another whole-deliverable pass on the corrected result. Before presenting, ensure no known unresolved inconsistency is hidden; report remaining assumptions, unavailable evidence or choices requiring the user rather than fabricating a resolution. Apply the entrypoint's approval gate to material changes of an already approved architecture. Reviewing a plan does not authorize implementation, migrations, paid probes or delegation.

Keep the user-facing report concise: meaningful corrections, what was actually checked and any remaining limits. For a saved substantial plan, leave a short verification record identifying the reviewed revision, semantic self-review, executed mechanical checks, whether independent review occurred, and remaining evidence gaps. Use the existing verification/progress owner; do not require a new artifact, independent agent, paid probe or a large appendix. Small mechanical checks may support the pass, but cannot replace semantic review or justify tests that merely match instruction wording.

When maintaining these planning instructions, validate behavior on representative cases: a substantial initial request, sequential requirement corrections, compression of an existing plan, and a focused small task as applicable to the change. Assess requirement preservation, usable dependencies/checks and proportional output; matching prescribed headings alone is insufficient. Report whether this was self-application or an authorized independent evaluation.

## MVP Builder Mode

When the user asks for a new domain design, produce a concrete diagnosis and MVP plan under the entrypoint's approval gate. Use [mvp-agent-blueprint.md](mvp-agent-blueprint.md) for a new blueprint, the refinement route for supplied requirements, and other references only as needed. A request to make or scaffold the system does not bypass plan approval.

Default behavior:

1. Infer a reasonable first version from the user's domain and stated constraints.
2. State assumptions briefly instead of blocking on missing details.
3. Design the smallest safe harness that can accomplish useful work.
4. Specify the chosen workflow or loop, model/application boundary, budgets, permission policy, context, observability, evals and implementation path. Include tools, persistent memory, compaction, goals or connectors only when required by the chosen design.
5. Mark high-risk actions as draft-only or approval-gated by default.
6. Keep the MVP to the smallest reliable fixed workflow or single-agent loop unless the user explicitly asks for broader architecture or measured evidence requires it.

## Troubleshooting Mode

When the user brings a failing run or runtime symptom, start with [troubleshooting](security-observability.md#troubleshooting) and load the linked mechanism owner as needed. Return the symptom, earliest failing boundary, observed evidence versus hypotheses, one discriminating probe, smallest corrective change, and regression coverage. State missing evidence explicitly; use a focused diagnostic handoff instead of the full architecture blueprint. Inspect advanced components only when the failing deployment uses them.

## Hardware and Board Deployment Mode

Use this mode when the user wants an agent running on a physical board, a firmware/app installation, or device-runtime debugging. Read [hardware-agents.md](hardware-agents.md) before choosing an installer. Establish board/runtime identity, inference location, actual boot/launcher/partition path, resource headroom, preserved state, and recovery access. A filesystem app may not require reflashing base firmware.
For installation or debugging of an existing agent, use a focused target/install/preservation/verification handoff rather than the full MVP blueprint or unrelated workflow/subagent/connector design. Use the blueprint when the agent architecture itself is being created.

Keep deployment authority separate from runtime tool authority and public posting. Default to one read-only cycle; recurring autonomy and programmable/physical tools remain post-MVP unless requested. An installation handoff must name the measured target, pinned artifact, write boundary, preservation/rollback plan, and physical commissioning evidence. Do not equate a successful upload or host-backed emulator with standalone device health.

## Environment-Adaptive Tool Mode

Use this mode when the useful tool catalogue, schemas, versions, or implementations are late-bound rather than fully configured before the run. Read [environment-adaptive-tools.md](environment-adaptive-tools.md) together with the standard tool, connector, security, and eval references.

Require a small trusted bootstrap interface, host-owned capability ledger, provenance-labeled descriptors, bounded read-only or isolated probes, opaque scope-and-version bindings, call-time permission checks, and drift invalidation. Discovery, generated code, and inferred schemas must never grant authority. Keep this post-MVP unless adapting to changing environments is the product's primary job; even then, establish a fixed read-only baseline first.

## Realtime Voice Agent Mode

For spoken interaction, read [realtime-voice-agents.md](realtime-voice-agents.md) with the tool and eval owners. Use it alongside MVP Builder Mode for a new voice agent or Troubleshooting Mode for an existing audio failure. Voice can be an MVP; independent backend orchestration and durable recovery are optional unless the selected architecture requires them.

Return the media topology, interaction/turn policy, execution placement, input and playback evidence, interruption repair, correction/result freshness, and audible-versus-task acceptance criteria. Reuse existing authority and task contracts; do not assume speech interruption cancels work or audio capability implies weaker reasoning.

## Always-on Agent and Durable Runtime Mode

Use this mode when the user requests an always-on service, recoverable sessions, accepted inputs during active work, durable task/application state, or observer reconnection. Read [always-on-agents.md](always-on-agents.md) with the loop, tool, context, security, and eval owners. Keep this post-MVP: measure a simpler request-scoped or resumable baseline first. Availability, persistence, inference activity, and authority are separate choices; ordinary always-on operation does not require recursion or self-refinement.

Return the acceptance and commit boundary, input/control lanes, owned task lifecycle, document scope and fork policy, observation/reconnection contract, resident ownership, recovery, and aggregate limits. Reuse the existing goals, permissions, compaction, and child protocols; a durable submission receipt or idle generation is not proof of completed work.

For recurring monitoring, use the [collection and delivery contract](always-on-agents.md#recurring-collection-and-delivery-bookkeeping) for per-source progress, explicit coverage gaps, and receipt-backed delivery state.

## Advanced Recursive and Continual Harness Mode

Use this mode only when the user explicitly asks for programmable context, recursive execution, retained children, continual refinement, or executable skills. Treat it as post-MVP: establish a measured single-loop baseline first, then read [self-refining-recursive-harnesses.md](self-refining-recursive-harnesses.md) together with the context, workflow, permission, security, and eval references. For resident operation or scheduled re-entry alone, use [Always-on Agent and Durable Runtime Mode](#always-on-agent-and-durable-runtime-mode).

Make the context representation, recursive unit, mutable state, promotion scope, lifecycle, budgets, validation probes, and rollback path explicit. Keep base authority, permission enforcement, credentials, budgets, and evaluation policy outside the mutable surface.

## Adaptive Agent Team Mode

Use this mode when the user requests collaborating teams that explore distinct approaches and change assignments as evidence develops. Keep it post-MVP: establish measured single-agent and ordinary parallel-worker baselines first, then read [adaptive-agent-teams.md](adaptive-agent-teams.md) with the workflow, recursive lifecycle, goal, and eval owners.

Return the approach portfolio, work-overlap policy, communication dependencies, evidence-linked transitions, host authority, aggregate budget, and acceptance evidence. Use the profile's contracts and existing owners rather than repeating their manuals. Treat it as an architecture composition; published scale or outcomes do not establish a model change or general performance gain. Routine lookup tasks still use the single-loop MVP.

## Experimental Speculative Tool Execution Mode

Use this mode only when the user explicitly asks to reduce latency by launching tool work before a generated program or action is complete. Establish measured sequential and ordinary committed-parallel baselines first, then read [speculative-tool-execution.md](speculative-tool-execution.md) together with the loop, tool, security, and eval references.

Require host-owned eligibility, permission at physical dispatch, isolated disposable state, exact versioned claim identity, occurrence-safe handling of stochastic calls, separate waste and cost budgets, confirmed cancellation accounting, and task-parity evaluation. Partial model output never grants authority, and risky or approval-gated effects must not execute speculatively.

## Reference map

- Read [mvp-agent-blueprint.md](mvp-agent-blueprint.md) first when the user asks to create a new domain-specific agent or MVP harness.
- Read [coding-agents.md](coding-agents.md) when the requested agent reads, edits, tests, reviews, migrates, or opens changes against a software repository, including measured post-MVP action-interface selection.
- Read [hardware-agents.md](hardware-agents.md) for embedded execution location, launcher-versus-firmware installation, resource-bounded transport, reset-safe state, wake behavior, rollback, and physical commissioning.
- Read [realtime-voice-agents.md](realtime-voice-agents.md) for media/turn/playback lifecycles, interruption repair, continuous-input evidence, voice/execution bridges, correction freshness, and audio-specific acceptance.
- Read [architecture.md](architecture.md) for the full harness model, component boundaries, and trusted runtime instruction/tool configuration events.
- Read [always-on-agents.md](always-on-agents.md) for post-MVP service availability and durability boundaries, atomic local publication, accepted inputs/control lanes, owned tasks, application documents/forks, observer convergence, resident ownership, recovery, and recurring collection/delivery bookkeeping.
- Read [agent-legibility-feedback-loops.md](agent-legibility-feedback-loops.md) for source-of-truth knowledge bases, agent-legible environments, validation loops, mechanical invariants, and recurring cleanup.
- Read [agentic-loop.md](agentic-loop.md) for the provider-neutral loop, step budgets, retries, loop variants, and post-MVP per-request model routing.
- Read [speculative-tool-execution.md](speculative-tool-execution.md) when an advanced code-mode or programmatic-tool harness should prelaunch eligible work during generation while retaining completed-program authority and occurrence-aware claiming.
- Read [tools-and-permissions.md](tools-and-permissions.md) for tool contracts, record provenance, presentation receipts, resulting-state limits, approval logic, structured results, and sandboxing.
- Read [environment-adaptive-tools.md](environment-adaptive-tools.md) when the tool environment is partially known or changes at runtime and needs bootstrap discovery, schema validation, safe probing, exact binding, or drift handling.
- Read [context-memory-compaction.md](context-memory-compaction.md) for context assembly, user-memory lifecycle and source eligibility, layered retrieval, staged elision-before-summary, optional historical-output recall, and handoff preservation.
- Read [prompt-caching-and-cost.md](prompt-caching-and-cost.md) for stable-prefix design, cache-aware context ordering, compaction/cache tradeoffs, telemetry, cost control, and optional economic cache warming.
- Read [planning-and-goals.md](planning-and-goals.md) to distinguish read-only planning mode from execution-time progress scaffolds, and for approval-gated execution, goals, checkpoints, and stopping conditions.
- Read [workflow-orchestration.md](workflow-orchestration.md) for planner-generated workflows, bounded work packets, worker/verifier contexts, integration, durable workflow state, and orchestration anti-patterns.
- Read [adaptive-agent-teams.md](adaptive-agent-teams.md) for post-MVP approach portfolios, shared work intentions, selective finding exchange, and evidence-linked team reallocation.
- Read [self-refining-recursive-harnesses.md](self-refining-recursive-harnesses.md) for strict RLM and RLM-inspired patterns, programmable context, recursive execution units, retained child protocols, continual refinement, and executable skills.
- Read [skills-and-connectors.md](skills-and-connectors.md) for Agent Skills, progressive disclosure, predictive loading, MCP, external connectors, tool search, and attachment strategy. For public-board communication, use its [public disclosure and publication contract](skills-and-connectors.md#agent-communication-via-public-boards).
- Read [system-prompts-instructions.md](system-prompts-instructions.md) for system/developer/user instruction hierarchy and prompt templates.
- Read [provider-api-patterns.md](provider-api-patterns.md) for OpenAI, Anthropic, and OpenAI-compatible API implementation patterns.
- Read [security-observability.md](security-observability.md) for guardrails, threat models, approval records, trace design, symptom-based troubleshooting, launch safety gates, and incident response.
- Read [evals.md](evals.md) for evaluation strategy, runtime-state fixtures, cross-capability cases, failure-aware component diagnostics across context budgets, safety trace invariants, model/configuration sweeps, routing/configuration/warming probes, and launch criteria. For performance improvement, use its [measurement, proxy-validation, and regression-ratchet contract](evals.md#performance-optimization-and-regression-ratchets).
- Read [checklists.md](checklists.md) for condensed implementation and audit checklists.
- Read [source-links.md](source-links.md) for official links and provider-specific references.
- Read [coverage-audit.md](coverage-audit.md) to verify the skill covers the requested harness topics.

## Default answer structure when advising a user

When the user asks for architecture guidance, produce a concrete diagnosis and plan, not generic principles, and apply the entrypoint's approval gate. For supplied requirements, use [Requirements Refinement Mode](#requirements-refinement-mode); for an existing failure, use [Troubleshooting Mode](#troubleshooting-mode). Substantial implementation plans follow [Implementation Plan Structure](#implementation-plan-structure); every final result receives the [Final Deliverable Review](#final-deliverable-review). The topics below are a conditional menu, not mandatory sections or components for every response:

0. **MVP boundary**: smallest useful version, assumptions, non-goals, and launch criteria.
1. **Harness boundary**: what the model does versus what application code does.
2. **Execution shape**: fixed stages or model-directed actions, model calls, applicable tool results, stopping and retries; when model routing is requested, state selected/dispatched identities, compatibility, continuation policy and routing-state semantics.
3. **Instructions**: system/developer/user instruction hierarchy and scoped memory; when runtime changes are requested, include the authorized configuration timeline, effective-state replay, and provider projection.
4. **Tools**: tool registry, schemas, outputs, risk classes, permissions, and approval points.
5. **Environment adaptation, when requested**: stable bootstrap, discovery, descriptor provenance, safe probes, exact bindings, drift invalidation, and fallback.
6. **Context**: retrieval, memory, staged compaction when context pressure warrants it, recall utility, cache-aware ordering, and rehydration.
7. **Planning/goals**: permission-gated planning versus execution progress tracking, when to run a goal-like loop, and how to stop.
8. **Workflow orchestration**: when to decompose into durable work packets, worker contexts, verifier contexts, and integration.
9. **Skills/connectors**: how skills and MCP/external connectors are discovered, loaded, permissioned, and audited; when public-board communication is requested, make the public audience and publication approval explicit.
10. **Safety**: prompt injection boundaries, secrets, sandboxing, data access, and guardrails.
11. **Observability**: traces, metrics, replay, auditability, and incident readiness.
12. **Evals**: test cases, failure probes, component-selection diagnostics, trace grading, regression suites, and launch criteria.
13. **Rollout**: minimal viable harness first, then add autonomy only when measured results justify it.
14. **Legibility loop**: source-of-truth artifacts, validation signals, feedback capture, and recurring cleanup.
15. **Advanced recursive/continual profile, when requested**: context handles, recursive unit, retained lifecycle, mutable state boundary, observed validation, promotion, and rollback.
16. **Experimental speculative execution, when requested**: eligibility, exact claim identity, isolated state, waste budgets, cancellation evidence, and parity evaluation against speculation-off.
17. **Adaptive agent teams, when requested**: distinct approach ownership, work-intent overlap and declared replication, communication dependence, versioned portfolio decisions, stale-result handling, and independently validated completion.
18. **Always-on/durable runtime, when requested**: accepted-input and local-commit semantics, owned task completion, application-state fork policies, reconnecting observations, resident ownership, restart recovery, and recurring source coverage/collection/delivery state when applicable.

For hardware requests, append the target/runtime inventory, installation route and exact write boundary, resource/deadline budget, preserved state and recovery plan, and evidence split between host checks and physical-board commissioning.

For voice requests, append the conditional output contract in [Realtime Voice Agent Mode](#realtime-voice-agent-mode); use the profile for media details and the linked eval owner for acceptance cases.

## Non-negotiable principles

- The model does not execute actions directly; the harness does.
- Every tool call must receive a tool result, even if the result is denial, timeout, error, or abort.
- Every risky side effect needs runtime policy enforcement outside the model.
- Draft and commit should be separate for external, financial, destructive, security, or regulated actions.
- Tool schemas must be narrow, typed, validated locally, and auditable.
- A changing capability catalogue must enter through a trusted bootstrap contract; discovery, schema inference, and generated helpers never create permissions.
- Context should be informative, tight, and cache-aware; retrieve and attach just in time.
- Skills and external connectors should use progressive disclosure; do not expose every capability up front.
- Public-board posts are public external disclosures, not private agent memory; make this visible to the agent and user, and enforce publication approval in the host.
- Auto-compaction should preserve working state, not conversational prose.
- Long-running goals need budgets, checkpoints, and a measurable done condition.
- Workflow orchestration needs durable packet state, independent verification, integration rules, and total budget enforcement.
- Recursive and continual harnesses may mutate only typed supplemental state; immutable runtime policy must validate changes, preserve authority boundaries, and support rollback.
- Speculative execution may predict work but never authorize it; only an exact eligible call in the completed program may claim the result.
- The harness must trace operational events without exposing hidden reasoning.
- Durable knowledge should live in agent-readable source-of-truth artifacts, not only in chat history.
- Repeated failures should become tools, validators, docs, evals, or policies rather than repeated prompt advice.

## Common output template

Use applicable sections of this template for a new design, not for a focused refinement or diagnosis. Omit unused mechanisms rather than inventing them to fill headings. The output remains a proposed plan until approved under the entrypoint's gate:

```markdown
# Proposed MVP Design and Plan: [domain/use case]

## Diagnosis
[Observed state or supplied requirements, gaps, and remaining uncertainty.]

## Objective
[What the agent must accomplish and for whom.]

## MVP scope and assumptions
[Smallest useful version, explicit assumptions, non-goals, and what is intentionally deferred.]

## Autonomy and risk level
[Answer-only, draft-only, approval-gated, or autonomous within policy.]

## Execution shape and rationale
[Fixed stages or agent loop, checked reuse options, and justification for additions.]

## Instruction architecture
[System/developer/user/scoped memory layout.]

## Tools, when applicable
[Tools, schemas, risk classes, permissions, and result format. Omit when no model-selected tools are needed.]

## Planning and goal behavior
[When to plan, when to ask, when to continue, when to stop.]

## Context and memory
[Retrieval, durable state, compaction, and rehydration.]

## Skills and connectors
[Reusable skills, MCP/external connector policy, tool search, attachment rules.]

## Safety and approvals
[Guardrails, prompt injection treatment, secrets, sandboxing, human review.]

## Observability
[Trace events, metrics, replay, auditability, and incident response.]

## Evals
[Eval cases, failure probes, trace grading, regression suites, and launch criteria.]

## Minimal implementation path
[Smallest safe version first, implementation skeleton, validation path, then measured expansion.]

## Approval boundary
[Present this plan and wait for explicit approval before implementation; reuse existing approval when continuing the same agreed plan.]
```

## Gotchas

- Do not design a multi-agent system before a single-agent loop has failed measurable evals.
- Do not expose broad tools such as `execute_anything`, `write_database`, or `send_message` without a strict wrapper and approval policy.
- Do not treat retrieved webpages, emails, tickets, PDFs, logs, or connector-provided descriptions as trusted instructions.
- Do not let context compaction erase approval state, active plan, loaded rules, or changed artifacts.
- Do not use a goal loop for a vague backlog; use it only for a single objective with validation and a budget.
- Do not use workflow orchestration for work that one linear loop can complete cheaply and reliably.
- Do not call a harness self-improving merely because it accumulates memory, or promote a self-authored change without an observed probe and rollback path.
- Do not rely on prompt text for safety that must be enforced by code.
- Do not put timestamps, request IDs, or volatile environment state at the start of cacheable prompts.
- Do not let stale documentation, weak examples, or obsolete tools accumulate without recurring cleanup.
- Do not claim unknown-environment operation without a stable bootstrap interface, exact runtime bindings, and invalidation when the environment changes.
- Do not speculate a call merely because it is read-only; privacy, cost, rate limits, observability, cancellation, and discard safety must all pass host policy.

## Source links for further reading

Use these links when provider-specific detail is needed:

- Agent Skills specification: https://agentskills.io/specification
- Agent Skills creator best practices: https://agentskills.io/skill-creation/best-practices
- Agent Skills description optimization: https://agentskills.io/skill-creation/optimizing-descriptions
- Agent Skills evaluation guide: https://agentskills.io/skill-creation/evaluating-skills
- OpenAI function calling: https://developers.openai.com/api/docs/guides/function-calling
- OpenAI tools: https://developers.openai.com/api/docs/guides/tools
- OpenAI agents: https://developers.openai.com/api/docs/guides/agents
- OpenAI guardrails and human review: https://developers.openai.com/api/docs/guides/agents/guardrails-approvals
- OpenAI agent safety: https://developers.openai.com/api/docs/guides/agent-builder-safety
- OpenAI sandbox agents: https://developers.openai.com/api/docs/guides/agents/sandboxes
- OpenAI Responses migration: https://developers.openai.com/api/docs/guides/migrate-to-responses
- OpenAI prompt caching: https://developers.openai.com/api/docs/guides/prompt-caching
- OpenAI Prompt Caching 201: https://developers.openai.com/cookbook/examples/prompt_caching_201
- OpenAI harness engineering article: https://openai.com/index/harness-engineering/
- Anthropic building effective agents: https://www.anthropic.com/research/building-effective-agents
- Anthropic effective context engineering: https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents
- Anthropic writing effective tools for agents: https://www.anthropic.com/engineering/writing-tools-for-agents
- Anthropic long-running harnesses: https://www.anthropic.com/engineering/effective-harnesses-for-long-running-agents
- Anthropic code execution with MCP: https://www.anthropic.com/engineering/code-execution-with-mcp
- MCP specification: https://modelcontextprotocol.io/specification/2026-07-28
