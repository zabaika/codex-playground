import importlib
import os
from pathlib import Path
import sys

import pytest

from notifier import NotifierError, TelegramConnectorNotifier


def test_connector_root_from_settings_overrides_inherited_environment(
    tmp_path: Path, monkeypatch
) -> None:
    connector_dir = tmp_path / "telegram_connector"
    connector_dir.mkdir()
    (connector_dir / "telegram_bridge.py").write_text("# test bridge\n", encoding="utf-8")
    monkeypatch.setenv("TELEGRAM_CONNECTOR_PROJECT_ROOT", "/unrelated/connector")
    loaded: list[str] = []

    def fake_import(name: str):
        loaded.append(name)
        return object()

    monkeypatch.setattr(importlib, "import_module", fake_import)
    bridge = TelegramConnectorNotifier(connector_dir)._load_bridge()

    assert bridge is not None
    assert loaded == ["telegram_connector.telegram_bridge"]
    assert os.environ["TELEGRAM_CONNECTOR_PROJECT_ROOT"] == str(connector_dir.resolve())


def test_preloaded_bridge_from_another_project_is_rejected(tmp_path: Path, monkeypatch) -> None:
    connector_dir = tmp_path / "telegram_connector"
    connector_dir.mkdir()
    (connector_dir / "telegram_bridge.py").write_text("# test bridge\n", encoding="utf-8")
    other_bridge = tmp_path / "other_connector" / "telegram_bridge.py"
    other_bridge.parent.mkdir()
    other_bridge.write_text("# other bridge\n", encoding="utf-8")
    monkeypatch.setitem(
        sys.modules,
        "telegram_connector.telegram_bridge",
        type("Bridge", (), {"__file__": str(other_bridge)})(),
    )

    with pytest.raises(NotifierError, match="different project"):
        TelegramConnectorNotifier(connector_dir)._load_bridge()


def test_transport_error_does_not_expose_connector_secrets(monkeypatch, tmp_path) -> None:
    from types import SimpleNamespace

    sentinel = "synthetic-secret-do-not-expose"

    def fail(*args, **kwargs):
        raise RuntimeError(sentinel)

    bridge = SimpleNamespace(
        load_runtime_config=lambda: {},
        get_config_value=lambda *args: "synthetic-chat",
        require_token=lambda: sentinel,
        resolve_text_chunk_size=lambda _: 100,
        send_text_chunks=fail,
    )
    notifier = TelegramConnectorNotifier(tmp_path)
    monkeypatch.setattr(notifier, "_load_bridge", lambda: bridge)
    with pytest.raises(NotifierError) as caught:
        notifier.send("test message")
    assert str(caught.value) == "telegram_connector could not confirm delivery (RuntimeError)."
    assert sentinel not in str(caught.value)
