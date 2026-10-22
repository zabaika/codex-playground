"""Opt-in, run-scoped HTTP connection reuse without transport-level retries."""

import io
import ssl
from contextlib import contextmanager
from contextvars import ContextVar
from urllib import error, parse, request, response


_CURRENT_SESSION = ContextVar("http_session", default=None)


class HttpSession:
    def __init__(self):
        proxies = request.getproxies()
        if any(proxies.get(scheme) for scheme in ("http", "https", "all", "socks")):
            raise ValueError(
                "Proxy configuration is not supported; "
                "direct HTTP connections are required."
            )
        # Only opt-in consumers need this dependency in their runtime.
        import urllib3

        self._urllib3 = urllib3
        self._direct = urllib3.PoolManager(ssl_context=ssl.create_default_context())
        self._closed = False

    def urlopen(self, req, *, timeout):
        if self._closed:
            raise RuntimeError("HTTP session is closed")
        if parse.urlsplit(req.full_url).scheme not in {"http", "https"}:
            raise ValueError("HTTP session requires an HTTP(S) URL")
        raw = None
        try:
            raw = self._direct.request(
                req.get_method(), req.full_url, body=req.data,
                headers=dict(req.header_items()), timeout=timeout,
                retries=False, redirect=False, preload_content=False,
                decode_content=False,
            )
            # Error consumers need only bounded diagnostics. Discard an unread
            # error body rather than returning a dirty connection to the pool.
            if raw.status >= 300:
                try:
                    body = raw.read(8192)
                except (OSError, self._urllib3.exceptions.HTTPError):
                    body = b""
                raise error.HTTPError(
                    req.full_url, raw.status, raw.reason, raw.headers,
                    io.BytesIO(body),
                )
            return response.addinfourl(
                io.BytesIO(raw.read()), raw.headers, req.full_url, raw.status,
            )
        except self._urllib3.exceptions.HTTPError as exc:
            cause = exc.__cause__ or exc.__context__
            while cause is not None and not isinstance(cause, OSError):
                cause = cause.__cause__ or cause.__context__
            if isinstance(cause, TimeoutError):
                raise TimeoutError(*cause.args) from exc
            if isinstance(cause, OSError):
                raise error.URLError(cause) from exc
            # NewConnectionError inherits TimeoutError even for permanent OS
            # failures. Only genuine timeout wrappers should become timeouts.
            if isinstance(exc, self._urllib3.exceptions.TimeoutError) and not isinstance(
                exc, self._urllib3.exceptions.NewConnectionError,
            ):
                raise TimeoutError("HTTP request timed out") from exc
            # Do not expose authenticated URLs from urllib3 wrapper strings.
            raise error.URLError(type(exc).__name__) from exc
        finally:
            if raw is not None:
                raw.close()
                raw.release_conn()

    def close(self):
        self._closed = True
        self._direct.clear()


def current_urlopen():
    session = _CURRENT_SESSION.get()
    return session.urlopen if session is not None else None


@contextmanager
def pooled_http_session():
    """Enable pooled senders in this context; close all pools on every exit."""
    session = HttpSession()
    token = _CURRENT_SESSION.set(session)
    try:
        yield session
    finally:
        _CURRENT_SESSION.reset(token)
        session.close()
