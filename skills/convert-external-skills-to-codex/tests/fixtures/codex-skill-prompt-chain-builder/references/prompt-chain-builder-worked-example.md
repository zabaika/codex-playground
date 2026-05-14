# Prompt Chain Example

## Task

Research a topic and write a newsletter issue about it.

## Chain Shape

Four stages:

1. research
2. angle selection
3. draft writing
4. supporting content

## Stage 1 — Research

Prompt purpose:
- collect findings before writing

Representative prompt:

```json
{
  "task": "Research this topic and return what you find",
  "topic": "[Your topic]",
  "focus": "Find current thinking, surprising evidence, and real examples of the problem this topic addresses.",
  "format": "Return findings as concise bullets with one short source note for each finding.",
  "do_not_do": "Do not draft the newsletter yet."
}
```

Expected output:
- a compact list of findings with source notes

## Stage 2 — Angle Selection

Prompt purpose:
- choose the strongest audience-relevant angle from the findings

Representative prompt:

```json
{
  "task": "Based on this research, identify the strongest newsletter angle",
  "research_findings": "[Paste findings from Stage 1]",
  "audience": "[Audience description]",
  "format": "Return three possible angles, why this audience would care, and what makes each one different from the obvious take.",
  "do_not_do": "Do not write the newsletter yet."
}
```

Expected output:
- 2-3 candidate angles
- one chosen angle before proceeding

## Stage 3 — Draft Writing

Prompt purpose:
- write the main newsletter draft using the research and chosen angle

Representative prompt:

```json
{
  "task": "Write a newsletter issue based on this research and chosen angle",
  "research_findings": "[Paste findings from Stage 1]",
  "chosen_angle": "[Chosen angle from Stage 2]",
  "voice": "[Voice or brand instruction block]",
  "audience": "[Audience description]",
  "format": {
    "length": "400 to 500 words",
    "structure": "Strong opening, evidence-backed body, clear close",
    "avoid": ["AI buzzwords", "weak generic opening"]
  }
}
```

Expected output:
- one full draft with explicit structure and voice constraints

## Stage 4 — Supporting Content

Prompt purpose:
- generate subject lines and one teaser post from the approved draft

Representative prompt:

```json
{
  "task": "Create supporting content from this approved newsletter draft",
  "newsletter_draft": "[Paste edited Stage 3 draft]",
  "output_1": "Three subject line options under 50 characters",
  "output_2": "One short teaser post in the same voice"
}
```

Expected output:
- multiple subject lines
- one short social teaser

## Handoff Pattern

- Research -> Angle Selection: pass findings only after checking they are specific enough
- Angle Selection -> Draft: pass one chosen angle, not all candidates
- Draft -> Supporting Content: pass the edited draft, not the raw first draft

## What Good Looks Like

- each stage changes the type of thinking
- each output is directly usable by the next stage
- the user makes decisions only at the important branch points
- the whole chain is easier to rerun than one giant prompt
- each prompt is concrete enough to run with only the stage inputs filled in

## Practical Cue

A chain like this is usually worth the extra setup when the single-prompt approach keeps failing. The first run may feel slower, but reuse becomes faster after the structure is stable.
