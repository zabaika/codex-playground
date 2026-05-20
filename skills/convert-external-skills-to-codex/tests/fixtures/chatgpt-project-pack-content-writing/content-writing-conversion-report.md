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
## 02 — Content & Writing -> chatgpt-project-pack

### Source artifact type

Claude-oriented multi-module writing bundle with 12 internal skills, frontmatter, broad tool assumptions, and vendor-specific setup language.

### Selected output family

`chatgpt-project-pack`

### Why this family was selected

The source is a broad writing handbook rather than one narrow runtime workflow. A ChatGPT Project package preserves the modular writing reference while keeping always-active behavior short.

### Alternative families considered

- `codex-skill`: rejected because the source covers too many distinct writing workflows for one installable runtime path
- `codex-agents-md`: rejected because the source is not repo-scoped
- `conversion-report-only`: not needed because the source could be adapted safely

### Package contents

- `02-Content-and-Writing-chatgpt-handbook-v3.md`
- `02-Content-and-Writing-chatgpt-project-runtime-v3.md`
- `02-Content-and-Writing-chatgpt-examples-v3.md`
- this report

### Mixed-pack status

Mixed pack confirmed. The source contains 12 writing modules plus shared writing rules.

### Split or preserve-whole decision

Preserved as one instruction-rich handbook plus one compact runtime because the modules belong to one coherent writing domain and benefit from one router and one shared writing-rules layer.

### Main security findings

- Original setup was vendor-bound to Claude.
- Original description and packaging implied a broad always-on writing skill.
- Original permissions were broader than the converted package needs.
- Several modules depend on current external facts and needed explicit freshness gates.

### Vendor residue removed

- `Upload this file to a Claude project`
- `paste it into your custom instructions`
- `What Claude asks you first`
- direct `Claude` execution wording across modules

### What was removed

- frontmatter and installable skill packaging
- source branding/footer
- broad `allowed-tools: Read Write WebSearch WebFetch`
- Claude-specific setup and execution wording

### What was substantially adapted

- The source was converted from one vendor-bound skill bundle into a `chatgpt-project-pack`.
- Module intakes were compressed from long interviews into handbook input fields plus compact runtime blocking-question behavior.
- Always-active behavior was extracted into a compact runtime file, while richer specimens and writing-rules support moved into a dedicated examples pack.
- The package now uses one explicit router for 12 workflows instead of relying on a long vendor-specific bundle.
- Explicit `when not to use` guidance was added so the package does not over-trigger on every writing-related chat.
- Freshness-sensitive modules now require verification instead of assuming current facts.
- Approval gating now appears only where long-form structure or high-stakes staging materially helps.
- Writing rules were scoped as public-facing defaults rather than universal law across all domains.
- Concrete examples were preserved in a dedicated examples pack instead of being reduced to abstract descriptions only.

### Remaining assumptions

- The package assumes one shared writing handbook is more useful than splitting 12 modules into separate files.
- Current SEO, platform, media, and performance facts still need live verification when relevant.

### Follow-up required before use

- Upload the handbook to the ChatGPT Project.
- Paste the compact runtime into project instructions.
- Verify current external facts before using the package for SEO, PR, launch, or performance-dependent work.

### Block-level change log

- `Frontmatter and allowed-tools`: removed. Reason class `permission-normalization` and `naming-or-packaging`. Function removed because ChatGPT project files do not use installable skill frontmatter.
- `How to use this file`: rewritten. Reason class `surface-mismatch`. Function preserved and retargeted from Claude setup to ChatGPT Project usage.
- `What Claude asks you first` blocks: rewritten. Reason class `surface-mismatch`. Function preserved in compressed form as module input contracts and runtime blocking-question rules.
- `Blog Post`: adapted. Reason class `surface-mismatch`. The old default staged-outline expectation was narrowed so approval happens only when structure truly matters.
- `Press Release`: gated. Reason class `freshness-gating`. Function preserved with explicit current-fact verification.
- `Content Calendar`: gated. Reason class `freshness-gating`. Function preserved with explicit verification for recent performance and platform behavior.
- `Writing rules`: adapted. Reason class `surface-mismatch`. Function preserved, but banned-word and style bans are now explicitly scoped to public-facing writing rather than every possible task.
- `Representative examples`: adapted. Reason class `surface-mismatch`. Function preserved in the examples pack as neutralized output specimens rather than removed.
