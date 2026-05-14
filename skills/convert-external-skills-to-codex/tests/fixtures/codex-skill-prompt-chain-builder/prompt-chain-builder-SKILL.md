---
name: prompt-chain-builder
description: Break one complex AI task into a sequence of linked prompts, design each chain stage, and write clear handoff rules so the output of one stage becomes the usable input of the next.
---

# Prompt Chain Builder

## Overview

Use this skill when one prompt is doing too much and the task needs multiple distinct thinking stages.

This skill does not execute the chain for the user. It designs the chain: stages, prompts, handoffs, checks, and final output shape.

## When To Use

Use this skill when:

- one prompt is trying to do research, analysis, drafting, and refinement all at once
- the task requires distinct thinking modes such as research, synthesis, writing, evaluation, or repurposing
- the user wants a repeatable chain they can run again for similar work

## When Not To Use

Do not use this skill when:

- one prompt is already enough for the task
- the user wants the final artifact directly rather than a reusable chain
- the task does not have meaningful stage boundaries
- the user needs live research results now rather than a chain design

## Required Inputs

- the complex task
- the intended final output

## Optional Inputs

- known thinking stages
- audience
- voice or brand constraints
- source or evidence expectations
- time or depth limits

If the user has not mapped the stages yet, infer them from the task. Ask only if the final output or intended workflow is still unclear.

## Design Rules

- split by thinking mode, not by arbitrary action count
- each chain link must produce a concrete output that the next link can consume
- each handoff must say what to pass forward and what to check before continuing
- do not ask one link to both generate and judge the same thing unless there is a strong reason
- keep the chain as short as possible while preserving quality

## Workflow

1. Identify the minimum distinct thinking stages.
2. Name each stage by purpose, not tool.
3. Write one prompt for each stage.
4. Define the expected output of each stage.
5. Write handoff instructions between stages.
6. Add one check for poor output or ambiguity at each critical handoff.
7. Return the full chain in runnable order.

## Response Contract

Return exactly these sections:

### Chain Summary

- task
- final output
- stage count
- why one prompt is not enough

### Chain Stages

For each stage, include:

- stage name
- stage purpose
- prompt
- expected output

### Handoff Rules

For each handoff, include:

- what to pass to the next stage
- what to verify before moving on
- what to do if the output is weak

### Execution Notes

- where the user must make a decision
- where quality review matters most
- one short estimate of how many iterations or how much orchestration effort this chain is likely to need

## Operating Rules

- prefer the narrowest useful stage boundaries
- if the user already knows the chain shape, refine it instead of replacing it
- keep each prompt concrete and stage-specific
- preserve explicit evidence or sourcing requirements when they matter to the task
- if a later stage depends on voice or format, name that dependency explicitly in the handoff

## Success Signal

- each stage has one clear job
- each handoff is concrete enough to run without guesswork
- the resulting chain is reusable on the next similar task
- the user should need fewer retries than with one overloaded prompt

## Reference

If a representative chain example would help, use [references/prompt-chain-builder-worked-example.md](references/prompt-chain-builder-worked-example.md) as the canonical example instead of inventing a broader handbook.
