# Conversion Report
## 18 — AI & Prompting -> chatgpt-project-pack

### Source artifact type

Claude-oriented multi-module skill bundle with 10 internal skills, frontmatter, broad tool assumptions, and vendor-specific setup instructions.

### Selected output family

`chatgpt-project-pack`

### Why this family was selected

The source is a broad instructional bundle rather than one narrow installable workflow. It is better suited to a ChatGPT Project package with:

- one rich handbook for uploaded project files
- one compact runtime for always-active project instructions
- one sidecar report

### Alternative families considered

- `codex-skill`: rejected because the source is too broad and multi-module for one clean installable runtime path
- `codex-agents-md`: rejected because the source is not repo-scoped guidance
- `conversion-report-only`: not needed because the source could be adapted safely

### Package contents

- `18-AI-and-Prompting-chatgpt-handbook-v3.md`
- `18-AI-and-Prompting-chatgpt-project-runtime-v3.md`
- `18-AI-and-Prompting-chatgpt-examples-v3.md`
- this report

### Mixed-pack status

Mixed pack confirmed. The source contains 10 distinct modules plus shared rules.

### Split or preserve-whole decision

Preserved as one instruction-rich handbook plus one compact runtime because the modules belong to one coherent prompting domain and benefit from a shared router.

### Name-collision result

No output uses the original skill name as an installable Codex skill, so system-skill collision risk was avoided.

### Tool and permission mapping

- `needed_tools`: none expressed inside the converted ChatGPT artifacts
- `forbidden_tools`: broad default `Read Write WebSearch WebFetch` assumptions
- `web_required`: only for freshness-sensitive model or policy questions
- `write_required`: no
- `destructive_actions`: none
- `approval_expectation`: only for staged long-form or high-stakes work, not by default

### Main security findings

- Overbroad original description triggered on almost any prompting or workflow task.
- Original setup assumed vendor-specific installation into Claude projects or custom instructions.
- Original permissions were broader than the converted ChatGPT package needs.
- Model-selection and AI-policy sections are freshness-sensitive.

### Vendor residue removed

- `Upload this file to a Claude project`
- `paste into your custom instructions`
- `What Claude asks you first`
- direct `Claude` execution wording throughout the module descriptions

### Sections compressed or relocated

- Large teaching prose was compressed into module-level purpose, inputs, behavior, and quality bars.
- Runtime behavior was extracted into a separate compact file instead of staying mixed into the handbook.
- Representative templates, filled examples, chain structure, voice profile material, and policy companion outputs were relocated into a dedicated examples pack.

### Intake compressed

Original module interviews were compressed into `minimum inputs`, `optional inputs`, and shared blocking-question behavior for the runtime layer.

### Router added

Yes. The package now includes an explicit module router instead of relying on a long table of contents alone.

### Freshness gates added

Yes. Added for:

- model choice and model behavior
- disclosure and policy norms
- platform behavior
- legal or compliance expectations around AI-use policy

### Approval gates kept or removed

- Kept only as an optional pattern for staged long-form or high-stakes work
- Removed as a default operating expectation

### What was removed

- frontmatter and installable skill packaging
- source branding/footer
- broad `allowed-tools: Read Write WebSearch WebFetch`
- Claude-specific setup and execution wording

### What was substantially adapted

- The source was converted from one vendor-bound mega-skill into a `chatgpt-project-pack`.
- The original “What Claude asks you first” sections became `minimum inputs` plus compact runtime blocking-question logic.
- The original long teaching blocks were compressed into operational handbook modules.
- Voice-training and policy material were neutralized so they preserve function without keeping Prompt Guy branding as the default voice target.
- Freshness-sensitive areas now require current verification instead of making static claims.
- Concrete examples were moved into a dedicated examples pack so the handbook could stay cleaner without losing operational value.

### Remaining assumptions

- The handbook assumes the project user wants one shared prompting reference rather than separate files per module.
- Model and policy guidance still needs live verification when used for current recommendations.

### Follow-up required before use

- Upload the handbook to the ChatGPT Project.
- Paste the compact runtime into project instructions.
- Verify current model and policy facts before relying on those sections for final decisions.

### Block-level change log

- `Frontmatter and allowed-tools`: removed. Reason class `permission-normalization` and `naming-or-packaging`. Function removed because ChatGPT project files do not use installable skill frontmatter.
- `How to use this file`: rewritten. Reason class `surface-mismatch`. Function preserved and retargeted from Claude setup to ChatGPT Project usage.
- `What Claude asks you first` blocks: rewritten. Reason class `surface-mismatch`. Function preserved in compressed form as `minimum inputs` and runtime blocking-question logic.
- `Model Selector`: gated. Reason class `freshness-gating`. Function preserved with explicit current-verification requirement.
- `AI Content Policy`: gated. Reason class `freshness-gating`. Function preserved with explicit verification for disclosure/compliance expectations.
- `Prompt Guy voice example`: adapted. Reason class `surface-mismatch`. Function preserved as neutral voice-training guidance rather than source-specific branding.
- `Concrete templates and worked examples`: adapted. Reason class `surface-mismatch`. Function preserved in the examples pack as neutralized reusable examples rather than removed or reduced to abstract descriptions.
