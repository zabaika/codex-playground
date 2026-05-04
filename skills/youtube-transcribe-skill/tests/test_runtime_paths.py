from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest


RUNTIME_PATHS_PATH = (
    Path(__file__).resolve().parents[1] / "scripts" / "runtime_paths.py"
)
HELPER_PATH = (
    Path(__file__).resolve().parents[1] / "scripts" / "fetch_with_youtube_transcript_api.py"
)


def load_module(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


RUNTIME_PATHS = load_module(RUNTIME_PATHS_PATH, "youtube_transcribe_runtime_paths")
HELPER = load_module(HELPER_PATH, "youtube_transcribe_helper")


class YoutubeTranscribeRuntimePathTests(unittest.TestCase):
    def test_runtime_paths_resolve_relative_paths_from_project_root(self) -> None:
        with TemporaryDirectory() as tmpdir:
            tmp_root = Path(tmpdir)
            project_root = (tmp_root / "Playground").resolve(strict=False)
            skill_dir = tmp_root / ".codex" / "skills" / "youtube-transcribe-skill"
            config_dir = skill_dir / "config"
            config_dir.mkdir(parents=True)
            config_path = config_dir / "runtime.local.toml"
            config_path.write_text(
                "[paths]\n"
                f"project_root = '{project_root}'\n"
                "output_dir = 'scratch'\n"
                "log_file = 'scratch/youtube-transcribe.log'\n",
                encoding="utf-8",
            )

            resolved = RUNTIME_PATHS.resolve_runtime_paths(
                config_path=config_path,
                skill_dir=skill_dir,
            )

            self.assertEqual(resolved.output_dir, project_root / "scratch")
            self.assertEqual(
                resolved.log_file,
                project_root / "scratch" / "youtube-transcribe.log",
            )

    def test_relative_output_dir_fails_without_project_root(self) -> None:
        with TemporaryDirectory() as tmpdir:
            tmp_root = Path(tmpdir)
            skill_dir = tmp_root / ".codex" / "skills" / "youtube-transcribe-skill"
            config_dir = skill_dir / "config"
            config_dir.mkdir(parents=True)
            config_path = config_dir / "runtime.local.toml"
            config_path.write_text(
                "[paths]\n"
                "output_dir = 'scratch'\n",
                encoding="utf-8",
            )

            with self.assertRaisesRegex(ValueError, "paths.output_dir uses a relative path"):
                RUNTIME_PATHS.resolve_runtime_paths(
                    config_path=config_path,
                    skill_dir=skill_dir,
                )

    def test_helper_resolves_default_output_from_project_root_not_cwd(self) -> None:
        with TemporaryDirectory() as tmpdir:
            project_root = (Path(tmpdir) / "Playground").resolve(strict=False)
            resolved = HELPER.resolve_output_dir(None, str(project_root))
            self.assertEqual(resolved, project_root / "scratch")


if __name__ == "__main__":
    unittest.main()
