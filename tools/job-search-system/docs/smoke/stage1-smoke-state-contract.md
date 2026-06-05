# Stage 1 Smoke State Contract

Machine-state smoke-runner хранится в `KIT_ROOT/.state/checkpoints.json`.

## Назначение

Файл нужен для:

- безопасного повторного запуска smoke-сценария
- пропуска уже завершённых шагов
- хранения важных runtime identifiers между шагами
- отделения машинной логики от человеческих заметок в `run-log.md`

## Формат

```json
{
  "plan_id": "stage1-core",
  "plan_version": "2026-05-21.2",
  "kit_root": ".",
  "updated_at": "2026-05-21T12:00:00+00:00",
  "values": {
    "CANDIDATE_A_ID": "candidate-001",
    "CANDIDATE_A_DRAFT_MULTI_ID": "draft-001"
  },
  "steps": {
    "step-01-candidate-context": {
      "status": "completed",
      "step_number": 1,
      "title": "Создание кандидатов и выбор активного контекста",
      "completed_at": "2026-05-21T12:05:00+00:00"
    }
  }
}
```

## Поля

- `plan_id`: стабильный идентификатор сценария
- `plan_version`: версия сценария; при изменении старые completed checkpoints не должны автоматически считаться валидными
- `kit_root`: относительная ссылка на корень kit внутри самого machine-state; для текущего runner она фиксируется как `.`, чтобы не записывать персональные абсолютные пути
- `updated_at`: время последнего изменения machine-state
- `values`: runtime identifiers и другие машинные значения, нужные следующим шагам
- `steps`: статусы шагов по стабильным `step_id`

## Правила

- `run-log.md` не является частью machine-state
- completed step определяется по `step_id` и текущему `plan_version`
- если план меняется, раннер должен либо заново инициализировать state, либо явно инвалидировать старые checkpoints
- шаг считается пропускаемым только если его `status=completed`
- при `--redo-from N` раннер должен очищать не только completed steps начиная с `N`, но и зависимые runtime values поздних шагов, чтобы не использовать устаревшие ids
- machine-state не должен хранить секреты
- machine-state может хранить локальные ids, но не должен без необходимости сохранять персональные абсолютные пути
