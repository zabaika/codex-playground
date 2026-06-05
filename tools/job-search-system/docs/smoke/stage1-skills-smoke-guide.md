# Stage 1+2 Skills Smoke Guide

Этот smoke проверяет не core CLI напрямую, а thin Codex skills поверх уже работающего `job-search-system`.

Stage 1 и Stage 2 non-UI считаются закрытыми. Новые хвосты и улучшения после smoke фиксируются в `../stage3-backlog.md`, а не размазываются по smoke-документам.

## Цель

Проверить, что пользовательский workflow можно пройти через skills:

1. `$jss-candidate-intake`
2. `$jss-resume-positioning`
3. `$jss-vacancy-pipeline`
4. `$jss-job-board-operations` для Stage 2 manual-sync проверки

Критерий успеха: skills не обходят service layer, не пишут в БД напрямую и ведут оператора к тем же command/query handlers, что и CLI smoke.

## Перед стартом

Проверить:

- `tools/job-search-system/config/runtime.local.toml` существует
- smoke kit содержит `workspace.local.toml`
- candidate sources и vacancy batches подготовлены
- skills установлены в `${CODEX_HOME:-$HOME/.codex}/skills`
- Codex session перезапущена, если новая установка skills не видна текущей сессии

## Skill 1: `$jss-candidate-intake`

Проверить сценарий:

- создать или выбрать кандидата
- ingest resume source
- ingest LinkedIn/profile source
- build AI extraction request
- импортировать AI draft или deterministic draft
- показать conflicts и missing fields
- подтвердить draft только после явного review
- проверить `show-profile`

Обязательные инварианты:

- AI output импортируется только как draft
- canonical profile не меняется до `confirm-draft`
- source artifacts сохраняются через artifact registry
- conflicts не скрываются

## Skill 2: `$jss-resume-positioning`

Проверить сценарий:

- прочитать active candidate/profile
- сгенерировать positioning brief
- сгенерировать role-based resume artifact
- запустить resume quality gate
- не выдавать quality gate за external action approval

Обязательные инварианты:

- generated resume имеет artifact id
- quality gate result привязан к artifact
- skill не выдумывает недостающие достижения или метрики

## Skill 3: `$jss-vacancy-pipeline`

Проверить сценарий:

- импортировать vacancy JSON batch через `import-json`
- выполнить `rank`
- выбрать shortlist вручную
- создать application draft
- подготовить review-first application payload
- создать touchpoint/reminder при необходимости
- проверить `daily-actions`
- проверить `pipeline-report`
- отметить processed и проверить material-change сценарий
- проверить Stage 2 LinkedIn manual page / email alert / search-results markdown intake через `import-linkedin-text`

Обязательные инварианты:

- ranking не мутирует lifecycle
- shortlist/processed/application/touchpoint идут только через commands
- payload не считается отправленным наружу
- LinkedIn-specific intake в Stage 2 принимает только copied/manual page text, alert-email style text, search-results markdown cards с job URLs или manually prepared CSV-like rows; native LinkedIn CSV export не предполагается, browser automation и URL-only import запрещены

## Skill 4: `$jss-job-board-operations`

Проверить сценарий:

- получить `board-checklist` для выбранной платформы
- проверить saved-search settings projection
- вручную выполнить действие на площадке, если это реальный пользовательский smoke
- записать manual action через `record-board-action`
- повторить запись с тем же idempotency key и проверить `reused = true`
- проверить `list-board-actions`

Обязательные инварианты:

- skill не использует browser automation
- checklist не создаёт operational records
- manual external action не считается выполненной системой
- submit / send / profile update action требует artifact id
- artifact-bearing action создаёт usage event через command handler

## Что фиксировать

В `run-log.md` фиксировать только локальные наблюдения текущего прогона:

- регрессию, из-за которой smoke нельзя считать пройденным
- локальные особенности входных данных
- новые открытые проблемы, которые ещё не перенесены в `../stage3-backlog.md`

Закрытые проблемы не нужно держать в `Must-fix`. Долгосрочные улучшения должны жить в `../stage3-backlog.md`.
