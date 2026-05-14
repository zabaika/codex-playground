---
name: prompt-debugger
description: Diagnose why a prompt produced the wrong output, identify the primary failure mode, and rewrite only the minimum part needed to get to a usable next iteration instead of starting from scratch.
---

# Prompt Debugger

## Overview

Use this skill when a prompt already exists, the output was wrong or weak, and the goal is to fix the prompt surgically instead of replacing it wholesale.

Treat the prompt as a debuggable artifact. Diagnose the primary failure first, change the smallest useful thing, and prefer one iteration at a time over full rewrites.

## When To Use

Use this skill when:

- the user already has a prompt and a disappointing output
- the output is too generic, too long, missing something, includes something unwanted, sounds wrong, or is structured badly
- a prompt that used to work has degraded after a model change and the user wants the smallest fix first

## When Not To Use

Do not use this skill when:

- the user has no original prompt to debug
- the task is to create a prompt from scratch rather than diagnose an existing one
- the real problem is factual verification, tool choice, or missing source material rather than prompt quality
- the user wants a broad prompting handbook instead of one concrete debugging pass

## Required Inputs

- original prompt
- received output
- what is wrong with the output
- what the user needed instead

## Optional Inputs

- target audience
- desired tone or voice
- target length
- required structure
- include list
- avoid list

If optional inputs are missing, infer only the minimum needed to explain the failure. Do not invent a full new brief unless the user asks for one.

## Failure Modes

### 1. Too generic

Typical cause:
- missing audience, tone, or point of view

Typical fix:
- add audience specificity
- add 2-3 tone descriptors
- add the intended point or angle

### 2. Too long

Typical cause:
- no explicit length or scope limit

Typical fix:
- add a word, sentence, or section limit
- narrow the requested outcome

### 3. Missing something specific

Typical cause:
- required content was never explicitly requested

Typical fix:
- add an `Include:` list

### 4. Includes something unwanted

Typical cause:
- no explicit exclusion boundary

Typical fix:
- add an `Avoid:` list

### 5. Sounds like AI or has the wrong voice

Typical cause:
- no concrete voice guidance

Typical fix:
- describe the desired voice in behavioral terms
- name what the voice should not do

### 6. Wrong structure

Typical cause:
- no explicit format contract

Typical fix:
- specify start, middle, end, and forbidden structures

## Workflow

1. Read the prompt, output, and user complaint.
2. Identify the primary failure mode.
3. Name any secondary failure modes, but do not fix them first.
4. Explain the most likely cause in plain language.
5. Rewrite only the smallest prompt fragment needed to fix the primary failure.
6. Return one improved prompt version.
7. If a secondary issue remains likely, mention the next best follow-up change.

## Response Contract

Return exactly these sections:

### Diagnosis

- primary failure mode
- secondary failure modes, if any
- likely cause

### Smallest Useful Fix

- the exact change to make

### Revised Prompt

- one improved prompt version

### Next Check

- one short note describing what to verify after the next run

## Operating Rules

- prefer minimal edits over full rewrites
- fix the primary failure before secondary ones
- do not turn one debugging pass into a giant prompt framework
- stop at usable, not perfect
- if the user's complaint is vague, ask only the minimum clarifying question needed to identify the primary failure
- if the same failure recurs across prompts, tell the user which missing variable should become part of their default prompting pattern

## Success Signal

- the revised prompt should be usable after one light human edit, not a full rewrite
- prefer one or two focused iterations over a long debugging loop

## Reference

If a concrete worked example would help, use [references/prompt-debugger-worked-example.md](references/prompt-debugger-worked-example.md) as the neutral reference pattern rather than inventing a broader handbook.
