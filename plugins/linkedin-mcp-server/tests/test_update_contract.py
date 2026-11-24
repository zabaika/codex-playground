import copy
from pathlib import Path
import runpy
import subprocess
import sys
import tempfile
import unittest


checks = runpy.run_path(
    str(Path(__file__).resolve().parents[1] / "scripts/check_update_contract.py")
)


class UpdateContractTests(unittest.TestCase):
    def run_checker(self, *arguments):
        script = Path(__file__).resolve().parents[1] / "scripts/check_update_contract.py"
        return subprocess.run(
            [sys.executable, "-B", str(script), *arguments],
            capture_output=True, text=True, timeout=5, check=False,
        )

    def configuration(self):
        return {"mcpServers": {"linkedin": {
            "command": "uvx",
            "args": ["mcp-server-linkedin==9.8.7", "--no-auto-import"],
        }}}

    def test_exact_new_release_remains_allowed(self):
        self.assertEqual(checks["configuration_errors"](self.configuration()), [])

    def test_unpinned_and_mutable_dependencies_are_rejected(self):
        for requirement in (
            "mcp-server-linkedin@latest", "mcp-server-linkedin>=4.31.0",
            "mcp-server-linkedin", "mcp-server-linkedin==4.*",
        ):
            with self.subTest(requirement=requirement):
                configuration = self.configuration()
                configuration["mcpServers"]["linkedin"]["args"][0] = requirement
                self.assertTrue(checks["configuration_errors"](configuration))

    def test_missing_flag_and_explicit_import_are_rejected(self):
        for flags in ([], ["--auto-import"], ["--no-auto-import",
                     "--import-from-browser=chrome"]):
            with self.subTest(flags=flags):
                configuration = self.configuration()
                configuration["mcpServers"]["linkedin"]["args"][1:] = flags
                self.assertTrue(checks["configuration_errors"](configuration))

    def test_changed_launcher_and_invalid_arguments_fail(self):
        configuration = self.configuration()
        changed = copy.deepcopy(configuration)
        changed["mcpServers"]["linkedin"]["command"] = "sh"
        self.assertTrue(checks["configuration_errors"](changed))
        configuration["mcpServers"]["linkedin"]["args"] = "invalid"
        self.assertTrue(checks["configuration_errors"](configuration))

    def test_inventory_detects_hidden_extra_and_changed_files(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / ".mcp.json").write_text("original")
            baseline = checks["inventory"](root)
            (root / ".mcp.json").write_text("changed")
            self.assertNotEqual(baseline, checks["inventory"](root))
            (root / ".mcp.json").write_text("original")
            (root / ".extra").write_text("extra")
            self.assertNotEqual(baseline, checks["inventory"](root))

    def test_inventory_rejects_symlinks(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "link").symlink_to("absent")
            with self.assertRaises(ValueError):
                checks["inventory"](root)

    def test_cli_rejects_incomplete_installed_copy(self):
        with tempfile.TemporaryDirectory() as directory:
            result = self.run_checker("--installed-root", directory)
        self.assertEqual(result.returncode, 1)
        self.assertIn("FAIL:", result.stderr)

    def test_cli_rejects_unknown_option(self):
        self.assertEqual(self.run_checker("--unknown").returncode, 2)


if __name__ == "__main__":
    unittest.main()
