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
- CLI-контракт для `build`, `update`, `search`, `status`.

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

Текущие runtime-артефакты в `data/`:

- `kb_index.sqlite`
- `kb_index_state.json`

## CLI Commands

Сейчас реализованы:

CLI сначала читает `config/runtime.local.toml`, а явные аргументы командной строки могут переопределить пути для конкретного запуска. Scope индексирования тоже задается через конфиг, а не хардкодится в коде.

Ранжирование retrieval тоже задается через конфиг: веса формулы, `note_type`-веса и пороги `exact-title` bonus не хардкодятся в `search.py`. Дефолтное количество результатов поиска тоже задается через `retrieval.default_limit`.

Поиск заметок работает не только по `title`, `Суть`, `headings` и `tags`, но и использует:

- `links_out` как слабый graph-aware сигнал для блока `Связанные заметки`, чтобы заметки, уже ссылающиеся на нужный концепт или соседний узел, поднимались выше при related-note discovery


- `build_kb_index`
- `update_kb_index`
- `search_kb`
- `status_kb_index`

Пока не реализованы и остаются roadmap items:

- `read_kb`
- `watch_kb_index`
- `scheduled` auto-update

## Корпус индексации

Корпус индексирования задается в `config/runtime.local.toml` через `include_roots`, `exclude_roots` и `exclude_globs`.

Стартовая конфигурация для текущего vault:

- include: `Ideas/`, `Daily notes/`
- exclude roots: `.obsidian/`, `Templates/`, `Ideas/attachments/`
- exclude globs: `*.canvas`, `*.png`, `*.jpg`, `*.jpeg`, `*.gif`, `*.pdf`, `*.webp`, `*.csv`, `*.xlsx`

## Архитектурный принцип

Любой knowledge workflow должен использовать двухшаговый путь:

1. `search`
2. `read`

Полный проход по vault допустим только как fallback, если индекс отсутствует, поврежден или явно устарел.

## Ближайшие этапы реализации

1. Реализовать parser заметки и chunking с отдельным блоком `Суть`.
2. Реализовать schema и базовые команды `build/update/search/status`.
3. Проверить retrieval на реальных запросах.
4. Добавить `scheduled` auto-update.
5. Перевести один skill на новый retrieval path.
6. Финализировать `README.md`, `AGENTS.md` и обновление `RULEBOOK.md`.
