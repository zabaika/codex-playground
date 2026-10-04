from pathlib import Path

import pytest

from config import ConfigurationError, load_settings


RUNTIME_SUFFIX = """
[telegram_connector]
project_root = "../telegram_connector"

[ocr]
character_whitelist = "abcdefghijklmnopqrstuvwxyz0123456789"
scale = 5
contrast = 2.5
median_filter_size = 3
expected_length_min = 5
expected_length_max = 5
vision_timeout_seconds = 30
vision_minimum_confidence = 1.0
vision_fallback_minimum_confidence = 0.5
vision_fallback_scales = [5]
vision_fallback_median_filter_sizes = [1, 3]
vision_fallback_minimum_consensus_votes = 2
vision_minimum_text_height = 0.0
vision_language = "en-US"
vision_recognition_level = "accurate"
vision_diagnostic_candidate_limit = 5
vision_language_correction = false
vision_use_confidence = true

[launchd]
first_run_time = "10:00"
interval_hours = 5
last_run_time = "20:00"
weekdays = [1, 2, 3, 4, 5]
"""


def test_load_settings_uses_local_toml_file(tmp_path: Path) -> None:
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    (config_dir / "runtime.local.toml").write_text(
        "[infoext]\nnie = \"Z12AB34C\"\nfecha_presentacion = \"01/02/2026\"\n"
        "captcha_max_attempts = 5\nportal_min_check_interval_seconds = 60\nrun_timeout_seconds = 240\n"
        "captcha_retry_delay_seconds = 1.0\n"
        "result_settle_delay_seconds = 0.35\nfailure_alert_threshold = 3\n"
        "notify_on_unchanged_status = true\ndebug = \"enable\"\n"
        + RUNTIME_SUFFIX.replace("vision_language_correction = false", "vision_language_correction = true").replace("vision_use_confidence = true", "vision_use_confidence = false"),
        encoding="utf-8",
    )

    settings = load_settings(tmp_path)

    assert settings.masked_nie == "Z1****4C"
    assert settings.captcha_max_attempts == 5
    assert settings.portal_min_check_interval_seconds == 60
    assert settings.captcha_retry_delay_seconds == 1.0
    assert settings.result_settle_delay_seconds == 0.35
    assert settings.failure_alert_threshold == 3
    assert settings.notify_on_unchanged_status is True
    assert settings.debug_submitted_captcha_responses is True
    assert settings.launchd.weekdays == (1, 2, 3, 4, 5)
    assert [value.strftime("%H:%M") for value in settings.launchd.calendar_times] == [
        "10:00",
        "15:00",
        "20:00",
    ]
    assert settings.ocr.vision_diagnostic_candidate_limit == 5
    assert settings.ocr.vision_language_correction is True
    assert settings.ocr.vision_use_confidence is False
    assert settings.ano_nacimiento == ""
    assert settings.ocr.vision_minimum_confidence == 1.0
    assert settings.ocr.vision_fallback_minimum_confidence == 0.5
    assert settings.ocr.vision_fallback_scales == (5,)
    assert settings.ocr.vision_fallback_median_filter_sizes == (1, 3)
    assert settings.ocr.vision_fallback_minimum_consensus_votes == 2
    assert settings.telegram_connector_dir == tmp_path / "telegram_connector"


def test_load_settings_rejects_missing_identity(tmp_path: Path) -> None:
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    (config_dir / "runtime.local.toml").write_text(
        "[infoext]\nfecha_presentacion = \"01/02/2026\"\n"
        "captcha_max_attempts = 5\nportal_min_check_interval_seconds = 60\nrun_timeout_seconds = 240\n"
        "captcha_retry_delay_seconds = 1.0\n"
        "result_settle_delay_seconds = 0.35\nfailure_alert_threshold = 3\n"
        "notify_on_unchanged_status = true\ndebug = \"disable\"\n"
        + RUNTIME_SUFFIX,
        encoding="utf-8",
    )

    with pytest.raises(ConfigurationError, match="infoext.nie"):
        load_settings(tmp_path)


def test_load_settings_rejects_placeholder_or_invalid_submission_date(tmp_path: Path) -> None:
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    (config_dir / "runtime.local.toml").write_text(
        "[infoext]\nnie = \"Z12AB34C\"\nfecha_presentacion = \"DD/MM/YYYY\"\n"
        "captcha_max_attempts = 5\nportal_min_check_interval_seconds = 60\nrun_timeout_seconds = 240\n"
        "captcha_retry_delay_seconds = 1.0\n"
        "result_settle_delay_seconds = 0.35\nfailure_alert_threshold = 3\n"
        "notify_on_unchanged_status = true\ndebug = \"disable\"\n"
        + RUNTIME_SUFFIX,
        encoding="utf-8",
    )

    with pytest.raises(ConfigurationError, match="fecha_presentacion"):
        load_settings(tmp_path)


def test_load_settings_requires_five_character_captcha(tmp_path: Path) -> None:
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    (config_dir / "runtime.local.toml").write_text(
        "[infoext]\nnie = \"Z12AB34C\"\nfecha_presentacion = \"01/02/2026\"\n"
        "captcha_max_attempts = 3\nportal_min_check_interval_seconds = 60\nrun_timeout_seconds = 240\n"
        "captcha_retry_delay_seconds = 1.0\n"
        "result_settle_delay_seconds = 0.35\nfailure_alert_threshold = 3\n"
        "notify_on_unchanged_status = true\ndebug = \"disable\"\n"
        + RUNTIME_SUFFIX.replace("expected_length_max = 5", "expected_length_max = 6"),
        encoding="utf-8",
    )

    with pytest.raises(ConfigurationError, match="fixed at five"):
        load_settings(tmp_path)


def test_load_settings_rejects_uppercase_captcha_alphabet(tmp_path: Path) -> None:
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    (config_dir / "runtime.local.toml").write_text(
        "[infoext]\nnie = \"Z12AB34C\"\nfecha_presentacion = \"01/02/2026\"\n"
        "captcha_max_attempts = 3\nportal_min_check_interval_seconds = 60\nrun_timeout_seconds = 240\n"
        "captcha_retry_delay_seconds = 1.0\n"
        "result_settle_delay_seconds = 0.35\nfailure_alert_threshold = 3\n"
        "notify_on_unchanged_status = true\ndebug = \"disable\"\n"
        + RUNTIME_SUFFIX.replace(
            'character_whitelist = "abcdefghijklmnopqrstuvwxyz0123456789"',
            'character_whitelist = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789"',
        ),
        encoding="utf-8",
    )

    with pytest.raises(ConfigurationError, match="lowercase Latin letters and digits"):
        load_settings(tmp_path)


def test_load_settings_rejects_invalid_launchd_window(tmp_path: Path) -> None:
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    (config_dir / "runtime.local.toml").write_text(
        "[infoext]\nnie = \"Z12AB34C\"\nfecha_presentacion = \"01/02/2026\"\n"
        "captcha_max_attempts = 5\nportal_min_check_interval_seconds = 60\nrun_timeout_seconds = 240\n"
        "captcha_retry_delay_seconds = 1.0\n"
        "result_settle_delay_seconds = 0.35\nfailure_alert_threshold = 3\n"
        "notify_on_unchanged_status = true\ndebug = \"disable\"\n"
        + RUNTIME_SUFFIX.replace('last_run_time = "20:00"', 'last_run_time = "09:00"'),
        encoding="utf-8",
    )

    with pytest.raises(ConfigurationError, match="last_run_time"):
        load_settings(tmp_path)
