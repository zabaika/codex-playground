import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
INSTALLER = ROOT / "scripts" / "install_launch_agent.sh"
RESTARTER = ROOT / "scripts" / "restart_launch_agent.sh"
CLEAR_LOGS = ROOT / "scripts" / "clear_launch_logs.sh"
BRIDGE_PLIST_TEMPLATE = ROOT / "scripts" / "com.zabaika.telegram-connector-bridge.plist"


class LaunchAgentScriptTests(unittest.TestCase):
    def test_installer_writes_logs_into_project_data_dir(self) -> None:
        content = INSTALLER.read_text(encoding="utf-8")
        self.assertIn('PROJECT_LOG_DIR="$SOURCE_ROOT/data/launchd"', content)
        self.assertIn('STARTUP_LOG="$PROJECT_LOG_DIR/bridge.startup.log"', content)
        self.assertIn('STDOUT_LOG="$PROJECT_LOG_DIR/bridge.stdout.log"', content)
        self.assertIn('STDERR_LOG="$PROJECT_LOG_DIR/bridge.stderr.log"', content)
        self.assertIn('DIGEST_LAST_ATTEMPT_LOG="$PROJECT_LOG_DIR/digest.last_attempt.json"', content)
        self.assertIn('BRIDGE_LAUNCHER="$SERVICE_ROOT/scripts/telegram-connector-bridge-launcher"', content)
        self.assertIn('DIGEST_LAUNCHER="$SERVICE_ROOT/scripts/telegram-connector-digest-launcher"', content)
        self.assertNotIn('rm -f "$PROJECT_LOG_DIR"', content)
        self.assertNotIn('"$SERVICE_ROOT"/data/launchd', content)

    def test_clear_logs_is_an_explicit_separate_command(self) -> None:
        content = CLEAR_LOGS.read_text(encoding="utf-8")
        self.assertIn('PROJECT_LOG_DIR="$SOURCE_ROOT/data/launchd"', content)
        self.assertIn('rm -f "$PROJECT_LOG_DIR"/bridge.startup.log', content)
        self.assertIn('rm -f "$PROJECT_LOG_DIR"/digest.last_attempt.json', content)

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
        self.assertIn('exec /bin/bash "$SERVICE_ROOT/scripts/run_telegram_bridge.sh"', content)
        self.assertIn('AUDIT_LOG="\\$TELEGRAM_CONNECTOR_PROJECT_ROOT/data/launchd/digest.last_attempt.json"', content)
        self.assertIn('arming digest ttl=%ss grace=%ss', content)
        self.assertIn('exec "$PYTHON_BIN" "\\$ROOT/common/ttl_runner.py" \\', content)
        self.assertIn('--audit-file "\\$AUDIT_LOG" \\', content)
        self.assertIn('--use-caffeinate \\', content)
        self.assertIn('-- "$PYTHON_BIN" "\\$ROOT/telegram_digest.py" run', content)
        self.assertIn('exec /bin/bash "$SERVICE_ROOT/scripts/run_telegram_digest.sh"', content)

    def test_installer_sets_project_root_env_in_plist(self) -> None:
        content = INSTALLER.read_text(encoding="utf-8")
        self.assertIn("<key>EnvironmentVariables</key>", content)
        self.assertIn("<key>TELEGRAM_CONNECTOR_PROJECT_ROOT</key>", content)
        self.assertIn("<string>$SOURCE_ROOT</string>", content)

    def test_restart_script_reloads_launch_agent(self) -> None:
        content = RESTARTER.read_text(encoding="utf-8")
        self.assertIn('launchctl bootout "$LAUNCHCTL_DOMAIN" "$PLIST_PATH"', content)
        self.assertIn('launchctl bootstrap "$LAUNCHCTL_DOMAIN" "$PLIST_PATH"', content)
        self.assertIn('launchctl kickstart -k "$LAUNCHCTL_DOMAIN/com.zabaika.telegram-connector-bridge"', content)
        self.assertIn('com.zabaika.telegram-connector-bridge', content)

    def test_bridge_plist_template_uses_launcher_entrypoint(self) -> None:
        content = BRIDGE_PLIST_TEMPLATE.read_text(encoding="utf-8")
        self.assertIn("__SERVICE_ROOT__/scripts/telegram-connector-bridge-launcher", content)
        self.assertNotIn("__SERVICE_ROOT__/scripts/run_telegram_bridge.sh", content)

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
        self.assertIn('<string>$DIGEST_LAUNCHER</string>', content)
        self.assertIn('<string>$BRIDGE_LAUNCHER</string>', content)


if __name__ == "__main__":
    unittest.main()
