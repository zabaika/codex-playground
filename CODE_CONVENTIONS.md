# Code Conventions

This document owns the repository's Python coding conventions and size-review
signals. Apply it to new and changed Python code together with project-specific
guidance. Other languages retain their own conventions; Python syntax and sizing
guidance do not automatically apply to them.

## Sources and Precedence

- Apply the adopted rules written in this local document. They are adapted from
  PEP 8 and Google's function-length guidance; neither external guide is adopted
  in full or required reading for ordinary coding work.
- Background references, for provenance only (last external source check:
  2026-10-21): [PEP 8](https://peps.python.org/pep-0008/) and
  [Google Python Style Guide: Function length](https://google.github.io/styleguide/pyguide.html#318-function-length).
- Use this local revision without fetching those references. Check external sources
  or update the adopted rules only when the user explicitly requests it; an upstream
  change does not silently change this convention.
- Explicit user decisions and applicable repository/project contracts take
  precedence. Follow an existing documented formatter or lint configuration rather
  than introducing competing settings.
- PEP 8 prioritizes consistency within a project and preserving compatibility.
  Style recommendations are not grounds to change behavior or break an interface.
- Apply the judgment, approval and uncertainty policy in
  [RULEBOOK: Modularity and Refactoring Decisions](./RULEBOOK.md#modularity-and-refactoring-decisions).
  If local guidance is missing or ambiguous, ask the user before choosing an
  interpretation or consulting external guidance; do not fill the gap by silently
  importing additional rules from the broader guides.
  Readability guidance does not waive security, authorization or verification requirements.

## Python Baseline

- Use four spaces for indentation, descriptive names, `snake_case` for functions
  and variables, `CapWords` for classes, and `UPPER_CASE` for constants.
- Keep imports explicit and grouped into standard library, third-party and local
  imports. Prefer module-level imports; preserve justified lazy/conditional imports
  required by optional dependencies or supported entrypoints. Avoid wildcard imports.
- Follow the project's configured line length. Without an explicit project
  setting, use PEP 8's 79-character code and 72-character comment/docstring guidance.
  Treat these as readability guidance, not an instruction to rewrite surrounding code.
- Match the established local style in existing code. Limit formatting changes to
  the work's scope; a convention update does not authorize a formatting migration.
- Use comments and docstrings to explain non-obvious intent, contracts or constraints
  rather than restating the implementation.

## Function and Module Size Signals

| Signal | Scope and interpretation |
| --- | --- |
| A function exceeds about 40 lines | Google recommends considering whether it can be split without harming program structure. This is a review prompt, not a hard limit. |
| A module exceeds 1000 physical lines | A repository starting heuristic for reviewing cohesion before extending production Python code. Count includes blank lines, comments and docstrings; size alone does not establish a problem. |
| A new independent responsibility appears | Review its owner regardless of the current file length. |

The module threshold is a local review policy, not a PEP 8 or Google requirement
and not a measured optimum. Exclude test-only, generated and vendored files from
this module-size signal; ordinary maintenance and correctness obligations still apply.
Keep numeric size guidance here, not repeated in agent instructions or the rulebook.

These signals require a decision under the linked rulebook policy, not automatic
extraction, a required target file size or a ban on extending a cohesive module.
No linter, CI gate or new development dependency is enabled by this document.
If an automated check is later adopted, make its settings consistent with this
owner and introduce it within separately approved tooling scope.
