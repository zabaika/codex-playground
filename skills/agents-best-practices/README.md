# Agents Best Practices — converted Codex package

Local Codex skill for designing, auditing, troubleshooting and refining specifications for agent applications and bounded LLM workflows. The package contains instructions and references; it does not start a service, connect MCP, register accounts or send messages.

## Usage

After installation, invoke `$agents-best-practices`. Example: “Use $agents-best-practices to audit Telegram digest recovery after a timeout. Identify observable causes and minimal changes.”

By default, a new substantive task produces a diagnosis and proposed plan. Implementation starts only after explicit approval of the presented plan; continuing an already approved plan does not require renewed approval for each reversible step. The exact permission boundary is defined in [SKILL.md](SKILL.md#request-routing-and-implementation-gate). A simple capability question receives a brief answer without project investigation.

Use the [requirements refinement route](references/harness-workflows.md#requirements-refinement-mode) to improve an existing specification. When designing a system, first choose a [fixed workflow or agent loop](references/architecture.md#execution-shape-and-design-justification). Justify new components and settings against existing ones, and label initial numerical limits as proposals unless they are measured or prescribed by an external contract.

A [Pre-implementation Architecture Risk Review](references/harness-workflows.md#pre-implementation-architecture-risk-review) is mandatory before implementing substantial multi-stage, stateful or LLM-driven architecture. It checks semantic contracts, failure and recovery, information preservation, resource feasibility, and simplicity with stopping conditions. Its findings belong in the existing plan and acceptance criteria; no separate document is required. An ordinary local Python fix without these risks needs no full audit. The repository-root `RULEBOOK.md` owns universal policies, `CODE_CONVENTIONS.md` owns Python practices, and the skill owns the design procedure. Document reading order does not replace ownership of a specific contract.

For substantial multi-step implementation, use the [implementation plan structure](references/harness-workflows.md#implementation-plan-structure): consolidate current requirements and risk-review findings, then check that step cards are executable and an early end-to-end scenario is planned. When analysis inputs are frozen, apply the [input readiness and freezing contract](references/harness-workflows.md#input-readiness-and-freezing). Define shared instructions once; when compressing a plan, verify that requirement meaning is preserved. Keep brief diagnoses and small specification edits compact. The reference owns the detailed contract; `RULEBOOK.md` owns general project rules.

During approved execution, when TDD applies, take the nearest testable behavior through a short RED → GREEN → REFACTOR cycle. Each step card identifies checks for the affected contract and its direct consumers, plus an integration checkpoint for full regression under project rules. Select check types, fresh runs and reuse of prior evidence under the [Regression loop](references/evals.md#regression-loop); instruction changes are not merely documentation formatting. This does not require detailing the entire remaining plan again before each step. Agree on shared SQL, transaction and public contracts before changes that affect them.

Iterative work follows the repository-root `RULEBOOK.md` section `Bounded Improvement and Stopping Policy`. Cases for comparing rules and the limits of self-evaluation are defined in [architecture-review evals](references/evals.md#architecture-risk-review-evaluation); they assess decisions rather than keyword presence.

After drafting each result, including revisions, perform a separate [final review](references/harness-workflows.md#final-deliverable-review) of the complete current document. Fix findings before delivery. For a substantial saved plan, briefly record the verification method and its limits, distinguishing self-review, automated checks and independent review. Check [result dependencies and retention](references/always-on-agents.md#result-dependencies-and-retention), including completed and failed tasks. For data transformations, verify [information preservation between stages](references/workflow-orchestration.md#information-preservation-and-handoffs) against the specific project's contract. Apply checks relevant to the content; a brief answer does not require a detailed plan.

For one-shot processes subject to the hard-TTL rule, the final review traces every applicable public launch path to its outer limiter. Contrasting [eval cases](references/evals.md#what-to-test) check bypasses and shared use of the existing mechanism. For public capabilities, verify the path from discovery in the project's designated help to a valid invocation. Internal entrypoints do not require public listing.

For migrations, the [coding agents route](references/coding-agents.md#task-profiles) covers supported launch modes, owners of substituted dependencies, saved delegates, evaluator hooks, Python symbol scopes, installers and provenance. Contrasting [validation cases](references/coding-agents.md#evals) cover fixture and isolation failures, import modes, hook relocation, dependency completeness of extracted modules, and checks that are still running. `RULEBOOK.md` owns the general test-isolation policy; the skill does not prescribe one protection mechanism for every project.

The main workflow and permission boundaries are defined in [SKILL.md](SKILL.md). Detailed modes and the output template are in [harness-workflows.md](references/harness-workflows.md); example requests are in [upstream-guide.md](references/upstream-guide.md). All 25 topical references from the source are preserved. Provider comparisons and historical cases remain reference material that requires verification before practical use.

The distinction between technical dependencies, mandatory gates and chosen execution order is described in the [plan structure](references/harness-workflows.md#implementation-plan-structure). Verifying that a separate model corresponds to the saved deliverable is part of the [final review](references/harness-workflows.md#final-deliverable-review); contrasting cases are in [evals](references/evals.md#what-to-test).

## Source and maintenance

The origin, commit and checksums of the source documents are recorded in [provenance.json](provenance.json); the license is in [LICENSE](LICENSE). Check upstream only when the user explicitly requests a version check or an update from upstream. During ordinary invocation, task startup, follow-up questions and local edits, the skill uses the reviewed snapshot without automatic upstream checks. The exact policy is in [SKILL.md](SKILL.md#freshness-and-self-update). Automatic instruction replacement is disabled; an upstream update requires a separate request, renewed audit and validation. Verifying current APIs, models and other changing external facts remains a separate requirement.

The canonical editable source is `skills/agents-best-practices/` in Playground. Follow `RULEBOOK.md` at the project root when maintaining it. Install or refresh the installed copy from the project root:

```bash
bash skills/agents-best-practices/install-local.sh
```

The installer replaces only `${CODEX_HOME:-$HOME/.codex}/skills/agents-best-practices` with the local reviewed package and makes no network requests. Do not clone upstream over the conversion or edit the installed copy as a second source. `placement_mode` in provenance records the original preparation in scratch, not the current installation status.

Use this skill within Playground; it loads the root engineering rules through the task's repository context, as specified in [SKILL.md](SKILL.md#execution-boundaries).

Reading the package requires no secrets, local configuration, logs or runtime. Implementing an agent, running checks or connecting a service is a separate task with its own permissions and artifacts.

## References

- Design: [MVP](references/mvp-agent-blueprint.md), [architecture](references/architecture.md), [loop](references/agentic-loop.md), [tools](references/tools-and-permissions.md), [prompts](references/system-prompts-instructions.md), [providers](references/provider-api-patterns.md).
- State and execution: [memory](references/context-memory-compaction.md), [goals](references/planning-and-goals.md), [orchestration](references/workflow-orchestration.md), [always-on agents](references/always-on-agents.md).
- Specialized modes: [coding](references/coding-agents.md), [voice](references/realtime-voice-agents.md), [hardware](references/hardware-agents.md), [adaptive environments](references/environment-adaptive-tools.md), [teams](references/adaptive-agent-teams.md), [recursion](references/self-refining-recursive-harnesses.md), [speculative execution](references/speculative-tool-execution.md).
- Integrations and quality: [skills and connectors](references/skills-and-connectors.md), [cost](references/prompt-caching-and-cost.md), [security and troubleshooting](references/security-observability.md), [evals](references/evals.md), [artifacts and feedback](references/agent-legibility-feedback-loops.md), [checklists](references/checklists.md), [coverage audit](references/coverage-audit.md), [sources](references/source-links.md).

If a referenced tool is unavailable in the current environment, use an available equivalent under the same permissions or identify a manual step. References to the author's private logs do not imply that those logs are available here.
