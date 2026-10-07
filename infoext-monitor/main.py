#!/usr/bin/env python3
"""Local scheduled monitor for the official InfoExt expediente status form."""

from __future__ import annotations

import argparse
import fcntl
import logging
import json
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from config import ConfigurationError, PROJECT_ROOT, RUNTIME_ROOT, Settings, load_settings

# Installed runtimes carry common/ locally; source runs use its sibling.
SHARED_ROOT = RUNTIME_ROOT if (RUNTIME_ROOT / "common").is_dir() else RUNTIME_ROOT.parent
if str(SHARED_ROOT) not in sys.path:
    sys.path.insert(0, str(SHARED_ROOT))

from common import process as common_process, ttl_runner
from captcha_solver import create_captcha_solver
from infoext import InfoExtClient, InfoExtError, InfoExtResult, normalize_status
from notifier import Notifier, NotifierError, TelegramConnectorNotifier
from state import StateError, StateStore


LOCAL_TZ = ZoneInfo("Europe/Madrid")


def now() -> datetime:
    return datetime.now(LOCAL_TZ)


def timestamp() -> str:
    return now().isoformat(timespec="seconds")


def configure_logging(project_root: Path) -> logging.Logger:
    log_dir = project_root / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger("infoext")
    logger.setLevel(logging.INFO)
    logger.handlers.clear()
    handler = logging.FileHandler(log_dir / "infoext.log", encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    logger.addHandler(handler)
    return logger


def debug_directory(project_root: Path) -> Path:
    return project_root / "debug" / now().strftime("%Y-%m-%d_%H-%M-%S")


def captcha_sample_directory(project_root: Path) -> Path:
    return project_root / "data" / "captcha"


def submitted_captcha_response_directory(project_root: Path) -> Path:
    return project_root / "debug" / "captcha-responses"


def format_status_notification(payload: dict[str, str]) -> str:
    checked_at = datetime.fromisoformat(payload["detected_at"]).astimezone(LOCAL_TZ)
    lines = [
        "InfoExt: статус expediente изменился",
        "",
        f"{payload['old_status']} → {payload['new_status']}",
        "",
        f"Проверено: {checked_at:%d.%m.%Y %H:%M}",
    ]
    if payload.get("fecha_resolucion"):
        lines.extend(["", f"Дата решения: {payload['fecha_resolucion']}"])
    return "\n".join(lines)


def format_current_notification(payload: dict[str, str]) -> str:
    checked_at = datetime.fromisoformat(payload["detected_at"]).astimezone(LOCAL_TZ)
    lines = [
        "InfoExt: статус разового запроса" if payload.get("query") == "one-time" else "InfoExt: текущий статус expediente",
        "",
        payload["status"],
        "",
        f"Проверено: {checked_at:%d.%m.%Y %H:%M}",
    ]
    if payload.get("fecha_resolucion"):
        lines.extend(["", f"Дата решения: {payload['fecha_resolucion']}"])
    return "\n".join(lines)


def format_health_notification(payload: dict[str, str]) -> str:
    if payload["event"] == "failure":
        return f"InfoExt monitor: не удалось проверить expediente {payload['count']} раза подряд."
    lines = ["InfoExt monitor: проверка снова работает."]
    if payload.get("old_status") and payload.get("new_status"):
        lines.extend(["", "Статус expediente изменился:",
                      f"{payload['old_status']} → {payload['new_status']}"])
    else:
        lines.append(f"Текущий статус: {payload['status']}")
    if payload.get("detected_at"):
        checked_at = datetime.fromisoformat(payload["detected_at"]).astimezone(LOCAL_TZ)
        lines.extend(["", f"Проверено: {checked_at:%d.%m.%Y %H:%M}"])
    if payload.get("fecha_resolucion"):
        lines.extend(["", f"Дата решения: {payload['fecha_resolucion']}"])
    return "\n".join(lines)


def notification_text(payload: dict[str, str]) -> str:
    if payload["kind"] == "status":
        text = format_status_notification(payload)
    elif payload["kind"] == "current":
        text = format_current_notification(payload)
    else:
        text = format_health_notification(payload)
    if payload.get("nie"):
        lines = text.splitlines()
        lines.insert(1, f"NIE: {payload['nie']}")
        return "\n".join(lines)
    return text


def deliver_pending(
    state: dict[str, object], store: StateStore, notifier: Notifier, logger: logging.Logger
) -> None:
    pending = list(state["pending_notifications"])
    for event in pending:
        try:
            notifier.send(notification_text(event))
        except NotifierError as exc:
            logger.warning("Telegram notification remains pending: %s", exc)
            return
        state["pending_notifications"].remove(event)
        store.save(state)
        logger.info("Telegram notification delivered: %s", event["kind"])


def record_failure(
    state: dict[str, object],
    store: StateStore,
    error: Exception,
    logger: logging.Logger,
    failure_alert_threshold: int,
    *,
    identity: dict[str, str],
) -> None:
    failure_identity = state.get("failure_request_identity") or state.get("request_identity")
    if failure_identity != identity:
        state.update(consecutive_failures=0, failure_alerted=False)
    state["failure_request_identity"] = identity
    failures = int(state.get("consecutive_failures", 0)) + 1
    state["consecutive_failures"] = failures
    if failures >= failure_alert_threshold and not state.get("failure_alerted", False):
        state["failure_alerted"] = True
        state["pending_notifications"].append(
            {"kind": "health", "event": "failure", "nie": identity["nie"],
             "count": str(failures), "detected_at": timestamp()}
        )
    store.save(state)
    logger.error("InfoExt check failed (%s): %s", error.__class__.__name__, error)


def handle_check_failure(
    state: dict[str, object],
    store: StateStore,
    notifier: Notifier,
    error: Exception,
    logger: logging.Logger,
    failure_alert_threshold: int,
    *,
    identity: dict[str, str],
) -> None:
    """Persist the failed check before immediately attempting its health alert."""
    record_failure(state, store, error, logger, failure_alert_threshold, identity=identity)
    deliver_pending(state, store, notifier, logger)


def request_identity(nie: str, fecha_presentacion: str) -> dict[str, str]:
    return {"nie": nie.strip().upper(), "fecha_presentacion": fecha_presentacion}


def previous_status_for_request(state: dict[str, object], identity: dict[str, str]) -> object:
    return state.get("status") if state.get("request_identity") == identity else None


def record_success(
    state: dict[str, object],
    store: StateStore,
    result: InfoExtResult,
    checked_at: str,
    *,
    notify_on_unchanged_status: bool,
    nie: str,
    fecha_presentacion: str,
) -> bool:
    identity = request_identity(nie, fecha_presentacion)
    previous = previous_status_for_request(state, identity)
    changed = bool(previous and normalize_status(str(previous)) != normalize_status(result.status))
    failure_identity = state.get("failure_request_identity") or state.get("request_identity")
    had_failure_alert = failure_identity == identity and bool(state.get("failure_alerted", False))

    state.update(
        {
            "request_identity": identity,
            "failure_request_identity": identity,
            "last_successful_check": checked_at,
            "status": result.status,
            "expediente": result.expediente,
            "fecha_resolucion": result.fecha_resolucion,
            "consecutive_failures": 0,
            "failure_alerted": False,
        }
    )
    if had_failure_alert:
        recovery = {
            "kind": "health",
            "nie": nie,
            "event": "recovery",
            "status": result.status,
            "fecha_resolucion": result.fecha_resolucion or "",
            "detected_at": checked_at,
        }
        if changed:
            recovery.update(old_status=str(previous), new_status=result.status)
        state["pending_notifications"].append(recovery)
    elif changed:
        state["pending_notifications"].append(
            {
                "kind": "status",
                "nie": nie,
                "old_status": str(previous),
                "new_status": result.status,
                "fecha_resolucion": result.fecha_resolucion or "",
                "detected_at": checked_at,
            }
        )
    elif notify_on_unchanged_status:
        state["pending_notifications"].append(
            {
                "kind": "current",
                "nie": nie,
                "status": result.status,
                "fecha_resolucion": result.fecha_resolucion or "",
                "detected_at": checked_at,
            }
        )
    # The state with a new pending event is committed before any external send.
    store.save(state)
    store.append_history(
        {
            "timestamp": checked_at,
            "request_identity": identity,
            "status": result.status,
            "expediente": result.expediente,
            "fecha_resolucion": result.fecha_resolucion,
        }
    )
    return changed


def reserve_portal_check(
    state: dict[str, object],
    store: StateStore,
    minimum_interval_seconds: int,
    *,
    started_at: datetime | None = None,
) -> bool:
    """Atomically reserve the next permitted visit to the InfoExt portal."""
    current = started_at or now()
    previous = state.get("last_portal_check_started_at")
    if previous is not None:
        try:
            previous_start = datetime.fromisoformat(str(previous))
        except ValueError as exc:
            raise StateError("data/state.json last_portal_check_started_at must be an ISO timestamp.") from exc
        if previous_start.tzinfo is None:
            raise StateError("data/state.json last_portal_check_started_at must include a timezone.")
        elapsed_seconds = (current - previous_start.astimezone(current.tzinfo)).total_seconds()
        if elapsed_seconds < minimum_interval_seconds:
            return False
    state["last_portal_check_started_at"] = current.isoformat(timespec="seconds")
    store.save(state)
    return True


def run_check(args: argparse.Namespace, settings: Settings, logger: logging.Logger) -> int:
    store = StateStore(settings.project_root)
    state = store.load()
    notifier = TelegramConnectorNotifier(settings.telegram_connector_dir)
    deliver_pending(state, store, notifier, logger)
    if not reserve_portal_check(state, store, settings.portal_min_check_interval_seconds):
        logger.info("InfoExt check skipped because the portal minimum interval has not elapsed.")
        print("InfoExt check skipped: portal minimum interval has not elapsed.")
        return 0
    logger.info("Starting check for NIE %s", settings.masked_nie)
    captcha_dir = captcha_sample_directory(settings.project_root)
    submission_artifact_dir = (
        submitted_captcha_response_directory(settings.project_root)
        if settings.debug_submitted_captcha_responses and not args.query_stdin
        else None
    )
    process_config = common_process.load_process_config()
    started_continuous = common_process.continuous_time()
    started_awake = common_process.awake_time()

    def interrupted_by_sleep() -> bool:
        if common_process.sleep_elapsed(started_continuous, started_awake) < process_config.sleep_interruption_threshold_seconds:
            return False
        logger.warning("InfoExt check interrupted by host sleep; status and failure counters preserved.")
        print("InfoExt check interrupted: host sleep.", file=sys.stderr)
        return True

    try:
        result = InfoExtClient(settings, create_captcha_solver(settings), logger.info).check(
            captcha_dir=captcha_dir,
            submission_artifact_dir=submission_artifact_dir,
        )
    except InfoExtError as exc:
        if interrupted_by_sleep():
            return process_config.sleep_interruption_exit_code
        if args.query_stdin:
            logger.error("One-time InfoExt query failed (%s): %s", exc.__class__.__name__, exc)
        else:
            handle_check_failure(
                state, store, notifier, exc, logger, settings.failure_alert_threshold,
                identity=request_identity(settings.nie, settings.fecha_presentacion),
            )
        print(f"InfoExt check failed: {exc}", file=sys.stderr)
        if captcha_dir.exists():
            print(f"CAPTCHA samples: {captcha_dir.relative_to(settings.project_root)}", file=sys.stderr)
        return 1

    if interrupted_by_sleep():
        return process_config.sleep_interruption_exit_code
    checked_at = timestamp()
    previous = None if args.query_stdin else previous_status_for_request(
        state, request_identity(settings.nie, settings.fecha_presentacion),
    )
    pending_before_check = len(state["pending_notifications"])
    if args.query_stdin:
        state["pending_notifications"].append({
            "kind": "current", "query": "one-time", "status": result.status,
            "nie": settings.nie,
            "fecha_resolucion": result.fecha_resolucion or "", "detected_at": checked_at,
        })
        store.save(state)
        changed = False
    else:
        changed = record_success(
            state, store, result, checked_at,
            notify_on_unchanged_status=settings.notify_on_unchanged_status or args.notify,
            nie=settings.nie,
            fecha_presentacion=settings.fecha_presentacion,
        )
    logger.info("Status: %s", result.status)
    logger.info("Status %s", "changed" if changed else "unchanged")
    check_notifications = state["pending_notifications"][pending_before_check:]
    deliver_pending(state, store, notifier, logger)
    checked = datetime.fromisoformat(checked_at).astimezone(LOCAL_TZ)
    print("InfoExt check")
    print(f"Timestamp: {checked:%Y-%m-%d %H:%M:%S}")
    print(f"Status: {result.status}")
    print(f"Previous status: {previous or '-'}")
    print(f"Changed: {'yes' if changed else 'no'}")
    print(f"Captcha attempts: {result.captcha_attempts}")
    if not check_notifications:
        print("Telegram notification: not requested")
    elif any(event in state["pending_notifications"] for event in check_notifications):
        print("Telegram notification: pending")
    else:
        print("Telegram notification: delivered")
    if captcha_dir.exists():
        print(f"CAPTCHA samples: {captcha_dir.relative_to(settings.project_root)}")
    return 0


def run_debug(settings: Settings, logger: logging.Logger) -> int:
    store = StateStore(settings.project_root)
    state = store.load()
    if not reserve_portal_check(state, store, settings.portal_min_check_interval_seconds):
        logger.info("InfoExt CAPTCHA debug skipped because the portal minimum interval has not elapsed.")
        print("InfoExt CAPTCHA debug skipped: portal minimum interval has not elapsed.")
        return 0
    debug_dir = debug_directory(settings.project_root)
    logger.info("Starting CAPTCHA debug for NIE %s", settings.masked_nie)
    try:
        assessment = InfoExtClient(settings, create_captcha_solver(settings), logger.info).debug_captcha(
            debug_dir
        )
    except InfoExtError as exc:
        logger.error("CAPTCHA debug failed (%s): %s", exc.__class__.__name__, exc)
        print(f"InfoExt CAPTCHA debug failed: {exc}", file=sys.stderr)
        return 1

    print("InfoExt CAPTCHA debug")
    print("CONSULTAR submitted: no")
    selected = assessment.solution.text or "[empty]"
    confidence = assessment.solution.confidence
    confidence_text = "not reported" if confidence is None else f"{confidence:.1f}"
    print("CAPTCHAs captured: 1")
    print(
        f"CAPTCHA 1: selected={selected}; confidence={confidence_text}; "
        f"valid variants={assessment.valid_length_variants}/{len(assessment.variants)}; "
        f"consensus={assessment.consensus_votes}/{len(assessment.variants)}"
    )
    print(f"OCR report: {debug_dir.relative_to(settings.project_root) / 'ocr-report.json'}")
    return 0


def acquire_lock(project_root: Path):
    data_dir = project_root / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    handle = (data_dir / "monitor.lock").open("w", encoding="utf-8")
    try:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        handle.close()
        return None
    return handle


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Check InfoExt expediente status.")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--check-now", action="store_true", help="Run one InfoExt check now.")
    mode.add_argument("--test-telegram", action="store_true", help="Send a test through telegram_connector.")
    parser.add_argument(
        "--notify",
        action="store_true",
        help="Send a current-status report when unchanged, even if disabled in local config.",
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        help="Capture and assess the first CAPTCHA visibly without submitting the form.",
    )
    parser.add_argument("--_ttl-worker", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--query-stdin", action="store_true", help=argparse.SUPPRESS)
    return parser


def validate_args(parser: argparse.ArgumentParser, args: argparse.Namespace) -> None:
    if args.debug and not args.check_now:
        parser.error("--debug requires --check-now.")
    if args.query_stdin and (not args.check_now or args.debug):
        parser.error("--query-stdin requires --check-now without --debug.")


def run_with_timeout(args: argparse.Namespace, settings: Settings, logger: logging.Logger) -> int:
    """Supervise manual modes through the same hard TTL used by launchd."""
    process_config = common_process.load_process_config()
    command = [sys.executable, str(RUNTIME_ROOT / "main.py"), "--_ttl-worker"]
    command.append("--test-telegram" if args.test_telegram else "--check-now")
    if args.debug:
        command.append("--debug")
    if args.notify:
        command.append("--notify")
    if args.query_stdin:
        command.append("--query-stdin")
    logger.info("Starting supervised command; whole-run timeout: %s seconds", settings.timeout_seconds)
    exit_code = ttl_runner.run_with_ttl(
        command,
        timeout_seconds=settings.timeout_seconds,
        grace_seconds=process_config.default_termination_grace_seconds,
        poll_interval_seconds=process_config.poll_interval_seconds,
        timeout_exit_code=process_config.timeout_exit_code,
        term_signal=process_config.term_signal,
        kill_signal=process_config.kill_signal,
        audit_file=None,
        timeout_reason="process_ttl_expired",
        use_caffeinate=True,
    )
    if exit_code == process_config.timeout_exit_code:
        logger.error("Command timed out after %s seconds; process group terminated", settings.timeout_seconds)
    else:
        logger.info("Supervised command finished; exit code: %s", exit_code)
    return exit_code


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    validate_args(parser, args)
    logger = configure_logging(PROJECT_ROOT)
    try:
        query_overrides = None
        if args.query_stdin and args._ttl_worker:
            try:
                query_overrides = json.load(sys.stdin)
            except (ValueError, OSError) as exc:
                raise ConfigurationError("Invalid InfoExt query input.") from exc
            if not isinstance(query_overrides, dict) or not query_overrides:
                raise ConfigurationError("InfoExt query input must contain overrides.")
        settings = load_settings(
            PROJECT_ROOT,
            require_infoext=not args.test_telegram and (not args.query_stdin or args._ttl_worker),
            query_overrides=query_overrides,
        )
        if not args._ttl_worker:
            return run_with_timeout(args, settings, logger)
        if args.test_telegram:
            TelegramConnectorNotifier(settings.telegram_connector_dir).send(
                "InfoExt monitor: Telegram notifications configured successfully."
            )
            print("Telegram notification: OK")
            return 0
        lock = acquire_lock(PROJECT_ROOT)
        if lock is None:
            logger.info("Another monitor process already holds the lock.")
            print("InfoExt check skipped: another process is already running.")
            return 0
        try:
            if args.debug:
                return run_debug(settings, logger)
            return run_check(args, settings, logger)
        finally:
            lock.close()
    except (ConfigurationError, StateError, NotifierError) as exc:
        logger.error("Setup failure: %s", exc)
        print(f"InfoExt monitor setup failed: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
