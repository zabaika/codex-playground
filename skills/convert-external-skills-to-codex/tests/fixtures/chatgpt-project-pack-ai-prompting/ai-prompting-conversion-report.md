---
selected_output_family: chatgpt-project-pack
mixed_pack_status: mixed-pack
split_decision: preserve-whole
functional_parity_branch_status: none
name_collision_status: none
codex_skill_placement_mode: not-applicable
installation_status: not-applicable
---

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

### What was removed

- frontmatter and installable skill packaging
- source branding/footer
- broad `allowed-tools: Read Write WebSearch WebFetch`
- Claude-specific setup and execution wording

### What was substantially adapted

- The source was converted from one vendor-bound mega-skill into a `chatgpt-project-pack`.
- The original “What Claude asks you first” sections became `minimum inputs` plus compact runtime blocking-question logic.
- The original long teaching blocks were compressed into operational handbook modules.
- Runtime behavior was extracted into a compact instructions file, while templates and richer specimens moved into a dedicated examples pack.
- The package now relies on an explicit module router instead of one long vendor-specific handbook flow.
- Freshness-sensitive areas now require current verification instead of making static claims.
- Approval gating was narrowed to optional high-stakes or staged work instead of remaining a default operating pattern.
- Voice-training and policy material were neutralized so they preserve function without keeping Prompt Guy branding as the default voice target.
- Concrete examples were preserved in the examples pack so the handbook could stay cleaner without losing operational value.

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
