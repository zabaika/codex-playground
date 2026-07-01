import subprocess
import tempfile
import unittest
from pathlib import Path

from telegram_shared.bridge_env import is_user_allowed
from telegram_shared.bridge_env import parse_allowed_chat_ids
from telegram_shared.bridge_env import parse_allowed_usernames
from telegram_shared.bridge_env import run_worker_subprocess


def get_config_value(config: dict[str, object], section: str, key: str) -> str:
    section_data = config.get(section, {})
    if not isinstance(section_data, dict):
        return ""
    return str(section_data.get(key, "")).strip()


class SharedBridgeEnvTests(unittest.TestCase):
    def test_parse_allowed_chat_ids_falls_back_to_default_chat(self) -> None:
        config = {"telegram": {"default_chat_id": "133126275"}}

        self.assertEqual(parse_allowed_chat_ids(config, get_config_value=get_config_value), {"133126275"})

    def test_parse_allowed_usernames_normalizes_values(self) -> None:
        config = {"bridge": {"allowed_usernames": "@Andrej, codex_user"}}

        self.assertEqual(
            parse_allowed_usernames(
                config,
                get_config_value=get_config_value,
                resolve_secret_value=lambda value, label: value,
            ),
            {"andrej", "codex_user"},
        )

    def test_parse_allowed_usernames_supports_secret_resolver(self) -> None:
        config = {"bridge": {"allowed_usernames": "keychain://telegram-connector/allowed_users"}}
        seen: dict[str, str] = {}

        def fake_resolver(value: str, label: str) -> str:
            seen["value"] = value
            seen["label"] = label
            return "@Andrej, codex_user"

        result = parse_allowed_usernames(config, get_config_value=get_config_value, resolve_secret_value=fake_resolver)

        self.assertEqual(result, {"andrej", "codex_user"})
        self.assertEqual(seen["value"], "keychain://telegram-connector/allowed_users")
        self.assertEqual(seen["label"], "allowed Telegram usernames")

    def test_is_user_allowed_denies_empty_user_allowlist(self) -> None:
        self.assertFalse(
            is_user_allowed(
                user_id=7,
                username="alice",
                allowed_user_ids=set(),
                allowed_usernames=set(),
            )
        )

    def test_is_user_allowed_accepts_configured_user_id(self) -> None:
        self.assertTrue(
            is_user_allowed(
                user_id=7,
                username="alice",
                allowed_user_ids={"7"},
                allowed_usernames=set(),
            )
        )

    def test_run_worker_subprocess_uses_standard_bridge_subprocess_options(self) -> None:
        captured: dict[str, object] = {}

        def fake_run(*args, **kwargs):
            captured["argv"] = args[0]
            captured.update(kwargs)
            return subprocess.CompletedProcess(args=args[0], returncode=0, stdout="ok", stderr="")

        with tempfile.TemporaryDirectory() as tmp_dir:
            result = run_worker_subprocess(
                ["python3", "worker.py"],
                cwd=Path(tmp_dir),
                env={"TOKEN": "secret"},
                timeout_seconds=7200,
                run_func=fake_run,
            )

        self.assertEqual(result.returncode, 0)
        self.assertEqual(captured["argv"], ["python3", "worker.py"])
        self.assertEqual(captured["cwd"], tmp_dir)
        self.assertEqual(captured["capture_output"], True)
        self.assertEqual(captured["env"], {"TOKEN": "secret"})
        self.assertEqual(captured["text"], True)
        self.assertEqual(captured["timeout"], 7200)
        self.assertEqual(captured["check"], False)


if __name__ == "__main__":
    unittest.main()
