from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import tomllib


PACKAGE_ROOT = Path(__file__).resolve().parents[3]


@dataclass(frozen=True, slots=True)
class RuntimeSettings:
    db_path: Path
    artifact_root: Path
    sqlite_config_path: Path
    default_locale: str
    enable_ai_extraction: bool
    api_max_body_bytes: int
    api_allow_local_file_sources: bool
    kb_index_config_path: Path | None = None


@dataclass(frozen=True, slots=True)
class WorkspaceSettings:
    active_candidate_id: str | None


def load_runtime_settings(config_path: Path) -> RuntimeSettings:
    with config_path.open("rb") as fh:
        raw = tomllib.load(fh)

    paths = raw.get("paths", {})
    runtime = raw.get("runtime", {})
    integrations = raw.get("integrations", {})
    db_path = Path(paths["db_path"]).expanduser()
    artifact_root = Path(paths["artifact_root"]).expanduser()
    sqlite_config_path = Path(paths["sqlite_config_path"]).expanduser()
    kb_index_config_raw = integrations.get("kb_index_config_path")

    return RuntimeSettings(
        db_path=db_path,
        artifact_root=artifact_root,
        sqlite_config_path=sqlite_config_path,
        default_locale=str(runtime.get("default_locale", "en")),
        enable_ai_extraction=bool(runtime.get("enable_ai_extraction", False)),
        api_max_body_bytes=int(runtime.get("api_max_body_bytes", 1024 * 1024)),
        api_allow_local_file_sources=bool(runtime.get("api_allow_local_file_sources", False)),
        kb_index_config_path=Path(kb_index_config_raw).expanduser() if kb_index_config_raw else None,
    )


def load_workspace_settings(config_path: Path) -> WorkspaceSettings:
    if not config_path.exists():
        return WorkspaceSettings(active_candidate_id=None)
    with config_path.open("rb") as fh:
        raw = tomllib.load(fh)
    return WorkspaceSettings(active_candidate_id=raw.get("active_candidate_id"))


def save_workspace_settings(config_path: Path, settings: WorkspaceSettings) -> None:
    config_path.parent.mkdir(parents=True, exist_ok=True)
    lines = []
    if settings.active_candidate_id is None:
        lines.append("active_candidate_id = ''")
    else:
        lines.append(f"active_candidate_id = '{settings.active_candidate_id}'")
    config_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
