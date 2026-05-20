# OpenAI Surface Guidance

Use this note when deciding which OpenAI-compatible target surface should own the converted output.

This file owns only:

- output-family selection
- target-surface distinctions
- current OpenAI operational constraints that affect those distinctions

It does not own:

- source-risk severity
- conversion sequencing
- change disclosure
- package validation

## Output families in v1

### `chatgpt-project-pack`

Use when the source should become a clean ChatGPT Project package rather than one hybrid file.

This family contains:

- `full handbook` for uploaded project files or sources
- `compact runtime` for project instructions
- `conversion report` sidecar
- default-companion `examples-pack` sidecar when instruction-rich sources carry a heavy examples layer that should not live in the always-active runtime and would overinflate the handbook

Use this for large prompt books, multi-module handbooks, writing systems, and broad instructional bundles that are still useful inside ChatGPT Projects.

### `codex-skill`

Use when the output should become a reusable local skill with its own `SKILL.md`, narrow triggers, and one repeatable workflow.

### `codex-agents-md`

Use when the source is really project-level or repository-level guidance. This output belongs in `AGENTS.md` or `AGENTS.override.md`, not in a reusable general-purpose skill.

### `conversion-report-only`

Use when the user explicitly chooses an audit-only outcome, or when the source was requested as an audit from the start.

## Unsupported direct target in v1

If the source is mainly about MCP, apps, connectors, tool selection, tool sequencing, read or write distinctions, or confirmation behavior in ChatGPT Developer Mode, do not invent a fake output family.

Treat that as a functional-parity branch in the main workflow:

- explain that v1 has no direct specialized target for that source shape
- present explicit options such as `conversion-report-only`, a narrower selected subset, or deferring to a future specialized MCP or app-guidance converter
- do not enter `conversion-report-only` automatically unless the user asked for it from the start

## Operational notes from current OpenAI docs

- Codex project instructions are layered through `AGENTS.md`, and nested scopes can use `AGENTS.override.md`.
- Codex behavior also depends on sandbox mode, network access, and approval policy. Do not assume full write access or network access by default.
- Project-scoped Codex config is only loaded in trusted projects.
- ChatGPT Projects support uploaded files and project instructions, and project instructions override global custom instructions inside that project. Keep the reference layer and the always-active instruction layer separate rather than merging them into one hybrid file.
- Current-model wording should stay neutral unless the user explicitly requests a fixed model family. Do not hardcode stale phrases when converting a source.

If the conversion task depends on the latest OpenAI behavior, use the system skill `$openai-docs` before finalizing the output.

## Official documentation anchors

- Codex `AGENTS.md` layering:
  https://developers.openai.com/codex/guides/agents-md#layer-project-instructions
- Codex approvals and sandbox behavior:
  https://developers.openai.com/codex/agent-approvals-security#sandbox-and-approvals
- Common Codex sandbox and approval combinations:
  https://developers.openai.com/codex/agent-approvals-security#common-sandbox-and-approval-combinations
- Codex config and trusted-project behavior:
  https://developers.openai.com/codex/config-reference#configtoml
- ChatGPT Projects, uploaded files, and project instructions:
  https://help.openai.com/en/articles/10169521-using-projects-in-chatgpt
- ChatGPT Developer Mode and MCP-backed apps:
  https://developers.openai.com/api/docs/guides/developer-mode#how-to-use
- Latest-model guidance:
  https://developers.openai.com/api/docs/guides/latest-model.md
