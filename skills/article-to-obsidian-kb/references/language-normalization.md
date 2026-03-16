# Language Normalization

## Goal

- Notes must read like natural Russian technical prose, not like Russian sentences with random English nouns inserted into them.
- English is allowed only where it improves precision, searchability, or matches an industry-standard name.

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
- Code and platform terms whose English form is the stable norm:
  - `CI/CD`
  - `SDLC`
  - `monorepo`
  - `build`
  - `deploy`
  - `code review`
  - `pull request`
  - `feature flag`
- Very common engineering nouns when the Russian equivalent would sound unnatural inside the sentence.
- Note titles should keep these canonical English labels instead of translating them into Russian when the English term is how practitioners normally search for and recognize the method.

## Translate To Russian By Default

- Translate ordinary business, management, and product vocabulary when a clear Russian equivalent exists.
- Especially translate words and phrases like:
  - `customer pain` -> `боль пользователя` or `боль внутреннего клиента`
  - `outcome` -> `результат` or `ожидаемый эффект`
  - `interruptions` -> `прерывания`
  - `meetings` -> `встречи`
  - `workflow` -> `рабочий процесс`
  - `rollout` -> `внедрение`
  - `leadership alignment` -> `согласованность руководства`
  - `product thinking` -> `продуктовый подход`
  - `people problems` -> `проблемы в организации работы людей`
  - `technical problems` -> `технические проблемы`
  - `fit` -> `соответствие задаче` or `подходящий сценарий применения`
  - `vendor` -> `внешний поставщик` or `вендор` only if the domain already uses that word naturally
  - `intake` -> `сбор входящих сигналов`
  - `guardrails` -> `ограничители качества` or `страхующие практики`
  - `throughput` -> `пропускная способность` or `скорость потока`
  - `productivity` -> `эффективность`

## First Mention Rule

- If the English term matters for recognition or later search, use:
  - Russian term first, then English in parentheses.
- Exception:
  - keep the English form first for canonical method names that are commonly used as fixed labels in practice.
- Example:
  - `продуктовый подход (product mindset)`
  - `согласованность руководства (leadership alignment)`
  - `обратные связи (feedback loops)`
- Keep as canonical title:
  - `Impact Mapping`
  - `Customer Journey Map`
  - `Value Stream Mapping`
- After the first mention, prefer the Russian form unless the English name is the actual canonical label.

## Sentence-Level Rules

- Do not leave more than 1-2 untranslated English common nouns in one sentence unless they are framework or tool names.
- Rewrite mixed phrases such as:
  - `customer pain, backlog и outcome`
  - `interruptions и meetings`
  - `product portfolio и structured trade-off`
- Prefer Russian sentence flow over literal source phrasing.

## Lessons Notes

- Write lesson headings in Russian by default.
- Keep English in the heading only for a canonical framework name or a term that loses precision in translation.
- Good:
  - `Продуктивность разработчиков - это socio-technical system`
  - `Метрики полезны как инструмент координации, а не как scorecard`
- Better:
  - `Продуктивность разработчиков - это социотехническая система`
  - `Метрики полезны как инструмент координации, а не как оценочная шкала`

## Operating-Model Notes

- Translate organization and management vocabulary more aggressively than framework vocabulary.
- A dense operating-model note should mostly read in Russian even if it references English metric names or platform terms.
- Good:
  - `Команда сочетает DORA и DXI с опросами и прямой обратной связью от инженеров.`
- Bad:
  - `Команда сочетает system metrics, surveys и direct feedback от engineers.`

## Final Check

- Before saving, scan each note and fix:
  - English words that appear only once and are not canonical terms
  - bullets whose leading clause is mostly English
  - sentences where Russian grammar is carrying several unrelated English nouns
  - translated text that became less clear than the original; in that case keep the English term in parentheses after the Russian one
