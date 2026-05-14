# 18 — AI & Prompting
## Examples Pack For ChatGPT Project

Use this file as a sidecar reference for worked examples, reusable templates, comparison blocks, and richer operational specimens that support the main handbook.

## Skill 01 — Context Framework

### Representative example

**Before**

`Write a LinkedIn post about why AI output sounds generic.`

**After**

`My audience is business owners who use AI daily but still get inconsistent output. The tone is direct, practical, and slightly irreverent. The purpose is to make them see that generic output is usually a prompting problem, not a tool problem. Write a LinkedIn post about why AI output sounds generic.`

## Skill 02 — JSON Prompt Builder

### Representative template

```json
{
  "task": "Write a newsletter issue",
  "audience": "Freelancers using AI tools daily but getting inconsistent output",
  "purpose": "Show why prompting quality matters more than tool-switching",
  "tone": ["direct", "practical", "plain"],
  "context": {
    "topic": "Why AI output sounds generic",
    "anchor_example": "A prompt that asks for 'a post about AI' with no audience or goal",
    "length": "400-500 words"
  },
  "include": [
    "one concrete example",
    "one explanation of the failure",
    "one practical fix"
  ],
  "avoid": [
    "AI buzzwords",
    "vague takeaways",
    "generic motivational close"
  ],
  "output_format": {
    "sections": ["opening", "body", "close"],
    "closing_goal": "leave the reader with one specific prompting change to test"
  }
}
```

## Skill 03 — Prompt Debugger

### Representative debugging pattern

```text
Original prompt:
Write a newsletter about AI for founders.

What went wrong:
Too generic. Wrong audience depth. Sounds like AI. No output goal.

Likely failures:
1. Missing audience specificity
2. Missing tone control
3. Missing purpose

Improved prompt:
Write a 450-word newsletter for SaaS founders who already use AI tools but still get inconsistent output. The tone is direct, plain, and slightly skeptical. The purpose is to make them realize they do not need a new tool; they need clearer prompts. Include one concrete example of a vague prompt and a better rewritten version. Avoid AI buzzwords and do not end with a summary.

Why this should work better:
The audience, tone, and job of the piece are now explicit, so the model no longer fills the blanks with average assumptions.
```

## Skill 04 — Prompt Library Builder

### Representative library entry

```text
ID: PROMPT-NEWS-02
Name: Newsletter issue from one idea
Use when: You have one clear topic and want one finished issue
Required inputs: topic, audience, takeaway
Optional inputs: story, offer, desired length
Template owner: content team
Review cadence: every 60 days or after 5 uses
Success signal: requires only light editing before send
Archive rule: replace if a newer version consistently performs better
```

## Skill 05 — AI Workflow Builder

### Representative workflow

```text
Workflow: Weekly research-backed newsletter

1. Human: choose the topic and define the audience
2. AI: research current arguments, data, and examples
3. Human: remove weak or unverified findings
4. AI: propose 3 angles based on the verified research
5. Human: choose the angle
6. AI: draft the newsletter in the chosen voice
7. Human: edit for truth, voice, and sharpness
8. AI: produce supporting subject lines and teaser post

Time expectation:
- before workflow: 3 to 4 hours
- after workflow stabilizes: about 50 to 60 minutes
```

## Skill 06 — AI Use Case Finder

### Representative scorecard

```text
Task: Turn call notes into follow-up emails
Frequency: high
Structure: high
Risk if wrong: low
Expected time saved: medium-high
Recommended experiment: Build one structured follow-up prompt and test it on 5 recent calls
```

## Skill 07 — Model Selector

### Representative handoff rule

```text
If one model is stronger for research and another is stronger for final writing, keep the handoff explicit:

1. Use the research model to return findings in bullets with source notes.
2. Verify the findings you plan to use.
3. Pass only the verified findings into the writing model.
4. Ask the writing model to use the verified findings as evidence, not to repeat the research phase.
```

## Skill 08 — AI Writing Voice Trainer

### Representative neutral voice profile

```text
VOICE PROFILE

The sound:
Writes like someone who already has a point and is trying to make it useful fast. Short sentences by default. Plain vocabulary. Examples appear early, not as decoration at the end.

What this voice never does:
- opens with a question nobody asked
- uses inflated jargon to fake authority
- ends by summarizing what the reader just read
- writes three-word dramatic sentences for effect

Instruction block:
Write in a direct, plain, human voice. Make the point early. Use examples to clarify, not to pad. Avoid inflated business jargon. Do not open with an empty hook question. Do not end with a summary.

Validation checklist:
- Does this sound like a person with a point of view?
- Would the real writer actually use these words?
- Is the rhythm natural rather than mechanically "punchy"?
- Did the model avoid the forbidden patterns?
```

## Skill 09 — Prompt Chain Builder

### Representative chain

```text
Chain link 1: Research the topic and return verified findings only.
Chain link 2: Turn the findings into 3 plausible angles for the target audience.
Chain link 3: Draft the core asset in the desired voice using the chosen angle.
Chain link 4: Generate supporting assets from the edited draft.

Handoff rule:
- Never pass raw, unverified findings into the writing stage.
- Review each stage before moving to the next if the task is high-stakes.

Time expectation:
- first run: 35 to 45 minutes
- after the chain stabilizes: shorter, because each stage has a clear job
```

## Skill 10 — AI Content Policy

### Representative companion outputs

```text
Public disclosure statement:
Some content in this project may be drafted with AI assistance. Final published work is reviewed, edited, and approved by the accountable human owner before release.

FAQ:
Does AI publish directly?
No. AI helps with drafting, structure, or research support, but final judgment stays human.

Can I trust factual claims?
Only after verification. Unverified statistics or references should not survive into the final output.
```

## Detailed public-facing defaults

Avoid words like:

- leverage
- transformative
- comprehensive
- revolutionary
- seamless

Avoid phrases like:

- in today's fast-paced world
- it's important to note
- in conclusion
- moving forward

These are defaults for public-facing writing, not universal bans for technical or precision-heavy work.
