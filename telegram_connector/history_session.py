"""Telethon authentication, exclusive sessions and bounded connection lifecycle."""
import asyncio
import errno
import fcntl
import os
import socket
import sys
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from common import process as common_process
from common.process import awake_time
from telegram_shared.async_retry import AsyncCleanupError, AsyncRetryError, await_with_awake_timeout, retry_async
if __package__:
    from . import history_messages, history_settings
else:
    import history_messages
    import history_settings


RETRYABLE_CONNECTION_ERRNOS = frozenset(
    getattr(errno, name) for name in (
        "ETIMEDOUT", "ECONNRESET", "ECONNABORTED", "ECONNREFUSED", "EPIPE",
        "ENETDOWN", "ENETUNREACH", "EHOSTUNREACH", "EHOSTDOWN",
    ) if hasattr(errno, name)
) | frozenset({socket.EAI_AGAIN, socket.EAI_NONAME})


def require_telethon() -> Any:
    try:
        from telethon import TelegramClient
    except ImportError as exc:
        raise SystemExit(
            "Telethon is not installed. Install it first, for example: python3 -m pip install telethon"
        ) from exc
    return TelegramClient


async def current_read_inbox_max_id(client: Any, entity: Any) -> int | None:
    try:
        from telethon.tl import functions, types
    except ImportError as exc:
        raise SystemExit(
            "Telethon is not installed. Install it first, for example: python3 -m pip install telethon"
        ) from exc

    response = await client(
        functions.messages.GetPeerDialogsRequest(
            peers=[types.InputDialogPeer(peer=entity)]
        )
    )
    dialogs = getattr(response, "dialogs", None) or []
    if not dialogs:
        return None
    return getattr(dialogs[0], "read_inbox_max_id", None)


def make_session_path(runtime: history_settings.RuntimeConfig, auth_mode: str) -> Path:
    session_name = runtime.user_session_name if auth_mode == "user" else runtime.bot_session_name
    return history_settings.SESSION_DIR / session_name


def canonical_session_identity(runtime: history_settings.RuntimeConfig, auth_mode: str) -> Path:
    path = str(make_session_path(runtime, auth_mode))
    # SQLiteSession appends .session, rather than replacing an existing suffix.
    return Path(path if path.endswith(".session") else path + ".session").resolve()


async def _acquire_session_lock(runtime: history_settings.RuntimeConfig, auth_mode: str) -> int:
    identity = canonical_session_identity(runtime, auth_mode)
    identity.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(str(identity) + ".lock", os.O_CREAT | os.O_RDWR, 0o600)
    deadline = common_process.continuous_time() + runtime.connection_timeout_seconds
    interval = common_process.load_process_config().poll_interval_seconds
    try:
        while True:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                return fd
            except BlockingIOError:
                remaining = deadline - common_process.continuous_time()
                if remaining <= 0:
                    raise TimeoutError("Telegram session is already in use") from None
                await asyncio.sleep(min(interval, remaining))
    except BaseException:
        os.close(fd)
        raise


@asynccontextmanager
async def open_telethon_session(runtime: history_settings.RuntimeConfig, auth_mode: str, *, managed_requests: bool = False):
    fd = await _acquire_session_lock(runtime, auth_mode)
    release = True
    try:
        try:
            client = (await open_telethon_client(runtime, auth_mode, managed_requests=True)
                      if managed_requests else await open_telethon_client(runtime, auth_mode))
        except TelegramConnectionError as exc:
            if isinstance(exc.cause, AsyncCleanupError):
                release = False
            raise
        try:
            try:
                if not managed_requests:
                    await await_with_awake_timeout(client.__aenter__(), runtime.connection_timeout_seconds,
                                                   cleanup_timeout_seconds=runtime.connection_cleanup_timeout_seconds)
            except AsyncCleanupError:
                release = False
                raise
            try:
                yield client
            except AsyncCleanupError:
                release = False
                raise
        finally:
            try:
                cleanup = client.disconnect() if managed_requests else client.__aexit__(*sys.exc_info())
                await await_with_awake_timeout(cleanup, runtime.connection_cleanup_timeout_seconds,
                                               cleanup_timeout_seconds=runtime.connection_cleanup_timeout_seconds)
            except BaseException as exc:
                release = False
                if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                    raise
                raise AsyncCleanupError("Telegram session cleanup did not complete") from None
    finally:
        if release:
            os.close(fd)


def normalize_auth_mode(auth_mode: str) -> str:
    if auth_mode not in {"auto", "bot", "user"}:
        raise SystemExit(f"Unsupported auth mode: {auth_mode}")
    return auth_mode


def classify_channel_reference(channel: str) -> str:
    channel = channel.strip()
    if channel.startswith("@"):
        return "public"
    if channel.startswith("https://t.me/+") or channel.startswith("t.me/+"):
        return "private"
    if channel.startswith("https://t.me/joinchat/") or channel.startswith("t.me/joinchat/"):
        return "private"
    if channel.startswith("https://t.me/") or channel.startswith("t.me/"):
        return "public"
    if channel.lstrip("-").isdigit():
        return "private"
    return "private"


def resolve_auth_mode(runtime: history_settings.RuntimeConfig, auth_mode: str, channel: str) -> str:
    auth_mode = normalize_auth_mode(auth_mode)
    if auth_mode != "auto":
        return auth_mode

    if classify_channel_reference(channel) == "public":
        return normalize_auth_mode(runtime.public_auth_mode)
    return normalize_auth_mode(runtime.private_auth_mode)


def build_entity_lookup_reference(channel: str) -> Any:
    normalized = channel.strip()
    if normalized.startswith("-100") and normalized[4:].isdigit():
        try:
            from telethon.tl.types import PeerChannel
        except ImportError:
            return normalized
        return PeerChannel(int(normalized[4:]))
    return normalized


class TelegramConnectionError(AsyncRetryError):
    def operator_summary(self) -> str:
        cause = self.cause
        if isinstance(cause, TimeoutError):
            reason = "timeout"
        elif isinstance(cause, AsyncCleanupError):
            reason = "cleanup_error"
        elif isinstance(cause, OSError) and cause.errno is not None:
            reason = f"{type(cause).__name__}: {errno.errorcode.get(cause.errno, cause.errno)}"
        else:
            reason = type(cause).__name__
        return f"Telegram connection {reason} (attempts: {self.attempts}/{self.max_attempts})"


def is_retryable_connection_error(exc: Exception) -> bool:
    if isinstance(exc, TimeoutError):
        return True
    if isinstance(exc, OSError):
        return exc.errno in RETRYABLE_CONNECTION_ERRNOS or (
            isinstance(exc, ConnectionError) and exc.errno is None
        )
    return False


async def open_telethon_client(runtime: history_settings.RuntimeConfig, auth_mode: str, *, managed_requests: bool = False) -> Any:
    if not runtime.api_id or not runtime.api_hash:
        raise SystemExit(
            "Missing Telethon credentials. Fill [telethon].api_id and [secrets].api_hash in runtime.local.toml."
        )

    TelegramClient = require_telethon()
    if auth_mode == "bot":
        if not runtime.bot_token:
            raise SystemExit("Missing bot token for bot auth mode. Fill [secrets].bot_token in runtime.local.toml.")
        start_args = {"bot_token": runtime.bot_token}
    else:
        if not runtime.phone:
            raise SystemExit("Missing phone for user auth mode. Fill [telethon].phone in runtime.local.toml.")
        start_args = {"phone": runtime.phone, "password": runtime.user_password or None}

    attempt_started = 0.0

    async def connect_once() -> Any:
        nonlocal attempt_started
        attempt_started = awake_time()
        # Analytics owns page retries, FloodWait and the outer TTL. Zero disables
        # SDK retry multiplication; legacy callers retain Telethon defaults.
        options = {"request_retries": 0, "connection_retries": 0, "retry_delay": 0,
                   "auto_reconnect": False, "flood_sleep_threshold": 0,
                   "raise_last_call_error": True, "receive_updates": False} if managed_requests else {}
        client = TelegramClient(str(make_session_path(runtime, auth_mode)), int(runtime.api_id), runtime.api_hash, **options)

        async def start_client():
            if managed_requests and auth_mode == "user":
                # A background analytics task may reuse an authenticated session,
                # but must not initiate phone/OTP prompts through .start().
                await client.connect()
                if await client.get_me() is None:
                    raise PermissionError("Telegram user session is not authorized")
            else:
                await client.start(**start_args)

        try:
            await await_with_awake_timeout(
                start_client(), runtime.connection_timeout_seconds,
                cleanup_timeout_seconds=runtime.connection_cleanup_timeout_seconds,
            )
            return client
        except BaseException:
            try:
                await await_with_awake_timeout(
                    client.disconnect(), runtime.connection_cleanup_timeout_seconds,
                    cleanup_timeout_seconds=runtime.connection_cleanup_timeout_seconds,
                )
            except Exception as cleanup_exc:
                raise AsyncCleanupError("Telegram disconnect did not complete") from cleanup_exc
            raise

    def log_failure(exc: Exception, attempt: int) -> None:
        summary = TelegramConnectionError(exc, attempt, runtime.connection_attempts).operator_summary()
        print(f"{summary}; awake_elapsed={awake_time() - attempt_started:.1f}s", file=sys.stderr, flush=True)

    def retryable_startup(exc: Exception) -> bool:
        if is_retryable_connection_error(exc):
            return True
        if managed_requests:
            from telethon import errors
            return isinstance(exc, (errors.ServerError, errors.InvalidDCError))
        return False

    try:
        return await retry_async(
            connect_once, max_attempts=runtime.connection_attempts,
            backoff_seconds=runtime.connection_retry_backoff_seconds,
            is_retryable=retryable_startup, on_failure=log_failure,
            on_recovery=lambda attempt: print(
                f"Telegram connection recovered (attempts: {attempt}/{runtime.connection_attempts})",
                file=sys.stderr, flush=True,
            ),
        )
    except AsyncRetryError as exc:
        raise TelegramConnectionError(exc.cause, exc.attempts, exc.max_attempts) from None


async def resolve_channel_entity(client, channel: str):
    reference = build_entity_lookup_reference(channel)
    try:
        return await client.get_entity(reference)
    except ValueError:
        if not (channel.startswith("-100") and channel[1:].isdigit()):
            raise
        await client.get_dialogs()
        return await client.get_entity(reference)


def channel_dir(runtime: history_settings.RuntimeConfig, channel_id: int) -> Path:
    path = runtime.media_root / str(channel_id)
    path.mkdir(parents=True, exist_ok=True)
    return path


async def resolve_sender_metadata(entity: Any, message: Any) -> tuple[str | None, str | None]:
    is_channel_post = bool(getattr(message, "post", False))
    sender_id = getattr(message, "sender_id", None)
    if is_channel_post and (sender_id is None or sender_id == getattr(entity, "id", None)):
        return history_messages.optional_text(getattr(entity, "username", None)), history_messages.entity_display_name(entity)

    username, display_name = history_messages.direct_sender_metadata(message)
    if username or display_name:
        return username, display_name

    sender = getattr(message, "sender", None)
    if sender is None and sender_id is not None:
        sender = await message.get_sender()
    if sender is None:
        return None, None
    return history_messages.optional_text(getattr(sender, "username", None)), history_messages.entity_display_name(sender)


async def download_media_if_present(runtime: history_settings.RuntimeConfig, client: Any, message: Any, channel_id: int) -> tuple[str | None, int | None]:
    if not getattr(message, "media", None):
        return None, None

    target_dir = channel_dir(runtime, channel_id)
    file_base = target_dir / f"{getattr(message, 'id')}"
    downloaded = await client.download_media(message, file=str(file_base))
    if not downloaded:
        return None, None

    path = Path(downloaded)
    size = path.stat().st_size if path.exists() else None
    return str(path), size
