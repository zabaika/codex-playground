# Agents Best Practices — converted Codex package

Локальный Codex-скилл для проектирования, аудита и диагностики приложений с агентами. Это инструкции и справочники; пакет не запускает службу, не подключает MCP, не регистрирует аккаунты и не отправляет сообщения.

## Использование

После установки вызывайте `$agents-best-practices`. Пример: «Используй $agents-best-practices для аудита восстановления Telegram-дайджеста после таймаута. Нужны наблюдаемые причины и минимальные изменения».

Основной сценарий и границы разрешений находятся в [SKILL.md](SKILL.md). Подробные режимы и шаблон результата — в [harness-workflows.md](references/harness-workflows.md); готовые примеры запросов — в [upstream-guide.md](references/upstream-guide.md). Все 25 тематических справочников исходника сохранены. Сравнения провайдеров и исторические кейсы оставлены как справочный материал, требующий проверки перед практическим применением.

## Источник и обслуживание

Происхождение, commit и контрольные суммы исходных документов записаны в [provenance.json](provenance.json); лицензия — [LICENSE](LICENSE). Проверка upstream допустима, автоматическая замена проверенных инструкций отключена. Обновление требует отдельного запроса, повторного аудита и проверки.

Канонический редактируемый исходник — `skills/agents-best-practices/` в Playground. При сопровождении следуйте `RULEBOOK.md` в корне проекта. Установка и обновление установленной копии выполняются из корня проекта:

```bash
bash skills/agents-best-practices/install-local.sh
```

Установщик заменяет только `${CODEX_HOME:-$HOME/.codex}/skills/agents-best-practices` локальным проверенным пакетом и не обращается к сети. Не клонируйте upstream поверх конвертации и не редактируйте установленную копию как второй исходник. `placement_mode` в provenance фиксирует исходную подготовку в scratch, а не текущий статус установки.

Секреты, локальная конфигурация, логи и runtime не нужны для чтения пакета. Реализация агента, запуск проверок или подключение сервиса — отдельная задача со своими доступами и артефактами.

## Справочники

- Проектирование: [MVP](references/mvp-agent-blueprint.md), [архитектура](references/architecture.md), [цикл](references/agentic-loop.md), [инструменты](references/tools-and-permissions.md), [промпты](references/system-prompts-instructions.md), [провайдеры](references/provider-api-patterns.md).
- Состояние и исполнение: [память](references/context-memory-compaction.md), [цели](references/planning-and-goals.md), [оркестрация](references/workflow-orchestration.md), [фоновые задачи](references/always-on-agents.md).
- Специализированные режимы: [код](references/coding-agents.md), [голос](references/realtime-voice-agents.md), [устройства](references/hardware-agents.md), [изменяемая среда](references/environment-adaptive-tools.md), [команды](references/adaptive-agent-teams.md), [рекурсия](references/self-refining-recursive-harnesses.md), [спекулятивное исполнение](references/speculative-tool-execution.md).
- Интеграции и качество: [скиллы и коннекторы](references/skills-and-connectors.md), [стоимость](references/prompt-caching-and-cost.md), [безопасность и диагностика](references/security-observability.md), [оценка качества](references/evals.md), [артефакты и обратная связь](references/agent-legibility-feedback-loops.md), [чек-листы](references/checklists.md), [карта покрытия](references/coverage-audit.md), [источники](references/source-links.md).

Если упомянутого инструмента нет в текущей среде, используйте доступный аналог с теми же разрешениями либо обозначьте ручной шаг. Указания на частные логи автора не означают доступность этих логов здесь.
