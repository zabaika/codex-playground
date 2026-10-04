"""Runtime configuration loaded from local, untracked TOML."""

from __future__ import annotations

import tomllib
from dataclasses import dataclass
from datetime import datetime, time
from pathlib import Path
import re


PROJECT_ROOT = Path(__file__).resolve().parent
INFOEXT_CAPTCHA_CHARACTER_WHITELIST = "abcdefghijklmnopqrstuvwxyz0123456789"


class ConfigurationError(ValueError):
    """Raised when the local monitor configuration is incomplete or invalid."""


def _required(config: dict[str, object], section: str, name: str) -> str:
    value = str(config.get(section, {}).get(name, "")).strip()
    if not value:
        raise ConfigurationError(f"Missing {section}.{name}; set it in config/runtime.local.toml.")
    return value


def _date(config: dict[str, object], section: str, name: str) -> str:
    value = _required(config, section, name)
    if not re.fullmatch(r"\d{2}/\d{2}/\d{4}", value):
        raise ConfigurationError(f"{section}.{name} must use the DD/MM/YYYY format.")
    try:
        datetime.strptime(value, "%d/%m/%Y")
    except ValueError as exc:
        raise ConfigurationError(f"{section}.{name} must be a real calendar date.") from exc
    return value


def _positive_int(config: dict[str, object], section: str, name: str, *, minimum: int, maximum: int) -> int:
    raw = config.get(section, {}).get(name)
    try:
        value = int(raw)
    except (TypeError, ValueError) as exc:
        raise ConfigurationError(f"{section}.{name} must be an integer.") from exc
    if not minimum <= value <= maximum:
        raise ConfigurationError(f"{section}.{name} must be between {minimum} and {maximum}.")
    return value


def _float(config: dict[str, object], section: str, name: str, *, minimum: float, maximum: float) -> float:
    raw = config.get(section, {}).get(name)
    try:
        value = float(raw)
    except (TypeError, ValueError) as exc:
        raise ConfigurationError(f"{section}.{name} must be a number.") from exc
    if not minimum <= value <= maximum:
        raise ConfigurationError(f"{section}.{name} must be between {minimum} and {maximum}.")
    return value


def _bool(config: dict[str, object], section: str, name: str) -> bool:
    value = config.get(section, {}).get(name)
    if not isinstance(value, bool):
        raise ConfigurationError(f"{section}.{name} must be true or false.")
    return value


def _debug_mode(config: dict[str, object]) -> bool:
    value = _required(config, "infoext", "debug")
    if value not in {"enable", "disable"}:
        raise ConfigurationError('infoext.debug must be "enable" or "disable".')
    return value == "enable"


def _int_list(config: dict[str, object], section: str, name: str, *, minimum: int, maximum: int) -> tuple[int, ...]:
    values = config.get(section, {}).get(name)
    if not isinstance(values, list) or not values:
        raise ConfigurationError(f"{section}.{name} must be a non-empty integer list.")
    if not all(isinstance(value, int) and minimum <= value <= maximum for value in values):
        raise ConfigurationError(f"{section}.{name} values must be integers between {minimum} and {maximum}.")
    return tuple(values)


def _weekdays(config: dict[str, object]) -> tuple[int, ...]:
    values = _int_list(config, "launchd", "weekdays", minimum=0, maximum=7)
    normalized = tuple(7 if value == 0 else value for value in values)
    if len(set(normalized)) != len(normalized):
        raise ConfigurationError("launchd.weekdays must not contain duplicate days.")
    return normalized


def _clock_time(config: dict[str, object], section: str, name: str) -> time:
    raw = str(config.get(section, {}).get(name, "")).strip()
    if not re.fullmatch(r"\d{2}:\d{2}", raw):
        raise ConfigurationError(f"{section}.{name} must use the HH:MM 24-hour format.")
    hour, minute = (int(part) for part in raw.split(":", 1))
    if hour > 23 or minute > 59:
        raise ConfigurationError(f"{section}.{name} must be a valid local time.")
    return time(hour=hour, minute=minute)


@dataclass(frozen=True)
class OCRSettings:
    character_whitelist: str
    scale: int
    contrast: float
    median_filter_size: int
    expected_length_min: int
    expected_length_max: int
    vision_timeout_seconds: int
    vision_minimum_confidence: float
    vision_fallback_minimum_confidence: float
    vision_fallback_scales: tuple[int, ...]
    vision_fallback_median_filter_sizes: tuple[int, ...]
    vision_fallback_minimum_consensus_votes: int
    vision_minimum_text_height: float
    vision_language: str
    vision_recognition_level: str
    vision_diagnostic_candidate_limit: int
    vision_language_correction: bool
    vision_use_confidence: bool


@dataclass(frozen=True)
class LaunchdSettings:
    first_run_time: time
    interval_hours: int
    last_run_time: time
    weekdays: tuple[int, ...]

    @property
    def calendar_times(self) -> tuple[time, ...]:
        start_minutes = self.first_run_time.hour * 60 + self.first_run_time.minute
        end_minutes = self.last_run_time.hour * 60 + self.last_run_time.minute
        interval_minutes = self.interval_hours * 60
        return tuple(
            time(hour=minutes // 60, minute=minutes % 60)
            for minutes in range(start_minutes, end_minutes + 1, interval_minutes)
        )

    def contains(self, candidate: time) -> bool:
        start_minutes = self.first_run_time.hour * 60 + self.first_run_time.minute
        end_minutes = self.last_run_time.hour * 60 + self.last_run_time.minute
        candidate_minutes = candidate.hour * 60 + candidate.minute
        return start_minutes <= candidate_minutes <= end_minutes

    def allows_weekday(self, candidate: datetime) -> bool:
        return candidate.isoweekday() in self.weekdays


@dataclass(frozen=True)
class Settings:
    project_root: Path
    nie: str
    fecha_presentacion: str
    ano_nacimiento: str
    captcha_max_attempts: int
    portal_min_check_interval_seconds: int
    timeout_seconds: int
    captcha_retry_delay_seconds: float
    result_settle_delay_seconds: float
    failure_alert_threshold: int
    notify_on_unchanged_status: bool
    debug_submitted_captcha_responses: bool
    launchd: LaunchdSettings
    ocr: OCRSettings
    telegram_connector_dir: Path

    @property
    def masked_nie(self) -> str:
        if len(self.nie) <= 4:
            return "****"
        return f"{self.nie[:2]}{'*' * (len(self.nie) - 4)}{self.nie[-2:]}"


def load_runtime_config(runtime_file: Path) -> dict[str, object]:
    if not runtime_file.is_file():
        raise ConfigurationError(
            "Missing config/runtime.local.toml; copy config/runtime.example.toml and fill it in."
        )
    try:
        config = tomllib.loads(runtime_file.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise ConfigurationError("Cannot read config/runtime.local.toml.") from exc
    required_sections = ("infoext", "telegram_connector", "ocr", "launchd")
    if not all(isinstance(config.get(section), dict) for section in required_sections):
        raise ConfigurationError(
            "config/runtime.local.toml must contain [infoext], [telegram_connector], [ocr] and [launchd]."
        )
    return config


def load_settings(project_root: Path = PROJECT_ROOT, *, require_infoext: bool = True) -> Settings:
    runtime_file = project_root / "config" / "runtime.local.toml"
    config = load_runtime_config(runtime_file)
    connector_raw = _required(config, "telegram_connector", "project_root")
    connector_dir = Path(connector_raw).expanduser()
    if not connector_dir.is_absolute():
        connector_dir = (runtime_file.parent / connector_dir).resolve()
    retry_delay = _float(config, "infoext", "captcha_retry_delay_seconds", minimum=0, maximum=30)
    median_filter_size = _positive_int(
        config, "ocr", "median_filter_size", minimum=1, maximum=9
    )
    scale = _positive_int(config, "ocr", "scale", minimum=1, maximum=8)
    if median_filter_size % 2 == 0:
        raise ConfigurationError("ocr.median_filter_size must be odd.")
    expected_length_min = _positive_int(
        config, "ocr", "expected_length_min", minimum=1, maximum=20
    )
    expected_length_max = _positive_int(
        config, "ocr", "expected_length_max", minimum=1, maximum=20
    )
    if expected_length_max < expected_length_min:
        raise ConfigurationError("ocr.expected_length_max must be at least the configured minimum.")
    if (expected_length_min, expected_length_max) != (5, 5):
        raise ConfigurationError("InfoExt CAPTCHA length is fixed at five characters.")
    character_whitelist = _required(config, "ocr", "character_whitelist")
    if character_whitelist != INFOEXT_CAPTCHA_CHARACTER_WHITELIST:
        raise ConfigurationError(
            "ocr.character_whitelist must contain only lowercase Latin letters and digits."
        )
    vision_recognition_level = _required(config, "ocr", "vision_recognition_level")
    if vision_recognition_level not in {"accurate", "fast"}:
        raise ConfigurationError("ocr.vision_recognition_level must be accurate or fast.")
    vision_use_confidence = _bool(config, "ocr", "vision_use_confidence")
    fallback_scales = _int_list(config, "ocr", "vision_fallback_scales", minimum=1, maximum=8)
    fallback_median_filter_sizes = _int_list(
        config, "ocr", "vision_fallback_median_filter_sizes", minimum=1, maximum=9
    )
    if any(size % 2 == 0 for size in fallback_median_filter_sizes):
        raise ConfigurationError("ocr.vision_fallback_median_filter_sizes must contain only odd values.")
    if len(set(fallback_scales)) != len(fallback_scales):
        raise ConfigurationError("ocr.vision_fallback_scales must not contain duplicates.")
    if len(set(fallback_median_filter_sizes)) != len(fallback_median_filter_sizes):
        raise ConfigurationError("ocr.vision_fallback_median_filter_sizes must not contain duplicates.")
    if scale not in fallback_scales:
        raise ConfigurationError("ocr.vision_fallback_scales must include ocr.scale.")
    if median_filter_size not in fallback_median_filter_sizes:
        raise ConfigurationError(
            "ocr.vision_fallback_median_filter_sizes must include ocr.median_filter_size."
        )
    if not vision_use_confidence and len(fallback_scales) * len(fallback_median_filter_sizes) < 2:
        raise ConfigurationError("OCR without confidence requires at least two Vision variants.")
    vision_minimum_confidence = _float(
        config, "ocr", "vision_minimum_confidence", minimum=0, maximum=1
    )
    vision_fallback_minimum_confidence = _float(
        config, "ocr", "vision_fallback_minimum_confidence", minimum=0, maximum=1
    )
    if vision_fallback_minimum_confidence > vision_minimum_confidence:
        raise ConfigurationError(
            "ocr.vision_fallback_minimum_confidence must not exceed ocr.vision_minimum_confidence."
        )
    ano_nacimiento = str(config["infoext"].get("ano_nacimiento", "")).strip()
    if ano_nacimiento and (not re.fullmatch(r"[0-9]{4}", ano_nacimiento) or int(ano_nacimiento) == 0):
        raise ConfigurationError("infoext.ano_nacimiento must be blank or a four-digit birth year.")
    first_run_time = _clock_time(config, "launchd", "first_run_time")
    last_run_time = _clock_time(config, "launchd", "last_run_time")
    if last_run_time < first_run_time:
        raise ConfigurationError("launchd.last_run_time must not be earlier than launchd.first_run_time.")
    launchd = LaunchdSettings(
        first_run_time=first_run_time,
        interval_hours=_positive_int(config, "launchd", "interval_hours", minimum=1, maximum=12),
        last_run_time=last_run_time,
        weekdays=_weekdays(config),
    )
    ocr = OCRSettings(
        character_whitelist=character_whitelist,
        scale=scale,
        contrast=_float(config, "ocr", "contrast", minimum=0.1, maximum=10),
        median_filter_size=median_filter_size,
        expected_length_min=expected_length_min,
        expected_length_max=expected_length_max,
        vision_timeout_seconds=_positive_int(
            config, "ocr", "vision_timeout_seconds", minimum=1, maximum=120
        ),
        vision_minimum_confidence=vision_minimum_confidence,
        vision_fallback_minimum_confidence=vision_fallback_minimum_confidence,
        vision_fallback_scales=fallback_scales,
        vision_fallback_median_filter_sizes=fallback_median_filter_sizes,
        vision_fallback_minimum_consensus_votes=_positive_int(
            config,
            "ocr",
            "vision_fallback_minimum_consensus_votes",
            minimum=1 if vision_use_confidence else 2,
            maximum=len(fallback_scales) * len(fallback_median_filter_sizes),
        ),
        vision_minimum_text_height=_float(
            config, "ocr", "vision_minimum_text_height", minimum=0, maximum=1
        ),
        vision_language=_required(config, "ocr", "vision_language"),
        vision_recognition_level=vision_recognition_level,
        vision_diagnostic_candidate_limit=_positive_int(
            config, "ocr", "vision_diagnostic_candidate_limit", minimum=1, maximum=10
        ),
        vision_language_correction=_bool(config, "ocr", "vision_language_correction"),
        vision_use_confidence=vision_use_confidence,
    )
    return Settings(
        project_root=project_root,
        nie=_required(config, "infoext", "nie") if require_infoext else "",
        fecha_presentacion=_date(config, "infoext", "fecha_presentacion") if require_infoext else "",
        ano_nacimiento=ano_nacimiento,
        captcha_max_attempts=_positive_int(
            config, "infoext", "captcha_max_attempts", minimum=1, maximum=5
        ),
        portal_min_check_interval_seconds=_positive_int(
            config, "infoext", "portal_min_check_interval_seconds", minimum=1, maximum=86400
        ),
        timeout_seconds=_positive_int(
            config, "infoext", "run_timeout_seconds", minimum=30, maximum=900
        ),
        captcha_retry_delay_seconds=retry_delay,
        result_settle_delay_seconds=_float(
            config, "infoext", "result_settle_delay_seconds", minimum=0, maximum=30
        ),
        failure_alert_threshold=_positive_int(
            config, "infoext", "failure_alert_threshold", minimum=1, maximum=100
        ),
        notify_on_unchanged_status=_bool(config, "infoext", "notify_on_unchanged_status"),
        debug_submitted_captcha_responses=_debug_mode(config),
        launchd=launchd,
        ocr=ocr,
        telegram_connector_dir=connector_dir,
    )
