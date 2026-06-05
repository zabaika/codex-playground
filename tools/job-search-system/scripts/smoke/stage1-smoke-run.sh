#!/usr/bin/env bash
set -euo pipefail

PLAN_ID="stage1-core"
PLAN_VERSION="2026-05-21.2"
RUN_JSON_LAST_OUTPUT=""

INVOKED_PATH="${BASH_SOURCE[0]}"
LINK_DIR="$(cd -- "$(dirname -- "$INVOKED_PATH")" && pwd)"
REAL_SCRIPT_PATH="$(python3 - "$INVOKED_PATH" <<'PY'
import os
import sys
print(os.path.realpath(sys.argv[1]))
PY
)"
REAL_SCRIPT_DIR="$(cd -- "$(dirname -- "$REAL_SCRIPT_PATH")" && pwd)"
JSS_ROOT_DEFAULT="$(cd -- "$REAL_SCRIPT_DIR/../.." && pwd)"
PLAYGROUND_ROOT_DEFAULT="$(cd -- "$JSS_ROOT_DEFAULT/../.." && pwd)"

detect_default_kit_root() {
  local candidate
  for candidate in "${KIT_ROOT:-}" "$LINK_DIR" "$PWD"; do
    [[ -z "$candidate" ]] && continue
    if [[ -f "$candidate/workspace.local.toml" || -f "$candidate/run-log.md" || -d "$candidate/candidate-a" || -d "$candidate/vacancies" ]]; then
      printf "%s" "$candidate"
      return 0
    fi
  done
  if [[ "$LINK_DIR" != "$REAL_SCRIPT_DIR" ]]; then
    printf "%s" "$LINK_DIR"
    return 0
  fi
  printf ""
}

KIT_ROOT="${KIT_ROOT:-$(detect_default_kit_root)}"
JSS_ROOT="${JSS_ROOT:-$JSS_ROOT_DEFAULT}"
PLAYGROUND_ROOT="${PLAYGROUND_ROOT:-$PLAYGROUND_ROOT_DEFAULT}"
CONFIG_PATH="${CONFIG_PATH:-$JSS_ROOT/config/runtime.local.toml}"
WORKSPACE_PATH="${WORKSPACE_PATH:-$KIT_ROOT/workspace.local.toml}"
CHECKLIST_PATH="${CHECKLIST_PATH:-}"
if [[ -n "$KIT_ROOT" ]]; then
  STATE_DIR="$KIT_ROOT/.state"
  OUTPUT_DIR="$STATE_DIR/outputs"
  STATE_FILE="$STATE_DIR/checkpoints.json"
  RUN_LOG_PATH="$KIT_ROOT/run-log.md"
else
  STATE_DIR=""
  OUTPUT_DIR=""
  STATE_FILE=""
  RUN_LOG_PATH=""
fi

STEP_ID_1="step-01-candidate-context"
STEP_ID_2="step-02-candidate-a-single-source-intake"
STEP_ID_3="step-03-candidate-a-multi-source-enrichment"
STEP_ID_4="step-04-candidate-a-confirm-multi-source-draft"
STEP_ID_5="step-05-candidate-a-career-pathing"
STEP_ID_6="step-06-candidate-a-playbook"
STEP_ID_7="step-07-candidate-a-resume-artifacts"
STEP_ID_8="step-08-candidate-a-import-vacancies"
STEP_ID_9="step-09-candidate-a-rank"
STEP_ID_10="step-10-candidate-a-shortlist"
STEP_ID_11="step-11-candidate-a-application-draft"
STEP_ID_12="step-12-candidate-a-application-payload"
STEP_ID_13="step-13-candidate-a-touchpoint"
STEP_ID_14="step-14-candidate-a-daily-actions-and-report"
STEP_ID_15="step-15-candidate-a-processed-and-material-change"
STEP_ID_16="step-16-candidate-b-intake"
STEP_ID_17="step-17-candidate-b-vacancies-and-report"

if [[ -n "$STATE_DIR" ]]; then
  mkdir -p "$STATE_DIR" "$OUTPUT_DIR"
fi

usage() {
  cat <<EOF
Использование:
  $(basename "$0")                     Запустить smoke-шаги со следующего незавершённого шага
  $(basename "$0") --from N            Запустить smoke-шаги, начиная с шага N
  $(basename "$0") --redo-from N       Принудительно перезапустить шаги, начиная с шага N
  $(basename "$0") status              Показать текущее состояние
  $(basename "$0") reset               Очистить локальное состояние smoke-run
  $(basename "$0") self-test-state     Проверить machine-state логику без запуска smoke-шагов

Переопределяемые переменные окружения:
  KIT_ROOT
  JSS_ROOT
  CONFIG_PATH
  WORKSPACE_PATH
  CHECKLIST_PATH
EOF
}

now_iso() {
  python3 - <<'PY'
from datetime import datetime, timezone
print(datetime.now(timezone.utc).replace(microsecond=0).isoformat())
PY
}

create_state_template() {
  python3 - "$STATE_FILE" "$PLAN_ID" "$PLAN_VERSION" <<'PY'
import json
import sys
from datetime import datetime, timezone

path, plan_id, plan_version = sys.argv[1:4]
payload = {
    "plan_id": plan_id,
    "plan_version": plan_version,
    "kit_root": ".",
    "updated_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
    "values": {},
    "steps": {},
}
with open(path, "w", encoding="utf-8") as fh:
    json.dump(payload, fh, ensure_ascii=False, indent=2)
    fh.write("\n")
PY
}

ensure_state_file() {
  require_kit_root
  if [[ ! -f "$STATE_FILE" ]]; then
    create_state_template
    sync_run_log
    return 0
  fi

  local version_check
  version_check="$(python3 - "$STATE_FILE" "$PLAN_ID" "$PLAN_VERSION" <<'PY'
import json
import sys

path, plan_id, plan_version = sys.argv[1:4]
with open(path, "r", encoding="utf-8") as fh:
    data = json.load(fh)
if data.get("plan_id") == plan_id and data.get("plan_version") == plan_version:
    print("ok")
else:
    print("mismatch")
PY
)"
  if [[ "$version_check" == "mismatch" ]]; then
    local backup="$STATE_DIR/checkpoints.backup.$(date +%Y%m%d-%H%M%S).json"
    cp "$STATE_FILE" "$backup"
    create_state_template
    printf "Обнаружена новая версия smoke-плана. Старое состояние сохранено в %s\n" "$backup"
  fi
  backfill_step_state
  sync_run_log
}

state_get_value() {
  local key="$1"
  python3 - "$STATE_FILE" "$key" <<'PY'
import json
import sys

path, key = sys.argv[1:3]
with open(path, "r", encoding="utf-8") as fh:
    data = json.load(fh)
value = data.get("values", {}).get(key, "")
if value is None:
    print("")
elif isinstance(value, (dict, list)):
    print(json.dumps(value, ensure_ascii=False))
else:
    print(value)
PY
}

state_set_value() {
  local key="$1"
  local value="$2"
  python3 - "$STATE_FILE" "$key" "$value" <<'PY'
import json
import sys
from datetime import datetime, timezone

path, key, value = sys.argv[1:4]
with open(path, "r", encoding="utf-8") as fh:
    data = json.load(fh)
data.setdefault("values", {})[key] = value
data["updated_at"] = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
with open(path, "w", encoding="utf-8") as fh:
    json.dump(data, fh, ensure_ascii=False, indent=2)
    fh.write("\n")
PY
}

state_mark_completed() {
  local step_id="$1"
  local step_number="$2"
  local title="$3"
  python3 - "$STATE_FILE" "$step_id" "$step_number" "$title" <<'PY'
import json
import sys
from datetime import datetime, timezone

path, step_id, step_number, title = sys.argv[1:5]
with open(path, "r", encoding="utf-8") as fh:
    data = json.load(fh)
data.setdefault("steps", {})[step_id] = {
    "status": "completed",
    "step_number": int(step_number),
    "title": title,
    "completed_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
}
data["updated_at"] = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
with open(path, "w", encoding="utf-8") as fh:
    json.dump(data, fh, ensure_ascii=False, indent=2)
    fh.write("\n")
PY
  sync_run_log
}

state_clear_from_step() {
  local from_step="$1"
  state_clear_from_file "$STATE_FILE" "$from_step"
  sync_run_log
}

state_clear_from_file() {
  local state_file="$1"
  local from_step="$2"
  python3 - "$state_file" "$from_step" <<'PY'
import json
import sys
from datetime import datetime, timezone

path, from_step = sys.argv[1], int(sys.argv[2])
value_min_steps = {
    "CANDIDATE_A_ID": 1,
    "CANDIDATE_B_ID": 16,
    "CANDIDATE_A_RESUME_V1_ARTIFACT_ID": 2,
    "CANDIDATE_A_DRAFT_SINGLE_ID": 2,
    "CANDIDATE_A_RESUME_V2_ARTIFACT_ID": 3,
    "CANDIDATE_A_LINKEDIN_ARTIFACT_ID": 3,
    "CANDIDATE_A_SEARCH_CONTEXT_ARTIFACT_ID": 3,
    "CANDIDATE_A_DRAFT_MULTI_ID": 3,
    "CANDIDATE_A_CAREER_ARTIFACT_ID": 5,
    "CANDIDATE_A_PLAYBOOK_ARTIFACT_ID": 6,
    "CANDIDATE_A_TOP_VACANCY_ID": 9,
    "CANDIDATE_A_TOP_VACANCY_ROLE": 9,
    "CANDIDATE_A_SECOND_VACANCY_ID": 9,
    "CANDIDATE_A_MATERIAL_CHANGE_VACANCY_ID": 9,
    "CANDIDATE_A_PRIMARY_VACANCY_ID": 10,
    "CANDIDATE_A_APPLICATION_ID": 11,
    "CANDIDATE_A_MESSAGE_ARTIFACT_ID": 11,
    "CANDIDATE_A_PAYLOAD_APPLICATION_ID": 12,
    "CANDIDATE_A_PAYLOAD_MESSAGE_ARTIFACT_ID": 12,
    "CANDIDATE_A_PAYLOAD_RESUME_ARTIFACT_ID": 12,
    "CANDIDATE_A_TOUCHPOINT_ID": 13,
    "CANDIDATE_A_REMINDER_ID": 13,
    "CANDIDATE_B_DRAFT_ID": 16,
}
with open(path, "r", encoding="utf-8") as fh:
    data = json.load(fh)
steps = data.get("steps", {})
data["steps"] = {
    key: value
    for key, value in steps.items()
    if int(value.get("step_number", 0)) < from_step
}
values = data.get("values", {})
data["values"] = {
    key: value
    for key, value in values.items()
    if value_min_steps.get(key, 0) < from_step
}
data["updated_at"] = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
with open(path, "w", encoding="utf-8") as fh:
    json.dump(data, fh, ensure_ascii=False, indent=2)
    fh.write("\n")
PY
}

backfill_step_state() {
  python3 - "$STATE_FILE" <<'PY'
import json
import sys
from datetime import datetime, timezone

path = sys.argv[1]
with open(path, "r", encoding="utf-8") as fh:
    data = json.load(fh)

steps = data.setdefault("steps", {})
values = data.get("values", {})
changed = False

if "step-02-candidate-a-single-source-intake" not in steps:
    if values.get("CANDIDATE_A_RESUME_V1_ARTIFACT_ID") and values.get("CANDIDATE_A_DRAFT_SINGLE_ID"):
        steps["step-02-candidate-a-single-source-intake"] = {
            "status": "completed",
            "step_number": 2,
            "title": "Candidate A: intake из одного источника",
            "completed_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        }
        changed = True

if changed:
    data["updated_at"] = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=2)
        fh.write("\n")
PY
}

sync_run_log() {
  [[ -n "$RUN_LOG_PATH" ]] || return 0
  python3 - "$STATE_FILE" "$RUN_LOG_PATH" <<'PY'
import json
import sys
from pathlib import Path

state_path = Path(sys.argv[1])
run_log_path = Path(sys.argv[2])

step_rows = [
    (1, "Создание кандидатов и выбор активного контекста", "Candidate creation and active context"),
    (2, "Candidate A: intake из одного источника", "Single-source intake"),
    (3, "Candidate A: обогащение из нескольких источников", "Multi-source enrichment"),
    (4, "Candidate A: разрешение конфликтов и подтверждение", "Conflict resolution and confirm"),
    (5, "Candidate A: career pathing lite", "Career pathing lite"),
    (6, "Candidate A: job search playbook", "Job search playbook"),
    (7, "Candidate A: генерация resume artifacts", "Resume artifacts"),
    (8, "Candidate A: импорт вакансий", "Vacancy import"),
    (9, "Candidate A: ranking вакансий", "Scoring and ranking"),
    (10, "Candidate A: перевод вакансий в shortlist", "Shortlist"),
    (11, "Candidate A: создание application draft", "Application draft"),
    (12, "Candidate A: подготовка application payload", "Prepare application payload"),
    (13, "Candidate A: touchpoint и reminder", "Touchpoints and reminder"),
    (14, "Candidate A: daily actions и pipeline report", "Daily actions and pipeline report"),
    (15, "Candidate A: processed и material change", "Processed and material change"),
    (16, "Candidate B: intake и профиль", "Candidate B intake and profile"),
    (17, "Candidate B: вакансии, ranking, playbook, report", "Candidate B isolation flow"),
]

with state_path.open("r", encoding="utf-8") as fh:
    state = json.load(fh)

steps = state.get("steps", {})
by_number = {
    int(step.get("step_number", 0)): step
    for step in steps.values()
    if step.get("status") == "completed"
}

existing_manual = ""
if run_log_path.exists():
    current = run_log_path.read_text(encoding="utf-8")
    marker = "## Findings"
    if marker in current:
        existing_manual = current[current.index(marker):].rstrip() + "\n"

if not existing_manual:
    existing_manual = """## Findings

### Must-fix before Stage 2

- Нет открытых must-fix на момент последнего прогона.

### Acceptable prototype limitations

-

### Stage 2 enhancements

-
"""

lines = [
    "# Stage 1 Smoke Run Log",
    "",
    "Этот файл синхронизируется раннером по machine-state. Поле `Notes` и секцию `Findings` можно редактировать вручную.",
    "",
    "| Step | Runtime Step | Status | Notes |",
    "| --- | --- | --- | --- |",
]

for step_number, runtime_title, label in step_rows:
    step = by_number.get(step_number)
    status = "ok" if step else "todo"
    note = step.get("completed_at", "") if step else ""
    lines.append(f"| {step_number} | {label} | {status} | {note} |")

lines.extend(["", existing_manual.rstrip(), ""])
run_log_path.write_text("\n".join(lines), encoding="utf-8")
PY
}

is_step_completed() {
  local step_id="$1"
  python3 - "$STATE_FILE" "$step_id" <<'PY'
import json
import sys

path, step_id = sys.argv[1:3]
with open(path, "r", encoding="utf-8") as fh:
    data = json.load(fh)
step = data.get("steps", {}).get(step_id)
sys.exit(0 if step and step.get("status") == "completed" else 1)
PY
}

highest_completed_step() {
  python3 - "$STATE_FILE" <<'PY'
import json
import sys

with open(sys.argv[1], "r", encoding="utf-8") as fh:
    data = json.load(fh)
numbers = [
    int(step.get("step_number", 0))
    for step in data.get("steps", {}).values()
    if step.get("status") == "completed"
]
print(max(numbers) if numbers else 0)
PY
}

show_status() {
  ensure_state_file
  local highest
  highest="$(highest_completed_step)"
  printf "KIT_ROOT=%s\n" "$KIT_ROOT"
  printf "JSS_ROOT=%s\n" "$JSS_ROOT"
  printf "CONFIG_PATH=%s\n" "$CONFIG_PATH"
  printf "WORKSPACE_PATH=%s\n" "$WORKSPACE_PATH"
  printf "STATE_FILE=%s\n" "$STATE_FILE"
  printf "PLAN_ID=%s\n" "$PLAN_ID"
  printf "PLAN_VERSION=%s\n" "$PLAN_VERSION"
  if [[ -n "$CHECKLIST_PATH" ]]; then
    printf "CHECKLIST_PATH=%s\n" "$CHECKLIST_PATH"
  fi
  printf "\nЗавершённые шаги:\n"
  python3 - "$STATE_FILE" <<'PY'
import json
import sys

with open(sys.argv[1], "r", encoding="utf-8") as fh:
    data = json.load(fh)
steps = sorted(
    data.get("steps", {}).items(),
    key=lambda item: int(item[1].get("step_number", 0)),
)
if not steps:
    print("  нет")
else:
    for _, step in steps:
        print(f"  {step['step_number']}: {step['title']} ({step['completed_at']})")
PY
  if [[ "$highest" -ge 17 ]]; then
    printf "\nСледующий шаг: нет, сценарий завершён.\n"
  else
    printf "\nСледующий шаг: %s\n" "$((highest + 1))"
  fi
}

require_kit_root() {
  if [[ -z "$KIT_ROOT" ]]; then
    printf "Не удалось определить KIT_ROOT автоматически.\n" >&2
    printf "Запусти скрипт через symlink внутри smoke-kit или явно передай KIT_ROOT.\n" >&2
    exit 1
  fi
}

reset_state() {
  require_kit_root
  rm -f "$STATE_FILE"
  rm -rf "$OUTPUT_DIR"
  mkdir -p "$OUTPUT_DIR"
  create_state_template
  printf "Локальное состояние smoke-run очищено.\n"
}

self_test_state() {
  local tmp_dir
  local tmp_state
  tmp_dir="$(mktemp -d)"
  tmp_state="$tmp_dir/checkpoints.json"
  trap 'rm -rf "$tmp_dir"' RETURN

  python3 - "$tmp_state" "$PLAN_ID" "$PLAN_VERSION" <<'PY'
import json
import sys

path, plan_id, plan_version = sys.argv[1:4]
payload = {
    "plan_id": plan_id,
    "plan_version": plan_version,
    "kit_root": ".",
    "updated_at": "2026-01-01T00:00:00+00:00",
    "values": {
        "CANDIDATE_A_ID": "candidate-a",
        "CANDIDATE_A_DRAFT_MULTI_ID": "draft-multi",
        "CANDIDATE_A_TOP_VACANCY_ID": "vacancy-top",
        "CANDIDATE_A_APPLICATION_ID": "application-a",
        "CANDIDATE_B_DRAFT_ID": "draft-b",
    },
    "steps": {
        "step-01-candidate-context": {"status": "completed", "step_number": 1, "title": "one", "completed_at": "now"},
        "step-03-candidate-a-multi-source-enrichment": {"status": "completed", "step_number": 3, "title": "three", "completed_at": "now"},
        "step-09-candidate-a-rank": {"status": "completed", "step_number": 9, "title": "nine", "completed_at": "now"},
        "step-16-candidate-b-intake": {"status": "completed", "step_number": 16, "title": "sixteen", "completed_at": "now"},
    },
}
with open(path, "w", encoding="utf-8") as fh:
    json.dump(payload, fh, ensure_ascii=False, indent=2)
    fh.write("\n")
PY

  state_clear_from_file "$tmp_state" 9

  python3 - "$tmp_state" <<'PY'
import json
import sys

with open(sys.argv[1], "r", encoding="utf-8") as fh:
    data = json.load(fh)

steps = data["steps"]
values = data["values"]
assert "step-01-candidate-context" in steps
assert "step-03-candidate-a-multi-source-enrichment" in steps
assert "step-09-candidate-a-rank" not in steps
assert "step-16-candidate-b-intake" not in steps
assert values["CANDIDATE_A_ID"] == "candidate-a"
assert values["CANDIDATE_A_DRAFT_MULTI_ID"] == "draft-multi"
assert "CANDIDATE_A_TOP_VACANCY_ID" not in values
assert "CANDIDATE_A_APPLICATION_ID" not in values
assert "CANDIDATE_B_DRAFT_ID" not in values
PY

  printf "Machine-state self-test пройден: partial rerun cleanup работает.\n"
}

require_state() {
  local key="$1"
  if [[ -z "$(state_get_value "$key")" ]]; then
    printf "Отсутствует обязательное состояние: %s\n" "$key" >&2
    exit 1
  fi
}

require_file() {
  local path="$1"
  if [[ ! -f "$path" ]]; then
    printf "Не найден обязательный файл: %s\n" "$path" >&2
    exit 1
  fi
}

resolve_source_file() {
  local base="$1"
  local candidate
  for candidate in \
    "${base}.txt" \
    "${base}.md" \
    "${base}.pdf" \
    "${base}.docx"
  do
    if [[ -f "$candidate" ]]; then
      printf "%s" "$candidate"
      return 0
    fi
  done
  return 1
}

require_source_file() {
  local base="$1"
  local label="$2"
  local resolved
  resolved="$(resolve_source_file "$base")" || {
    printf "Не найден обязательный source-файл для %s. Проверял варианты: %s.{txt,md,pdf,docx}\n" "$label" "$base" >&2
    exit 1
  }
  printf "%s" "$resolved"
}

require_runtime_config() {
  require_file "$CONFIG_PATH"
  if grep -q "/absolute/path/to/project" "$CONFIG_PATH"; then
    printf "В конфиге всё ещё остались placeholder-пути: %s\n" "$CONFIG_PATH" >&2
    exit 1
  fi
}

pause_for_step() {
  local step="$1"
  local title="$2"
  printf "\n== Шаг %s: %s ==\n" "$step" "$title"
  read -r -p "Нажми Enter, чтобы продолжить, или Ctrl+C, чтобы остановиться... " _
}

json_get() {
  local file="$1"
  local path="$2"
  python3 - "$file" "$path" <<'PY'
import json
import sys

file_path, dotted = sys.argv[1], sys.argv[2]
with open(file_path, "r", encoding="utf-8") as fh:
    data = json.load(fh)
cur = data
for part in dotted.split("."):
    if isinstance(cur, list):
        cur = cur[int(part)]
    else:
        cur = cur[part]
if cur is None:
    print("")
elif isinstance(cur, (dict, list)):
    print(json.dumps(cur, ensure_ascii=False))
else:
    print(cur)
PY
}

run_json() {
  local name="$1"
  shift
  local output="$OUTPUT_DIR/${name}.json"
  "$@" | tee "$output"
  RUN_JSON_LAST_OUTPUT="$output"
}

candidate_cli() {
  PYTHONPATH="$JSS_ROOT/src:$PLAYGROUND_ROOT" \
    python3 -m job_search.interfaces.cli.candidate_cli \
    --config-path "$CONFIG_PATH" \
    --workspace-path "$WORKSPACE_PATH" \
    "$@"
}

vacancy_cli() {
  PYTHONPATH="$JSS_ROOT/src:$PLAYGROUND_ROOT" \
    python3 -m job_search.interfaces.cli.vacancy_cli \
    --config-path "$CONFIG_PATH" \
    --workspace-path "$WORKSPACE_PATH" \
    "$@"
}

iso_due_at() {
  python3 - <<'PY'
from datetime import datetime, timedelta, timezone
print((datetime.now(timezone.utc) + timedelta(days=7)).replace(microsecond=0).isoformat())
PY
}

step_1() {
  local title="Создание кандидатов и выбор активного контекста"
  pause_for_step 1 "$title"
  require_runtime_config

  run_json "step-01-candidate-a-create" candidate_cli create --display-name "Example Candidate"
  state_set_value "CANDIDATE_A_ID" "$(json_get "$RUN_JSON_LAST_OUTPUT" "candidate_id")"

  run_json "step-01-candidate-b-create" candidate_cli create --display-name "Second Example Candidate"
  state_set_value "CANDIDATE_B_ID" "$(json_get "$RUN_JSON_LAST_OUTPUT" "candidate_id")"

  run_json "step-01-select-candidate-a" candidate_cli select --candidate-id "$(state_get_value CANDIDATE_A_ID)" >/dev/null
  run_json "step-01-active" candidate_cli active >/dev/null
  run_json "step-01-list-candidates" candidate_cli list >/dev/null
  state_mark_completed "$STEP_ID_1" 1 "$title"
}

step_2() {
  local title="Candidate A: intake из одного источника"
  pause_for_step 2 "$title"
  require_runtime_config
  require_state "CANDIDATE_A_ID"
  local resume_path
  resume_path="$(require_source_file "$KIT_ROOT/candidate-a/resume-v1" "candidate-a/resume-v1")"

  run_json "step-02-ingest-resume-v1" candidate_cli ingest-file --candidate-id "$(state_get_value CANDIDATE_A_ID)" --source-kind resume --file-path "$resume_path"
  state_set_value "CANDIDATE_A_RESUME_V1_ARTIFACT_ID" "$(json_get "$RUN_JSON_LAST_OUTPUT" "artifact_id")"

  run_json "step-02-generate-draft-single" candidate_cli generate-draft --candidate-id "$(state_get_value CANDIDATE_A_ID)"
  state_set_value "CANDIDATE_A_DRAFT_SINGLE_ID" "$(json_get "$RUN_JSON_LAST_OUTPUT" "draft_id")"

  run_json "step-02-confirm-draft-single" candidate_cli confirm-draft --candidate-id "$(state_get_value CANDIDATE_A_ID)" --draft-id "$(state_get_value CANDIDATE_A_DRAFT_SINGLE_ID)" >/dev/null
  run_json "step-02-show-profile" candidate_cli show-profile --candidate-id "$(state_get_value CANDIDATE_A_ID)" >/dev/null
  state_mark_completed "$STEP_ID_2" 2 "$title"
}

step_3() {
  local title="Candidate A: обогащение из нескольких источников"
  pause_for_step 3 "$title"
  require_runtime_config
  require_state "CANDIDATE_A_ID"
  local resume_path linkedin_path profile_path
  resume_path="$(require_source_file "$KIT_ROOT/candidate-a/resume-v2" "candidate-a/resume-v2")"
  linkedin_path="$(require_source_file "$KIT_ROOT/candidate-a/linkedin-profile" "candidate-a/linkedin-profile")"
  profile_path="$(require_source_file "$KIT_ROOT/candidate-a/search-context" "candidate-a/search-context")"

  run_json "step-03-ingest-resume-v2" candidate_cli ingest-file --candidate-id "$(state_get_value CANDIDATE_A_ID)" --source-kind resume --file-path "$resume_path"
  state_set_value "CANDIDATE_A_RESUME_V2_ARTIFACT_ID" "$(json_get "$RUN_JSON_LAST_OUTPUT" "artifact_id")"

  run_json "step-03-ingest-linkedin" candidate_cli ingest-file --candidate-id "$(state_get_value CANDIDATE_A_ID)" --source-kind linkedin --file-path "$linkedin_path"
  state_set_value "CANDIDATE_A_LINKEDIN_ARTIFACT_ID" "$(json_get "$RUN_JSON_LAST_OUTPUT" "artifact_id")"

  run_json "step-03-ingest-search-context" candidate_cli ingest-file --candidate-id "$(state_get_value CANDIDATE_A_ID)" --source-kind profile --file-path "$profile_path"
  state_set_value "CANDIDATE_A_SEARCH_CONTEXT_ARTIFACT_ID" "$(json_get "$RUN_JSON_LAST_OUTPUT" "artifact_id")"

  run_json "step-03-generate-draft-multi" candidate_cli generate-draft --candidate-id "$(state_get_value CANDIDATE_A_ID)"
  state_set_value "CANDIDATE_A_DRAFT_MULTI_ID" "$(json_get "$RUN_JSON_LAST_OUTPUT" "draft_id")"
  run_json "step-03-show-latest-draft" candidate_cli show-latest-draft --candidate-id "$(state_get_value CANDIDATE_A_ID)" >/dev/null
  state_mark_completed "$STEP_ID_3" 3 "$title"
}

step_4() {
  local title="Candidate A: разрешение конфликтов и подтверждение"
  pause_for_step 4 "$title"
  require_runtime_config
  require_state "CANDIDATE_A_ID"
  require_state "CANDIDATE_A_DRAFT_MULTI_ID"

  printf "\nНеобязательный формат accepted-field:\n"
  printf "  current_title=VP Engineering;current_location=Barcelona, Spain\n\n"
  read -r -p "Accepted fields [оставь пустым, чтобы подтвердить с дефолтами]: " accepted_raw
  local -a args=()
  if [[ -n "$accepted_raw" ]]; then
    IFS=';' read -r -a pairs <<< "$accepted_raw"
    local pair trimmed
    for pair in "${pairs[@]}"; do
      trimmed="$(printf "%s" "$pair" | sed 's/^ *//; s/ *$//')"
      [[ -n "$trimmed" ]] && args+=(--accepted-field "$trimmed")
    done
  fi

  local -a confirm_cmd=(
    candidate_cli
    confirm-draft
    --candidate-id "$(state_get_value CANDIDATE_A_ID)"
    --draft-id "$(state_get_value CANDIDATE_A_DRAFT_MULTI_ID)"
  )
  if ((${#args[@]} > 0)); then
    confirm_cmd+=("${args[@]}")
  fi

  run_json "step-04-confirm-draft-multi" "${confirm_cmd[@]}" >/dev/null
  run_json "step-04-show-profile" candidate_cli show-profile --candidate-id "$(state_get_value CANDIDATE_A_ID)" >/dev/null
  python3 - "$RUN_JSON_LAST_OUTPUT" <<'PY'
import json
import sys

with open(sys.argv[1], "r", encoding="utf-8") as fh:
    profile = json.load(fh)
core = profile.get("core_profile") or {}
missing = [field for field in ("full_name", "current_title", "summary_text") if not core.get(field)]
if missing:
    raise SystemExit(f"После подтверждения профиля отсутствуют обязательные smoke-поля: {', '.join(missing)}")
PY
  run_json "step-04-show-sources" candidate_cli show-sources --candidate-id "$(state_get_value CANDIDATE_A_ID)" >/dev/null
  state_mark_completed "$STEP_ID_4" 4 "$title"
}

step_5() {
  local title="Candidate A: career pathing lite"
  pause_for_step 5 "$title"
  require_runtime_config
  require_state "CANDIDATE_A_ID"
  run_json "step-05-career-pathing" candidate_cli career-pathing-lite --candidate-id "$(state_get_value CANDIDATE_A_ID)" --target-role CTO --target-role "Head of Engineering" --target-role "VP Engineering"
  state_set_value "CANDIDATE_A_CAREER_ARTIFACT_ID" "$(json_get "$RUN_JSON_LAST_OUTPUT" "artifact_id")"
  state_mark_completed "$STEP_ID_5" 5 "$title"
}

step_6() {
  local title="Candidate A: job search playbook"
  pause_for_step 6 "$title"
  require_runtime_config
  require_state "CANDIDATE_A_ID"
  run_json "step-06-playbook" candidate_cli generate-playbook --candidate-id "$(state_get_value CANDIDATE_A_ID)"
  state_set_value "CANDIDATE_A_PLAYBOOK_ARTIFACT_ID" "$(json_get "$RUN_JSON_LAST_OUTPUT" "artifact_id")"
  state_mark_completed "$STEP_ID_6" 6 "$title"
}

step_7() {
  local title="Candidate A: генерация resume artifacts"
  pause_for_step 7 "$title"
  require_runtime_config
  require_state "CANDIDATE_A_ID"
  run_json "step-07-resume-cto" candidate_cli generate-resume --candidate-id "$(state_get_value CANDIDATE_A_ID)" --language en --target-role CTO >/dev/null
  run_json "step-07-resume-hoe" candidate_cli generate-resume --candidate-id "$(state_get_value CANDIDATE_A_ID)" --language en --target-role "Head of Engineering" >/dev/null
  run_json "step-07-positioning-cto" candidate_cli generate-positioning-brief --candidate-id "$(state_get_value CANDIDATE_A_ID)" --target-role CTO --language en >/dev/null
  run_json "step-07-positioning-hoe" candidate_cli generate-positioning-brief --candidate-id "$(state_get_value CANDIDATE_A_ID)" --target-role "Head of Engineering" --language en >/dev/null
  state_mark_completed "$STEP_ID_7" 7 "$title"
}

step_8() {
  local title="Candidate A: импорт вакансий"
  pause_for_step 8 "$title"
  require_runtime_config
  require_state "CANDIDATE_A_ID"
  require_file "$KIT_ROOT/vacancies/candidate-a-batch-01.json"
  run_json "step-08-import-vacancies-a" vacancy_cli import-json --candidate-id "$(state_get_value CANDIDATE_A_ID)" --source-kind manual --items-path "$KIT_ROOT/vacancies/candidate-a-batch-01.json" >/dev/null
  run_json "step-08-list-vacancies-a" vacancy_cli list --candidate-id "$(state_get_value CANDIDATE_A_ID)" >/dev/null
  state_mark_completed "$STEP_ID_8" 8 "$title"
}

step_9() {
  local title="Candidate A: ranking вакансий"
  pause_for_step 9 "$title"
  require_runtime_config
  require_state "CANDIDATE_A_ID"
  run_json "step-09-rank-a" vacancy_cli rank --candidate-id "$(state_get_value CANDIDATE_A_ID)"
  python3 - "$RUN_JSON_LAST_OUTPUT" <<'PY'
import json
import sys

with open(sys.argv[1], "r", encoding="utf-8") as fh:
    ranked = json.load(fh)
for index, item in enumerate(ranked):
    company = str(item.get("company_name") or "").casefold()
    if "sberbank" in company and item.get("fit_label") != "skip":
        raise SystemExit("Sberbank должен быть отфильтрован company_avoid_list и иметь fit_label=skip")
    if index < 2 and item.get("fit_label") == "skip":
        raise SystemExit("Top-2 ranking не должен содержать skip-вакансию")
PY
  state_set_value "CANDIDATE_A_TOP_VACANCY_ID" "$(json_get "$RUN_JSON_LAST_OUTPUT" "0.canonical_vacancy_id")"
  state_set_value "CANDIDATE_A_TOP_VACANCY_ROLE" "$(json_get "$RUN_JSON_LAST_OUTPUT" "0.role_title")"
  local material_change_vacancy_id
  material_change_vacancy_id="$(python3 - "$RUN_JSON_LAST_OUTPUT" <<'PY'
import json
import sys

with open(sys.argv[1], "r", encoding="utf-8") as fh:
    ranked = json.load(fh)
for item in ranked:
    if str(item.get("company_name") or "").casefold() == "good corp":
        print(item["canonical_vacancy_id"])
        break
PY
)"
  if [[ -n "$material_change_vacancy_id" ]]; then
    state_set_value "CANDIDATE_A_MATERIAL_CHANGE_VACANCY_ID" "$material_change_vacancy_id"
  else
    run_json "step-09-list-for-material-change-a" vacancy_cli list --candidate-id "$(state_get_value CANDIDATE_A_ID)" >/dev/null
    material_change_vacancy_id="$(python3 - "$RUN_JSON_LAST_OUTPUT" <<'PY'
import json
import sys

with open(sys.argv[1], "r", encoding="utf-8") as fh:
    vacancies = json.load(fh)
for item in vacancies:
    if str(item.get("company_name") or "").casefold() == "good corp":
        print(item["canonical_vacancy_id"])
        break
PY
)"
    if [[ -n "$material_change_vacancy_id" ]]; then
      state_set_value "CANDIDATE_A_MATERIAL_CHANGE_VACANCY_ID" "$material_change_vacancy_id"
    fi
  fi
  if python3 - "$RUN_JSON_LAST_OUTPUT" <<'PY'
import json
import sys
with open(sys.argv[1], 'r', encoding='utf-8') as fh:
    data = json.load(fh)
sys.exit(0 if len(data) > 1 else 1)
PY
  then
    state_set_value "CANDIDATE_A_SECOND_VACANCY_ID" "$(json_get "$RUN_JSON_LAST_OUTPUT" "1.canonical_vacancy_id")"
  fi
  state_mark_completed "$STEP_ID_9" 9 "$title"
}

step_10() {
  local title="Candidate A: перевод вакансий в shortlist"
  pause_for_step 10 "$title"
  require_runtime_config
  require_state "CANDIDATE_A_ID"
  require_state "CANDIDATE_A_TOP_VACANCY_ID"
  run_json "step-10-shortlist-top1" vacancy_cli shortlist --candidate-id "$(state_get_value CANDIDATE_A_ID)" --canonical-vacancy-id "$(state_get_value CANDIDATE_A_TOP_VACANCY_ID)" >/dev/null
  if [[ -n "$(state_get_value CANDIDATE_A_SECOND_VACANCY_ID)" ]]; then
    run_json "step-10-shortlist-top2" vacancy_cli shortlist --candidate-id "$(state_get_value CANDIDATE_A_ID)" --canonical-vacancy-id "$(state_get_value CANDIDATE_A_SECOND_VACANCY_ID)" >/dev/null
  fi
  run_json "step-10-list-shortlisted" vacancy_cli list --candidate-id "$(state_get_value CANDIDATE_A_ID)" --workflow-stage shortlisted >/dev/null
  state_set_value "CANDIDATE_A_PRIMARY_VACANCY_ID" "$(state_get_value CANDIDATE_A_TOP_VACANCY_ID)"
  state_mark_completed "$STEP_ID_10" 10 "$title"
}

step_11() {
  local title="Candidate A: создание application draft"
  pause_for_step 11 "$title"
  require_runtime_config
  require_state "CANDIDATE_A_ID"
  require_state "CANDIDATE_A_PRIMARY_VACANCY_ID"
  require_state "CANDIDATE_A_TOP_VACANCY_ROLE"
  run_json "step-11-application-draft" vacancy_cli create-application-draft --candidate-id "$(state_get_value CANDIDATE_A_ID)" --canonical-vacancy-id "$(state_get_value CANDIDATE_A_PRIMARY_VACANCY_ID)" --language en
  python3 - "$RUN_JSON_LAST_OUTPUT" "$(state_get_value CANDIDATE_A_TOP_VACANCY_ROLE)" <<'PY'
import json
import sys
from pathlib import Path

with open(sys.argv[1], "r", encoding="utf-8") as fh:
    result = json.load(fh)
expected_role = sys.argv[2]
markdown = Path(result["storage_path"]).read_text(encoding="utf-8")
if expected_role and expected_role not in markdown:
    raise SystemExit(f"Application draft does not mention selected vacancy role: {expected_role}")
PY
  state_set_value "CANDIDATE_A_APPLICATION_ID" "$(json_get "$RUN_JSON_LAST_OUTPUT" "application_id")"
  state_set_value "CANDIDATE_A_MESSAGE_ARTIFACT_ID" "$(json_get "$RUN_JSON_LAST_OUTPUT" "artifact_id")"
  state_mark_completed "$STEP_ID_11" 11 "$title"
}

step_12() {
  local title="Candidate A: подготовка application payload"
  pause_for_step 12 "$title"
  require_runtime_config
  require_state "CANDIDATE_A_ID"
  require_state "CANDIDATE_A_PRIMARY_VACANCY_ID"
  run_json "step-12-application-payload" vacancy_cli prepare-application-payload --candidate-id "$(state_get_value CANDIDATE_A_ID)" --canonical-vacancy-id "$(state_get_value CANDIDATE_A_PRIMARY_VACANCY_ID)" --language en
  state_set_value "CANDIDATE_A_PAYLOAD_APPLICATION_ID" "$(json_get "$RUN_JSON_LAST_OUTPUT" "application_id")"
  state_set_value "CANDIDATE_A_PAYLOAD_MESSAGE_ARTIFACT_ID" "$(json_get "$RUN_JSON_LAST_OUTPUT" "message_artifact_id")"
  state_set_value "CANDIDATE_A_PAYLOAD_RESUME_ARTIFACT_ID" "$(json_get "$RUN_JSON_LAST_OUTPUT" "resume_artifact_id")"
  state_mark_completed "$STEP_ID_12" 12 "$title"
}

step_13() {
  local title="Candidate A: touchpoint и reminder"
  pause_for_step 13 "$title"
  require_runtime_config
  require_state "CANDIDATE_A_ID"
  require_state "CANDIDATE_A_PRIMARY_VACANCY_ID"
  local application_id message_artifact_id due_at
  application_id="$(state_get_value CANDIDATE_A_PAYLOAD_APPLICATION_ID)"
  [[ -z "$application_id" ]] && application_id="$(state_get_value CANDIDATE_A_APPLICATION_ID)"
  message_artifact_id="$(state_get_value CANDIDATE_A_PAYLOAD_MESSAGE_ARTIFACT_ID)"
  [[ -z "$message_artifact_id" ]] && message_artifact_id="$(state_get_value CANDIDATE_A_MESSAGE_ARTIFACT_ID)"
  if [[ -z "$application_id" || -z "$message_artifact_id" ]]; then
    printf "Для шага touchpoint не хватает application id или message artifact id.\n" >&2
    exit 1
  fi
  due_at="$(iso_due_at)"
  run_json "step-13-touchpoint" vacancy_cli create-touchpoint --candidate-id "$(state_get_value CANDIDATE_A_ID)" --canonical-vacancy-id "$(state_get_value CANDIDATE_A_PRIMARY_VACANCY_ID)" --application-id "$application_id" --message-artifact-id "$message_artifact_id" --channel email --direction outgoing --touchpoint-state sent --contact-name recruiter@example.com --notes "Sent tailored application message." --follow-up-due-at "$due_at"
  state_set_value "CANDIDATE_A_TOUCHPOINT_ID" "$(json_get "$RUN_JSON_LAST_OUTPUT" "touchpoint.touchpoint_id")"
  state_set_value "CANDIDATE_A_REMINDER_ID" "$(json_get "$RUN_JSON_LAST_OUTPUT" "reminder.reminder_id")"
  run_json "step-13-list-touchpoints" vacancy_cli list-touchpoints --candidate-id "$(state_get_value CANDIDATE_A_ID)" --canonical-vacancy-id "$(state_get_value CANDIDATE_A_PRIMARY_VACANCY_ID)" >/dev/null
  state_mark_completed "$STEP_ID_13" 13 "$title"
}

step_14() {
  local title="Candidate A: daily actions и pipeline report"
  pause_for_step 14 "$title"
  require_runtime_config
  require_state "CANDIDATE_A_ID"
  run_json "step-14-daily-actions-a" vacancy_cli daily-actions --candidate-id "$(state_get_value CANDIDATE_A_ID)" >/dev/null
  run_json "step-14-pipeline-report-a" vacancy_cli pipeline-report --candidate-id "$(state_get_value CANDIDATE_A_ID)" >/dev/null
  state_mark_completed "$STEP_ID_14" 14 "$title"
}

step_15() {
  local title="Candidate A: processed и material change"
  pause_for_step 15 "$title"
  require_runtime_config
  require_state "CANDIDATE_A_ID"
  require_state "CANDIDATE_A_MATERIAL_CHANGE_VACANCY_ID"
  require_file "$KIT_ROOT/vacancies/candidate-a-batch-02-material-change.json"
  local material_change_vacancy_id
  material_change_vacancy_id="$(state_get_value CANDIDATE_A_MATERIAL_CHANGE_VACANCY_ID)"
  run_json "step-15-mark-processed" vacancy_cli mark-processed --candidate-id "$(state_get_value CANDIDATE_A_ID)" --canonical-vacancy-id "$material_change_vacancy_id" >/dev/null
  run_json "step-15-import-material-change" vacancy_cli import-json --candidate-id "$(state_get_value CANDIDATE_A_ID)" --source-kind manual --items-path "$KIT_ROOT/vacancies/candidate-a-batch-02-material-change.json" >/dev/null
  python3 - "$RUN_JSON_LAST_OUTPUT" "$material_change_vacancy_id" <<'PY'
import json
import sys

with open(sys.argv[1], "r", encoding="utf-8") as fh:
    imported = json.load(fh).get("imported", [])
expected_id = sys.argv[2]
if not imported or imported[0].get("canonical_vacancy_id") != expected_id:
    raise SystemExit("Material-change import обновил не ту canonical vacancy, которая была помечена processed")
PY
  run_json "step-15-show-vacancy" vacancy_cli show --candidate-id "$(state_get_value CANDIDATE_A_ID)" --canonical-vacancy-id "$material_change_vacancy_id" >/dev/null
  state_mark_completed "$STEP_ID_15" 15 "$title"
}

step_16() {
  local title="Candidate B: intake и профиль"
  pause_for_step 16 "$title"
  require_runtime_config
  local resume_path linkedin_path
  resume_path="$(require_source_file "$KIT_ROOT/candidate-b/resume-v1" "candidate-b/resume-v1")"
  linkedin_path="$(require_source_file "$KIT_ROOT/candidate-b/linkedin-profile" "candidate-b/linkedin-profile")"

  if [[ -z "$(state_get_value CANDIDATE_B_ID)" ]]; then
    run_json "step-16-candidate-b-create" candidate_cli create --display-name "Second Example Candidate"
    state_set_value "CANDIDATE_B_ID" "$(json_get "$RUN_JSON_LAST_OUTPUT" "candidate_id")"
  fi

  run_json "step-16-select-candidate-b" candidate_cli select --candidate-id "$(state_get_value CANDIDATE_B_ID)" >/dev/null
  run_json "step-16-ingest-resume-b" candidate_cli ingest-file --candidate-id "$(state_get_value CANDIDATE_B_ID)" --source-kind resume --file-path "$resume_path" >/dev/null
  run_json "step-16-ingest-linkedin-b" candidate_cli ingest-file --candidate-id "$(state_get_value CANDIDATE_B_ID)" --source-kind linkedin --file-path "$linkedin_path" >/dev/null
  run_json "step-16-generate-draft-b" candidate_cli generate-draft --candidate-id "$(state_get_value CANDIDATE_B_ID)"
  state_set_value "CANDIDATE_B_DRAFT_ID" "$(json_get "$RUN_JSON_LAST_OUTPUT" "draft_id")"
  run_json "step-16-confirm-draft-b" candidate_cli confirm-draft --candidate-id "$(state_get_value CANDIDATE_B_ID)" --draft-id "$(state_get_value CANDIDATE_B_DRAFT_ID)" >/dev/null
  state_mark_completed "$STEP_ID_16" 16 "$title"
}

step_17() {
  local title="Candidate B: вакансии, ranking, playbook, report"
  pause_for_step 17 "$title"
  require_runtime_config
  require_state "CANDIDATE_B_ID"
  require_file "$KIT_ROOT/vacancies/candidate-b-batch-01.json"
  run_json "step-17-import-vacancies-b" vacancy_cli import-json --candidate-id "$(state_get_value CANDIDATE_B_ID)" --source-kind manual --items-path "$KIT_ROOT/vacancies/candidate-b-batch-01.json" >/dev/null
  run_json "step-17-rank-b" vacancy_cli rank --candidate-id "$(state_get_value CANDIDATE_B_ID)" >/dev/null
  run_json "step-17-playbook-b" candidate_cli generate-playbook --candidate-id "$(state_get_value CANDIDATE_B_ID)" >/dev/null
  run_json "step-17-pipeline-report-b" vacancy_cli pipeline-report --candidate-id "$(state_get_value CANDIDATE_B_ID)" >/dev/null
  state_mark_completed "$STEP_ID_17" 17 "$title"
}

step_id_for_number() {
  local step="$1"
  case "$step" in
    1) printf "%s" "$STEP_ID_1" ;;
    2) printf "%s" "$STEP_ID_2" ;;
    3) printf "%s" "$STEP_ID_3" ;;
    4) printf "%s" "$STEP_ID_4" ;;
    5) printf "%s" "$STEP_ID_5" ;;
    6) printf "%s" "$STEP_ID_6" ;;
    7) printf "%s" "$STEP_ID_7" ;;
    8) printf "%s" "$STEP_ID_8" ;;
    9) printf "%s" "$STEP_ID_9" ;;
    10) printf "%s" "$STEP_ID_10" ;;
    11) printf "%s" "$STEP_ID_11" ;;
    12) printf "%s" "$STEP_ID_12" ;;
    13) printf "%s" "$STEP_ID_13" ;;
    14) printf "%s" "$STEP_ID_14" ;;
    15) printf "%s" "$STEP_ID_15" ;;
    16) printf "%s" "$STEP_ID_16" ;;
    17) printf "%s" "$STEP_ID_17" ;;
    *) return 1 ;;
  esac
}

run_steps() {
  local start="$1"
  local redo_from="${2:-0}"
  local step step_id
  for step in $(seq "$start" 17); do
    step_id="$(step_id_for_number "$step")"
    if [[ "$redo_from" -eq 0 ]] && is_step_completed "$step_id"; then
      printf "Шаг %s уже завершён, пропускаю.\n" "$step"
      continue
    fi
    "step_${step}"
  done
  printf "\nSmoke-раннер завершил сценарий до максимально доступного шага.\n"
  if [[ -n "$CHECKLIST_PATH" ]]; then
    printf "Для ручной верификации используй checklist:\n"
    printf "  %s\n" "$CHECKLIST_PATH"
  fi
  printf "Результаты и находки фиксируй в:\n"
  printf "  %s/run-log.md\n" "$KIT_ROOT"
}

main() {
  local arg="${1:-}"
  local start
  local redo_from=0

  case "$arg" in
    -h|--help)
      usage
      exit 0
      ;;
    status)
      require_kit_root
      show_status
      exit 0
      ;;
    reset)
      require_kit_root
      reset_state
      exit 0
      ;;
    self-test-state)
      self_test_state
      exit 0
      ;;
  esac

  ensure_state_file

  start="$(( $(highest_completed_step) + 1 ))"
  if [[ "$start" -gt 17 ]]; then
    start=17
  fi

  if [[ "$arg" == "--from" ]]; then
    [[ $# -eq 2 ]] || { usage; exit 1; }
    start="$2"
  elif [[ "$arg" == "--redo-from" ]]; then
    [[ $# -eq 2 ]] || { usage; exit 1; }
    start="$2"
    redo_from=1
    state_clear_from_step "$start"
  fi

  if [[ "$start" -lt 1 || "$start" -gt 17 ]]; then
    printf "Некорректный стартовый шаг: %s\n" "$start" >&2
    exit 1
  fi

  run_steps "$start" "$redo_from"
}

main "$@"
