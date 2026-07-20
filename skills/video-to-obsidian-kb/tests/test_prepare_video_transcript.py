from __future__ import annotations

import importlib.util
import io
import json
import sys
from contextlib import redirect_stdout
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest import mock


SCRIPT_PATH = Path(__file__).resolve().parents[1] / "scripts" / "prepare_video_transcript.py"
ARTICLE_RUNTIME_PATHS = (
    Path(__file__).resolve().parents[2]
    / "article-to-obsidian-kb"
    / "scripts"
    / "runtime_paths.py"
)


def load_module(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


VIDEO = load_module(SCRIPT_PATH, "prepare_video_transcript")


def build_offline_runtime(tmp_root: Path) -> tuple[Path, Path]:
    project_root = tmp_root / "Playground"
    article_skill_dir = tmp_root / "skills" / "article-to-obsidian-kb"
    transcribe_skill_dir = tmp_root / "skills" / "video-transcribe-skill"
    video_config_dir = tmp_root / "skills" / "video-to-obsidian-kb" / "config"

    article_config_dir = article_skill_dir / "config"
    transcribe_config_dir = transcribe_skill_dir / "config"
    article_config_dir.mkdir(parents=True)
    transcribe_config_dir.mkdir(parents=True)
    video_config_dir.mkdir(parents=True)

    article_config_path = article_config_dir / "runtime.local.toml"
    article_config_path.write_text(
        "[note_roots]\n"
        f"article = '{project_root}/vault/Ideas'\n"
        f"concept = '{project_root}/vault/Ideas/Concepts'\n",
        encoding="utf-8",
    )
    transcribe_config_path = transcribe_config_dir / "runtime.local.toml"
    transcribe_config_path.write_text("[paths]\nlog_file = 'scratch/video-transcribe.log'\n", encoding="utf-8")

    video_config_path = video_config_dir / "runtime.local.toml"
    video_config_path.write_text(
        "[skills]\n"
        f"article_to_obsidian_config = '{article_config_path}'\n"
        f"video_transcribe_config = '{transcribe_config_path}'\n"
        "[paths]\n"
        f"project_root = '{project_root}'\n"
        "prepared_transcripts_dir = 'scratch/video-to-obsidian-kb'\n"
        "log_file = 'scratch/video-to-obsidian-kb.log'\n",
        encoding="utf-8",
    )

    subtitle_path = tmp_root / "Demo video [dQw4w9WgXcQ].ru.srt"
    subtitle_path.write_text(
        "1\n"
        "00:00:00,000 --> 00:00:01,000\n"
        "Это не проблема\n\n"
        "2\n"
        "00:00:01,000 --> 00:00:02,000\n"
        "Настоящая проблема — это формулировка нежелательного явления\n",
        encoding="utf-8",
    )
    return video_config_path, subtitle_path


class VideoRuntimePathTests(unittest.TestCase):
    def test_wrapper_derives_project_root_from_sibling_article_config(self) -> None:
        with TemporaryDirectory() as tmpdir:
            tmp_root = Path(tmpdir)
            project_root = (tmp_root / "Playground").resolve(strict=False)
            article_skill_dir = tmp_root / ".codex" / "skills" / "article-to-obsidian-kb"
            video_skill_dir = tmp_root / ".codex" / "skills" / "video-to-obsidian-kb"

            article_config_dir = article_skill_dir / "config"
            article_scripts_dir = article_skill_dir / "scripts"
            article_config_dir.mkdir(parents=True)
            article_scripts_dir.mkdir(parents=True)
            (article_scripts_dir / "runtime_paths.py").write_text(
                ARTICLE_RUNTIME_PATHS.read_text(encoding="utf-8"),
                encoding="utf-8",
            )
            article_config_path = article_config_dir / "runtime.local.toml"
            article_config_path.write_text(
                "[paths]\n"
                "scratch_root = 'scratch/article-to-obsidian-kb'\n"
                f"kb_index_config = '{project_root}/tools/kb-index/config/runtime.local.toml'\n",
                encoding="utf-8",
            )

            resolved = VIDEO.project_root({}, video_skill_dir, article_config_path)

            self.assertEqual(resolved, project_root)


class VideoHumanOutputTests(unittest.TestCase):
    def test_json_summary_does_not_include_route_fields(self) -> None:
        with TemporaryDirectory() as tmpdir:
            video_config_path, subtitle_path = build_offline_runtime(Path(tmpdir))
            buffer = io.StringIO()

            with mock.patch.object(
                sys,
                "argv",
                [
                    "prepare_video_transcript.py",
                    "--url",
                    "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
                    "--config",
                    str(video_config_path),
                    "--subtitle-file",
                    str(subtitle_path),
                    "--json",
                ],
            ), redirect_stdout(buffer):
                self.assertEqual(VIDEO.main(), 0)

            payload = json.loads(buffer.getvalue())
            self.assertNotIn("route_used", payload)
            self.assertNotIn("route_reason", payload)
            self.assertEqual(payload["selected_subtitle_language"], "ru")
            self.assertTrue(Path(payload["prepared_transcript_file"]).exists())

    def test_human_summary_does_not_print_route_block(self) -> None:
        with TemporaryDirectory() as tmpdir:
            video_config_path, subtitle_path = build_offline_runtime(Path(tmpdir))
            buffer = io.StringIO()

            with mock.patch.object(
                sys,
                "argv",
                [
                    "prepare_video_transcript.py",
                    "--url",
                    "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
                    "--config",
                    str(video_config_path),
                    "--subtitle-file",
                    str(subtitle_path),
                ],
            ), redirect_stdout(buffer):
                self.assertEqual(VIDEO.main(), 0)

            output = buffer.getvalue()
            self.assertNotIn("Route used:", output)
            self.assertNotIn("Route reason:", output)
            self.assertIn("Prepared transcript file:", output)
            self.assertIn("Subtitle file:", output)
            self.assertIn("Engine used:", output)
            self.assertIn("Selected subtitle language:", output)

    def test_detect_video_platform_supports_youtube_and_vimeo(self) -> None:
        self.assertEqual(
            VIDEO.detect_video_platform("https://www.youtube.com/watch?v=dQw4w9WgXcQ"),
            "youtube",
        )
        self.assertEqual(
            VIDEO.detect_video_platform("https://vimeo.com/1188411048/a963501823"),
            "vimeo",
        )

    def test_extract_video_id_supports_vimeo_numeric_ids(self) -> None:
        self.assertEqual(
            VIDEO.extract_video_id("https://player.vimeo.com/video/1188411048"),
            "1188411048",
        )


if __name__ == "__main__":
    unittest.main()
