"""Analytics policy, prompt contracts, profiles and reviewed model metadata."""

import copy
import hashlib
from pathlib import Path
import re
from string import Formatter
import tomllib
from typing import Any

APP_DIR = Path(__file__).resolve().parent
DEFAULT_CONFIG_FILE = APP_DIR / "config" / "runtime.example.toml"
_MISSING = object()
# Versioned application/template contracts, not operator sizing defaults.
ANALYSIS_SCHEMA_VERSION = 1
ANALYSIS_ALGORITHM_VERSION = 7


def resolve_analysis_model_capabilities(model):
    # Reviewed provider metadata, not inferred from the model name or system limits.
    # Endpoint/account availability is a separate rollout check.
    if model == "gpt-5.6-luna":
        return {"model": model, "context_window": 1050000, "max_input_tokens": 922000, "max_output_tokens": 128000,
            "source": "https://developers.openai.com/api/docs/models/gpt-5.6-luna", "verified_at": "2026-10-17"}
    # Example-config model; official page fetched 2026-10-16.
    if model not in {"gpt-5.4-mini", "gpt-5.4-mini-2026-03-17"}:
        raise ValueError("model_capability_unverified")
    return {"model": model, "context_window": 400000, "max_input_tokens": 272000, "max_output_tokens": 128000,
        "source": "https://developers.openai.com/api/docs/models/gpt-5.4-mini", "verified_at": "2026-10-16"}


def _analysis_reasoning(settings):
    ai = settings["digest_ai"]
    reasoning = {"effort": ai["reasoning_effort"]}
    if ai["reasoning_summary"] != "none":
        reasoning["summary"] = ai["reasoning_summary"]
    return reasoning


def _synthesis_limits(job):
    request, settings = job["request"]["request"], job["request"]["settings"]
    output = settings["analysis"]["output"]
    profile = output["profiles"][request["detail"]]
    limit, sources = request["limit"], request["sources"]
    if type(limit) is not int or not 1 <= limit <= min(profile["max_items"], output["max_items"]):
        raise ValueError("synthesis_limit")
    if type(sources) is not int or not 1 <= sources <= output["sources_per_item"]:
        raise ValueError("synthesis_sources_limit")
    return {"detail": request["detail"], "max_items": limit, "sources_per_item": sources,
            "sentences_per_item": profile["sentences_per_item"], "max_chars": profile["max_chars"],
            "max_output_tokens": profile["max_output_tokens"]}


PROMPT_FIELDS = {
    "prepare_query": frozenset({"query", "max_terms", "max_term_chars"}),
    "extract": frozenset({"command_rubric", "query", "messages", "context"}),
    "link": frozenset({"command_rubric", "query", "records"}),
    "merge": frozenset({"command_rubric", "query", "records"}),
    "synthesize": frozenset({"command_rubric", "detail_instructions", "query", "evidence", "limits"}),
    "repair": frozenset({"invalid_output", "validation_errors"}),
}


def _leaf_paths(data: dict[str, Any], prefix: str = "") -> list[str]:
    paths: list[str] = []
    for key, value in data.items():
        path = f"{prefix}.{key}" if prefix else key
        paths.extend(_leaf_paths(value, path) if isinstance(value, dict) else [path])
    return paths


def _resolve_new_settings(default: Any, supplied: Any, path: str, sources: dict[str, str]) -> Any:
    if isinstance(default, dict):
        if supplied is _MISSING:
            supplied = {}
        if not isinstance(supplied, dict) or supplied.keys() - default.keys():
            raise ValueError(f"Invalid section or unknown setting: {path}")
        return {
            key: _resolve_new_settings(value, supplied.get(key, _MISSING), f"{path}.{key}", sources)
            for key, value in default.items()
        }
    sources[path] = "default" if supplied is _MISSING else "config"
    value = copy.deepcopy(default) if supplied is _MISSING else supplied
    if type(default) is bool:
        if isinstance(value, str) and value.lower() in {"true", "false"}:
            value = value.lower() == "true"
        if type(value) is not bool:
            raise ValueError(f"Expected boolean: {path}")
    elif type(default) is int:
        if isinstance(value, str) and re.fullmatch(r"[0-9]+", value.strip()):
            value = int(value)
        minimum = 0 if path in {"analysis.overlap_messages", "analysis.max_repair_attempts"} else 1
        if path == "analysis.merge_fan_in":
            minimum = 2
        if type(value) is not int or value < minimum:
            raise ValueError(f"Expected integer >= {minimum}: {path}")
    elif isinstance(default, list):
        if (not isinstance(value, list) or len(value) != 2
                or any(type(item) is not int for item in value)
                or not 1 <= value[0] <= value[1]):
            raise ValueError(f"Expected sentence range [min,max]: {path}")
        value = list(value)
    elif not isinstance(value, str) or not value.strip():
        raise ValueError(f"Expected nonempty string: {path}")
    return value


def load_analysis_prompts(config: dict[str, Any], *, base_dir: Path) -> dict[str, Any]:
    """Use the digest bundle's local-TOML pattern with explicit field validation."""
    filename = config["analysis_prompts"]["file"]
    path = Path(filename).expanduser()
    if not path.is_absolute():
        path = base_dir / path
    try:
        blob = path.read_bytes()
        values = tomllib.loads(blob.decode("utf-8"))["analysis_prompts"]
    except (OSError, ValueError, KeyError) as exc:
        raise ValueError("Missing or invalid analysis_prompts bundle") from exc
    required_strings = ("version", "system_instructions", "shared_prefix")
    required_keys = set(required_strings) | {"templates", "commands", "details"}
    if not isinstance(values, dict) or set(values) != required_keys or any(
        not isinstance(values.get(key), str) or not values[key].strip()
        for key in required_strings
    ):
        raise ValueError("Invalid analysis_prompts metadata or instructions")
    for section, names in (
        ("templates", set(PROMPT_FIELDS)), ("commands", {"topics", "search", "qa"}),
        ("details", {"short", "normal", "full"}),
    ):
        data = values.get(section)
        if not isinstance(data, dict) or set(data) != names or any(
            not isinstance(value, str) or not value.strip() for value in data.values()
        ):
            raise ValueError(f"Invalid analysis_prompts.{section}")
    for stage, required in PROMPT_FIELDS.items():
        try:
            parts = list(Formatter().parse(values["templates"][stage]))
        except ValueError as exc:
            raise ValueError(f"Invalid prompt template: {stage}") from exc
        actual = {field for _, field, _, _ in parts if field is not None}
        if actual != required or any(spec or conversion for _, _, spec, conversion in parts):
            raise ValueError(f"Invalid prompt placeholders: {stage}")
    return {**values, "file": filename, "hash": hashlib.sha256(blob).hexdigest()}


def render_analysis_prompt(bundle: dict[str, Any], stage: str, **values: str | int) -> str:
    required = PROMPT_FIELDS.get(stage)
    if required is None or set(values) != required or any(type(value) not in {str, int} for value in values.values()):
        raise ValueError("Invalid analysis prompt arguments")
    return bundle["templates"][stage].format_map(values)


def resolve_analysis_policy(config: dict[str, Any]) -> dict[str, Any]:
    """Validate new limits independently of inference credentials/model settings."""
    defaults = tomllib.loads(DEFAULT_CONFIG_FILE.read_text(encoding="utf-8"))
    sources: dict[str, str] = {}
    settings = {
        key: _resolve_new_settings(defaults[key], config.get(key, _MISSING), key, sources)
        for key in ("analysis", "analysis_prompts")
    }
    analysis = settings["analysis"]
    output = analysis["output"]
    if analysis["search"]["mode"] not in {"fast", "full"}:
        raise ValueError("Invalid analysis.search.mode")
    if output["default_detail"] not in output["profiles"]:
        raise ValueError("Invalid analysis.output.default_detail")
    for name, profile in output["profiles"].items():
        if not profile["default_limit"] <= profile["max_items"] <= output["max_items"]:
            raise ValueError(f"Incompatible analysis.output.profiles.{name} limits")
    results_path = Path(output["results_dir"])
    if (results_path.is_absolute() or not results_path.parts
            or results_path.parts[0] != "scratch" or ".." in results_path.parts):
        raise ValueError("analysis.output.results_dir must remain under repo scratch")
    settings["settings_sources"] = sources
    return settings


def finalize_analysis_settings(settings, config, reused, *, config_dir):
    """Complete the policy with legacy values supplied by the composition root."""
    sources = settings["settings_sources"]
    settings.update(reused)
    for path in _leaf_paths(reused):
        section, key = path.split(".", 1)
        sources[path] = "common-config" if section in {"process", "sqlite"} else (
            "config" if key in config.get(section, {}) else "legacy-default"
        )
    settings["settings_sources"] = sources
    settings["analysis_prompts"] = load_analysis_prompts(settings, base_dir=config_dir)
    settings["schema_version"] = ANALYSIS_SCHEMA_VERSION
    settings["algorithm_version"] = ANALYSIS_ALGORITHM_VERSION
    return settings
