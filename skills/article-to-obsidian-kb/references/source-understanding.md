# Source Understanding

Use this reference before choosing `engineering` or `general`. This pass is route-agnostic: it extracts what the source is really about, what practical material must survive, and what the vault should be searched for. Do not output this structure directly into final Obsidian notes.

## Extract

- **Dominant topic.** What reusable idea, method, mechanism, system, or problem is the source mainly about?
- **Source type.** Classify the source as one or more of:
  - method
  - practice workshop
  - interview
  - case study
  - engineering operating model
  - expert commentary
  - research or article synthesis
- **Practical signal level.** Classify as `high`, `medium`, or `low` using the source-supported practicality gate in [vault-conventions.md](vault-conventions.md).
- **Concrete practical anchors to preserve.** Extract actions, checklists, examples, numbers, decision criteria, failure modes, facilitation moves, workflows, constraints, and before/after transitions.
- **Engineering-term role.** State whether engineering terms are:
  - the core subject
  - examples inside another method
  - incidental vocabulary
- **Operating-model evidence.** State whether the source explains a real operating model through concrete organization, team, system, workflow, tooling, platform, architecture, metrics, ownership, handoffs, constraints, or rollout mechanics.
- **Candidate concepts and notes.** Name likely existing concepts, source-derived notes, and possible new concept titles.
- **Vault search queries.** Generate search queries from the extracted meaning and candidate note titles, not from isolated surface words in the source.

## Rules

- Do not choose the final route in this pass.
- If engineering vocabulary appears only in participant examples, case snippets, or analogies, do not classify the retained signal as engineering.
- If the source teaches a method, facilitation move, TOC thinking tool, communication protocol, decision technique, problem-framing practice, or productivity/career method, preserve that as the dominant topic even when examples mention software development.
- If no concrete engineering system is explained, do not create or route as `operating-model`.
- Prefer update/create decisions based on semantic overlap with the vault, not on the literal title, thumbnail, transcript title, or the first high-frequency vocabulary cluster.
