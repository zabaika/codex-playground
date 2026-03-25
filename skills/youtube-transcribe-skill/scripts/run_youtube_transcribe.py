#!/usr/bin/env python3
from __future__ import annotations

import argparse
from datetime import datetime
import os
import re
import subprocess
import sys
import time
from pathlib import Path
from urllib import parse

try:
    import tomllib
except ModuleNotFoundError as exc:  # pragma: no cover
    raise SystemExit("Python 3.11+ with tomllib is required.") from exc

DEFAULT_PRIORITY = ["orig", "ru", "en", "uk"]
DEFAULT_SUB_FORMAT = "srt/vtt/best"
DEFAULT_OUTPUT_TEMPLATE = "%(title).180B [%(id)s].%(ext)s"
DEFAULT_ENGINE_ORDER = ["youtube-transcript-api", "yt-dlp"]
DEFAULT_PROJECT_ROOT_ENV = "CODEX_PLAYGROUND_PROJECT_ROOT"
DEFAULT_BROWSER = "chrome"
DEFAULT_AUTH_MODE = "none"
DEFAULT_PROVIDER_KIND = "bgutil"
DEFAULT_PROVIDER_SCRIPT_PATH = "~/.codex/skills/youtube-transcribe-skill/vendor/bgutil-provider/server/src/generate_once.ts"
DEFAULT_PROVIDER_BASE_URL = "http://127.0.0.1:4416"
DEFAULT_PROVIDER_PLUGIN_DIR = "~/.codex/skills/youtube-transcribe-skill/vendor/bgutil-plugin"
DEFAULT_RETRY_ATTEMPTS = 3
DEFAULT_RETRY_INITIAL_DELAY = 2.0
DEFAULT_RETRY_BACKOFF = 2.0
DEFAULT_RETRY_MAX_DELAY = 10.0


def load_config(config_path: Path) -> dict:
    if not config_path.exists():
        return {}
    with config_path.open("rb") as handle:
        return tomllib.load(handle)


def _list_of_strings(raw_value: object, default: list[str]) -> list[str]:
    if not isinstance(raw_value, list):
        return list(default)
    result = [str(item).strip() for item in raw_value if str(item).strip()]
    return result or list(default)


def _string_value(raw_value: object, default: str) -> str:
    if raw_value is None:
        return default
    value = str(raw_value).strip()
    return value or default


def _float_value(raw_value: object, default: float) -> float:
    if raw_value is None:
        return default
    try:
        return float(raw_value)
    except (TypeError, ValueError):
        return default


def _bool_value(raw_value: object, default: bool) -> bool:
    if raw_value is None:
        return default
    if isinstance(raw_value, bool):
        return raw_value
    value = str(raw_value).strip().lower()
    if value in {"1", "true", "yes", "on"}:
        return True
    if value in {"0", "false", "no", "off"}:
        return False
    return default


def _int_value(raw_value: object, default: int) -> int:
    if raw_value is None:
        return default
    try:
        return int(raw_value)
    except (TypeError, ValueError):
        return default


def _path_value(raw_value: object, default: str = "") -> str:
    if raw_value is None:
        return default
    return str(raw_value).strip() or default


def resolve_runtime_path(raw_value: str, current_dir: Path) -> Path:
    path = Path(raw_value).expanduser()
    if path.is_absolute():
        return path
    return (current_dir / path).resolve()


def project_root(config: dict, current_dir: Path) -> Path:
    paths = config.get("paths", {})
    env_override = os.environ.get(DEFAULT_PROJECT_ROOT_ENV, "").strip()
    if env_override:
        return resolve_runtime_path(env_override, current_dir)
    raw_value = _path_value(paths.get("project_root"), "")
    if not raw_value:
        return current_dir
    return resolve_runtime_path(raw_value, current_dir)


def output_dir(config: dict, current_dir: Path) -> Path:
    paths = config.get("paths", {})
    base_dir = project_root(config, current_dir)
    raw_value = _path_value(paths.get("output_dir"), "")
    if not raw_value:
        return base_dir
    return resolve_runtime_path(raw_value, base_dir)


def log_file(config: dict, current_dir: Path) -> Path | None:
    paths = config.get("paths", {})
    base_dir = project_root(config, current_dir)
    raw_value = _path_value(paths.get("log_file"), "")
    if not raw_value:
        return None
    return resolve_runtime_path(raw_value, base_dir)


def ensure_directory(path: Path, label: str) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    if not path.is_dir():
        raise SystemExit(f"{label} is not a directory: {path}")
    return path


def extract_video_id(raw: str) -> str:
    raw = raw.strip()
    if not raw:
        return "unknown"
    if re.fullmatch(r"[\w-]{11}", raw):
        return raw
    parsed = parse.urlparse(raw)
    if parsed.netloc in {"youtu.be", "www.youtu.be"}:
        candidate = parsed.path.strip("/").split("/")[0]
        if re.fullmatch(r"[\w-]{11}", candidate):
            return candidate
    query_id = parse.parse_qs(parsed.query).get("v", [""])[0]
    if re.fullmatch(r"[\w-]{11}", query_id):
        return query_id
    return "unknown"


def build_log_path(config: dict, current_dir: Path) -> Path | None:
    configured_file = log_file(config, current_dir)
    if configured_file is None:
        return None
    ensure_directory(configured_file.parent, "Log directory")
    return configured_file


def append_log(log_path: Path | None, message: str) -> None:
    if log_path is None:
        return
    with log_path.open("a", encoding="utf-8") as handle:
        handle.write(message)
        if not message.endswith("\n"):
            handle.write("\n")


def append_prefixed_lines(log_path: Path | None, text: str, prefixes: tuple[str, ...]) -> None:
    if log_path is None or not text:
        return
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith(prefixes):
            append_log(log_path, stripped)


def retry_settings(config: dict) -> dict[str, float | int]:
    retry = config.get("retry", {})
    return {
        "attempts": max(1, _int_value(retry.get("attempts"), DEFAULT_RETRY_ATTEMPTS)),
        "initial_delay_seconds": max(
            0.0, _float_value(retry.get("initial_delay_seconds"), DEFAULT_RETRY_INITIAL_DELAY)
        ),
        "backoff_multiplier": max(1.0, _float_value(retry.get("backoff_multiplier"), DEFAULT_RETRY_BACKOFF)),
        "max_delay_seconds": max(0.0, _float_value(retry.get("max_delay_seconds"), DEFAULT_RETRY_MAX_DELAY)),
    }


def classify_failure(output: str) -> tuple[str, str]:
    lines = [line.strip() for line in output.splitlines() if line.strip()]
    lowered = output.lower()
    for line in lines:
        if line.startswith("TRANSIENT_ERROR:"):
            return "transient", line.split(":", 1)[1].strip()
        if line.startswith("NO_SUBTITLES:"):
            return "no_subtitles", line.split(":", 1)[1].strip()
        if line.startswith("ERROR:"):
            return "error", line.split(":", 1)[1].strip()
    if "failed to resolve" in lowered or "nameresolutionerror" in lowered or "nodename nor servname" in lowered:
        return "transient", "Temporary network or DNS error while contacting YouTube."
    if "unable to download api page" in lowered and "youtube.com" in lowered:
        return "transient", "Temporary network or DNS error while contacting YouTube."
    if "timed out" in lowered or "max retries exceeded" in lowered or "connection reset" in lowered:
        return "transient", "Temporary network timeout while contacting YouTube."
    if "http error 429" in lowered or "too many requests" in lowered:
        return "transient", "Temporary YouTube rate limit while requesting subtitles."
    if "transcriptsdisabled" in lowered or "subtitles are disabled for this video" in lowered:
        return "no_subtitles", "No subtitles are available for this video."
    if "has no automatic captions" in lowered and "has no subtitles" in lowered:
        return "no_subtitles", "No subtitles are available for this video."
    if "none of the preferred subtitle languages are available. available languages: none" in lowered:
        return "no_subtitles", "No subtitles are available for this video."
    if "authentication" in lowered or "sign in" in lowered:
        return "auth", "YouTube requires authentication for subtitle access."
    for line in lines:
        upper = line.upper()
        if "WARNING:" in upper:
            continue
        if any(token in upper for token in ("ERROR", "FAILED", "TRACEBACK", "EXCEPTION")):
            return "error", line
    for line in lines:
        if "WARNING:" not in line.upper():
            return "error", line
    return "error", "Subtitle extraction failed."


def log_failure(log_path: Path | None, label: str, message: str) -> None:
    append_log(log_path, f"{label}: {message}")


def emit_retry_notice(
    log_path: Path | None,
    label: str,
    message: str,
    next_attempt: int,
    total_attempts: int,
    delay_seconds: float,
) -> None:
    notice = (
        f"{label}: temporary failure, retry {next_attempt}/{total_attempts} "
        f"in {delay_seconds:.1f}s. {message}"
    )
    print(notice, file=sys.stderr)
    append_log(log_path, notice)


def sanitize_filename_component(raw_value: str) -> str:
    value = re.sub(r'[\x00-\x1f]+', " ", raw_value).strip()
    value = re.sub(r'[\\/:*?"<>|]+', " ", value)
    value = re.sub(r"\s+", " ", value).strip().rstrip(".")
    return value[:180].strip() or "YouTube"


def fetch_video_title(
    url: str,
    auth_args: list[str],
    network_args: list[str],
    env_updates: dict[str, str],
    log_path: Path | None,
    retry_config: dict[str, float | int],
) -> str:
    attempts = int(retry_config["attempts"])
    delay_seconds = float(retry_config["initial_delay_seconds"])
    backoff = float(retry_config["backoff_multiplier"])
    max_delay_seconds = float(retry_config["max_delay_seconds"])
    for attempt in range(1, attempts + 1):
        title_result = run_command(
            ["yt-dlp", *auth_args, *network_args, "--skip-download", "--print", "%(title)s", url],
            env_updates=env_updates,
        )
        if title_result.returncode == 0:
            for line in title_result.stdout.splitlines():
                candidate = line.strip()
                if candidate:
                    return candidate
            return ""
        kind, message = classify_failure(title_result.stdout + "\n" + title_result.stderr)
        if kind != "transient" or attempt >= attempts:
            log_failure(log_path, "yt-dlp title probe failed", message)
            return ""
        emit_retry_notice(log_path, "yt-dlp title probe", message, attempt + 1, attempts, delay_seconds)
        time.sleep(delay_seconds)
        delay_seconds = min(max_delay_seconds, delay_seconds * backoff) if max_delay_seconds > 0 else delay_seconds * backoff
    return ""


def provider_settings(config: dict) -> dict[str, object]:
    provider = config.get("provider", {})
    return {
        "kind": _string_value(provider.get("kind"), DEFAULT_PROVIDER_KIND),
        "disable_innertube": _bool_value(provider.get("disable_innertube"), True),
        "plugin_dir": _string_value(provider.get("plugin_dir"), DEFAULT_PROVIDER_PLUGIN_DIR),
        "script_path": _string_value(provider.get("script_path"), DEFAULT_PROVIDER_SCRIPT_PATH),
        "base_url": _string_value(provider.get("base_url"), DEFAULT_PROVIDER_BASE_URL),
        "token_ttl_hours": _float_value(provider.get("token_ttl_hours"), 6.0),
    }


def build_auth_args(config: dict) -> tuple[list[str], dict[str, str]]:
    auth = config.get("auth", {})
    mode = _string_value(auth.get("mode"), DEFAULT_AUTH_MODE)
    env_updates: dict[str, str] = {}
    if mode == "none":
        env_updates["YTDLP_NO_PLUGINS"] = "1"
        return [], env_updates
    if mode == "browser-cookies":
        browser = _string_value(auth.get("browser"), DEFAULT_BROWSER)
        env_updates["YTDLP_NO_PLUGINS"] = "1"
        return [f"--cookies-from-browser={browser}"], env_updates
    if mode in {"provider-script", "provider-http"}:
        provider = provider_settings(config)
        kind = str(provider["kind"])
        if kind != "bgutil":
            raise SystemExit(f"Unsupported provider kind: {kind}")
        plugin_dir = str(Path(str(provider["plugin_dir"])).expanduser())
        if not Path(plugin_dir).is_dir():
            raise SystemExit(
                f"Provider plugin directory does not exist: {plugin_dir}. "
                "Install the reviewed yt-dlp plugin before using provider modes."
            )
        extractor_name = "youtubepot-bgutilscript" if mode == "provider-script" else "youtubepot-bgutilhttp"
        extractor_parts: list[str] = []
        if mode == "provider-script":
            script_path = str(Path(str(provider["script_path"])).expanduser())
            extractor_parts.append(f"script_path={script_path}")
            token_ttl_hours = float(provider["token_ttl_hours"])
            if token_ttl_hours > 0:
                env_updates["TOKEN_TTL"] = str(token_ttl_hours)
        else:
            extractor_parts.append(f"base_url={provider['base_url']}")
        if bool(provider["disable_innertube"]):
            extractor_parts.append("disable_innertube=1")
        return [
            "--plugin-dirs",
            plugin_dir,
            "--extractor-args",
            f"{extractor_name}:" + ";".join(extractor_parts),
        ], env_updates
    raise SystemExit(f"Unsupported auth mode: {mode}")


def build_network_args(config: dict) -> list[str]:
    network = config.get("network", {})
    args: list[str] = []
    sleep_subtitles = _float_value(network.get("sleep_subtitles"), 0.0)
    sleep_requests = _float_value(network.get("sleep_requests"), 0.0)
    if sleep_subtitles > 0:
        args.extend(["--sleep-subtitles", str(sleep_subtitles)])
    if sleep_requests > 0:
        args.extend(["--sleep-requests", str(sleep_requests)])
    return args


def subtitles_priority(config: dict) -> list[str]:
    subtitles = config.get("subtitles", {})
    return _list_of_strings(subtitles.get("language_priority"), DEFAULT_PRIORITY)


def subtitle_format(config: dict) -> str:
    subtitles = config.get("subtitles", {})
    return _string_value(subtitles.get("format_priority"), DEFAULT_SUB_FORMAT)


def output_template(config: dict) -> str:
    subtitles = config.get("subtitles", {})
    return _string_value(subtitles.get("output_template"), DEFAULT_OUTPUT_TEMPLATE)


def engine_order(config: dict) -> list[str]:
    engine = config.get("engine", {})
    return _list_of_strings(engine.get("order"), DEFAULT_ENGINE_ORDER)


def vendored_yta_python() -> Path:
    return Path.home() / ".codex" / "skills" / "youtube-transcribe-skill" / "vendor" / "youtube-transcript-api" / "venv" / "bin" / "python"


def run_youtube_transcript_api(
    url: str, config: dict, target_output_dir: Path, title: str
) -> subprocess.CompletedProcess[str] | None:
    python_path = vendored_yta_python()
    if not python_path.is_file():
        return None
    script_path = Path(__file__).resolve().parent / "fetch_with_youtube_transcript_api.py"
    args = [
        str(python_path),
        str(script_path),
        "--url",
        url,
        "--languages",
        *subtitles_priority(config),
        "--output-dir",
        str(target_output_dir),
    ]
    if title:
        args.extend(["--title", title])
    return run_command(args)


def run_command(args: list[str], *, env_updates: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    if env_updates:
        for key, value in env_updates.items():
            env[key] = value
    return subprocess.run(
        args,
        capture_output=True,
        text=True,
        timeout=300,
        check=False,
        env=env,
    )


def run_command_with_retry(
    args: list[str],
    *,
    env_updates: dict[str, str] | None,
    label: str,
    retry_config: dict[str, float | int],
    log_path: Path | None,
) -> tuple[subprocess.CompletedProcess[str], str, str]:
    attempts = int(retry_config["attempts"])
    delay_seconds = float(retry_config["initial_delay_seconds"])
    backoff = float(retry_config["backoff_multiplier"])
    max_delay_seconds = float(retry_config["max_delay_seconds"])
    last_kind = "error"
    last_message = "Subtitle extraction failed."
    for attempt in range(1, attempts + 1):
        result = run_command(args, env_updates=env_updates)
        if result.returncode == 0:
            return result, "ok", ""
        last_kind, last_message = classify_failure(result.stdout + "\n" + result.stderr)
        if last_kind != "transient" or attempt >= attempts:
            return result, last_kind, last_message
        emit_retry_notice(log_path, label, last_message, attempt + 1, attempts, delay_seconds)
        time.sleep(delay_seconds)
        delay_seconds = min(max_delay_seconds, delay_seconds * backoff) if max_delay_seconds > 0 else delay_seconds * backoff
    return result, last_kind, last_message


def extract_available_languages(output: str) -> list[str]:
    languages: list[str] = []
    in_section = False
    for line in output.splitlines():
        stripped = line.strip()
        if stripped.startswith("[info] Available automatic captions") or stripped.startswith(
            "[info] Available subtitles"
        ):
            in_section = True
            continue
        if not in_section:
            continue
        if not stripped:
            continue
        if stripped.startswith("Language ") or stripped.startswith("Language\t") or stripped == "Formats":
            continue
        if stripped.startswith("[info]") or stripped.startswith("[youtube]"):
            continue
        code = stripped.split()[0]
        if code and code not in languages:
            languages.append(code)
    return languages


def choose_language(available: list[str], priority: list[str]) -> str:
    lowered = {lang: lang.lower() for lang in available}
    for wanted in priority:
        if wanted.lower() == "orig":
            for lang, lower_lang in lowered.items():
                if "orig" in lower_lang:
                    return lang
            continue
        for lang in available:
            if lang == wanted:
                return lang
    raise SystemExit(
        "None of the preferred subtitle languages are available. "
        f"Available languages: {', '.join(available) or 'none'}"
    )


def extract_written_subtitle_path(output: str) -> str:
    marker = "[info] Writing video subtitles to:"
    for line in output.splitlines():
        if marker in line:
            return line.split(marker, 1)[1].strip()
    return ""


def normalize_reported_path(raw_path: str, target_output_dir: Path) -> Path:
    candidate = Path(raw_path).expanduser()
    if candidate.is_absolute():
        return candidate
    return (target_output_dir / candidate).resolve()


def main() -> int:
    parser = argparse.ArgumentParser(description="Download YouTube subtitles with local config.")
    parser.add_argument("--url", required=True, help="YouTube video URL")
    parser.add_argument(
        "--config",
        help="Path to runtime.local.toml. Defaults to <skill>/config/runtime.local.toml",
    )
    args = parser.parse_args()

    script_dir = Path(__file__).resolve().parent
    skill_root = script_dir.parent
    config_path = Path(args.config).expanduser() if args.config else skill_root / "config" / "runtime.local.toml"
    config = load_config(config_path)
    current_dir = Path.cwd()
    target_output_dir = ensure_directory(output_dir(config, current_dir), "Subtitle output directory")
    current_log_path = build_log_path(config, current_dir)
    auth_args, env_updates = build_auth_args(config)
    network_args = build_network_args(config)
    retry_config = retry_settings(config)
    append_log(current_log_path, "")
    append_log(current_log_path, f"=== run started {datetime.now().isoformat()} ===")
    append_log(current_log_path, f"URL: {args.url}")

    preferred_title = fetch_video_title(
        args.url, auth_args, network_args, env_updates, current_log_path, retry_config
    )

    for engine in engine_order(config):
        if engine == "youtube-transcript-api":
            python_path = vendored_yta_python()
            if not python_path.is_file():
                yta_result = None
            else:
                script_path = Path(__file__).resolve().parent / "fetch_with_youtube_transcript_api.py"
                yta_args = [
                    str(python_path),
                    str(script_path),
                    "--url",
                    args.url,
                    "--languages",
                    *subtitles_priority(config),
                    "--output-dir",
                    str(target_output_dir),
                ]
                if preferred_title:
                    yta_args.extend(["--title", preferred_title])
                yta_result, yta_kind, yta_message = run_command_with_retry(
                    yta_args,
                    env_updates=None,
                    label="youtube-transcript-api",
                    retry_config=retry_config,
                    log_path=current_log_path,
                )
            if yta_result is None:
                print(
                    "youtube-transcript-api engine skipped: vendored venv is not installed.",
                    file=sys.stderr,
                )
                append_log(
                    current_log_path,
                    "youtube-transcript-api engine skipped: vendored venv is not installed.",
                )
                continue
            if yta_result.returncode == 0:
                if yta_result.stderr:
                    print(yta_result.stderr, end="", file=sys.stderr)
                append_prefixed_lines(
                    current_log_path,
                    yta_result.stderr,
                    ("Selected subtitle language:", "Saved subtitle file:"),
                )
                print("Engine used: youtube-transcript-api", file=sys.stderr)
                append_log(current_log_path, "Engine used: youtube-transcript-api")
                if current_log_path is not None:
                    print(f"Log file: {current_log_path}", file=sys.stderr)
                return 0
            log_failure(current_log_path, "youtube-transcript-api failed", yta_message)
            print(yta_message, file=sys.stderr)
            print("Falling back to yt-dlp provider path...", file=sys.stderr)
            continue
        if engine != "yt-dlp":
            print(f"Skipping unsupported engine entry: {engine}", file=sys.stderr)
            append_log(current_log_path, f"Skipping unsupported engine entry: {engine}")
            continue
        break

    base_args = [
        "yt-dlp",
        *auth_args,
        *network_args,
        "--skip-download",
        "--paths",
        str(target_output_dir),
    ]

    list_result, list_kind, list_message = run_command_with_retry(
        [*base_args, "--list-subs", args.url],
        env_updates=env_updates,
        label="yt-dlp --list-subs",
        retry_config=retry_config,
        log_path=current_log_path,
    )
    if list_result.returncode != 0:
        log_failure(current_log_path, "yt-dlp list-subs failed", list_message)
        print(list_message, file=sys.stderr)
        return list_result.returncode

    available_languages = extract_available_languages(list_result.stdout + "\n" + list_result.stderr)
    if not available_languages:
        message = "No subtitles are available for this video."
        log_failure(current_log_path, "subtitle selection failed", message)
        print(message, file=sys.stderr)
        return 1
    try:
        selected_language = choose_language(available_languages, subtitles_priority(config))
    except SystemExit:
        message = "Subtitles exist, but none match the configured language priority."
        log_failure(current_log_path, "subtitle selection failed", message)
        print(message, file=sys.stderr)
        return 1
    print(f"Selected subtitle language: {selected_language}", file=sys.stderr)
    append_log(current_log_path, f"Selected subtitle language: {selected_language}")
    print("Engine used: yt-dlp", file=sys.stderr)

    download_result, download_kind, download_message = run_command_with_retry(
        [
            *base_args,
            "--write-auto-sub",
            "--write-sub",
            "--sub-langs",
            selected_language,
            "--sub-format",
            subtitle_format(config),
            "--output",
            output_template(config),
            args.url,
        ],
        env_updates=env_updates,
        label="yt-dlp download",
        retry_config=retry_config,
        log_path=current_log_path,
    )
    if download_result.returncode != 0:
        log_failure(current_log_path, "yt-dlp download failed", download_message)
        print(download_message, file=sys.stderr)
        return download_result.returncode

    written_path = extract_written_subtitle_path(download_result.stdout + "\n" + download_result.stderr)
    if written_path:
        normalized_path = normalize_reported_path(written_path, target_output_dir)
        print(f"Saved subtitle file: {normalized_path}", file=sys.stderr)
        append_log(current_log_path, f"Saved subtitle file: {normalized_path}")
    append_log(current_log_path, "Engine used: yt-dlp")
    if current_log_path is not None:
        print(f"Log file: {current_log_path}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
