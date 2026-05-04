# KB Index

Локальный проект для индексирования базы знаний Obsidian и retrieval-доступа к заметкам без полного прохода по vault на каждый запрос.

## Цель

Проект строит канонический retrieval layer для knowledge-base workflow:

1. сначала ищет по локальному индексу;
2. затем открывает только нужные заметки целиком.

Этап 1 сознательно ограничен `SQLite` + `FTS5` + metadata-aware retrieval без embeddings.

## Границы проекта

Проект отвечает за:

- построение и обновление локального индекса;
- хранение note-level метаданных и lead-summary retrieval signals;
- полнотекстовый retrieval по `FTS5`;
- CLI-контракт для `build`, `update`, `search`, `status`;
- `scheduled` auto-update через `launchd` на macOS.

Проект пока не отвечает за:

- vector search;
- embeddings;
- `MCP`-сервер;
- отдельный search daemon;
- graph expansion по wikilinks.

## Структура

```text
tools/kb-index/
├── README.md
├── src/
│   └── kb_index/
├── tests/
├── config/
└── data/
```

### Назначение директорий

- `src/kb_index/` — код индексатора, поиска, CLI и служебных модулей.
- `tests/` — unit и integration tests.
- `config/` — локальная конфигурация проекта и примеры runtime config.
  Основной источник vault path и scope индексирования на этапе 1 — `config/runtime.local.toml`.
- `data/` — локальные runtime-артефакты проекта.

## Runtime Files

Текущие project-local runtime-артефакты в `data/`:

- `kb_index.sqlite`
- `kb_index_state.json`

При включенном `launchd` auto-update сервис использует отдельный service root:

- `~/Library/Application Support/kb_index_service`

Там живут:

- runtime-копия `src/kb_index`
- симлинк `config/runtime.local.toml` на репозиторный конфиг
- shell-runner для `launchd`

Канонические `launchd`-логи при этом пишутся в project root, а не в service root:

- `tools/kb-index/data/launchd/auto_update.startup.log`
- `tools/kb-index/data/launchd/auto_update.stdout.log`
- `tools/kb-index/data/launchd/auto_update.stderr.log`

## CLI Commands

Сейчас реализованы:

CLI сначала читает `config/runtime.local.toml`, а явные аргументы командной строки могут переопределить пути для конкретного запуска. Scope индексирования тоже задается через конфиг, а не хардкодится в коде.

Ранжирование retrieval тоже задается через конфиг: веса формулы, `note_type`-веса и пороги `exact-title` bonus не хардкодятся в `search.py`. Дефолтное количество результатов поиска тоже задается через `retrieval.default_limit`.

Поиск заметок работает не только по `title`, `Суть`, `headings` и `tags`, но и использует:

- `links_out` как слабый graph-aware сигнал для блока `Связанные заметки`, чтобы заметки, уже ссылающиеся на нужный концепт или соседний узел, поднимались выше при related-note discovery


- `build_kb_index`
- `list_kb_tags`
- `update_kb_index`
- `search_kb`
- `status_kb_index`
- `install_kb_index_auto_update`
- `uninstall_kb_index_auto_update`
- `status_kb_index_auto_update`

Для known-note lookup есть отдельный title-oriented режим:

```bash
search_kb --mode title-first --note-type concept "Known note title"
```

Его стоит использовать вместо прямого `rg` по именам файлов, когда workflow уже знает или почти знает имя нужной заметки и хочет найти её через индекс, а не мимо индекса.

Для tag discovery есть отдельный CLI:

```bash
list_kb_tags --config-path /absolute/path/to/runtime.local.toml --json
list_kb_tags --config-path /absolute/path/to/runtime.local.toml --tag developer-productivity --json
list_kb_tags --config-path /absolute/path/to/runtime.local.toml --prefix developer --json
```

Используй его вместо `rg` по vault, когда нужно:

- получить список всех используемых тегов
- проверить, существует ли уже конкретный тег
- посмотреть соседние существующие теги перед созданием нового

Пока не реализованы и остаются roadmap items:

- `read_kb`
- `watch_kb_index`

## Корпус индексации

Корпус индексирования задается в `config/runtime.local.toml` через `include_roots`, `exclude_roots` и `exclude_globs`.

Плановое автообновление тоже задается в `config/runtime.local.toml` через секцию `auto_update`.
На текущем этапе поддерживается один режим:

- `launchd` на macOS, который по расписанию вызывает тот же канонический `update_kb_index`

Стартовая конфигурация для текущего vault:

- include: `Ideas/`, `Daily notes/`
- exclude roots: `.obsidian/`, `Templates/`, `Ideas/attachments/`
- exclude globs: `*.canvas`, `*.png`, `*.jpg`, `*.jpeg`, `*.gif`, `*.pdf`, `*.webp`, `*.csv`, `*.xlsx`

## Архитектурный принцип

Любой knowledge workflow должен использовать двухшаговый путь:

1. `search`
2. `read`

Полный проход по vault допустим только как fallback, если индекс отсутствует, поврежден или явно устарел.

## Auto-Update

Автообновление не вводит отдельный daemon с собственной логикой индексации.
Оно только планово вызывает уже существующий `update_kb_index`, чтобы:

- новые заметки попадали в индекс без ручного rebuild;
- измененные заметки переиндексировались инкрементально;
- удаленные заметки удалялись из индекса тем же каноническим путем.

Команды:

- `install_kb_index_auto_update --config-path ...`
- `status_kb_index_auto_update --config-path ...`
- `uninstall_kb_index_auto_update --config-path ...`

Installer не запускает код напрямую из `Documents/Playground`.
Вместо этого он копирует runtime-слой в `~/Library/Application Support/kb_index_service`, оставляет `config/runtime.local.toml` симлинком на исходный конфиг в репозитории, генерирует там shell-runner и уже его регистрирует в `launchd`.
Этот deployment shape совпадает с уже рабочим паттерном `telegram_connector` и обходит проблемы запуска launch agents прямо из пользовательского project tree.
При этом stdout/stderr и startup trace остаются в `tools/kb-index/data/launchd`, чтобы operational logs жили рядом с проектом, а не были размазаны между двумя корнями.

`status_kb_index` показывает и `configured_auto_update`, чтобы runtime-настройки расписания были видны рядом с retrieval-конфигом.
`status_kb_index_auto_update` показывает уже состояние установленного launch agent, service root и канонического project-local log directory.

### Reload After Config Changes

Если меняется `config/runtime.local.toml` и нужно, чтобы `launchd` перечитал:

- новый `auto_update.interval_minutes`
- новый `launchd_label`
- или другие runtime-настройки service-root deployment

используй тот же канонический installer повторно:

```bash
install_kb_index_auto_update --config-path /absolute/path/to/runtime.local.toml
```

Повторный запуск installer:

- обновляет runtime-копию в `~/Library/Application Support/kb_index_service`
- пересоздаёт симлинк `runtime.local.toml` на указанный source-of-truth конфиг
- перегенерирует shell-runner
- переустанавливает `launchd` plist

То есть это и есть рекомендуемый способ "перезапустить демон и перечитать конфиг".

Для проверки после reload:

```bash
status_kb_index_auto_update --config-path /absolute/path/to/runtime.local.toml
status_kb_index --config-path /absolute/path/to/runtime.local.toml
```

## Freshness Model

Свежесть индекса теперь обеспечивается двумя путями:

1. `scheduled auto-update`
   - `launchd` запускает инкрементальный `update_kb_index` по интервалу из `auto_update.interval_minutes`
2. `post-write sync`
   - knowledge-base skills, которые реально создали или обновили заметки и знают `paths.kb_index_config`, могут один раз вызвать `update_kb_index` в конце run

Такой split нужен, чтобы:

- новые внешние изменения в vault не ждали ручного rebuild
- заметки, только что созданные через skill, попадали в индекс сразу, а не ждали следующего расписания

## Дальнейшие улучшения

Текущее `stage 1` ядро уже реализовано и используется skill-ами.

Дальше остаются только необязательные улучшения, например:

1. `watch`-режим поверх текущего `launchd`-расписания, если когда-нибудь понадобится более частая реакция на изменения.
2. `read_kb` как отдельный CLI для стандартизированного note-read access поверх уже найденного shortlist.
3. Более глубокий graph expansion по `wikilinks`, если related-note discovery упрётся в текущий `links_out` signal.
4. Позже, при реальной необходимости, отдельный vector layer или hybrid semantic retrieval поверх текущего `SQLite + FTS5`.
