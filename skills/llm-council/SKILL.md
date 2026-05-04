---
name: llm-council
description: Run a high-stakes decision, tradeoff, positioning question, or strategic uncertainty through a 5-advisor council, anonymous peer review, and a final chairman verdict. Use when the user explicitly asks to "council this", pressure-test a choice, compare meaningful options, validate a risky move, or get multiple conflicting perspectives before acting. Do not use for factual lookups, routine implementation tasks, simple yes/no questions, or requests with one objective answer.
---

# LLM Council

Run a structured multi-agent decision review. Use five deliberately conflicting thinking styles, force them to review each other anonymously, then synthesize one clear recommendation and one concrete first step.

## File responsibilities

| File | Canonical responsibility |
| --- | --- |
| [SKILL.md](SKILL.md) | Workflow entrypoint: decide whether to run the council, gather context, orchestrate advisors, and save the canonical council payload. |
| [references/role-prompts.md](references/role-prompts.md) | Single source of truth for advisor, reviewer, and chairman prompts plus shared output-format rules for role responses. |
| [references/payload-contract.md](references/payload-contract.md) | Single source of truth for the canonical council payload JSON shape and text-format rules. |
| [config/runtime.example.toml](config/runtime.example.toml) | Template and operator-facing reference for machine-local payload-path, model, phase, and cleanup configuration. |
| [scripts/render_common.py](scripts/render_common.py) | Shared normalization and path-validation helpers for council payload handling. |
| [scripts/council_payload_schema.py](scripts/council_payload_schema.py) | Single executable owner of the canonical `council-verdict` payload parser and validator used by both this skill and downstream structured-note consumers. |
| [scripts/render_council_report.py](scripts/render_council_report.py) | Adapter that converts a council payload into an `article-to-obsidian-kb` structured verdict note. |
| [scripts/prepare_canonical_payload.py](scripts/prepare_canonical_payload.py) | Canonical payload-prep step that normalizes and saves `council-payload-...json` before downstream note writing. |

## Quick gate

Run the council only when the question has real stakes, ambiguity, or competing tradeoffs.

Use the council for prompts like:

- "Council this decision."
- "Pressure-test this launch plan."
- "I'm torn between option A and B."
- "Validate this positioning before I commit."
- "What would you do if this were your business?"

Do not run the council for:

- factual lookups
- straightforward coding or writing tasks
- simple yes/no questions with little downside
- requests where the user wants execution, not deliberation

If the request is too vague to frame a real decision, ask one clarifying question, then proceed.

If subagents are unavailable, do not simulate a council as one voice pretending to be five. Say that the full council workflow cannot run in the current environment and offer a shorter single-agent critique only if the user wants it.

## Advisor set

Use exactly these five advisors:

1. `Contrarian`
Actively search for failure modes, hidden assumptions, and reasons the idea breaks.

2. `First Principles Thinker`
Strip the problem to fundamentals and challenge whether the user is solving the right problem.

3. `Expansionist`
Search for upside, asymmetric opportunity, and larger plays that others will underweight.

4. `Outsider`
Judge only what is visible in the prompt and supplied context. Catch jargon, assumptions, and curse-of-knowledge errors.

5. `Executor`
Reduce everything to practical sequencing, speed, cost, and the next real-world move.

Keep the tension. Do not ask advisors to be balanced. Balance comes from synthesis, not from diluted individual answers.

## Workflow

### 1. Frame the question

Build one neutral brief for every downstream agent.

Before framing:

- Read the user's message carefully.
- Read only the most relevant local context, capped to a quick pass.
- Prioritize files the user referenced directly, then workspace docs that define constraints, audience, business context, or prior decisions.
- Avoid broad filesystem wandering. Three useful artifacts beat twenty generic ones.
- Prefer existing verdict notes or payload JSON artifacts only when they are clearly related and help avoid re-litigating the same decision.

Frame the brief with:

- the core decision
- the real options
- known constraints
- relevant numbers or evidence
- what is at stake
- what a good outcome looks like

Do not inject your own opinion into the framing.

Save the framed brief into the council payload.

### 2. Convene the five advisors

Spawn five fresh subagents in parallel, one per advisor. Use direct prompts and keep each response in the 150-300 word range.

Load the exact advisor prompt template from [references/role-prompts.md](references/role-prompts.md).
Resolve advisor model settings from `roles.advisor` in `<skill-dir>/config/runtime.local.toml`. Pass `model` and `reasoning_effort` to each advisor spawn when configured. If they are absent, let advisors inherit the parent session defaults.
Keep the council language aligned with the original user question. Write the framed brief in the same dominant language as the user request, and require advisor, reviewer, and chairman outputs to use that same dominant language. Allow English only for standard technical or product terms when recognition benefits from it.

After all advisor responses are collected, issue `close_agent` for those advisor agents before starting the peer-review phase. Do not keep completed advisor threads open longer than needed.
Do not wait for full shutdown completion before spawning reviewers when the configured agent-thread budget is sufficient. If close dispatch succeeds, proceed immediately to the next batch. Fall back to waiting only if thread-budget pressure still blocks new spawns.

If one advisor fails, retry once with the same prompt. If the retry also fails, continue with a degraded council and disclose that the run used fewer than five completed voices.

### 3. Run anonymous peer review

Collect the five advisor responses. Randomize their order and label them `Response A` through `Response E` so reviewers cannot infer the author from position.
Persist the anonymization mapping as a full one-to-one table between the ordered `Response A...` label set for this run and the completed advisor set. Do not allow duplicate labels, duplicate advisor names, gaps, or unknown advisor names.

Resolve reviewer batch size from `phases.reviewer_count` in `<skill-dir>/config/runtime.local.toml`. If the key is absent, default to `3`.
Spawn that many fresh review subagents in parallel. Give each reviewer the framed brief and all anonymized responses. Ask for:

1. the strongest response and why
2. the response with the biggest blind spot and why
3. what all responses missed

Load the exact reviewer prompt template from [references/role-prompts.md](references/role-prompts.md).
Resolve reviewer model settings from `roles.reviewer` in `<skill-dir>/config/runtime.local.toml`. Pass `model` and `reasoning_effort` to each reviewer spawn when configured. If they are absent, let reviewers inherit the parent session defaults.

After the peer-review responses are collected, issue `close_agent` for those reviewer agents before running the chairman synthesis. Treat agent-thread budget as a real operational constraint and release completed threads eagerly between phases.
Do not wait for full reviewer shutdown completion before spawning the chairman when thread budget allows it. Fall back to waiting only if the environment still reports thread-budget pressure.

### 4. Synthesize the chairman verdict

Use one final synthesis pass after peer review. Give the synthesizer:

- the original user question
- the framed brief
- all advisor responses with real advisor names restored
- all peer reviews

Load the exact chairman prompt and output structure from [references/role-prompts.md](references/role-prompts.md).
Resolve chairman model settings from `roles.chairman` in `<skill-dir>/config/runtime.local.toml`. Pass `model` and `reasoning_effort` to the chairman spawn when configured. If they are absent, let the chairman inherit the parent session defaults.

Treat the chairman output as the canonical final verdict object for the run. Do not run a second freeform synthesis pass in the parent thread after the chairman responds. Capture the chairman JSON exactly once, validate only that the required fields exist and the JSON is well-formed, then move straight to artifact rendering.
Pass `verdict.agrees`, `verdict.clashes`, `verdict.blind_spots`, `verdict.recommendation`, and `verdict.first_step` through verbatim from the chairman JSON into the payload. Do not paraphrase, rewrite, expand, shorten, merge, or reinterpret those fields in the parent thread.

After the chairman verdict is collected, issue `close_agent` for that chairman agent before writing artifacts or returning the final user summary. No completed council-phase agent should remain open after its output has been captured.
Do not block artifact writing on chairman shutdown completion unless the environment explicitly reports a close failure that needs operator attention.

Do not accept a vague synthesis. The final recommendation must take a stand.
Do not accept a vague `first_step`. It must preserve the strongest reviewer-backed practical move and express it as an ordered, operational sequence with the maximum concrete detail the available context supports.

### 5. Write the artifacts

Before writing:

1. Read `<skill-dir>/config/runtime.local.toml` when it exists.
2. Use [config/runtime.example.toml](config/runtime.example.toml) as the canonical reference for key names, defaults, and local-runtime notes.
3. Resolve the default temporary-build destination from `paths.temp_root`.
4. If `paths.temp_root` is absent and the user did not provide an override, ask instead of inventing a path.

Save this artifact under the resolved path:

- `<temp-root>/council-payload-YYYYMMDD-HHMMSS.json`

Treat `paths.temp_root` from local config as the canonical default for this skill. Assume the configured directory is already prepared as part of local setup; do not treat directory creation as a normal per-run step.
Canonical payload writes must stay under the configured `paths.temp_root`. Do not allow `scripts/prepare_canonical_payload.py` or `write_canonical_payload()` to write outside that root in the normal path.

The council payload is the primary artifact owned by this skill. Final verdict-note rendering and placement are delegated to `article-to-obsidian-kb` structured mode with `type=council-verdict`.
Prefer generating the verdict note through `scripts/render_council_report.py`, which now acts as an adapter into that downstream structured writer instead of owning the note contract locally.

Artifact path after chairman:

1. capture the chairman JSON verdict object
2. assemble one payload object with question, frame, run status, advisors, anonymization mapping, peer reviews, and verdict
3. normalize and save that payload JSON through `scripts/prepare_canonical_payload.py` so the saved `council-payload-...json` is already canonical
4. invoke the downstream structured-note writer from `article-to-obsidian-kb` with `mode=structured` and `type=council-verdict`
5. let `article-to-obsidian-kb` own verdict placement, frontmatter, template rendering, and final note-contract verification

Do not insert an extra parent-thread interpretation pass between steps 1 and 2.
Payload assembly is a transport step, not an editorial step.
Validate `anonymization_mapping` before rendering. It must be a full bijection between the ordered `Response A...` label set for this run and the actual advisor names present in the payload.
Create the payload so it satisfies [references/payload-contract.md](references/payload-contract.md). Treat that file as the only canonical owner of payload shape and payload text-format rules.
Treat [scripts/council_payload_schema.py](scripts/council_payload_schema.py) as the only executable owner of that payload parser/validator. Downstream consumers must reuse it instead of keeping a second local copy of `council-verdict` schema logic.
Do not mark a run as `full` unless the payload contains exactly 5 completed advisor responses and a non-empty peer-review list. If the run used fewer than 5 completed advisors or lacks peer review, mark it as `degraded` and explain why in `run_status.details`.

The JSON payload is the canonical operational artifact. Include `payload_source` in the payload and point it at the saved JSON payload path so the downstream verdict note can cite the JSON as its source.
Do not persist a raw pre-cleanup council payload as the canonical artifact. The saved `council-payload-...json` must already reflect the prompt-layer formatting rules plus any best-effort non-editorial cleanup allowed by `payload_cleanup.enabled`.

### 6. Present the result

Return a concise summary to the user with:

- where the verdict note was saved
- where the payload JSON was saved
- the single-sentence recommendation

Do not paste the full payload into chat unless the user asks.

## Payload handoff

When using `scripts/render_council_report.py`, pass a payload object that satisfies the canonical contract from [references/payload-contract.md](references/payload-contract.md).

Keep the handoff boundary strict:

- the payload is the canonical operational artifact
- payload text fields should already satisfy the prompt-driven plain-text rules from [references/role-prompts.md](references/role-prompts.md)
- any cleanup between role output capture and payload write is a soft formatting-normalization step only, not an editorial rewrite
- resolve that cleanup stage from `payload_cleanup.enabled` in local config; if the key is absent, default to enabled
- apply that cleanup before persisting the canonical `council-payload-...json`, not only later during downstream rendering
- do not create a second parallel payload schema in `SKILL.md`
- do not recreate the final verdict-note contract locally; delegate that to `article-to-obsidian-kb`

## Quality rules

- Keep the frame neutral.
- Keep advisors independent.
- Keep peer review anonymous.
- Keep the verdict decisive.
- Keep artifact names timestamped.
- Keep context collection disciplined and relevant.
