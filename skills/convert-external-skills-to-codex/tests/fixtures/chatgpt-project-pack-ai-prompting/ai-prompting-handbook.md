# 18 — AI & Prompting
## ChatGPT Project Handbook

Use this file as the rich reference layer for AI prompting, workflow design, prompt debugging, model-choice framing, voice training, prompt chaining, and AI-use policy work inside a ChatGPT Project.

## How to use this handbook

Upload this file to a ChatGPT Project as reference material. Pair it with the compact runtime file for always-active behavior.

Use this handbook when the user wants to:

- improve a prompt or workflow
- build a reusable prompt structure
- diagnose why an AI output is weak
- design a repeatable AI workflow
- find high-value AI use cases
- choose or compare model roles
- train a writing voice
- build a multi-step prompt chain
- draft an internal or public AI-use policy

## When not to use this handbook

Do not use this handbook for:

- simple factual Q&A
- one-off casual chat
- tiny rewrite requests with already-clear constraints
- legal, financial, or medical decisions that need expert review rather than prompting help
- product, policy, or model claims that must be current unless you verify them first

## Module router

- If the user needs better task context, use `Context Framework`.
- If the user wants a reusable structured prompt, use `JSON Prompt Builder`.
- If the user has a bad prompt and wants to fix it, use `Prompt Debugger`.
- If the user repeats the same tasks and wants reuse, use `Prompt Library Builder`.
- If the user wants a repeatable end-to-end workflow, use `AI Workflow Builder`.
- If the user asks where AI can help most, use `AI Use Case Finder`.
- If the user asks which model or model combination fits a task, use `Model Selector`.
- If the user wants outputs to sound like them, use `AI Writing Voice Trainer`.
- If the user needs several prompts in sequence, use `Prompt Chain Builder`.
- If the user wants clear AI-use boundaries, disclosure, or policy language, use `AI Content Policy`.
- If multiple modules fit, start with the narrowest one and pull in another module only when it materially improves the result.

## Shared operating rules

- Ask only for missing inputs that block a useful answer.
- Label assumptions when optional inputs are absent.
- Keep prompts explicit about audience, tone, purpose, constraints, and output format.
- Preserve human review steps in workflows. Do not imply that AI replaces judgment.
- Treat model choice, platform behavior, and policy/disclosure norms as freshness-sensitive.

## Skill 01 — Context Framework

### Purpose
Define audience, tone, and purpose before asking the model to do real work.

### Minimum inputs
- task
- audience
- purpose

### Optional inputs
- tone descriptors
- examples of good or bad output

### Assistant behavior
- Identify the missing context variables.
- Rewrite the request so audience, tone, and purpose are explicit.
- Show the short reusable form the user can paste into future prompts.

### Good output looks like
- a vague prompt rewritten into a context-rich version
- a one-line reusable context add-on for future prompts

### What to do next
Use `JSON Prompt Builder` when the same task repeats or has many variables.

## Skill 02 — JSON Prompt Builder

### Purpose
Turn repeated prompting tasks into labeled, reusable structures.

### Minimum inputs
- task
- variables that change between runs
- what good output should contain

### Optional inputs
- audience
- tone
- constraints
- examples

### Assistant behavior
- Identify the variables that deserve their own fields.
- Produce a reusable structured template.
- Show one completed example and one blank reusable version.

### Good output looks like
- a field-based prompt contract with `task`, `context`, `include`, `avoid`, and output constraints
- one completed example that shows how the structure works in practice

### What to do next
Store the final structure in a prompt library if the task will recur.

## Skill 03 — Prompt Debugger

### Purpose
Diagnose why a prompt is failing and fix it without rewriting blindly.

### Minimum inputs
- original prompt
- what went wrong

### Optional inputs
- target audience
- preferred tone
- expected output format
- example of desired output

### Assistant behavior
- Name the failure mode before suggesting a fix.
- Explain what missing variable or bad instruction caused the failure.
- Produce a corrected prompt and a short reason it should work better.

### Good output looks like
- clear failure labeling such as “too generic”, “wrong structure”, or “sounds like AI”
- one improved prompt plus a concise explanation of the fix

### What to do next
If the fixed prompt will be reused, convert it into a structured template or library entry.

## Skill 04 — Prompt Library Builder

### Purpose
Organize repeatable prompts into a reusable library instead of ad hoc reuse.

### Minimum inputs
- recurring task list
- naming preference

### Optional inputs
- owner
- usage frequency
- success criteria
- review cadence

### Assistant behavior
- Group prompts by use case.
- Create a standard entry format with trigger, required inputs, template, and maintenance notes.
- Recommend a lightweight index so the user can find the right prompt quickly.

### Good output looks like
- a prompt entry template
- a small index that says when to use each prompt
- review/archive guidance for keeping the library current

### What to do next
Use the library entry format every time a reusable prompt proves its value twice.

## Skill 05 — AI Workflow Builder

### Purpose
Design a repeatable workflow that includes both AI and human steps.

### Minimum inputs
- workflow goal
- final output

### Optional inputs
- bottlenecks
- time currently spent
- tools used
- quality risks

### Assistant behavior
- Break the task into human and AI stages.
- Define what happens at each stage and what gets handed off.
- Include review gates only where they reduce real rework or risk.

### Good output looks like
- a step-by-step workflow with both human and AI responsibilities
- practical timing or effort cues, not just abstract steps

### What to do next
Run the workflow once on a real task and tighten the weakest step.

## Skill 06 — AI Use Case Finder

### Purpose
Find the highest-value AI use cases for a specific role or business.

### Minimum inputs
- role or business context
- main goals

### Optional inputs
- current workload
- biggest bottlenecks
- current AI usage

### Assistant behavior
- Identify repeated, time-consuming, or structure-heavy tasks.
- Rank opportunities by likely impact and ease of testing.
- Recommend the first few experiments rather than a giant wish list.

### Good output looks like
- a short prioritized list
- one or two concrete first experiments
- reasoning about likely impact

### What to do next
Turn the top-ranked use case into a workflow or prompt template.

## Skill 07 — Model Selector

### Purpose
Choose the best model role for a task and decide whether a multi-model flow is worth it.

### Minimum inputs
- task
- success criteria

### Optional inputs
- latency or cost sensitivity
- need for web verification
- need for structured output

### Assistant behavior
- Classify the task by reasoning, writing, coding, grounding, or speed needs.
- Recommend the primary model role and any second-pass verifier only when useful.
- Explain the handoff if more than one model role is suggested.

### Freshness-sensitive note
Current model capabilities and platform behavior must be verified before treating recommendations as final.

### What to do next
Test the recommendation on one real task before standardizing it.

## Skill 08 — AI Writing Voice Trainer

### Purpose
Capture a real writing voice so outputs sound like a person, not a generic assistant.

### Minimum inputs
- 3 to 5 writing samples
- description of what the voice should never sound like

### Optional inputs
- audience
- emotional effect
- preferred sentence rhythm

### Assistant behavior
- Analyze sentence rhythm, vocabulary, point of view, and forbidden patterns.
- Write a concrete voice profile.
- Show contrast between generic output, bad imitation, and better fit.
- Produce a reusable instruction block.

### Good output looks like
- a filled voice profile
- “never do this” patterns
- a pasteable instruction block
- a validation checklist for whether the voice is landing

### What to do next
Test the instruction block on a short real asset and refine only the parts that still sound wrong.

## Skill 09 — Prompt Chain Builder

### Purpose
Break a complex task into several prompts with explicit handoffs.

### Minimum inputs
- final task
- final output

### Optional inputs
- research needs
- different thinking stages
- supporting outputs

### Assistant behavior
- Separate research, analysis, drafting, and supporting-output stages when helpful.
- Write each chain link for one thinking job only.
- Add handoff instructions and review checks between stages.

### Good output looks like
- a chain with link-by-link prompts
- clear handoffs
- timing or effort expectations for the whole sequence

### What to do next
Run one full chain on a real project before deciding whether all links are necessary.

## Skill 10 — AI Content Policy

### Purpose
Draft an honest, usable AI policy with public disclosure and FAQ support.

### Minimum inputs
- business or team context
- who follows the policy
- main concerns or constraints

### Optional inputs
- client requirements
- regulatory needs
- current AI usage level

### Assistant behavior
- Separate what AI is for from what it is not for.
- State review and verification expectations plainly.
- Add a short public disclosure statement and FAQ when appropriate.

### Freshness-sensitive note
Disclosure norms, policy expectations, and regulatory requirements can change. Verify current rules before finalizing public or regulated policy language.

### Good output looks like
- policy rules plus reasoning
- disclosure statement
- FAQ covering trust, review, and verification

### What to do next
Publish the policy where the team or audience can actually use it, then review it when platform or policy expectations change.

## Writing and prompting rules

Apply these by default for prompting-related outputs in this handbook:

- Every serious prompt should make audience, tone, and purpose explicit.
- Include an `avoid` or anti-pattern layer when quality depends on what should not happen.
- Diagnose specific prompt failures before rewriting.
- Require real writing samples for voice capture.
- Include human review steps in workflows.
- For public-facing writing, prefer plain language over inflated jargon.
- Keep banned-word or banned-phrase rules scoped to writing tasks. Do not force them onto technical, legal, medical, or engineering outputs when precise wording matters.

## Freshness gates

Verify current information before treating guidance as final when the task depends on:

- current model choice or model behavior
- disclosure or policy norms
- platform behavior
- legal or compliance rules

If verification is unavailable, proceed with a useful draft but label what may be stale.
