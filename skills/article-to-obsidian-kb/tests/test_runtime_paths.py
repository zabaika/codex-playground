from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest


SCRIPT_PATH = Path(__file__).resolve().parents[1] / "scripts" / "runtime_paths.py"


def load_module(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


RUNTIME_PATHS = load_module(SCRIPT_PATH, "article_runtime_paths")


class RuntimePathsTests(unittest.TestCase):
    def test_matching_local_anchors_do_not_require_explicit_project_root(self) -> None:
        with TemporaryDirectory() as tmpdir:
            tmp_root = Path(tmpdir)
            project_root = (tmp_root / "Playground").resolve(strict=False)
            skill_dir = project_root / "skills" / "article-to-obsidian-kb"
            config_dir = skill_dir / "config"
            config_dir.mkdir(parents=True)
            (project_root / "RULEBOOK.md").write_text("rulebook\n", encoding="utf-8")
            config_path = config_dir / "runtime.local.toml"
            config_path.write_text(
                "[paths]\n"
                "scratch_root = 'scratch/article-to-obsidian-kb'\n"
                f"kb_index_config = '{project_root}/tools/kb-index/config/runtime.local.toml'\n",
                encoding="utf-8",
            )

            resolved = RUNTIME_PATHS.resolve_runtime_paths(
                config_path=config_path,
                skill_dir=skill_dir,
            )

            self.assertEqual(resolved.project_root, project_root)
            self.assertEqual(
                resolved.scratch_root,
                project_root / "scratch" / "article-to-obsidian-kb",
            )

    def test_conflicting_local_anchors_require_explicit_project_root(self) -> None:
        with TemporaryDirectory() as tmpdir:
            tmp_root = Path(tmpdir)
            inferred_root = (tmp_root / "RepoRoot").resolve(strict=False)
            derived_root = (tmp_root / "DerivedRoot").resolve(strict=False)
            skill_dir = inferred_root / "skills" / "article-to-obsidian-kb"
            config_dir = skill_dir / "config"
            config_dir.mkdir(parents=True)
            (inferred_root / "RULEBOOK.md").write_text("rulebook\n", encoding="utf-8")
            config_path = config_dir / "runtime.local.toml"
            config_path.write_text(
                "[paths]\n"
                "scratch_root = 'scratch/article-to-obsidian-kb'\n"
                f"kb_index_config = '{derived_root}/tools/kb-index/config/runtime.local.toml'\n",
                encoding="utf-8",
            )

            with self.assertRaisesRegex(
                ValueError,
                "Project root could not be resolved unambiguously from local anchors",
            ):
                RUNTIME_PATHS.resolve_runtime_paths(
                    config_path=config_path,
                    skill_dir=skill_dir,
                )

    def test_resolves_project_root_from_absolute_kb_index_config(self) -> None:
        with TemporaryDirectory() as tmpdir:
            tmp_root = Path(tmpdir)
            project_root = (tmp_root / "Playground").resolve(strict=False)
            skill_dir = tmp_root / ".codex" / "skills" / "article-to-obsidian-kb"
            config_dir = skill_dir / "config"
            config_dir.mkdir(parents=True)
            config_path = config_dir / "runtime.local.toml"
            config_path.write_text(
                "[paths]\n"
                "scratch_root = 'scratch/article-to-obsidian-kb'\n"
                f"kb_index_config = '{project_root}/tools/kb-index/config/runtime.local.toml'\n",
                encoding="utf-8",
            )

            resolved = RUNTIME_PATHS.resolve_runtime_paths(
                config_path=config_path,
                skill_dir=skill_dir,
            )

            self.assertEqual(resolved.project_root, project_root)
            self.assertEqual(
                resolved.scratch_root,
                project_root / "scratch" / "article-to-obsidian-kb",
            )
            self.assertEqual(
                resolved.kb_index_config,
                project_root / "tools" / "kb-index" / "config" / "runtime.local.toml",
            )

    def test_relative_project_local_paths_fail_without_resolved_project_root(self) -> None:
        with TemporaryDirectory() as tmpdir:
            tmp_root = Path(tmpdir)
            skill_dir = tmp_root / ".codex" / "skills" / "article-to-obsidian-kb"
            config_dir = skill_dir / "config"
            config_dir.mkdir(parents=True)
            config_path = config_dir / "runtime.local.toml"
            config_path.write_text(
                "[paths]\n"
                "scratch_root = 'scratch/article-to-obsidian-kb'\n",
                encoding="utf-8",
            )

            with self.assertRaisesRegex(ValueError, "paths.scratch_root uses a relative path"):
                RUNTIME_PATHS.resolve_runtime_paths(
                    config_path=config_path,
                    skill_dir=skill_dir,
                )

    def test_relative_kb_index_config_resolves_from_project_root_not_cwd(self) -> None:
        with TemporaryDirectory() as tmpdir:
            tmp_root = Path(tmpdir)
            project_root = (tmp_root / "Playground").resolve(strict=False)
            skill_dir = tmp_root / ".codex" / "skills" / "article-to-obsidian-kb"
            config_dir = skill_dir / "config"
            config_dir.mkdir(parents=True)
            config_path = config_dir / "runtime.local.toml"
            config_path.write_text(
                "[paths]\n"
                f"project_root = '{project_root}'\n"
                "scratch_root = 'scratch/article-to-obsidian-kb'\n"
                "kb_index_config = 'tools/kb-index/config/runtime.local.toml'\n",
                encoding="utf-8",
            )

            resolved = RUNTIME_PATHS.resolve_runtime_paths(
                config_path=config_path,
                skill_dir=skill_dir,
            )

            self.assertEqual(
                resolved.kb_index_config,
                project_root / "tools" / "kb-index" / "config" / "runtime.local.toml",
            )


if __name__ == "__main__":
    unittest.main()
