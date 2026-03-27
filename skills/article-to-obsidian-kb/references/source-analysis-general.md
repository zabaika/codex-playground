# Source Analysis: General Path

Use this reference only as an internal extraction pass for broad expert content, business or management material, career or productivity advice, and other articles, transcripts, or long-form sources that do not naturally fit an engineering operating-model note. Do not output this structure directly into the final Obsidian note.

## Role

You are a senior business analyst, technical expert, and editor with critical thinking. Deeply analyze the source text or transcript, extract the highest-signal material, and filter out filler, jokes, ads, and lyrical digressions.

## Internal Extraction Structure

### Суть

- In 1-2 sentences, explain what the source is specifically about and what main problem the author or speaker is trying to solve.

### Ключевые инсайты и тезисы

- Identify 5-7 main ideas.
- For each idea:
  - give it a short name
  - explain the core meaning
  - explain why it matters
  - capture the arguments, observations, metrics, studies, or cause-and-effect logic used by the speaker

### Практика

- Extract concrete recommendations as action-oriented steps.
- Prefer verb-led instructions such as:
  - `Внедрите`
  - `Проверьте`
  - `Упростите`
  - `Откажитесь`
  - `Сравните`
  - `Измерьте`
- Attach the most useful examples, real situations, cases, analogies, or metaphors directly to the recommendation they support.
- For each recommendation, note only the supporting example that adds the most concrete value:
  - what the situation was
  - which recommendation it illustrates
  - what practical conclusion follows from it

### Инструменты и фреймворки

- Extract mentioned services, books, technologies, methodologies, models, schemas, or frameworks.
- Keep this block only when at least two named items are independently useful and would add clear new information beyond `Практика`.
- For each one, note:
  - what it is
  - why it is useful
  - in which context the speaker recommends it
- Skip this block if nothing specific appears.

### Подводные камни и антипаттерны

- Capture what the speaker recommends avoiding, which mistakes are criticized, and which false approaches are rejected.
- Keep this block only when the source contains several distinct mistakes with their own consequences, not just inverted restatements of the recommendations.

### Что можно применить сразу

- Extract 3-7 ideas that can be applied immediately without extra preparation.
- Keep this block distinct from `Практика`.
- Use it as a short prioritized starter subset, not as a verbatim repeat of the full recommendations list.
- Omit it if it would only rephrase or reorder the same action steps.

## Quality Rules

- Write the extraction concisely and in professional Russian prose.
- Do not retell the source from beginning to end.
- Keep concrete numbers, company names, roles, named methods, steps, and constraints.
- Keep the extraction blocks semantically distinct:
  - ideas belong in `Ключевые инсайты и тезисы`
  - actions belong in `Практика`
  - source-specific illustrations sit under the recommendation they support instead of living in a separate block
  - named tools and methods belong in `Инструменты и фреймворки` only when they add new knowledge beyond the practice block
  - fast first steps belong in `Что можно применить сразу`
  - anti-patterns belong in `Подводные камни и антипаттерны` only when they are not just the negative form of the recommendations
- Make each next block additive: do not duplicate, invert, or paraphrase the previous blocks.
- If a statement is speculative, incomplete, or clearly a personal opinion, mark it as:
  - `(Примечание: это утверждение спикера, а не подтвержденный факт)`
- If the source is mostly reflection and light on practice, state that honestly.
- Do not invent details that are not present in the source.

## Mapping Rule

- After extraction, switch back to the main skill rules.
- Use the extracted signal to decide whether the vault needs:
  - a `lessons` note
  - a `general` note
  - one or more `concept` notes
  - updates to existing notes
- Do not force an `operating-model` note unless the source truly explains how a company or system works.
