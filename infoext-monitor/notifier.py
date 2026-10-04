"""Notification boundary backed by the existing telegram_connector project."""

from __future__ import annotations

import importlib
import os
import sys
from pathlib import Path


class NotifierError(RuntimeError):
    """A notification was not confirmed by the existing Telegram connector."""


class Notifier:
    def send(self, message: str) -> None:
        raise NotImplementedError


class TelegramConnectorNotifier(Notifier):
    """Use telegram_connector's established token, recipient and retry policy."""

    def __init__(self, connector_dir: Path) -> None:
        self.connector_dir = connector_dir.resolve()

    def send(self, message: str) -> None:
        bridge = self._load_bridge()
        try:
            config = bridge.load_runtime_config()
            chat_id = bridge.get_config_value(config, "telegram", "default_chat_id") or os.environ.get(
                "TELEGRAM_DEFAULT_CHAT_ID", ""
            ).strip()
            if not chat_id:
                raise NotifierError("telegram_connector has no configured default chat id.")
            token = bridge.require_token()
            bridge.send_text_chunks(
                token,
                chat_id,
                message,
                chunk_size=bridge.resolve_text_chunk_size(config),
            )
        except NotifierError:
            raise
        except BaseException as exc:
            # Connector errors can include transport context. Do not propagate it
            # into this monitor's CLI or logs, where secrets must never appear.
            raise NotifierError(
                f"telegram_connector could not confirm delivery ({exc.__class__.__name__})."
            ) from exc

    def _load_bridge(self) -> object:
        if not (self.connector_dir / "telegram_bridge.py").is_file():
            raise NotifierError("telegram_connector/telegram_bridge.py is unavailable.")
        parent = str(self.connector_dir.parent)
        if parent not in sys.path:
            sys.path.insert(0, parent)
        # The connector resolves its own config and Keychain references from this
        # source project, including when this monitor is launched by launchd.
        os.environ["TELEGRAM_CONNECTOR_PROJECT_ROOT"] = str(self.connector_dir)
        try:
            existing = sys.modules.get("telegram_connector.telegram_bridge")
            if existing is not None:
                loaded_file = getattr(existing, "__file__", None)
                if not loaded_file or Path(loaded_file).resolve().parent != self.connector_dir:
                    raise NotifierError(
                        "A telegram_connector bridge from a different project is already loaded."
                    )
            return importlib.import_module("telegram_connector.telegram_bridge")
        except NotifierError:
            raise
        except Exception as exc:
            raise NotifierError("Unable to import the existing telegram_connector bridge.") from exc
