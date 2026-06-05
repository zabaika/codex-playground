from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

from job_search.application.services.artifact_cleanup_service import ArtifactCleanupRequest, ArtifactCleanupService
from job_search.application.services.artifact_rename_service import ArtifactRenameRequest, ArtifactRenameService
from job_search.application.services.system_status_service import SystemStatusService
from job_search.config import load_runtime_settings, load_workspace_settings


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="job-search-system")
    parser.add_argument("--config-path", required=True)
    parser.add_argument("--workspace-path", required=True)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("version")
    sub.add_parser("status")
    sub.add_parser("doctor")
    observability = sub.add_parser("observability")
    observability.add_argument("--candidate-id")
    observability.add_argument("--limit", type=int, default=20)
    strategy = sub.add_parser("strategy-report")
    strategy.add_argument("--candidate-id")
    strategy.add_argument("--limit", type=int, default=20)
    cleanup = sub.add_parser("cleanup-artifacts")
    cleanup.add_argument("--keep-candidate-id", action="append", default=[])
    cleanup.add_argument("--keep-folder", action="append", default=[])
    cleanup.add_argument("--backup-dir")
    cleanup.add_argument("--apply", action="store_true")
    rename = sub.add_parser("rename-artifacts")
    rename.add_argument("--candidate-id", action="append", default=[])
    rename.add_argument("--backup-dir")
    rename.add_argument("--apply", action="store_true")
    return parser


def build_system_status_service(config_path: Path, workspace_path: Path) -> SystemStatusService:
    runtime_settings = load_runtime_settings(config_path)
    workspace_settings = load_workspace_settings(workspace_path)
    migrations_dir = Path(__file__).resolve().parents[2] / "infrastructure" / "migrations"
    return SystemStatusService(
        runtime_settings=runtime_settings,
        workspace_settings=workspace_settings,
        migrations_dir=migrations_dir,
    )


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    config_path = Path(args.config_path)
    workspace_path = Path(args.workspace_path)
    service = build_system_status_service(config_path, workspace_path)
    if args.command == "version":
        result = service.version()
    elif args.command == "observability":
        result = service.observability(candidate_id=args.candidate_id, limit=args.limit)
    elif args.command == "strategy-report":
        result = service.strategy_report(candidate_id=args.candidate_id, limit=args.limit)
    elif args.command == "cleanup-artifacts":
        runtime_settings = load_runtime_settings(config_path)
        result = ArtifactCleanupService(runtime_settings=runtime_settings).cleanup(
            ArtifactCleanupRequest(
                keep_candidate_ids=tuple(args.keep_candidate_id),
                keep_artifact_folders=tuple(args.keep_folder),
                apply=args.apply,
                backup_dir=Path(args.backup_dir) if args.backup_dir else None,
            )
        )
    elif args.command == "rename-artifacts":
        runtime_settings = load_runtime_settings(config_path)
        result = ArtifactRenameService(runtime_settings=runtime_settings).rename(
            ArtifactRenameRequest(
                candidate_ids=tuple(args.candidate_id),
                apply=args.apply,
                backup_dir=Path(args.backup_dir) if args.backup_dir else None,
            )
        )
    else:
        result = service.status()
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if args.command == "doctor" and result["summary"]["fail"]:
        return 1
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (ValueError, KeyError, PermissionError, RuntimeError) as exc:
        print(json.dumps({"error": {"type": type(exc).__name__, "message": str(exc)}}, ensure_ascii=False), file=sys.stderr)
        raise SystemExit(2)
