# Source Analysis: Engineering Path

Use this reference only as an internal extraction pass for sources that naturally fit engineering lessons, platform practices, or company/system operating models. Do not output this structure directly into the final Obsidian note.

## Extraction Goal

- Isolate the reusable engineering signal from the source.
- Ignore filler, sponsor messages, jokes, and repetitive transitions.
- Preserve concrete mechanics, constraints, metrics, and trade-offs.

## Extract

### Source Context

- Company, team, or system
- Main engineering theme
- Whether the source contains a real operating model

### Reusable Lessons

- Find 3-10 portable lessons.
- Each lesson should be a reusable principle, mechanism, or trade-off.
- Prefer lessons that explain how something works or why a decision was made.
- Drop banal conclusions such as:
  - improve productivity
  - use better tools
  - follow best practices

### Operating Detail

- Pull concrete detail that should survive into a note:
  - team scope
  - owned systems
  - partner functions
  - platform architecture
  - tooling
  - metrics
  - prioritization
  - feedback loops
  - AI rollout mechanics
  - build-vs-buy constraints

### Concepts

- Identify 3-7 reusable concepts or methods that can apply beyond the source.
- Prefer universal concepts over company-specific labels.

## Output Shape For The Internal Extraction

- `source context`
- `key lessons`
- `operating detail`
- `concept candidates`
- `non-obvious insights`

## Mapping Rule

- After extraction, switch back to the main skill rules.
- Convert the extracted signal into Obsidian `lessons`, `operating-model`, and `concept` notes only when the vault actually needs them.
