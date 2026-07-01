import os
import tempfile
import unittest
from pathlib import Path

from telegram_shared.paths import resolve_app_paths


class SharedPathsTests(unittest.TestCase):
    def test_resolve_app_paths_defaults_runtime_to_project_root(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            module_file = Path(tmp_dir) / "app" / "bridge.py"
            module_file.parent.mkdir()
            paths = resolve_app_paths(str(module_file), project_root_env_var="MISSING_PROJECT_ROOT")

        expected_app_dir = module_file.parent.resolve()
        self.assertEqual(paths.app_dir, expected_app_dir)
        self.assertEqual(paths.project_root, expected_app_dir)
        self.assertEqual(paths.base_dir, expected_app_dir)
        self.assertEqual(paths.runtime_local_file, expected_app_dir / "config" / "runtime.local.toml")

    def test_resolve_app_paths_can_keep_runtime_next_to_module(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            module_file = Path(tmp_dir) / "app" / "bridge.py"
            override_root = Path(tmp_dir) / "runtime-root"
            module_file.parent.mkdir()
            original = os.environ.get("TEST_PROJECT_ROOT")
            os.environ["TEST_PROJECT_ROOT"] = str(override_root)
            try:
                paths = resolve_app_paths(
                    str(module_file),
                    project_root_env_var="TEST_PROJECT_ROOT",
                    runtime_base="app_dir",
                )
            finally:
                if original is None:
                    os.environ.pop("TEST_PROJECT_ROOT", None)
                else:
                    os.environ["TEST_PROJECT_ROOT"] = original

        expected_app_dir = module_file.parent.resolve()
        self.assertEqual(paths.project_root, override_root)
        self.assertEqual(paths.base_dir, expected_app_dir)
        self.assertEqual(paths.data_dir, expected_app_dir / "data")


if __name__ == "__main__":
    unittest.main()
