import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
INSTALLER = ROOT / "scripts" / "install_launch_agent.sh"
RESTARTER = ROOT / "scripts" / "restart_launch_agent.sh"
DIGEST_CRON_INSTALLER = ROOT / "scripts" / "install_digest_crontab.sh"


class LaunchAgentScriptTests(unittest.TestCase):
    def test_installer_writes_logs_into_project_data_dir(self) -> None:
        content = INSTALLER.read_text(encoding="utf-8")
        self.assertIn('PROJECT_LOG_DIR="$SOURCE_ROOT/data/launchd"', content)
        self.assertIn('STARTUP_LOG="$PROJECT_LOG_DIR/bridge.startup.log"', content)
        self.assertIn('STDOUT_LOG="$PROJECT_LOG_DIR/bridge.stdout.log"', content)
        self.assertIn('STDERR_LOG="$PROJECT_LOG_DIR/bridge.stderr.log"', content)
        self.assertIn('rm -f "$PROJECT_LOG_DIR"/bridge.startup.log', content)

    def test_installer_runner_appends_startup_log(self) -> None:
        content = INSTALLER.read_text(encoding="utf-8")
        self.assertIn('SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"', content)
        self.assertIn('SOURCE_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"', content)
        self.assertIn(': "${TELEGRAM_CONNECTOR_PROJECT_ROOT:?TELEGRAM_CONNECTOR_PROJECT_ROOT is required}"', content)
        self.assertIn('printf \'[%s] starting telegram bridge from %s\\n\'', content)
        self.assertIn('STARTUP_LOG="$TELEGRAM_CONNECTOR_PROJECT_ROOT/data/launchd/bridge.startup.log"', content)

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

    def test_digest_cron_installer_replaces_tagged_entry(self) -> None:
        content = DIGEST_CRON_INSTALLER.read_text(encoding="utf-8")
        self.assertIn('CRON_TAG="telegram_connector_daily_digest"', content)
        self.assertIn('"$PYTHON_BIN" "$SOURCE_ROOT/telegram_digest.py" cron-line', content)
        self.assertIn('grep -v "$CRON_TAG"', content)
        self.assertIn('| crontab -', content)


if __name__ == "__main__":
    unittest.main()
