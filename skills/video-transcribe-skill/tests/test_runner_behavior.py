from __future__ import annotations

import importlib.util
import types
import sys
from pathlib import Path
import unittest
from unittest import mock


RUNNER_PATH = (
    Path(__file__).resolve().parents[1] / "scripts" / "run_video_transcribe.py"
)


def load_module(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


RUNNER = load_module(RUNNER_PATH, "video_transcribe_runner")


class VideoTranscribeRunnerBehaviorTests(unittest.TestCase):
    def test_detect_video_platform_supports_youtube_and_vimeo(self) -> None:
        self.assertEqual(
            RUNNER.detect_video_platform("https://www.youtube.com/watch?v=dQw4w9WgXcQ"),
            "youtube",
        )
        self.assertEqual(
            RUNNER.detect_video_platform("https://youtu.be/dQw4w9WgXcQ"),
            "youtube",
        )
        self.assertEqual(
            RUNNER.detect_video_platform("https://vimeo.com/1188411048/a963501823"),
            "vimeo",
        )
        self.assertEqual(
            RUNNER.detect_video_platform("https://player.vimeo.com/video/1188411048"),
            "vimeo",
        )

    def test_detect_video_platform_rejects_unsupported_hosts(self) -> None:
        with self.assertRaises(SystemExit):
            RUNNER.detect_video_platform("https://example.com/video/123")

    def test_vimeo_prefers_regular_subtitles_before_auto_subtitles(self) -> None:
        attempts = RUNNER.build_download_attempts("vimeo", "en-x-autogen", "srt/vtt/best")
        self.assertEqual(attempts[0][:1], ["--write-subs"])
        self.assertEqual(attempts[1][:2], ["--write-auto-sub", "--write-sub"])

    def test_vimeo_skips_youtube_provider_auth(self) -> None:
        config = {
            "auth": {"mode": "provider-script"},
            "provider": {"plugin_dir": "/definitely/missing"},
        }
        auth_args, env_updates = RUNNER.build_auth_args(config, platform="vimeo")
        self.assertEqual(auth_args, [])
        self.assertEqual(env_updates.get("YTDLP_NO_PLUGINS"), "1")

    def test_youtube_prefers_auto_plus_regular_path_first(self) -> None:
        attempts = RUNNER.build_download_attempts("youtube", "en", "srt/vtt/best")
        self.assertEqual(attempts[0][:2], ["--write-auto-sub", "--write-sub"])

    def test_choose_language_matches_language_family_suffixes(self) -> None:
        available = ["en-x-autogen", "de", "fr"]
        priority = ["orig", "ru", "en"]
        self.assertEqual(RUNNER.choose_language(available, priority), "en-x-autogen")

    def test_convert_vtt_text_to_srt(self) -> None:
        vtt_text = """WEBVTT

00:00:01.000 --> 00:00:03.500 align:start position:0%
Hello world

1
00:00:05.000 --> 00:00:07.250
Second cue
"""
        expected = (
            "1\n00:00:01,000 --> 00:00:03,500\nHello world\n\n"
            "2\n00:00:05,000 --> 00:00:07,250\nSecond cue\n"
        )
        self.assertEqual(RUNNER.convert_vtt_text_to_srt(vtt_text), expected)

    def test_infer_subtitle_source_type_detects_auto_generated(self) -> None:
        listing = """[info] Available subtitles for 1188411048:
Language     Name                                                Formats
en-x-autogen English (auto-generated), unknown, unknown, unknown vtt
"""
        self.assertEqual(
            RUNNER.infer_subtitle_source_type("en-x-autogen", listing),
            "auto-generated",
        )

    def test_youtube_transcript_api_main_path_passes_project_root(self) -> None:
        config = {
            "engine": {"order": ["youtube-transcript-api"]},
            "subtitles": {"language_priority": ["orig", "ru", "en", "uk"]},
        }

        class FakeResolvedRuntime:
            def __init__(self) -> None:
                self.config = config
                self.output_dir = Path("/tmp/project-root/scratch")
                self.log_file = None
                self.project_root = Path("/tmp/project-root")

        captured_args: list[str] = []
        fake_runtime_paths = types.SimpleNamespace(
            resolve_runtime_paths=lambda **kwargs: FakeResolvedRuntime()
        )

        def fake_run_command_with_retry(args, **kwargs):
            captured_args.extend(args)
            return mock.Mock(returncode=1, stdout="", stderr=""), "no_subtitles", "No subtitles are available for this video."

        with mock.patch.object(RUNNER, "vendored_yta_python", return_value=Path(sys.executable)), \
             mock.patch.object(RUNNER, "build_auth_args", return_value=([], {"YTDLP_NO_PLUGINS": "1"})), \
             mock.patch.object(RUNNER, "build_network_args", return_value=[]), \
             mock.patch.object(RUNNER, "retry_settings", return_value={"attempts": 1, "initial_delay_seconds": 0.0, "backoff_multiplier": 1.0, "max_delay_seconds": 0.0}), \
             mock.patch.object(RUNNER, "fetch_video_title", return_value=""), \
             mock.patch.object(RUNNER, "run_command_with_retry", side_effect=fake_run_command_with_retry), \
             mock.patch.object(RUNNER, "log_failure"), \
             mock.patch.object(RUNNER, "append_log"), \
             mock.patch.object(RUNNER, "build_log_path", return_value=None), \
             mock.patch.object(RUNNER, "ensure_directory", side_effect=lambda path, label: path), \
             mock.patch.object(RUNNER, "detect_video_platform", return_value="youtube"), \
             mock.patch.object(RUNNER, "subtitles_priority", return_value=["orig", "ru", "en", "uk"]), \
             mock.patch.object(RUNNER, "engine_order", return_value=["youtube-transcript-api"]), \
             mock.patch.dict(sys.modules, {"runtime_paths": fake_runtime_paths}), \
             mock.patch("sys.argv", ["run_video_transcribe.py", "--url", "https://www.youtube.com/watch?v=So5lre3ioVM"]):
            exit_code = RUNNER.main()

        self.assertEqual(exit_code, 1)
        self.assertIn("--project-root", captured_args)
        project_root_index = captured_args.index("--project-root")
        self.assertEqual(captured_args[project_root_index + 1], "/tmp/project-root")


if __name__ == "__main__":
    unittest.main()
