import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
INSTALLER = ROOT / "scripts" / "install_launch_agent.sh"
CLEAR_LOGS = ROOT / "scripts" / "clear_launch_logs.sh"


class LaunchAgentScriptTests(unittest.TestCase):
    def test_installer_preserves_project_logs(self) -> None:
        content = INSTALLER.read_text(encoding="utf-8")
        self.assertIn('PROJECT_LOG_DIR="$SOURCE_ROOT/data/launchd"', content)
        self.assertNotIn('rm -f "$PROJECT_LOG_DIR"', content)
        self.assertNotIn('rm -f "$SERVICE_ROOT"/data/launchd', content)

    def test_clear_logs_is_an_explicit_separate_command(self) -> None:
        content = CLEAR_LOGS.read_text(encoding="utf-8")
        self.assertIn('PROJECT_LOG_DIR="$SOURCE_ROOT/data/launchd"', content)
        self.assertIn('rm -f "$PROJECT_LOG_DIR"/bridge.startup.log', content)


if __name__ == "__main__":
    unittest.main()
