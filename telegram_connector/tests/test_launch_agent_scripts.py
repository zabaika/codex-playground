import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
INSTALLER = ROOT / "scripts" / "install_launch_agent.sh"
RESTARTER = ROOT / "scripts" / "restart_launch_agent.sh"


class LaunchAgentScriptTests(unittest.TestCase):
    def test_installer_writes_logs_into_project_data_dir(self) -> None:
        content = INSTALLER.read_text(encoding="utf-8")
        self.assertIn('PROJECT_LOG_DIR="$SOURCE_ROOT/data/launchd"', content)
        self.assertIn('STARTUP_LOG="$PROJECT_LOG_DIR/bridge.startup.log"', content)
        self.assertIn('STDOUT_LOG="$PROJECT_LOG_DIR/bridge.stdout.log"', content)
        self.assertIn('STDERR_LOG="$PROJECT_LOG_DIR/bridge.stderr.log"', content)
        self.assertIn('DIGEST_LAST_ATTEMPT_LOG="$PROJECT_LOG_DIR/digest.last_attempt.json"', content)
        self.assertIn('rm -f "$PROJECT_LOG_DIR"/bridge.startup.log', content)
        self.assertNotIn('"$SERVICE_ROOT"/data/launchd', content)

    def test_installer_runner_appends_startup_log(self) -> None:
        content = INSTALLER.read_text(encoding="utf-8")
        self.assertIn('SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"', content)
        self.assertIn('SOURCE_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"', content)
        self.assertIn('resolve_python_bin()', content)
        self.assertIn('PYTHON_BIN="$(resolve_python_bin)"', content)
        self.assertIn(': "\\${TELEGRAM_CONNECTOR_PROJECT_ROOT:?TELEGRAM_CONNECTOR_PROJECT_ROOT is required}"', content)
        self.assertIn('printf \'[%s] starting telegram bridge from %s\\n\'', content)
        self.assertIn('STARTUP_LOG="\\$TELEGRAM_CONNECTOR_PROJECT_ROOT/data/launchd/bridge.startup.log"', content)
        self.assertIn('exec "$PYTHON_BIN" "\\$ROOT/telegram_bridge.py" listen --run-commands', content)
        self.assertIn('AUDIT_LOG="\\$TELEGRAM_CONNECTOR_PROJECT_ROOT/data/launchd/digest.last_attempt.json"', content)
        self.assertIn('arming digest ttl=%ss grace=%ss', content)
        self.assertIn('exec "$PYTHON_BIN" "\\$ROOT/common/ttl_runner.py" \\', content)
        self.assertIn('--audit-file "\\$AUDIT_LOG" \\', content)
        self.assertIn('--use-caffeinate \\', content)
        self.assertIn('-- "$PYTHON_BIN" "\\$ROOT/telegram_digest.py" run', content)

    def test_installer_sets_project_root_env_in_plist(self) -> None:
        content = INSTALLER.read_text(encoding="utf-8")
        self.assertIn("<key>EnvironmentVariables</key>", content)
        self.assertIn("<key>TELEGRAM_CONNECTOR_PROJECT_ROOT</key>", content)
        self.assertIn("<string>$SOURCE_ROOT</string>", content)

    def test_restart_script_reloads_launch_agent(self) -> None:
        content = RESTARTER.read_text(encoding="utf-8")
        self.assertIn('launchctl unload "$PLIST_PATH"', content)
        self.assertIn('launchctl load "$PLIST_PATH"', content)
        self.assertIn('com.zabaika.telegram-connector-bridge', content)

    def test_launch_agent_installer_copies_digest_script(self) -> None:
        content = INSTALLER.read_text(encoding="utf-8")
        self.assertIn('cp "$SOURCE_ROOT/telegram_digest.py" "$SERVICE_ROOT/telegram_digest.py"', content)
        self.assertIn('cp -R "$SOURCE_ROOT/../common" "$SERVICE_ROOT/common"', content)

    def test_installer_creates_digest_launch_agent_with_calendar_schedule(self) -> None:
        content = INSTALLER.read_text(encoding="utf-8")
        self.assertIn('DIGEST_PLIST_PATH="$LAUNCH_AGENTS_DIR/com.zabaika.telegram-connector-digest.plist"', content)
        self.assertIn('<key>StartCalendarInterval</key>', content)
        self.assertIn('<key>Hour</key>', content)
        self.assertIn('<key>Minute</key>', content)
        self.assertIn('run_telegram_digest.sh', content)


if __name__ == "__main__":
    unittest.main()
