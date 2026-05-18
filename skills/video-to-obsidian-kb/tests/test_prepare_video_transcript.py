from __future__ import annotations

import importlib.util
import io
import sys
from contextlib import redirect_stdout
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest


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
    def test_print_route_block_emits_expected_lines(self) -> None:
        buffer = io.StringIO()

        with redirect_stdout(buffer):
            VIDEO.print_route_block(
                "engineering",
                "source combines a concrete company or system context with operating-model details",
            )

        self.assertEqual(
            buffer.getvalue(),
            "Route used: engineering\n"
            "Route reason: source combines a concrete company or system context with operating-model details\n",
        )

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
