from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import tomllib

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG_PATH = PROJECT_ROOT / 'config' / 'runtime.local.toml'


@dataclass(slots=True)
class IndexScope:
    include_roots: list[str]
    exclude_roots: list[str]
    exclude_globs: list[str]


@dataclass(slots=True)
class RankingConfig:
    note_type_weights: dict[str, float]
    weights: dict[str, float]
    exact_title_bonus: dict[str, float]


@dataclass(slots=True)
class RetrievalConfig:
    default_limit: int
    fts_candidate_limit: int
    title_candidate_limit: int
    links_out_candidate_limit: int
    min_term_coverage_ratio: float
    min_score_ratio_to_top: float
    always_keep_top_n: int


@dataclass(slots=True)
class AutoUpdateConfig:
    enabled: bool
    mode: str
    interval_minutes: int
    launchd_label: str
    plist_path: Path
    run_on_load: bool
    run_total_timeout_seconds: int
    termination_grace_seconds: int
    poll_interval_seconds: float
    timeout_exit_code: int
    term_signal: str
    kill_signal: str


@dataclass(slots=True)
class RuntimeConfig:
    vault_root: Path
    db_path: Path
    state_path: Path
    scope: IndexScope
    ranking: RankingConfig
    retrieval: RetrievalConfig
    auto_update: AutoUpdateConfig


def _as_list(value: object) -> list[str]:
    if not isinstance(value, list):
        raise TypeError(f'Expected list value, got: {type(value)!r}')
    return [str(item) for item in value]


def _as_float_map(value: object) -> dict[str, float]:
    if not isinstance(value, dict):
        raise TypeError(f'Expected mapping value, got: {type(value)!r}')
    return {str(key): float(item) for key, item in value.items()}


def _as_int(value: object) -> int:
    return int(value)


def _as_bool(value: object) -> bool:
    if not isinstance(value, bool):
        raise TypeError(f'Expected bool value, got: {type(value)!r}')
    return value


def _require_mapping(parent: dict[str, object], key: str) -> dict[str, object]:
    value = parent.get(key)
    if not isinstance(value, dict):
        raise KeyError(f"Missing required config mapping: {key}")
    return value


def _require_value(parent: dict[str, object], key: str) -> object:
    if key not in parent:
        raise KeyError(f"Missing required config value: {key}")
    return parent[key]


def load_runtime_config(config_path: Path | None = None) -> RuntimeConfig:
    path = config_path or DEFAULT_CONFIG_PATH
    if not path.exists():
        raise FileNotFoundError(f'Runtime config not found: {path}')
    data = tomllib.loads(path.read_text(encoding='utf-8'))
    vault_root = Path(data['vault']['root'])
    db_path = Path(data.get('paths', {}).get('db_path', PROJECT_ROOT / 'data' / 'kb_index.sqlite'))
    state_path = Path(data.get('paths', {}).get('state_path', PROJECT_ROOT / 'data' / 'kb_index_state.json'))
    scope_data = _require_mapping(data, 'scope')
    scope = IndexScope(
        include_roots=_as_list(_require_value(scope_data, 'include_roots')),
        exclude_roots=_as_list(_require_value(scope_data, 'exclude_roots')),
        exclude_globs=_as_list(_require_value(scope_data, 'exclude_globs')),
    )
    ranking_data = _require_mapping(data, 'ranking')
    ranking = RankingConfig(
        note_type_weights=_as_float_map(_require_value(ranking_data, 'note_type_weights')),
        weights=_as_float_map(_require_value(ranking_data, 'weights')),
        exact_title_bonus=_as_float_map(_require_value(ranking_data, 'exact_title_bonus')),
    )
    retrieval_data = _require_mapping(data, 'retrieval')
    retrieval = RetrievalConfig(
        default_limit=_as_int(_require_value(retrieval_data, 'default_limit')),
        fts_candidate_limit=_as_int(_require_value(retrieval_data, 'fts_candidate_limit')),
        title_candidate_limit=_as_int(_require_value(retrieval_data, 'title_candidate_limit')),
        links_out_candidate_limit=_as_int(_require_value(retrieval_data, 'links_out_candidate_limit')),
        min_term_coverage_ratio=float(_require_value(retrieval_data, 'min_term_coverage_ratio')),
        min_score_ratio_to_top=float(_require_value(retrieval_data, 'min_score_ratio_to_top')),
        always_keep_top_n=_as_int(_require_value(retrieval_data, 'always_keep_top_n')),
    )
    auto_update_data = _require_mapping(data, 'auto_update')
    auto_update = AutoUpdateConfig(
        enabled=_as_bool(_require_value(auto_update_data, 'enabled')),
        mode=str(_require_value(auto_update_data, 'mode')),
        interval_minutes=_as_int(_require_value(auto_update_data, 'interval_minutes')),
        launchd_label=str(_require_value(auto_update_data, 'launchd_label')),
        plist_path=Path(_require_value(auto_update_data, 'plist_path')),
        run_on_load=_as_bool(_require_value(auto_update_data, 'run_on_load')),
        run_total_timeout_seconds=_as_int(_require_value(auto_update_data, 'run_total_timeout_seconds')),
        termination_grace_seconds=_as_int(_require_value(auto_update_data, 'termination_grace_seconds')),
        poll_interval_seconds=float(_require_value(auto_update_data, 'poll_interval_seconds')),
        timeout_exit_code=_as_int(_require_value(auto_update_data, 'timeout_exit_code')),
        term_signal=str(_require_value(auto_update_data, 'term_signal')),
        kill_signal=str(_require_value(auto_update_data, 'kill_signal')),
    )
    return RuntimeConfig(
        vault_root=vault_root,
        db_path=db_path,
        state_path=state_path,
        scope=scope,
        ranking=ranking,
        retrieval=retrieval,
        auto_update=auto_update,
    )
