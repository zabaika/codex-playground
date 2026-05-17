from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from kb_index.auto_update import (
    _sync_runtime_copy,
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
                run_total_timeout_seconds=120,
                termination_grace_seconds=5,
                poll_interval_seconds=0.5,
                timeout_exit_code=124,
                term_signal='TERM',
                kill_signal='KILL',
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
                run_total_timeout_seconds=120,
                termination_grace_seconds=5,
                poll_interval_seconds=0.5,
                timeout_exit_code=124,
                term_signal='TERM',
                kill_signal='KILL',
            )

            payload = render_runner_script(auto_update, root)

            self.assertIn('ROOT="$HOME/Library/Application Support/kb_index_service"', payload)
            self.assertIn(': "${KB_INDEX_PROJECT_ROOT:?KB_INDEX_PROJECT_ROOT is required}"', payload)
            self.assertIn('STARTUP_LOG="$KB_INDEX_PROJECT_ROOT/data/launchd/auto_update.startup.log"', payload)
            self.assertIn('resolve_python_bin()', payload)
            self.assertIn('arming kb-index ttl=120s grace=5s poll=0.5s', payload)
            self.assertIn(f"exec \"$PYTHON_BIN\" \"{service_root_for(auto_update) / 'common' / 'ttl_runner.py'}\" \\", payload)
            self.assertIn('--timeout-seconds "120" \\', payload)
            self.assertIn('--grace-seconds "5" \\', payload)
            self.assertIn('--poll-interval-seconds "0.5" \\', payload)
            self.assertIn('--timeout-exit-code "124" \\', payload)
            self.assertIn('--term-signal "TERM" \\', payload)
            self.assertIn('--kill-signal "KILL" \\', payload)
            self.assertIn(f"sys.path.insert(0, {str(service_root_for(auto_update) / 'src')!r})", payload)
            self.assertIn("from kb_index.cli import main", payload)
            self.assertIn(str(service_runtime_config_path_for(auto_update)), payload)

    def test_sync_runtime_copy_links_runtime_config_to_source_of_truth(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            project_root = root / 'project'
            service_root = root / 'service-root'
            common_root = root / 'common'
            src_root = project_root / 'src' / 'kb_index'
            config_dir = project_root / 'config'
            src_root.mkdir(parents=True)
            config_dir.mkdir(parents=True)
            (common_root / 'config').mkdir(parents=True)
            (src_root / '__init__.py').write_text('', encoding='utf-8')
            (common_root / '__init__.py').write_text('', encoding='utf-8')
            (common_root / 'sqlite.py').write_text('VALUE = 1\n', encoding='utf-8')
            (common_root / 'config' / 'sqlite.toml').write_text('[sqlite]\nbusy_timeout_ms = 5000\njournal_mode = "WAL"\nsynchronous = "NORMAL"\nforeign_keys = true\nautocommit = true\n', encoding='utf-8')
            config_path = config_dir / 'runtime.local.toml'
            config_path.write_text('[vault]\nroot = \'/tmp\'\n', encoding='utf-8')

            auto_update = AutoUpdateConfig(
                enabled=True,
                mode='launchd',
                interval_minutes=15,
                launchd_label='local.kb-index.auto-update',
                plist_path=root / 'local.kb-index.auto-update.plist',
                run_on_load=True,
                run_total_timeout_seconds=120,
                termination_grace_seconds=5,
                poll_interval_seconds=0.5,
                timeout_exit_code=124,
                term_signal='TERM',
                kill_signal='KILL',
            )

            with patch('kb_index.auto_update.service_root_for', return_value=service_root):
                runtime_paths = _sync_runtime_copy(auto_update, config_path, project_root)

            runtime_config_path = service_root / 'config' / 'runtime.local.toml'
            self.assertEqual(runtime_paths['runtime_config_path'], runtime_config_path)
            self.assertTrue(runtime_config_path.is_symlink())
            self.assertEqual(runtime_config_path.resolve(), config_path.resolve())
            self.assertTrue((service_root / 'common' / 'sqlite.py').exists())


if __name__ == '__main__':
    unittest.main()
