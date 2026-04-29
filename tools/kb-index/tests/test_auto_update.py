from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from kb_index.auto_update import (
    project_launchd_log_dir_for,
    render_launchd_plist,
    render_runner_script,
    service_root_for,
    service_runner_path_for,
    service_runtime_config_path_for,
)
from kb_index.config import AutoUpdateConfig


class AutoUpdateTests(unittest.TestCase):
    def test_render_launchd_plist_uses_single_canonical_update_command(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            auto_update = AutoUpdateConfig(
                enabled=True,
                mode='launchd',
                interval_minutes=15,
                launchd_label='local.kb-index.auto-update',
                plist_path=root / 'local.kb-index.auto-update.plist',
                run_on_load=True,
            )
            payload = render_launchd_plist(auto_update, root)

            self.assertIn('<string>local.kb-index.auto-update</string>', payload)
            self.assertIn(
                f'<string>{service_runner_path_for(auto_update)}</string>',
                payload,
            )
            self.assertIn('<integer>900</integer>', payload)
            self.assertIn('<true/>', payload)
            self.assertIn(f'<string>{service_root_for(auto_update)}</string>', payload)
            self.assertIn(
                f'<string>{project_launchd_log_dir_for(root) / "auto_update.stdout.log"}</string>',
                payload,
            )
            self.assertIn('<key>KB_INDEX_PROJECT_ROOT</key>', payload)
            self.assertIn(f'<string>{root}</string>', payload)

    def test_render_runner_script_invokes_canonical_update_module(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            auto_update = AutoUpdateConfig(
                enabled=True,
                mode='launchd',
                interval_minutes=15,
                launchd_label='local.kb-index.auto-update',
                plist_path=root / 'local.kb-index.auto-update.plist',
                run_on_load=True,
            )

            payload = render_runner_script(auto_update, root)

            self.assertIn('ROOT="$HOME/Library/Application Support/kb_index_service"', payload)
            self.assertIn(': "${KB_INDEX_PROJECT_ROOT:?KB_INDEX_PROJECT_ROOT is required}"', payload)
            self.assertIn('STARTUP_LOG="$KB_INDEX_PROJECT_ROOT/data/launchd/auto_update.startup.log"', payload)
            self.assertIn('resolve_python_bin()', payload)
            self.assertIn("exec \"$PYTHON_BIN\" -c", payload)
            self.assertIn(f"sys.path.insert(0, {str(service_root_for(auto_update) / 'src')!r})", payload)
            self.assertIn("from kb_index.cli import main", payload)
            self.assertIn(str(service_runtime_config_path_for(auto_update)), payload)


if __name__ == '__main__':
    unittest.main()
