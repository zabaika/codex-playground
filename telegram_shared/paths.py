from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class AppPaths:
    app_dir: Path
    project_root: Path
    base_dir: Path
    config_dir: Path
    runtime_local_file: Path
    data_dir: Path


def resolve_app_paths(
    module_file: str,
    *,
    project_root_env_var: str,
    runtime_base: str = "project_root",
) -> AppPaths:
    app_dir = Path(module_file).resolve().parent
    raw_project_root = os.environ.get(project_root_env_var, "").strip()
    project_root = Path(raw_project_root).expanduser() if raw_project_root else app_dir
    if runtime_base == "app_dir":
        base_dir = app_dir
    elif runtime_base == "project_root":
        base_dir = project_root
    else:
        raise ValueError(f"Unsupported runtime_base: {runtime_base}")
    config_dir = base_dir / "config"
    return AppPaths(
        app_dir=app_dir,
        project_root=project_root,
        base_dir=base_dir,
        config_dir=config_dir,
        runtime_local_file=config_dir / "runtime.local.toml",
        data_dir=base_dir / "data",
    )
