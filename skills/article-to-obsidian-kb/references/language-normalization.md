# Language Normalization

## Goal

- Notes must read like natural Russian technical prose, not like Russian sentences with random English nouns inserted into them.
- English is allowed only where it improves precision, searchability, or matches an industry-standard name.
- Decide whether to keep an English term by semantic class, not by visual appearance alone.

## English Term Classes

- Keep in English by default:
  - canonical engineering process names
  - canonical method or framework names
  - stable role labels introduced by the source
  - company, product, and organization names
  - canonical note titles inside `wikilinks`
- Translate to Russian by default:
  - generic management or product vocabulary
  - non-canonical organizational shorthand
  - evaluative English prose labels that do not function as stable names
  - descriptive operational phrases that merely label a mechanism, budget, pricing rule, control layer, workflow mode, governance rule, or evaluation pattern
  - source-specific jargon when a direct Russian formulation is clearer
- Do not treat all English prose as one cleanup class.
- A term should not stay in English only because it appeared that way in the source.
- A term should not be translated only because it contains Latin characters.

## Keep In English

- Official framework names and metric families:
  - `DORA`
  - `DXI`
  - `SPACE`
  - `DX Core 4`
- Established product, discovery, and mapping method names whose English form is the standard label:
  - `Impact Mapping`
  - `Customer Journey Map`
  - `Value Stream Mapping`
- Tool names, product names, and vendor names.
- Stable role labels introduced by the source when the English label is itself the recognizable role name:
  - `Product Manager`
  - `product engineer`
  - `AI Engineer`
- Code and platform terms whose English form is the stable norm:
  - `CI/CD`
  - `SDLC`
  - `monorepo`
  - `build`
  - `deploy`
  - `code review`
  - `pull request`
  - `feature flag`
- Keep other English engineering nouns only when they are stable recognizable terms in practice, not because the source happened to use them that way.
- Do not invent ad hoc English exceptions sentence by sentence when the term does not clearly belong to one of the allowed semantic classes.
- Note titles should keep these canonical English labels instead of translating them into Russian when the English term is how practitioners normally search for and recognize the method.

## Translate To Russian By Default

- Translate ordinary business, management, and product vocabulary when a clear Russian equivalent exists.
- Translate non-canonical organizational shorthand and evaluative prose labels even when they are common in English-language sources.
- Translate descriptive operational noun phrases when they only describe how something works rather than naming a stable canonical thing.
- If the phrase answers “what kind of mechanism, budget rule, control layer, or workflow mode is this?” rather than “what is this canonical thing called?”, translate it.
- Keep concrete example mappings and disputed-term translations in `config/language_terms.yaml` instead of duplicating literal `english -> russian` pairs across prose docs.
- Use that registry when you need a recommended Russian rendering for a recurring term family.
- If a multi-word English phrase is not canonical, rewrite the whole phrase in Russian rather than cleaning it token by token.
- If a leftover token looks like an internal shorthand, mixed-case label, or nonstandard acronym, review whether it is a true canonical name; do not treat every such token as ordinary prose by default.

## First Mention Rule

- If the English term matters for recognition or later search, use:
  - Russian term first, then English in parentheses.
- Exception:
  - keep the English form first for canonical method names that are commonly used as fixed labels in practice.
- Use `config/language_terms.yaml` when you need a recurring example rendering rather than reintroducing literal `english -> russian` pairs into this document.
- After the first mention, prefer the Russian form unless the English name is the actual canonical label.
- Guidance-only examples:
  - good: `продуктовый подход (product mindset)`
  - good: `согласованность руководства (leadership alignment)`
  - good: `обратные связи (feedback loops)`
  - keep the English form as the canonical title for method names such as `Impact Mapping`

## Sentence-Level Rules

- Do not leave several unrelated English common nouns in one sentence just because each of them looked individually tolerable.
- A sentence may keep multiple English terms only when they belong to allowed semantic classes such as canonical process names, canonical method names, stable role labels, named entities, or canonical `wikilink` titles.
- If a sentence mixes allowed canonical English with generic business or organizational shorthand, translate the generic layer instead of treating the whole phrase as one exception.
- Do not preserve descriptive operational phrases in English just because they were visually short, wrapped in backticks, or copied verbatim from the source.
- Prefer Russian sentence flow over literal source phrasing.
- Guidance-only examples:
  - bad: `customer pain, backlog и outcome`
  - bad: `interruptions и meetings`
  - bad: `product portfolio и structured trade-off`
  - good: `боль пользователя или внутреннего клиента` instead of `customer pain`
  - good: `результат` or `ожидаемый эффект` instead of `outcome`
  - good: `рабочий процесс` instead of `workflow`
  - good: `согласованность руководства` instead of `leadership alignment`
  - good: `продуктовый подход` instead of `product thinking`
  - good: `численность штата` or `штат` instead of `headcount`
  - good: `пропускная способность` or `скорость потока` instead of `throughput`
  - good: `эффективность` instead of `productivity`
  - better: keep the canonical English term only where it is the stable label, and translate the surrounding business prose

## Lessons Notes

- Write lesson headings in Russian by default.
- For lesson-note titles, prefer the Russian topical name and drop the literal label `Lessons` unless it is part of a canonical source title.
- Keep English in the heading only for a canonical framework name or a term that loses precision in translation.
- Guidance-only examples:
  - weaker: `Продуктивность разработчиков - это socio-technical system`
  - stronger: `Продуктивность разработчиков - это социотехническая система`
  - weaker: `Метрики полезны как инструмент координации, а не как scorecard`
  - stronger: `Метрики полезны как инструмент координации, а не как оценочная шкала`

## Operating-Model Notes

- Translate organization and management vocabulary more aggressively than framework vocabulary.
- A dense operating-model note should mostly read in Russian even if it references English metric names or platform terms.
- Do not use the presence of one canonical metric or platform term as justification for leaving the surrounding organizational prose in English.
- Inline code may preserve canonical commands, identifiers, true product names, or stable method names, but it must not be used to preserve non-canonical English prose labels.
- Guidance-only examples:
  - good: `Команда сочетает DORA и DXI с опросами и прямой обратной связью от инженеров.`
  - bad: `Команда сочетает system metrics, surveys и direct feedback от engineers.`

## Final Check

- Before saving, scan each note and fix:
  - non-canonical English prose labels that should be translated even if they are common in source materials
  - multi-word English phrases that should be translated as one unit rather than as a pile of separate English tokens
  - descriptive operational phrases that are not true names but only labels for a mechanism, budget, pricing rule, control layer, workflow mode, or evaluation pattern
  - mixed-case shorthand labels that may need either explicit preservation as canonical names or a clearer Russian rewrite
  - English words that appear only once and are not canonical terms
  - bullets whose leading clause is mostly English
  - sentences where Russian grammar is carrying several unrelated English nouns
  - translated text that became less clear than the original; in that case keep the English term in parentheses after the Russian one
