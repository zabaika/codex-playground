#!/usr/bin/env python3
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import html
import importlib.util
import json
import os
from pathlib import Path
import re
import subprocess
import sys
from urllib.parse import parse_qs, urlparse

try:
    import tomllib
except ModuleNotFoundError as exc:  # pragma: no cover
    raise SystemExit("Python 3.11+ with tomllib is required.") from exc


DEFAULT_PROJECT_ROOT_ENV = "CODEX_PLAYGROUND_PROJECT_ROOT"
DEFAULT_TRANSCRIBE_CONFIG_FROM_SKILL = "../../youtube-transcribe-skill/config/runtime.local.toml"
DEFAULT_TRANSCRIBE_CONFIG_FROM_PROJECT = "skills/youtube-transcribe-skill/config/runtime.local.toml"
DEFAULT_ARTICLE_CONFIG_FROM_SKILL = "../../article-to-obsidian-kb/config/runtime.local.toml"
DEFAULT_ARTICLE_CONFIG_FROM_PROJECT = "skills/article-to-obsidian-kb/config/runtime.local.toml"
DEFAULT_ARTICLE_ROUTER_FROM_SKILL = "../../article-to-obsidian-kb/scripts/detect_source_route.py"
DEFAULT_ARTICLE_ROUTER_FROM_PROJECT = "skills/article-to-obsidian-kb/scripts/detect_source_route.py"
DEFAULT_PREPARED_DIR = "scratch/youtube-to-obsidian-kb"
DEFAULT_LOG_FILE = "scratch/youtube-to-obsidian-kb.log"
DEFAULT_TRANSCRIBE_LOG_FILE = "scratch/youtube-transcribe.log"
YOUTUBE_HOSTS = {
    "youtu.be",
    "www.youtu.be",
    "youtube.com",
    "www.youtube.com",
    "m.youtube.com",
}


def load_toml(path: Path) -> dict:
    if not path.exists():
        return {}
    with path.open("rb") as handle:
        return tomllib.load(handle)


def _string_value(raw_value: object, default: str = "") -> str:
    if raw_value is None:
        return default
    value = str(raw_value).strip()
    return value or default


def resolve_path(raw_value: str, base_dir: Path) -> Path:
    candidate = Path(raw_value).expanduser()
    if candidate.is_absolute():
        return candidate.resolve(strict=False)
    return (base_dir / candidate).resolve(strict=False)


def infer_repo_root(skill_root: Path) -> Path | None:
    for candidate in (skill_root, *skill_root.parents):
        if (candidate / "RULEBOOK.md").exists():
            return candidate.resolve(strict=False)
    return None


def load_article_runtime_paths_module(article_config_path: Path):
    script_path = article_config_path.parent.parent / "scripts" / "runtime_paths.py"
    if not script_path.exists():
        raise SystemExit(f"Article runtime path resolver is missing: {script_path}")
    module_name = f"article_runtime_paths_{abs(hash(script_path.resolve(strict=False)))}"
    spec = importlib.util.spec_from_file_location(module_name, script_path)
    if spec is None or spec.loader is None:
        raise SystemExit(f"Could not load article runtime path resolver from: {script_path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


def article_project_root(article_config_path: Path) -> Path | None:
    module = load_article_runtime_paths_module(article_config_path)
    config = module.load_toml(article_config_path)
    return module.resolve_project_root(config=config, skill_dir=article_config_path.parent.parent)


def project_root(config: dict, skill_root: Path, article_config_path: Path | None = None) -> Path:
    env_override = os.environ.get(DEFAULT_PROJECT_ROOT_ENV, "").strip()
    if env_override:
        return resolve_path(env_override, skill_root)
    paths = config.get("paths", {})
    raw_value = _string_value(paths.get("project_root"), "")
    if raw_value:
        return resolve_path(raw_value, skill_root)
    if article_config_path is not None and article_config_path.exists():
        resolved = article_project_root(article_config_path)
        if resolved is not None:
            return resolved
    inferred = infer_repo_root(skill_root)
    if inferred is not None:
        return inferred
    raise SystemExit(
        "Could not resolve project root for youtube-to-obsidian-kb. "
        "Set CODEX_PLAYGROUND_PROJECT_ROOT, [paths].project_root, or a sibling article-to-obsidian-kb config that resolves project-local paths."
    )


def prepare_output_dir(config: dict, resolved_project_root: Path) -> Path:
    paths = config.get("paths", {})
    raw_value = _string_value(paths.get("prepared_transcripts_dir"), DEFAULT_PREPARED_DIR)
    resolved = resolve_path(raw_value, resolved_project_root)
    resolved.mkdir(parents=True, exist_ok=True)
    return resolved


def log_path(config: dict, resolved_project_root: Path) -> Path:
    paths = config.get("paths", {})
    raw_value = _string_value(paths.get("log_file"), DEFAULT_LOG_FILE)
    resolved = resolve_path(raw_value, resolved_project_root)
    resolved.parent.mkdir(parents=True, exist_ok=True)
    return resolved


def configured_path(config: dict, resolved_project_root: Path, key: str, default_value: str) -> Path:
    paths = config.get("paths", {})
    raw_value = _string_value(paths.get(key), default_value)
    return resolve_path(raw_value, resolved_project_root)


def append_log(path: Path, message: str) -> None:
    with path.open("a", encoding="utf-8") as handle:
        handle.write(message)
        if not message.endswith("\n"):
            handle.write("\n")


def is_youtube_url(raw_url: str) -> bool:
    parsed = urlparse(raw_url.strip())
    if parsed.scheme not in {"http", "https"}:
        return False
    if parsed.netloc.lower() not in YOUTUBE_HOSTS:
        return False
    if parsed.netloc.lower() in {"youtu.be", "www.youtu.be"}:
        return bool(parsed.path.strip("/"))
    if parse_qs(parsed.query).get("v"):
        return True
    return parsed.path.startswith("/shorts/")


def extract_video_id(raw: str) -> str:
    raw = raw.strip()
    if re.fullmatch(r"[\w-]{11}", raw):
        return raw
    parsed = urlparse(raw)
    if parsed.netloc.lower() in {"youtu.be", "www.youtu.be"}:
        candidate = parsed.path.strip("/").split("/")[0]
        if re.fullmatch(r"[\w-]{11}", candidate):
            return candidate
    query_id = parse_qs(parsed.query).get("v", [""])[0]
    if re.fullmatch(r"[\w-]{11}", query_id):
        return query_id
    shorts_match = re.match(r"^/shorts/([\w-]{11})(?:/|$)", parsed.path)
    if shorts_match:
        return shorts_match.group(1)
    return "unknown"


def resolve_optional_config(
    explicit_path: str,
    config_dir: Path | None,
    fallback_relative_to_skill: str,
    fallback_relative_to_project: str,
    project_root_dir: Path | None,
) -> Path:
    candidates: list[Path] = []
    if explicit_path:
        if config_dir is None:
            if project_root_dir is None:
                raise SystemExit(
                    "Relative config override was provided but project root is unavailable. "
                    "Set CODEX_PLAYGROUND_PROJECT_ROOT or [paths].project_root."
                )
            candidates.append(resolve_path(explicit_path, project_root_dir))
        else:
            candidates.append(resolve_path(explicit_path, config_dir))
    if project_root_dir is not None:
        fallback_repo = resolve_path(fallback_relative_to_project, project_root_dir)
        candidates.append(fallback_repo)
    if config_dir is not None:
        candidates.append(resolve_path(fallback_relative_to_skill, config_dir))
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return candidates[0]


def parse_path_marker(text: str, marker: str) -> str:
    for line in reversed(text.splitlines()):
        if line.startswith(marker):
            return line.split(marker, 1)[1].strip()
    return ""


def parse_value_marker(text: str, marker: str) -> str:
    for line in reversed(text.splitlines()):
        if line.startswith(marker):
            return line[len(marker) :].strip()
    return ""


def lookup_transcribe_metadata_for_subtitle(subtitle_path: Path, transcribe_log_path: Path) -> dict[str, str]:
    if not transcribe_log_path.exists():
        return {}
    needle = f"Saved subtitle file: {subtitle_path.resolve()}"
    lines = transcribe_log_path.read_text(encoding="utf-8").splitlines()
    for index in range(len(lines) - 1, -1, -1):
        if lines[index].strip() != needle:
            continue
        engine_used = ""
        selected_language = ""
        for forward_index in range(index + 1, min(index + 6, len(lines))):
            candidate = lines[forward_index].strip()
            if candidate.startswith("Engine used:"):
                engine_used = candidate.split(":", 1)[1].strip()
                break
            if candidate.startswith("=== run started "):
                break
        for backward_index in range(index - 1, max(index - 6, -1), -1):
            candidate = lines[backward_index].strip()
            if candidate.startswith("Selected subtitle language:"):
                selected_language = candidate.split(":", 1)[1].strip()
                break
            if candidate.startswith("=== run started "):
                break
        result: dict[str, str] = {}
        if engine_used:
            result["engine_used"] = engine_used
        if selected_language:
            result["selected_subtitle_language"] = selected_language
        return result
    return {}


def parse_json_output(text: str) -> dict:
    stripped = text.strip()
    if not stripped:
        return {}
    try:
        return json.loads(stripped)
    except json.JSONDecodeError as exc:
        raise SystemExit(f"Could not parse JSON output from helper: {exc}") from exc


def infer_title(subtitle_path: Path, video_id: str) -> str:
    name = subtitle_path.name
    suffixes = "".join(subtitle_path.suffixes)
    stem = name[: -len(suffixes)] if suffixes else subtitle_path.stem
    lang_suffix = subtitle_path.suffixes[-2] if len(subtitle_path.suffixes) >= 2 else ""
    if lang_suffix:
        lang_token = lang_suffix.lstrip(".")
        if stem.endswith(f".{lang_token}"):
            stem = stem[: -(len(lang_token) + 1)]
    match = re.match(r"^(?P<title>.+?) \[(?P<video_id>[\w-]{11})\]$", stem)
    if match:
        return match.group("title").strip() or f"YouTube {video_id}"
    return stem.strip() or f"YouTube {video_id}"


def infer_language_from_subtitle_path(subtitle_path: Path) -> str:
    if len(subtitle_path.suffixes) < 2:
        return ""
    candidate = subtitle_path.suffixes[-2].lstrip(".")
    if re.fullmatch(r"[a-z]{2,3}(?:-[A-Za-z0-9]+)*", candidate):
        return candidate
    return ""


def clean_caption_line(raw_line: str) -> str:
    value = raw_line.strip().replace("\ufeff", "")
    if not value:
        return ""
    if value.isdigit():
        return ""
    upper = value.upper()
    if upper in {"WEBVTT", "NOTE", "STYLE", "REGION"}:
        return ""
    if upper.startswith(("NOTE ", "STYLE ", "REGION ", "X-TIMESTAMP-MAP=")):
        return ""
    if "-->" in value:
        return ""
    value = re.sub(r"<[^>]+>", " ", value)
    value = html.unescape(value)
    value = re.sub(r"\s+", " ", value).strip()
    value = re.sub(r"\s+([,.:;!?])", r"\1", value)
    return value


def subtitle_blocks(text: str) -> list[str]:
    blocks: list[str] = []
    current_lines: list[str] = []
    for raw_line in text.splitlines():
        cleaned = clean_caption_line(raw_line)
        if not cleaned:
            if current_lines:
                block = " ".join(current_lines).strip()
                if block and (not blocks or blocks[-1] != block):
                    blocks.append(block)
                current_lines = []
            continue
        current_lines.append(cleaned)
    if current_lines:
        block = " ".join(current_lines).strip()
        if block and (not blocks or blocks[-1] != block):
            blocks.append(block)
    return blocks


def build_transcript_markdown(
    *,
    title: str,
    url: str,
    video_id: str,
    subtitle_path: Path,
    prepared_path: Path,
    engine: str,
    language: str,
    blocks: list[str],
) -> str:
    lines = [
        f"# {title}",
        "",
        f"- Source: YouTube",
        f"- Video URL: {url}",
        f"- Video ID: {video_id}",
        f"- Subtitle engine: {engine or 'unknown'}",
        f"- Subtitle language: {language or 'unknown'}",
        f"- Subtitle file: {subtitle_path.resolve()}",
        f"- Prepared transcript file: {prepared_path.resolve()}",
        "",
        "## Transcript",
        "",
    ]
    lines.extend(blocks or ["Transcript cleanup produced no usable text."])
    lines.append("")
    return "\n".join(lines)


def ensure_article_config_is_ready(article_config_path: Path) -> None:
    if not article_config_path.exists():
        raise SystemExit(
            "Article-to-Obsidian config is missing. "
            f"Expected runtime.local.toml at: {article_config_path}"
        )
    config = load_toml(article_config_path)
    note_roots = config.get("note_roots", {})
    article_root = _string_value(note_roots.get("article"), "")
    concept_root = _string_value(note_roots.get("concept"), "")
    if not article_root or not concept_root:
        raise SystemExit(
            "Article-to-Obsidian config is incomplete. "
            "Both note_roots.article and note_roots.concept are required."
        )


def run_transcribe_runner(url: str, runner_path: Path, transcribe_config_path: Path) -> subprocess.CompletedProcess[str]:
    if not runner_path.exists():
        raise SystemExit(f"youtube-transcribe runner is missing: {runner_path}")
    if not transcribe_config_path.exists():
        raise SystemExit(
            "YouTube transcript config is missing. "
            f"Expected runtime.local.toml at: {transcribe_config_path}"
        )
    return subprocess.run(
        [sys.executable, str(runner_path), "--url", url, "--config", str(transcribe_config_path)],
        capture_output=True,
        text=True,
        check=False,
        timeout=300,
    )


def prepare_transcript_from_subtitle(
    *,
    subtitle_path: Path,
    url: str,
    video_id: str,
    engine: str,
    language: str,
    prepared_dir: Path,
) -> Path:
    if not subtitle_path.exists():
        raise SystemExit(f"Subtitle file does not exist: {subtitle_path}")
    blocks = subtitle_blocks(subtitle_path.read_text(encoding="utf-8"))
    if not blocks:
        raise SystemExit(
            "Transcript cleanup produced no usable caption text. "
            "Stop instead of drafting notes from partial metadata."
        )
    safe_title = infer_title(subtitle_path, video_id)
    safe_name = re.sub(r'[\\/:*?"<>|]+', " ", safe_title)
    safe_name = re.sub(r"\s+", " ", safe_name).strip().rstrip(".") or f"YouTube {video_id}"
    prepared_path = prepared_dir / f"{safe_name} [{video_id}].transcript.md"
    prepared_path.write_text(
        build_transcript_markdown(
            title=safe_title,
            url=url,
            video_id=video_id,
            subtitle_path=subtitle_path,
            prepared_path=prepared_path,
            engine=engine,
            language=language,
            blocks=blocks,
        ),
        encoding="utf-8",
    )
    return prepared_path


def run_article_router(prepared_path: Path, router_path: Path, title: str) -> subprocess.CompletedProcess[str]:
    if not router_path.exists():
        raise SystemExit(f"Article route detector is missing: {router_path}")
    return subprocess.run(
        [sys.executable, str(router_path), "--source-file", str(prepared_path), "--title", title, "--json"],
        capture_output=True,
        text=True,
        check=False,
        timeout=120,
    )


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Prepare a cleaned markdown transcript for youtube-to-obsidian-kb.",
    )
    parser.add_argument("--url", required=True, help="YouTube video URL")
    parser.add_argument(
        "--config",
        help="Optional path to this skill's runtime.local.toml",
    )
    parser.add_argument(
        "--subtitle-file",
        help="Optional existing subtitle file for offline validation; skips transcript fetching.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Print a JSON summary instead of human-readable lines.",
    )
    args = parser.parse_args()

    if not is_youtube_url(args.url):
        raise SystemExit("Expected a standard YouTube URL.")

    script_dir = Path(__file__).resolve().parent
    skill_root = script_dir.parent
    config_path = Path(args.config).expanduser() if args.config else skill_root / "config" / "runtime.local.toml"
    config = load_toml(config_path)
    config_dir = config_path.parent
    fallback_project_root = infer_repo_root(skill_root)

    skills_cfg = config.get("skills", {})
    article_config_path = resolve_optional_config(
        _string_value(skills_cfg.get("article_to_obsidian_config"), ""),
        config_dir,
        DEFAULT_ARTICLE_CONFIG_FROM_SKILL,
        DEFAULT_ARTICLE_CONFIG_FROM_PROJECT,
        fallback_project_root,
    )
    resolved_project_root = project_root(config, skill_root, article_config_path)
    prepared_dir = prepare_output_dir(config, resolved_project_root)
    current_log_path = log_path(config, resolved_project_root)
    transcribe_config_path = resolve_optional_config(
        _string_value(skills_cfg.get("youtube_transcribe_config"), ""),
        config_dir,
        DEFAULT_TRANSCRIBE_CONFIG_FROM_SKILL,
        DEFAULT_TRANSCRIBE_CONFIG_FROM_PROJECT,
        resolved_project_root,
    )
    ensure_article_config_is_ready(article_config_path)
    transcribe_runner = transcribe_config_path.parent.parent / "scripts" / "run_youtube_transcribe.py"
    article_router_path = resolve_optional_config(
        "",
        config_dir,
        DEFAULT_ARTICLE_ROUTER_FROM_SKILL,
        DEFAULT_ARTICLE_ROUTER_FROM_PROJECT,
        resolved_project_root,
    )

    append_log(current_log_path, "")
    append_log(current_log_path, f"=== run started {datetime.now(timezone.utc).isoformat()} ===")
    append_log(current_log_path, f"URL: {args.url}")

    if args.subtitle_file:
        subtitle_path = Path(args.subtitle_file).expanduser().resolve()
        selected_language = infer_language_from_subtitle_path(subtitle_path)
        transcribe_config = load_toml(transcribe_config_path)
        transcribe_log_path = configured_path(
            transcribe_config,
            resolved_project_root,
            "log_file",
            DEFAULT_TRANSCRIBE_LOG_FILE,
        )
        cached_metadata = lookup_transcribe_metadata_for_subtitle(subtitle_path, transcribe_log_path)
        selected_language = cached_metadata.get("selected_subtitle_language", selected_language)
        engine_used = cached_metadata.get("engine_used", "unknown")
    else:
        result = run_transcribe_runner(args.url, transcribe_runner, transcribe_config_path)
        runner_output = (result.stdout or "") + ("\n" if result.stdout and result.stderr else "") + (result.stderr or "")
        append_log(current_log_path, runner_output.strip() or "No transcribe output.")
        if result.returncode != 0:
            raise SystemExit(
                "Transcript extraction failed. Stop the pipeline and report the failure honestly.\n"
                f"Transcript runner output:\n{runner_output.strip() or 'No additional output.'}"
            )
        raw_subtitle_path = parse_path_marker(runner_output, "Saved subtitle file:")
        if not raw_subtitle_path:
            raise SystemExit(
                "Transcript extraction completed without reporting a subtitle file path. "
                "Stop instead of guessing."
            )
        subtitle_path = Path(raw_subtitle_path).expanduser().resolve()
        selected_language = parse_value_marker(runner_output, "Selected subtitle language:")
        engine_used = parse_value_marker(runner_output, "Engine used:")

    video_id = extract_video_id(args.url)
    prepared_path = prepare_transcript_from_subtitle(
        subtitle_path=subtitle_path,
        url=args.url,
        video_id=video_id,
        engine=engine_used,
        language=selected_language,
        prepared_dir=prepared_dir,
    )
    if args.subtitle_file:
        append_log(current_log_path, f"Selected subtitle language: {selected_language or 'unknown'}")
        append_log(current_log_path, f"Engine used: {engine_used or 'unknown'}")
    append_log(current_log_path, f"Prepared transcript file: {prepared_path}")
    prepared_title = infer_title(subtitle_path, video_id)

    route_result = run_article_router(prepared_path, article_router_path, prepared_title)
    route_stdout = route_result.stdout or ""
    route_stderr = route_result.stderr or ""
    route_json_log_output = route_stdout.strip()
    route_log_output = route_stderr.strip()
    if route_json_log_output:
        append_log(current_log_path, route_json_log_output)
    if route_log_output:
        sys.stderr.write(route_stderr if route_stderr.endswith("\n") else f"{route_stderr}\n")
        append_log(current_log_path, route_log_output)
    elif not route_json_log_output:
        append_log(current_log_path, "No route output.")
    if route_result.returncode != 0:
        raise SystemExit(
            "Route detection failed before note generation.\n"
            f"Route detector output:\n{(route_stdout + ('\n' if route_stdout and route_stderr else '') + route_stderr).strip() or 'No additional output.'}"
        )
    route_payload = parse_json_output(route_stdout)
    route_used = str(route_payload.get("route_used", "")).strip() or "unknown"
    route_reason = str(route_payload.get("route_reason", "")).strip() or "Route detector returned no reason"

    summary = {
        "prepared_transcript_file": str(prepared_path.resolve()),
        "subtitle_file": str(subtitle_path.resolve()),
        "engine_used": engine_used or "unknown",
        "selected_subtitle_language": selected_language or "unknown",
        "route_used": route_used,
        "route_reason": route_reason,
        "article_config": str(article_config_path.resolve()),
        "transcribe_config": str(transcribe_config_path.resolve()),
    }
    summary_json = json.dumps(summary, ensure_ascii=True, indent=2)
    if args.json:
        print(summary_json)
        return 0

    print(f"Prepared transcript file: {summary['prepared_transcript_file']}")
    print(f"Subtitle file: {summary['subtitle_file']}")
    print(f"Engine used: {summary['engine_used']}")
    print(f"Selected subtitle language: {summary['selected_subtitle_language']}")
    print(f"Article config: {summary['article_config']}")
    print(f"Transcript config: {summary['transcribe_config']}")
    print(f"Log file: {current_log_path.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
