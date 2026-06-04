from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
import tomllib


DEFAULT_PROJECT_ROOT_ENV = "CODEX_PLAYGROUND_PROJECT_ROOT"
DEFAULT_SCRATCH_ROOT = "scratch/article-to-obsidian-kb"


@dataclass(frozen=True, slots=True)
class RuntimePaths:
    skill_dir: Path
    config_path: Path
    project_root: Path | None
    scratch_root: Path
    kb_index_config: Path | None
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


def derive_project_root_from_kb_index_config(config: dict[str, object]) -> Path | None:
    paths = config.get("paths", {})
    if not isinstance(paths, dict):
        return None
    raw_kb_index = string_value(paths.get("kb_index_config"), "")
    if not raw_kb_index:
        return None
    candidate = Path(raw_kb_index).expanduser()
    if not candidate.is_absolute():
        return None
    resolved = candidate.resolve(strict=False)
    if (
        resolved.name == "runtime.local.toml"
        and resolved.parent.name == "config"
        and resolved.parent.parent.name == "kb-index"
        and resolved.parent.parent.parent.name == "tools"
    ):
        return resolved.parent.parent.parent.parent.resolve(strict=False)
    return None


def _consensus_project_root(candidates: list[Path]) -> Path:
    unique_candidates: list[Path] = []
    seen: set[Path] = set()
    for candidate in candidates:
        resolved = candidate.resolve(strict=False)
        if resolved in seen:
            continue
        seen.add(resolved)
        unique_candidates.append(resolved)
    if len(unique_candidates) == 1:
        return unique_candidates[0]
    formatted = ", ".join(str(candidate) for candidate in unique_candidates)
    raise ValueError(
        "Project root could not be resolved unambiguously from local anchors. "
        f"Derived candidates: {formatted}. "
        "Set CODEX_PLAYGROUND_PROJECT_ROOT or paths.project_root explicitly."
    )


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
    local_candidates: list[Path] = []
    if config:
        derived_root = derive_project_root_from_kb_index_config(config)
        if derived_root is not None:
            local_candidates.append(derived_root)
    inferred_root = infer_repo_root(skill_dir)
    if inferred_root is not None:
        local_candidates.append(inferred_root)
    if not local_candidates:
        return None
    return _consensus_project_root(local_candidates)


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
            "Set CODEX_PLAYGROUND_PROJECT_ROOT, paths.project_root, or an absolute paths.kb_index_config."
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

    scratch_raw = string_value(paths.get("scratch_root"), DEFAULT_SCRATCH_ROOT)
    scratch_root = resolve_project_local_path(
        scratch_raw,
        field_name="paths.scratch_root",
        project_root=project_root,
    )

    kb_index_raw = string_value(paths.get("kb_index_config"), "")
    kb_index_config = (
        resolve_project_local_path(
            kb_index_raw,
            field_name="paths.kb_index_config",
            project_root=project_root,
        )
        if kb_index_raw
        else None
    )

    return RuntimePaths(
        skill_dir=skill_dir.resolve(strict=False),
        config_path=config_path.resolve(strict=False),
        project_root=project_root,
        scratch_root=scratch_root,
        kb_index_config=kb_index_config,
        config=config,
    )
