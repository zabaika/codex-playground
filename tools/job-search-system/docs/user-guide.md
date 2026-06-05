# Job Search System: пользовательская инструкция

Документ описывает, как пользоваться локальной системой поиска работы без прямого доступа к SQLite и без ручного редактирования `data/`.

Основной пользовательский интерфейс сейчас: Codex skills `jss-*`.

CLI и shell scripts нужны для повторяемых операций, smoke/debug и batch-действий.

## Быстрый Старт

Минимальный первый цикл:

1. Подготовьте локальный workspace и входные файлы кандидата.
2. Проверьте систему через `doctor.sh`.
3. Используйте `$jss-candidate-intake`, чтобы создать кандидата, загрузить источники, получить draft review и подтвердить canonical profile.
4. Используйте `$jss-career-pathing`, чтобы выбрать primary target role.
5. Используйте `$jss-resume-positioning`, чтобы создать positioning brief, draft resume, quality gate и final resume после acceptance.
6. Используйте `$jss-job-search-playbook`, чтобы получить search strategy и saved-search pack.
7. Используйте `$jss-vacancy-pipeline`, чтобы импортировать 5-10 вакансий, отранжировать их, выбрать shortlist и подготовить review-first application payload.
8. Используйте `$jss-vacancy-pipeline` для daily actions и pipeline report.

Основной UX принцип: сначала просите Codex использовать нужный `$jss-*` skill. CLI-команды и shell scripts используйте только для batch/debug, smoke или когда skill просит выполнить конкретный fallback.

## Что Подготовить

Для нового кандидата:

- `resume.pdf` или другой файл резюме.
- `linkedin-export.pdf` или copied profile/page text, если есть.
- `profile-notes.md`: дополнительные факты, ограничения, предпочтения, ссылки на профили.
- `search-context.md`: целевые роли, география, формат работы, compensation floors/targets/aspirations.

Для регулярной работы с вакансиями:

- JSON batch, если вакансии уже структурированы.
- Copied LinkedIn job page / job alert / search-results markdown.
- Copied hh.ru vacancy page / search-results markdown.
- Generic copied vacancy text для остальных площадок.
- URL-only ссылки как seeds, если текста вакансии пока нет.

## Куда Идти

| Что нужно сделать | Основной путь | Output |
| --- | --- | --- |
| Создать профиль кандидата | `$jss-candidate-intake` | candidate, sources, profile draft, confirmed profile |
| Выбрать карьерное направление | `$jss-career-pathing` | `career-pathing-lite/full` artifact |
| Сделать стратегию поиска | `$jss-job-search-playbook` | search playbook artifact |
| Сгенерировать резюме | `$jss-resume-positioning` | draft/final resume artifacts, quality gate |
| Импортировать вакансии | `$jss-vacancy-pipeline` | canonical vacancies, source occurrences |
| Отранжировать вакансии | `$jss-vacancy-pipeline` | ranked JSON output, optional advisory AI review |
| Подготовить отклик | `$jss-vacancy-pipeline` | application record, resume/message artifacts |
| Работать с площадками вручную | `$jss-job-board-operations` | board actions, reconciliation items |
| Проверить ежедневные задачи | `$jss-vacancy-pipeline` | daily actions, pipeline report |

## Что Умеют Skills

### `$jss-candidate-intake`

Полезен для первичного профиля и обогащения кандидата.

- Создаёт или выбирает candidate context.
- Загружает resume, LinkedIn export/text, profile notes, search context, URL или existing artifact.
- Собирает deterministic или AI-assisted profile draft.
- Показывает conflicts, missing fields, assumptions и evidence.
- Подтверждает canonical profile только после явного approval.

### `$jss-career-pathing`

Полезен, когда нужно выбрать или пересмотреть карьерное направление.

- `lite`: быстро сравнивает 2-5 ролей и предлагает primary target role.
- `full`: строит стратегический report по нескольким траекториям, gaps, T-shape и brand plan.
- Может использовать local vacancy DB и optional KB context.
- Не меняет canonical candidate state автоматически.

### `$jss-job-search-playbook`

Полезен для настройки стратегии поиска до массовой обработки вакансий.

- Создаёт search strategy.
- Готовит saved-search design pack.
- Создаёт reusable outreach/message draft.
- Формирует compensation framework.
- Даёт lightweight interview-prep artifacts.

### `$jss-resume-positioning`

Полезен для резюме, positioning и контроля качества внешних текстов.

- Создаёт positioning brief.
- Генерирует role-based draft resume.
- Запускает resume quality gate.
- Создаёт или обновляет resume roast report.
- Финализирует accepted resume в `final/`.
- Даёт skill-side AI critique/rewrite guidance как review notes.

### `$jss-vacancy-pipeline`

Полезен для регулярной обработки вакансий и подготовки откликов.

- Импортирует JSON batches, generic vacancy text, LinkedIn text и hh.ru text.
- Обрабатывает URL-only seeds через supervised preview/confirm flow.
- Делает normalize, dedupe, scoring/ranking и advisory AI review.
- Переводит выбранные вакансии в shortlist/processed.
- Готовит application draft, application payload и vacancy-specific resume.
- Ведёт touchpoints, reminders, interviews, daily actions и pipeline report.

### `$jss-job-board-operations`

Полезен, когда работа уже переходит во внешние площадки, но остаётся ручной.

- Готовит manual checklist и saved-search settings для площадок.
- Логирует manual board actions.
- Связывает external actions с artifacts и approvals.
- Показывает reconciliation items между board-side действиями и internal state.
- Не делает browser automation, submit, send или publish.

## Что Не Делать Вручную

- Не редактируйте SQLite напрямую.
- Не правьте persisted artifacts вручную и не считайте их зарегистрированными после ручной правки.
- Не меняйте файлы в `tools/job-search-system/data/` руками, кроме удаления локальных generated artifacts после dry-run cleanup.
- Не отправляйте, не публикуйте и не обновляйте внешние системы без explicit external action approval.
- Не переносите AI suggestions в canonical profile или resume facts без подтверждённого evidence/intake flow.
- Не используйте UI, skills или Telegram как второй источник истины: все mutations идут через command handlers.

## Базовая настройка

Работайте из корня репозитория:

```bash
cd <playground-root>
export WORKSPACE_PATH="<scratch-or-user-workspace>/workspace.local.toml"
```

`WORKSPACE_PATH` должен указывать на локальный workspace-файл. Его нельзя коммитить, потому что он хранит текущий active candidate context.

Runtime config по умолчанию:

```text
tools/job-search-system/config/runtime.local.toml
```

Этот файл тоже локальный и не должен попадать в git.

Проверить систему:

```bash
tools/job-search-system/scripts/operator/doctor.sh
```

Формат output: JSON health report.

Что проверяется: runtime config, SQLite, migrations, artifact root, `pdftotext`.

## Запуск сервисов

Обычная работа через skills может идти через CLI fallback. Для более удобной работы skills и будущего UI можно поднять API-lite:

```bash
tools/job-search-system/scripts/operator/start-api.sh
```

По умолчанию API слушает:

```text
http://127.0.0.1:8765
```

Правило: API-lite остаётся local/loopback surface. UI, skills и AI не пишут в SQLite напрямую.

AI reasoning в этой системе живёт в `jss-*` skills, а не в backend runtime:

- `$jss-candidate-intake` может заполнить schema-bound extraction draft, но сохранение идёт только через validated `import-ai-draft`.
- `$jss-resume-positioning` может дать AI critique/rewrite guidance как review notes, но persisted resume artifact создаётся только validated command.
- `$jss-vacancy-pipeline` может сделать advisory rerank / semantic review поверх deterministic ranking, но не меняет score, shortlist, processed state или SQLite.

## Где лежат артефакты

Все runtime artifacts лежат локально и игнорируются git:

```text
tools/job-search-system/data/artifacts/
```

Кандидатские артефакты:

```text
tools/job-search-system/data/artifacts/candidates/<candidate-slug>--<candidate-short-id>/
```

Подпапки:

- `sources/`: исходники, которые были загружены для кандидата.
- `drafts/`: сгенерированные drafts, резюме, playbook, positioning, messages и application artifacts.
- `final/`: финальные, явно принятые резюме и будущие final artifacts.

Будущие namespace на уровне `artifacts/`:

- `vacancies/`: если vacancy-level artifacts станет неудобно хранить внутри candidate namespace.
- `boards/`: если manual board operations потребуют evidence/proof artifacts.
- `system/`: diagnostic reports, schema snapshots, служебные артефакты.

Маска новых имён:

```text
<artifact-type>--<human-context>--<artifact-short-id>.<ext>
```

Обезличенные примеры:

- `resume-source--candidate-resume--1af582c0.md`
- `linkedin-source--candidate-linkedin-export--9c3aa210.md`
- `profile-source--search-context--63ceb6a7.md`
- `candidate-profile-draft--deterministic-profile-draft--26930230.json`
- `candidate-profile-draft--ai-profile-draft--26930230.json`
- `career-pathing-lite--cto-vp-engineering--b6ca2231.md`
- `career-pathing-full--cto-vp-engineering--0f3d2a9b.md`
- `job-search-playbook--cto--c71a1120.md`
- `resume-positioning-brief--cto-en--a173e921.md`
- `resume-markdown--cto-en--4d6e4ff4.md`
- `resume-roast-report--cto-for-resume-4d6e4ff4--44d8c80d.md`
- `resume-final--cto-en--4d6e4ff4.md`
- `resume-vacancy--company-cto--98aa31fd.md`
- `resume-vacancy-final--company-cto--b42c8a19.md`
- `message-artifact--company-role--d9f02281.md`

Каноническая идентичность всё равно хранится в SQLite как `artifact_id`. Человекочитаемое имя файла нужно для удобства навигации.

## Часть 1: первичный прогон нового кандидата

### 1. Подготовить входные данные

Рекомендуемая локальная структура:

```text
scratch/job-search/<candidate-slug>/
  sources/
    resume.pdf
    linkedin-export.pdf
    profile-notes.md
    search-context.md
  vacancy-batches/
    batch-001.json
```

Эта папка не коммитится. Входные файлы могут содержать персональные данные.

### 2. Создать и подтвердить профиль через skill

В Codex используйте:

```text
$jss-candidate-intake
```

Что дать skill-у:

- имя кандидата для `display_name`
- путь к резюме
- путь к сохранённому LinkedIn profile/page text, если есть
- путь к profile/search context, если есть
- указание, нужно ли использовать AI extraction

Что делает skill:

- создаёт candidate record
- выбирает active candidate
- загружает источники
- создаёт profile draft
- показывает draft review: conflicts, missing fields, evidence, source artifacts и intake quality issues
- подтверждает canonical profile только после вашего явного согласия

Output:

- candidate id в JSON-ответах command layer
- source artifacts в `sources/`
- profile draft в `drafts/candidate-profile-draft--...json`
- draft review как JSON response из API/CLI; отдельный файл не создаётся
- canonical profile в SQLite
- audit events в SQLite

CLI fallback для редких debug-сценариев остаётся в `skills/jss-candidate-intake/references/commands.md`.

### 3. Выбрать направление через career pathing

В Codex используйте:

```text
$jss-career-pathing
```

Разница режимов:

- `lite` нужен для быстрого операционного выбора primary target role для текущего цикла поиска.
- `full` нужен для стратегического сравнения нескольких карьерных траекторий, gap analysis и brand plan.
- Обычный vacancy workflow не должен требовать `full`; запускайте его при стратегической переоценке роли или спорном результате `lite`.
- Оба режима создают advisory artifacts и не меняют canonical candidate state автоматически.

Lite mode:

- сравнивает 2-5 ролей
- разделяет realistic и stretch roles
- показывает title inflation risks
- предлагает primary target role
- сохраняет markdown artifact

Output:

```text
tools/job-search-system/data/artifacts/candidates/<candidate-slug>--<candidate-short-id>/drafts/career-pathing-lite--<roles>--<artifact-short-id>.md
```

Full mode:

- строит broader role universe по confirmed profile, target roles, `career-pathing-lite` и локальной vacancy DB
- показывает capability gaps, T-shape branches, professional brand plan и trajectory ranking
- добавляет KB context, если runtime config содержит рабочий `[integrations].kb_index_config_path`
- сохраняет advisory markdown artifact
- не меняет canonical candidate state автоматически

Несколько карьерных путей у одного кандидата поддерживаются как parallel trajectories:

- в одном full report можно передать несколько `target_roles`
- можно создать несколько отдельных `career_pathing_full` artifacts под разные наборы ролей
- отдельной mutable сущности `career_path` с lifecycle сейчас нет; canonical state меняется только если пользователь отдельно подтверждает target-role/evidence через candidate-intake

Output:

```text
tools/job-search-system/data/artifacts/candidates/<candidate-slug>--<candidate-short-id>/drafts/career-pathing-full--<roles>--<artifact-short-id>.md
```

Если full mode предлагает добавить новый факт кандидата, этот факт нужно подтвердить через `$jss-candidate-intake`, а не переносить в резюме напрямую.

KB context не является обязательным. Если `[integrations].kb_index_config_path` отсутствует, указывает на несуществующий файл, `kb-index` ещё не построен или недоступен `tools/kb-index/bin/search_kb`, full report строится без KB и возвращает KB status `unavailable`.

### 4. Сгенерировать search playbook

В Codex используйте:

```text
$jss-job-search-playbook
```

Что делает skill:

- search strategy
- saved-search design pack
- reusable message draft
- compensation framework
- lightweight interview-prep artifacts

Output:

```text
tools/job-search-system/data/artifacts/candidates/<candidate-slug>--<candidate-short-id>/drafts/job-search-playbook--<primary-role>--<artifact-short-id>.md
```

### 5. Подготовить positioning и базовое резюме

В Codex используйте:

```text
$jss-resume-positioning
```

Что делает skill:

- при необходимости ищет supporting evidence в базе знаний через `kb-index`/`search_kb`
- создаёт positioning brief
- генерирует role-based markdown resume
- запускает resume quality gate
- создаёт или обновляет roast report для конкретного draft resume
- финализирует принятое резюме в `final/` только после явного подтверждения
- даёт skill-side AI suggestions/roast, если нужно; это review notes, а не persisted artifact без validated command

Output:

```text
drafts/resume-positioning-brief--<role>-<lang>--<artifact-short-id>.md
drafts/resume-markdown--<role>-<lang>--<artifact-short-id>.md
drafts/resume-roast-report--<role>-for-resume-<resume-short-id>--<artifact-short-id>.md
final/resume-final--<role>-<lang>--<artifact-short-id>.md
```

Quality gate output хранится в SQLite как `quality_gate_runs`; результат также возвращается в JSON.

KB evidence retrieval:

- команда: `search-resume-kb-evidence`
- API: `GET /candidates/resume-kb-evidence`
- это read-only query, не artifact и не mutation;
- если `[integrations].kb_index_config_path` не настроен, результат будет `status = unavailable`;
- поиск ограничен surface terms `job-search` и `hiring`, чтобы не превращать его в общий поиск по vault.
- если KB подсказывает полезный, но отсутствующий или слабый candidate signal, результат должен вернуться как `candidate_review_suggestions`;
- такие suggestions нельзя сразу использовать как факты: сначала спросите пользователя, затем внесите подтверждённое через `$jss-candidate-intake` / confirm flow.

Roast report:

- хранится как `resume_roast_report`;
- связан с прожаренным draft resume через `derived_from_artifact_id`;
- на один draft resume существует только один roast report;
- повторный запуск перезаписывает тот же report artifact/file;
- будущая версия резюме, созданная на основе roast report, должна быть новым artifact, derived from этого report.

Правила финализации:

- `pass` можно финализировать после явного acceptance.
- `warn` можно финализировать только если предупреждения приняты вручную.
- `fail` блокирует final artifact.
- Финальный artifact создаётся как derived artifact от draft resume; draft остаётся в `drafts/`.

## Часть 2: регулярная работа с вакансиями и daily routine

### Типовой день

1. Запустите `$jss-vacancy-pipeline` и попросите показать `daily actions`.
2. Разберите новые/review вакансии: skip, shortlist или processed.
3. Для 1-2 приоритетных вакансий подготовьте application payload.
4. Если нужно, создайте `resume_vacancy` и финализируйте только после acceptance.
5. Зафиксируйте реальные touchpoints/reminders.
6. Если вручную сделали действие на площадке, используйте `$jss-job-board-operations`, чтобы записать board action и проверить reconciliation.

### Типовая неделя

1. Посмотрите `strategy-report`.
2. Проверьте, какие роли, источники, компании и версии резюме дают больше applications, responses и interviews.
3. Пересмотрите noisy scoring: какие high/medium/low решения были неверными.
4. При необходимости обновите search playbook или career pathing.
5. Очистите устаревшие локальные generated artifacts через dry-run cleanup.

### 1. Импортировать новые вакансии

Для обычной работы в Codex используйте:

```text
$jss-vacancy-pipeline
```

Что дать skill-у:

- JSON batch с вакансиями
- raw vacancy text, если источник не platform-specific и в тексте есть title/company
- copied LinkedIn job page, email alert или search-results markdown text, если источник LinkedIn
- hh.ru vacancy page text или search-results markdown text, если источник hh.ru
- active candidate context или candidate id

Shell script для structured JSON batch:

```bash
tools/job-search-system/scripts/operator/import-vacancies.sh --items-path "<scratch>/vacancy-batches/batch-001.json"
```

Shell script для LinkedIn copied/search/email text:

```bash
tools/job-search-system/scripts/operator/import-linkedin-text.sh --content-path "<scratch>/vacancy-batches/linkedin-text-001.txt"
```

LinkedIn raw page copy без URL можно импортировать, если из текста извлекаются title/company/location. В этом случае result должен содержать warning, что нет stable `external_vacancy_id`. LinkedIn job-alert/recommended-jobs emails и search-results pages можно сохранить как `.md`/`.txt` и импортировать той же командой: parser читает markdown-карточки вакансий со ссылками `linkedin.com/.../jobs/view/...`, нормализует URL в canonical `https://www.linkedin.com/jobs/view/<id>` и убирает query/tracking-параметры. Workplace type вроде `Remote` добавляется к `location_text`, а `linkedin_workplace_type`, `linkedin_employment_type` и `linkedin_poster_requirements_json` сохраняются в `raw_text` metadata. Native LinkedIn CSV export не предполагается; CSV-like rows поддерживаются только как manually prepared табличный input. URL-only LinkedIn input без title/company не импортируется import-командами как canonical vacancy; сначала сохраните ссылку как URL enrichment seed, затем добавьте manually copied page text, проверьте preview и только потом подтвердите import.

Shell script для hh.ru vacancy/search-results text:

```bash
tools/job-search-system/scripts/operator/import-hh-ru-text.sh --content-path "<scratch>/vacancy-batches/hh-ru-vacancies-001.md"
```

hh.ru import читает два user-provided формата: single vacancy page text с URL в начале файла и search-results markdown cards вида `vacancy link -> salary/experience/work model -> employer link -> location`. URL нормализуется в canonical `https://hh.ru/vacancy/<id>`. Salary, experience, work model marker и дата публикации/обновления сохраняются в `raw_text` metadata (`hh_salary_text`, `hh_experience_text`, `hh_work_model`, `hh_published_at`, `hh_updated_at`) и в structured `source_occurrences.raw_payload_json` (`source_published_at`, `source_updated_at`). Если в single vacancy page есть блок `Где предстоит работать`, он используется как `location_text`.

Generic raw vacancy text импортируется через `$jss-vacancy-pipeline` или CLI/API fallback `import-text`.
Используйте его для любых площадок, под которые пока нет отдельного adapter: company ATS, Indeed, Wellfound, Habr Career, Telegram-пост, email от рекрутера, Notion/Google Docs copy-paste и т.п. Если позже появится повторяющийся формат, который generic template плохо покрывает, под него можно добавить отдельный adapter.

Shell script для generic copied vacancy text:

```bash
tools/job-search-system/scripts/operator/import-vacancy-text.sh --content-path "<scratch>/vacancy-batches/vacancy-text-001.txt"
```

Ожидаемый формат блока:

```text
Title: <role title>
Company: <company name>
Location: <location or remote policy>
URL: <source URL>
<short copied job text>
```

URL-only input без title/company не импортируется import-командами, чтобы не создавать мусорные вакансии. Для таких ссылок используйте URL enrichment flow: seed -> supervised preview -> confirm import. До confirm import canonical vacancy не создаётся.

Output:

- `canonical_vacancies` в SQLite
- `source_occurrences` в SQLite
- scoring/ranking доступен через skill или CLI query
- новые файлы обычно не создаются, пока не готовится application payload

### 2. Отранжировать и выбрать shortlist

В Codex используйте:

```text
$jss-vacancy-pipeline
```

Попросите skill:

- показать ranked vacancies
- объяснить score reasons
- выделить dealbreakers
- при необходимости сделать advisory AI rerank / semantic review как review notes
- перевести выбранные вакансии в shortlist

Output:

- workflow state в SQLite
- audit events в SQLite
- ranked/list output как JSON в ответе команды
- advisory AI rerank, если запрошен, возвращается как текстовый review output skill-а и не сохраняется как canonical state

### 3. Подготовить application payload

В Codex используйте:

```text
$jss-vacancy-pipeline
```

Попросите skill подготовить review-first application payload для выбранной вакансии.

Output:

```text
drafts/resume-markdown--<role>-<lang>--<artifact-short-id>.md
drafts/resume-vacancy--<company>-<role>--<artifact-short-id>.md
final/resume-vacancy-final--<company>-<role>--<artifact-short-id>.md
drafts/message-artifact--<company-role>--<artifact-short-id>.md
```

Также создаются:

- application record в SQLite
- artifact usage events в SQLite:
  - `application_draft_attached` для message artifact
  - `application_resume_attached` для resume artifact
- resume/message quality gate results в SQLite

Vacancy-specific resume создаётся только если оно действительно нужно под выбранную вакансию. Система сначала берёт final resume по той же роли; если final нет, возвращает доступные draft resumes по роли и требует выбрать источник явно, даже если draft всего один. В ответе всегда указывается `source_resume_artifact_id`.

Правила `resume_vacancy`:

- тип и filename prefix всегда `resume_vacancy`, без `rewrite`;
- на одну вакансию существует один рабочий `resume_vacancy`, повторный запуск перезаписывает его;
- после acceptance создаётся или перезаписывается `resume_vacancy_final` в `final/`;
- deterministic skeleton содержит только vacancy tailoring notes и guidance через quality gate / resume roast report; AI rewrite guidance живёт в skills как review notes, а persisted artifact создаётся только через validated command.

Правило application payload: это не submit/send. Это только review-ready payload.

### 4. Ежедневная рутина

Через skill:

```text
$jss-vacancy-pipeline
```

Попросите:

- показать daily actions
- показать pipeline report
- закрыть/обновить processed state после ручной проверки

Shell script для регулярного запуска:

```bash
tools/job-search-system/scripts/operator/daily-routine.sh
```

Если запуск делается как one-shot scheduled/manual maintenance job, используйте TTL wrapper:

```bash
tools/job-search-system/scripts/operator/daily-routine-with-ttl.sh --timeout-seconds 300
```

TTL wrapper использует общий runtime `common/ttl_runner.py`, не создаёт daemon и не выполняет external actions. Audit-файл по умолчанию пишется в ignored runtime path:

```text
tools/job-search-system/data/runtime/daily-routine-audit.json
```

Output:

- JSON daily actions
- JSON pipeline report
- изменений state нет, пока вы явно не попросите сделать mutation

### 5. Посмотреть strategy report и эффективность резюме

Через CLI:

```bash
python3 -m job_search.interfaces.cli.system_cli \
  --config-path "tools/job-search-system/config/runtime.local.toml" \
  --workspace-path "<workspace.local.toml>" \
  strategy-report \
  --candidate-id "<candidate_id>"
```

Через API-lite:

```bash
curl -sS "$JSS_API_URL/system/strategy-report?candidate_id=<candidate_id>"
```

Output:

- `funnel`: вакансии, shortlist, applications, submissions, responses, interviews.
- `by_role`, `by_company`, `by_source_kind`: deterministic conversion breakdown.
- `resume_effectiveness`: какие resume artifacts использовались в application payload и какие outcomes по ним появились.
- `position_effectiveness`: какие target role / позиции дают больше applications, submissions, responses и interviews.
- `quality`: quality gate counts и recent issues.

Важно: эффективность резюме считается только по записанным `application_resume_attached` usage events. Если application была отправлена вручную без подготовки payload через систему, отчёт не будет приписывать её конкретной версии резюме.

### 6. Touchpoints и reminders

Через skill:

```text
$jss-vacancy-pipeline
```

Используйте только для реальных событий:

- planned follow-up
- sent message
- received reply
- reminder resolution

Output:

- `touchpoints` в SQLite
- `follow_up_reminders` в SQLite
- artifact usage event, если touchpoint ссылается на message artifact

### 7. Внешние площадки и manual board operations

Через skill:

```text
$jss-job-board-operations
```

Что можно делать:

- получить checklist для площадки
- получить saved-search settings
- сохранить URL-only вакансию как enrichment seed
- добавить вручную скопированный page text к seed и получить supervised preview
- подтвердить import из preview, если извлечена ровно одна вакансия
- вручную выполнить действие на внешней площадке
- записать manual board action
- посмотреть журнал manual board actions
- посмотреть reconciliation items для board/internal drift
- закрыть reconciliation item после ручного review

Output:

- `vacancy_url_enrichment_seeds` в SQLite для URL-only заготовок
- manual board action в SQLite
- reconciliation item в SQLite для каждого logged board action
- artifact usage event, если external-facing action связан с artifact
- pipeline report может учитывать board actions
- daily actions показывают reconciliation items со статусом `open`

Правила:

- система не делает unattended submit/send/publish
- browser automation и authenticated board reading остаются future decisions; URL seed preview принимает только вручную предоставленный text/content
- URL-only seed не является vacancy и не попадает в shortlist/ranking до confirm import
- logged board action означает “пользователь сделал/планирует действие вручную”, а не “система сделала действие сама”
- reconciliation item не создаёт второй lifecycle и не меняет internal state скрыто
- если reconciliation item требует изменения internal state, используйте отдельную явную command layer операцию
- quality gate, content acceptance и external action approval не заменяют друг друга

### 8. Telegram companion surface

Telegram пока не является рабочим интерфейсом `job-search-system`.

Текущий статус:

- есть только contract/design gate: `tools/job-search-system/docs/telegram-companion-contract.md`;
- actual bridge не реализован;
- Telegram отложен до первого Web UI slice;
- Web UI будет primary operational workspace;
- будущий Telegram owner по умолчанию: `telegram_agent_bot`;
- будущий UX: `/agent` для обычного agent mode и `/jss` для job-search mode;
- будущий JSS mode должен использовать allowlisted local actions, pending confirmations и запрет external actions.

Разрешённая будущая роль Telegram: тонкий inbox/control/review companion поверх существующего API/CLI/skills. Telegram не должен владеть candidate profile, vacancy lifecycle, artifact registry, external actions или SQLite mutations и не должен заменять Web UI.

### 9. Maintenance

Переименовать старые UUID-only artifact filenames:

```bash
tools/job-search-system/scripts/operator/rename-artifacts.sh
```

Dry-run output: JSON со списком `old_path` и `new_path`.

Применить:

```bash
tools/job-search-system/scripts/operator/rename-artifacts.sh --apply
```

Команда меняет только filename и `artifacts.storage_path`; candidate folder не переезжает.

Почистить старые candidates/artifacts:

```bash
tools/job-search-system/scripts/operator/cleanup-artifacts.sh --keep-candidate-id "<candidate_id>"
```

Применить:

```bash
tools/job-search-system/scripts/operator/cleanup-artifacts.sh --keep-candidate-id "<candidate_id>" --apply
```

Cleanup output:

- backup path
- deleted candidate ids
- deleted artifact folders
- before/after counts

## Troubleshooting

### Нет active candidate

Используйте `$jss-candidate-intake`:

- попросите показать список кандидатов;
- выберите нужного кандидата как active;
- если кандидата нет, создайте нового и начните intake.

### API-lite не запущен

Это не блокер для skills: они могут использовать CLI fallback. Если нужен API-lite для более удобной работы или будущего UI, запустите:

```bash
tools/job-search-system/scripts/operator/start-api.sh
```

### `doctor.sh` ругается на runtime config

Проверьте, что существует локальный файл:

```text
tools/job-search-system/config/runtime.local.toml
```

Он должен быть создан из example/config шаблона и указывать на локальные `db_path`, `artifact_root` и `sqlite_config_path`.

### PDF не импортируется

PDF ingestion требует `pdftotext`. Проверьте `doctor.sh`; если `pdftotext` отсутствует, установите его или временно передайте skill-у extracted text вместо PDF.

### LinkedIn или hh.ru текст не импортируется

Проверьте, что в тексте есть минимум:

- title;
- company/employer;
- location или remote/work-model context;
- URL, если формат площадки его обычно содержит.

Если есть только URL без текста вакансии, используйте URL seed flow: `create-url-seed` -> `preview-url-seed` с вручную скопированным текстом -> `confirm-url-seed-import`.

### Quality gate вернул `warn`

`warn` не блокирует final artifact автоматически, но требует явного acceptance предупреждений. Не используйте `--allow-warnings`, пока пользователь явно не принял warning.

### Quality gate вернул `fail`

Не финализируйте artifact и не готовьте external use. Используйте `$jss-resume-positioning` для review/roast/guidance или вернитесь в `$jss-candidate-intake`, если проблема связана с отсутствующими/неподтверждёнными фактами.

### Strategy report не показывает эффективность резюме

Resume effectiveness считается только по `application_resume_attached` usage events. Если отклик был отправлен вручную без application payload через систему, его нельзя корректно связать с конкретной версией резюме.

## Как проверять, что план не потерял capability

Для каждой плановой capability должен быть один из статусов:

- backend/API/CLI implemented
- skill wrapper implemented
- explicitly deferred to `docs/stage3-backlog.md`

Если capability есть в canonical docs `Job/job-search-skills/00-10`, но её нет ни в backend/API/CLI, ни в `skills/jss-*`, ни в `docs/capability-coverage.md`, ни в `docs/stage3-backlog.md`, это пропуск.

Текущая защита:

- `docs/capability-coverage.md` фиксирует статус каждой capability из canonical plans
- `test_skill_contracts.py` проверяет ожидаемый набор `jss-*` skills
- backend tests проверяют command/query behavior
- `docs/stage3-backlog.md` хранит отложенные Stage 3 хвосты
- smoke guide проверяет end-to-end journey, но не заменяет coverage audit по плану
