# Stage 1 Smoke Guide

Этот документ описывает канонический способ запуска smoke-сценария для `Stage 1` в `job-search-system`.

## Цель

Smoke-runner нужен для повторяемой ручной проверки короткого end-to-end сценария:

1. intake кандидата
2. обогащение профиля из нескольких источников
3. `career-pathing-lite`
4. `job-search-playbook`
5. resume artifacts
6. import / rank / shortlist вакансий
7. application draft / payload
8. touchpoints / reminders
9. pipeline report
10. `processed` плюс `material_change_detected`

## Разделение ответственности

- Канонический код раннера живёт в репозитории `job-search-system`.
- Конкретный smoke-kit с локальными данными, персональными профилями, `workspace.local.toml`, machine-state и `run-log.md` живёт вне продуктового кода, например в `scratch/`.
- Человеческие заметки и findings хранятся в `run-log.md`.
- Машинное состояние хранится в `KIT_ROOT/.state/checkpoints.json`.

## Artifact paths

Runtime artifacts сохраняются внутри `data/artifacts/`.

Первый уровень под `artifacts/` является namespace, а не id кандидата. Сейчас используется `candidates/`, потому что Stage 1 генерирует в основном кандидатские источники и черновики. Такой уровень оставлен намеренно, чтобы позже без миграции добавить соседние namespace:

- `shared/` для общих шаблонов, reusable messages и справочных артефактов
- `system/` для системных reports, schema snapshots и diagnostic artifacts
- `imports/` или `batches/` для raw import snapshots
- `exports/` для review packages или UI/API export bundles

Папки кандидатов именуются человекочитаемо, но стабильно: латинский slug ФИО плюс короткий id, например `example-candidate--94f574e8`. Чистое ФИО не используется, чтобы избежать коллизий и поломок при переименовании кандидата.

## Рекомендуемая структура

```text
tools/job-search-system/
  scripts/smoke/stage1-smoke-run.sh
  docs/smoke/stage1-smoke-guide.md
  docs/smoke/stage1-smoke-state-contract.md

scratch/job-search-smoke-kit/stage1/
  smoke-run.sh                  -> symlink на канонический скрипт
  README.md                     локальные инструкции и пути
  run-log.md                    человеческий журнал прогона
  workspace.local.toml          локальный workspace context
  .state/
    checkpoints.json            machine-state
    outputs/                    промежуточные JSON outputs
  candidate-a/
  candidate-b/
  vacancies/
  scenarios/
```

## Переменные окружения

Раннер поддерживает переопределения:

- `KIT_ROOT`: путь к конкретному smoke-kit
- `JSS_ROOT`: путь к `tools/job-search-system`
- `CONFIG_PATH`: путь к `runtime.local.toml`
- `WORKSPACE_PATH`: путь к локальному `workspace.local.toml`
- `CHECKLIST_PATH`: необязательный внешний путь к каноническому checklist, если он ведётся вне репозитория

`CHECKLIST_PATH` не нужен для выполнения шагов. Он нужен только для удобства: раннер покажет путь к checklist в статусе и в финальной подсказке оператору.

Если checklist лежит в Obsidian vault или другом внешнем месте, перед первым запуском можно выполнить:

```bash
export CHECKLIST_PATH="$HOME/Obsidian/Job/job-search-skills/09-job-search-stage1-smoke-test-checklist.md"
```

Если переменную не экспортировать, smoke-сценарий всё равно выполняется полностью.

## Подготовка candidate intake

Личные данные, резюме, выгрузки LinkedIn и локальные результаты smoke-прогона должны оставаться в `scratch/`, а не в репозитории. Раннер ожидает не конкретные персональные данные, а набор источников с разными ролями.

Поддерживаемые форматы источников: `.txt`, `.md`, `.pdf`, `.docx`. Для PDF нужен доступный в системе `pdftotext`; иначе PDF-источник нужно предварительно сохранить как текст или markdown.

### `candidate-a/resume-v1.*`

Базовый источник для первого intake кандидата A.

В нём желательно иметь:

- ФИО
- текущую или последнюю роль
- локацию
- контакты
- краткое позиционирование
- 2-4 ключевых места работы
- основные навыки
- языки
- образование или сертификации, если они важны

Ключевое отличие: это single-source baseline. По нему проверяется, что система может создать черновик профиля из одного резюме без обогащения.

### `candidate-a/resume-v2.*`

Второй источник того же кандидата A.

Он должен частично пересекаться с `resume-v1`, но полезно добавить:

- другое название целевой или текущей роли
- более свежую или более подробную версию опыта
- дополнительные достижения
- новые навыки, сертификаты, публикации, awards или recommendations
- потенциальные конфликты по локации, роли, датам или summary

Ключевое отличие: это источник для multi-source enrichment и проверки conflict resolution. Он не должен быть просто копией `resume-v1`.

### `candidate-a/linkedin-profile.*`

Экспорт или сохранённый текст LinkedIn-профиля кандидата A.

В нём желательно иметь:

- публичный profile URL
- headline
- about / summary
- experience
- skills
- education
- licenses / certifications
- recommendations, если есть
- public links на портфолио, GitHub, сайт или другие профили

Ключевое отличие: LinkedIn обычно более шумный и менее похож на резюме. Он проверяет, что intake может обогатить профиль, но не должен бездумно принимать мусорные ссылки, company URLs или platform noise как внешние профили кандидата.

### `candidate-a/search-context.md`

Это не резюме, а явное описание поискового контекста.

В нём должны быть:

- целевые роли
- realistic / stretch направления
- страны и форматы работы
- remote / hybrid / onsite constraints
- relocation / travel constraints
- предпочтительные и нежелательные компании
- compensation floors / targets / aspirations по валютам
- dealbreakers
- preferred stack, domain и company fit criteria

Ключевое отличие: этот файл управляет ranking/playbook, а не canonical profile facts. Например, зарплатные блоки в EUR, USD и RUB должны применяться к вакансиям в соответствующей валюте.

### `candidate-b/resume-v1.*` и `candidate-b/linkedin-profile.*`

Источники второго кандидата нужны не для полноты Candidate A, а для проверки isolation.

В них должны быть:

- другое ФИО
- другая роль или специализация
- другой набор навыков
- другой search context или career positioning, если применимо

Ключевое отличие: данные Candidate B не должны попадать в профиль, вакансии, artifacts, ranking или reports Candidate A.

### `vacancies/candidate-a-batch-01.json`

Первый batch вакансий для Candidate A.

Желательно включить 5-10 вакансий:

- 1-2 сильных совпадения
- 1 duplicate или near-duplicate
- 1 vacancy с avoid-company
- 1 vacancy ниже salary floor
- 1 vacancy с неподходящим work model
- 1 stretch role
- 1 incomplete vacancy с неполными salary/work-model данными

Ключевое отличие: этот batch проверяет normalize / dedupe / rank / shortlist на нормальном наборе, а не на одном идеальном примере.

### `vacancies/candidate-a-batch-02-material-change.json`

Второй batch для проверки material change.

Он должен содержать вакансию, которая матчится с уже импортированной canonical vacancy из `candidate-a-batch-01.json`, но имеет существенное изменение:

- новая salary band
- изменённый seniority
- изменённый remote/hybrid/onsite режим
- новый обязательный стек
- другой scope роли

Ключевое отличие: такая вакансия не должна возвращаться как новая. Она должна попасть в review path как materially changed processed vacancy.

### `vacancies/candidate-b-batch-01.json`

Batch вакансий для Candidate B.

Он должен соответствовать профилю Candidate B, а не Candidate A. Полезно включить роли, которые были бы плохим fit для Candidate A или наоборот.

Ключевое отличие: этот batch проверяет, что vacancy pipeline и reports изолированы по кандидату.

### `scenarios/touchpoints.md`

Это human-readable сценарий проверки касания, а не источник runtime IDs.

Раннер создаёт touchpoint после подготовки application payload, когда уже известны:

- candidate id
- canonical vacancy id
- application id
- message artifact id

В файле нужно описывать ожидаемый смысл касания: канал, кому отправляется сообщение, какой follow-up нужен, что считать успешной проверкой. Конкретные id подставляет код раннера.

## Базовый запуск

Если скрипт вызывается через symlink внутри `KIT_ROOT`, достаточно:

```bash
cd /path/to/kit
./smoke-run.sh
```

Полезные режимы:

```bash
./smoke-run.sh status
./smoke-run.sh --from 8
./smoke-run.sh --redo-from 8
./smoke-run.sh reset
```

## Правила resume/skip

- Уже завершённые шаги пропускаются по machine-state, а не по `run-log.md`.
- Источник истины для skip logic: `KIT_ROOT/.state/checkpoints.json`.
- Если меняется `plan_version`, старые checkpoints автоматически считаются недействительными для нового плана.
- `run-log.md` не должен участвовать в машинной логике.

## Findings And Backlog

Сам раннер не владеет каноническим описанием всех smoke-assertions.

- Раннер отвечает на вопрос: что запускать и как безопасно возобновлять прогон.
- Checklist отвечает на вопрос: что именно считать успешным результатом.
- `run-log.md` отвечает за локальные наблюдения конкретного smoke-kit.
- `../stage3-backlog.md` отвечает за незакрытые продуктовые хвосты после Stage 1/2.

Если checklist хранится вне репозитория, `CHECKLIST_PATH` можно экспортировать перед запуском. Это не обязательная зависимость smoke-runner.
