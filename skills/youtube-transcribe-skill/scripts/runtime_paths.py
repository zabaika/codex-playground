from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
import tomllib


DEFAULT_PROJECT_ROOT_ENV = "CODEX_PLAYGROUND_PROJECT_ROOT"
DEFAULT_OUTPUT_DIR = "scratch"


@dataclass(frozen=True, slots=True)
class RuntimePaths:
    skill_dir: Path
    config_path: Path
    project_root: Path | None
    output_dir: Path
    log_file: Path | None
    config: dict[str, object]


def load_toml(path: Path) -> dict[str, object]:
    if not path.exists():
        return {}
    with path.open("rb") as handle:
        return tomllib.load(handle)


def string_value(raw_value: object, default: str = "") -> str:
    if raw_value is None:
        return default
    value = str(raw_value).strip()
    return value or default


def resolve_against(base_dir: Path, raw_value: str) -> Path:
    candidate = Path(raw_value).expanduser()
    if candidate.is_absolute():
        return candidate.resolve(strict=False)
    return (base_dir / candidate).resolve(strict=False)


def infer_repo_root(skill_dir: Path) -> Path | None:
    for candidate in (skill_dir, *skill_dir.parents):
        if (candidate / "RULEBOOK.md").exists():
            return candidate.resolve(strict=False)
    return None


def resolve_project_root(
    *,
    config: dict[str, object] | None,
    skill_dir: Path,
) -> Path | None:
    env_root = os.environ.get(DEFAULT_PROJECT_ROOT_ENV, "").strip()
    if env_root:
        return resolve_against(skill_dir, env_root)

    if config:
        paths = config.get("paths", {})
        if isinstance(paths, dict):
            configured_root = string_value(paths.get("project_root"), "")
            if configured_root:
                return resolve_against(skill_dir, configured_root)

    return infer_repo_root(skill_dir)


def resolve_project_local_path(
    raw_value: str,
    *,
    field_name: str,
    project_root: Path | None,
) -> Path:
    candidate = Path(raw_value).expanduser()
    if candidate.is_absolute():
        return candidate.resolve(strict=False)
    if project_root is None:
        raise ValueError(
            f"{field_name} uses a relative path but project root could not be resolved. "
            "Set CODEX_PLAYGROUND_PROJECT_ROOT or paths.project_root."
        )
    return (project_root / candidate).resolve(strict=False)


def resolve_runtime_paths(
    *,
    config_path: Path,
    skill_dir: Path,
) -> RuntimePaths:
    config = load_toml(config_path)
    project_root = resolve_project_root(config=config, skill_dir=skill_dir)
    paths = config.get("paths", {})
    if not isinstance(paths, dict):
        paths = {}

    output_raw = string_value(paths.get("output_dir"), DEFAULT_OUTPUT_DIR)
    output_dir = resolve_project_local_path(
        output_raw,
        field_name="paths.output_dir",
        project_root=project_root,
    )

    log_raw = string_value(paths.get("log_file"), "")
    log_file = (
        resolve_project_local_path(
            log_raw,
            field_name="paths.log_file",
            project_root=project_root,
        )
        if log_raw
        else None
    )

    return RuntimePaths(
        skill_dir=skill_dir.resolve(strict=False),
        config_path=config_path.resolve(strict=False),
        project_root=project_root,
        output_dir=output_dir,
        log_file=log_file,
        config=config,
    )
