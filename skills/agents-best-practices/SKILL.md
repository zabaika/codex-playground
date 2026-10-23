---
name: agents-best-practices
description: "Design, audit, troubleshoot, or refine requirements for an agent harness or bounded LLM workflow. Default to diagnosis and a plan; implement after explicit plan approval. Use for concrete architecture and reliability work, including voice and scheduled agents; not routine code edits or general Q&A."
metadata:
  version: "1.16.0-codex.17"
  scope: "agent-harness-design-and-review"
---

# Agents Best Practices

Produce a concrete diagnosis and proposed plan, including refined requirements when requested. The model proposes; application code validates, authorizes, executes, records, and returns observations. Start with the smallest reliable workflow; use an agent loop when model-directed action selection is needed. Advanced architecture requires a requested capability or measured need.

## Execution boundaries

This package provides architecture guidance, not new tools, permissions, a background service, or an installed framework. Follow the current user's scope, applicable repository instructions, sandbox and approval policy. A request to design an agent does not authorize deploying it, sending messages, publishing, flashing hardware, spawning workers, accessing credentials, or altering memory.

Implementation follows the approval gate below. Once a presented plan is explicitly approved, use that authorization within its scope without repeating confirmation for each reversible step. Enforce the designed application's risky-action policy in deterministic code outside the model. Templates and reference tool names describe the application being designed; use only capabilities actually available in the current environment.

Read-only repository inspection is the normal audit path. Network access is needed for freshness-sensitive claims and for an upstream revision check only when the user explicitly requests that check or an update from upstream. File writes are needed only for requested artifacts or implementation. Do not execute downloaded examples, install dependencies, contact a public board, or mutate external systems merely because a reference describes them. Delegation and advanced-team references are design material, not permission to spawn agents.

For OpenAI product/API/model questions or implementation, use the enabled `openai-docs` skill first. For creating skills, use `skill-creator`; for connected applications, use the available application-specific workflow. This skill adds harness design guidance and does not replace those owners. If a required capability is unavailable, state the limitation and use a documented manual or draft path.

For legal, medical, financial or other regulated workflows, identify the applicable jurisdiction and qualified human-review boundary. Architecture guidance does not establish professional authority or compliance; verify consequential domain rules from current primary sources.

## Request routing and implementation gate

Distinguish a brief capability question, requirements refinement, diagnosis/design, and execution of an already approved plan. For every new substantive task, default to diagnosis and a concrete proposed plan, then stop for the user's explicit approval of that presented plan. An initial "build it" request does not approve an unseen plan. A simple capability question needs only a brief answer, without repository investigation or a forced blueprint.

Before plan approval, perform necessary read-only investigation and prepare requested planning artifacts; do not change project code, working configuration, database schema/data, run migrations, or deploy. Read-only inspection does not authorize paid model probes or other external effects. A previously presented and explicitly approved plan remains authorized within its agreed scope; do not ask again merely because the user continues it. Material scope or architecture changes require approval of the revised plan. Plan approval does not grant unrelated runtime permissions.

Use [requirements refinement](references/harness-workflows.md#requirements-refinement-mode) for specification edits and the primary routes below for diagnosis/design. The approval gate applies to every route, including troubleshooting and deployment guidance.

## Freshness and self-update

Use the recorded reviewed snapshot by default. Do not check upstream on skill invocation, task startup, a schedule, or ordinary follow-up questions. Check only when the user explicitly asks to verify this skill's upstream revision or update it from upstream. Local skill edits and requests for current provider documentation, model capabilities, or pricing do not by themselves request a skill-upstream check.

For an explicitly requested check, read [provenance.json](provenance.json), use an approved network capability to resolve `main` at the canonical repository named there, and compare its exact commit with the recorded revision. Do not transmit workspace content in this check.

If upstream changed, report that the local reviewed snapshot is older and continue using it consistently. Do not fetch and apply unreviewed instructions in place of this package, fast-forward its source tree, or overwrite installed copies. Updating requires a separate user request, renewed source audit/conversion, validation, and the canonical repository installer after promotion. Preserve customizations and never mix revisions. If the user requires verified-latest guidance, an outdated or unverifiable package cannot satisfy that requirement; offer the separate update path before relying on new upstream material.

If an explicitly requested upstream check is unavailable, say freshness is unverified and use the recorded snapshot when the task permits. When no check was requested, do not add a routine upstream-freshness warning. A successful revision check does not verify every factual claim. Provider APIs, model capabilities, pricing, SDKs, external services, standards and legal/medical/financial rules still require current primary-source verification before consequential advice or implementation, independently of this skill's upstream policy. Keep provider comparisons as comparisons; do not translate one provider's protocol into another by renaming it. Dates and first-person evidence in [source-links.md](references/source-links.md) are the upstream author's historical observations, not checks performed in this session. Private transcripts cited there are unavailable, not workspace dependencies.

## Workflow

1. Select the request mode under the approval gate. Establish domain, autonomy/risk, state lifetime, available tools, and observable success criteria from supplied evidence. Ask only for missing information that changes correctness or authorization; state reasonable assumptions otherwise.
2. Choose one primary route. Read its required procedure and only the mechanism owners needed for the current decision; do not load all references or the complete blueprint template for a focused question. Narrow large reads so the relevant content is inspectable; truncated output is incomplete evidence. Follow required full-read rules for instruction files rather than treating snippets as a completed read.
3. For requirements, return concrete edits, gaps, tradeoffs and acceptance criteria using the refinement route. For a new design, choose the [execution shape](references/architecture.md#execution-shape-and-design-justification) first: fixed application stages or model-directed action selection. State purpose, model/application boundary, budgets, permissions, state/context, validation and minimal implementation path; add tools, memory, compaction or orchestration only when needed. For an audit, prioritize the smallest evidence-backed changes. For a failure, identify the earliest failing boundary, one safe discriminating probe, a proposed correction and regression case.
4. Justify significant new components and public settings against a concrete requirement and checked reuse options. Distinguish source-prescribed constraints, measured values and proposed defaults; do not present an unmeasured number as a best-practice optimum. Verify a claimed property across its relevant producer/transformation/consumer path and keep project observations, source guidance and your proposals distinguishable in the explanation.
5. For applicable model/tool stages, preserve every result, including denial, timeout and cancellation. Design bounded retries inside total budgets, durable state where recovery matters, action-bound approvals, and uncertain-outcome reconciliation. Read advanced invariants from their owning reference before using that profile.
6. For substantial multi-step implementation, use the [implementation plan structure](references/harness-workflows.md#implementation-plan-structure): reconcile requirements first, check step-local executability, and plan an early observable integration slice. Keep shared rules canonical and focused work compact. Verify the deliverable against its success criteria with applicable [checklist](references/checklists.md) items and meaningful [eval](references/evals.md) cases.
7. After drafting each final skill result, including revisions, perform the mandatory [final deliverable review](references/harness-workflows.md#final-deliverable-review) on the complete current result. Correct contradictions, obsolete remnants, dependency/contract gaps and unjustified or missing settings before presenting it; scale the review to the content rather than forcing a large plan for a short answer.
8. Report observed evidence, untested guarantees and the next step. Before approval, deliver the reviewed diagnosis/plan and stop; after approval, implement and verify within the agreed scope. A document review or successful upload alone does not prove runtime health.

## Primary routes

| Request | Primary reference and required companions |
| --- | --- |
| Refine an agent or LLM-workflow specification | [Requirements refinement](references/harness-workflows.md#requirements-refinement-mode); add a mechanism owner only for a decision requiring it |
| New domain agent or MVP | [MVP blueprint](references/mvp-agent-blueprint.md); select relevant loop, tool, context, security and eval owners below |
| Existing failure or stalled runtime | [Security and troubleshooting](references/security-observability.md#troubleshooting); follow the symptom's mechanism-owner links |
| Overall architecture or component boundaries | [Architecture](references/architecture.md) |
| Repository-facing coding agent | [Coding agents](references/coding-agents.md); keep repository instructions authoritative |
| Durable service, recovery, scheduled collection/delivery | [Always-on agents](references/always-on-agents.md); loop, tools, context, security and evals |
| Voice turns, interruptions, playback or corrections | [Realtime voice](references/realtime-voice-agents.md); tools and evals, plus MVP or troubleshooting as appropriate |
| Hardware or board deployment | [Hardware agents](references/hardware-agents.md); tools, security and hardware evals; require actual target evidence before deployment |
| Late-bound capabilities and environment drift | [Environment-adaptive tools](references/environment-adaptive-tools.md); tools, connectors, security and evals |
| Requested recursive/context-programming or continual refinement | [Recursive harnesses](references/self-refining-recursive-harnesses.md); context, workflow, permissions, security and evals |
| Requested teams exploring different approaches | [Adaptive teams](references/adaptive-agent-teams.md); workflow, recursive lifecycle, goals and evals |
| Requested execution before a generated program completes | [Speculative execution](references/speculative-tool-execution.md); loop, tools, security and evals; measure serial and ordinary parallel baselines first |

## Mechanism owners

- [Agentic loop](references/agentic-loop.md): results, retries, budgets, stopping and model routing.
- [Tools and permissions](references/tools-and-permissions.md): schemas, provenance, presentation, action approvals and cumulative limits.
- [Context and memory](references/context-memory-compaction.md): retrieval, memory corrections/deletion, compaction and recovery.
- [Planning and goals](references/planning-and-goals.md): planning versus execution, checkpoints and measurable completion.
- [Workflow orchestration](references/workflow-orchestration.md): work packets, integration, verification and durable workflow state.
- [Skills and connectors](references/skills-and-connectors.md): attachment/governance, MCP and opt-in public-board communication; architectural guidance only.
- [System prompts](references/system-prompts-instructions.md): instruction layers and templates.
- [Provider API patterns](references/provider-api-patterns.md): explicit provider comparisons; verify current implementation details.
- [Caching and cost](references/prompt-caching-and-cost.md): context layout, cost telemetry and optional measured warming.
- [Security and observability](references/security-observability.md): trust boundaries, traces, incidents and diagnosis.
- [Evals](references/evals.md): acceptance, failure fixtures, performance proxies and regression gates.
- [Agent-readable artifacts](references/agent-legibility-feedback-loops.md): canonical knowledge, validators and feedback loops.
- [Checklists](references/checklists.md): applicable implementation and audit closure checks.
- [Coverage audit](references/coverage-audit.md): source topic-coverage map, not proof of this conversion's validation.
- [Source links](references/source-links.md): attributed historical evidence and official sources.
- [Upstream guide](references/upstream-guide.md): worked use cases and application examples.

## Example: scheduled digest audit

Request: “Audit a scheduled digest that hangs after network loss and sometimes sends twice.”

Start with troubleshooting and the recurring collection/delivery contract in always-on agents. Inspect existing run status, source progress, timeout/cleanup behavior, operation identities and actual delivery receipts. Separate a confirmed hang from a suspected resend. Propose one bounded network-loss probe and a crash-between-send-and-record probe in an isolated fixture. A useful result names the observed boundary, minimal correction, repeat-delivery policy and the evidence needed to declare recovery complete; it does not introduce a new always-on daemon by default.
