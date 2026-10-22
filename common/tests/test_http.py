import threading
import errno
import ssl
import subprocess
import time
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib import error, request

import pytest

from common import http
from telegram_shared.openai_api import post_responses, read_network_error


@contextmanager
def server(tls_context=None):
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def do_POST(self):
            self.rfile.read(int(self.headers.get("Content-Length", 0)))
            self.server.calls.append((self.path, self.connection))
            if self.path == "/drop":
                self.close_connection = True
                return
            if self.path == "/timeout":
                time.sleep(0.2)
                self.close_connection = True
                return
            status = 302 if self.path == "/redirect" else 200
            body = b'{"ok":true,"result":{"message_id":1},"output":[]}'
            self.send_response(status)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Location", "/target")
            if self.path == "/close":
                self.send_header("Connection", "close")
                self.close_connection = True
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    instance = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    instance.daemon_threads = True
    if tls_context is not None:
        instance.socket = tls_context.wrap_socket(instance.socket, server_side=True)
    instance.calls = []
    thread = threading.Thread(target=instance.serve_forever, daemon=True)
    thread.start()
    try:
        scheme = "https" if tls_context is not None else "http"
        yield instance, f"{scheme}://127.0.0.1:{instance.server_port}"
    finally:
        instance.shutdown()
        instance.server_close()
        thread.join(timeout=2)


def post(url):
    return request.Request(url, data=b"{}", method="POST")


def test_reuses_connection_and_restores_context(monkeypatch):
    monkeypatch.setattr(http.request, "getproxies", lambda: {})
    assert http.current_urlopen() is None
    with server() as (host, url):
        with http.pooled_http_session():
            sender = http.current_urlopen()
            for _ in range(3):
                with sender(post(url), timeout=2) as response:
                    assert b'"ok":true' in response.read()
            assert len(host.calls) == 3
            assert len({id(conn) for _, conn in host.calls}) == 1
        assert http.current_urlopen() is None
        with pytest.raises(RuntimeError, match="closed"):
            sender(post(url), timeout=2)


def test_origins_have_separate_connections(monkeypatch):
    monkeypatch.setattr(http.request, "getproxies", lambda: {})
    with server() as (first, first_url), server() as (second, second_url):
        with http.pooled_http_session():
            for url in (first_url, second_url, first_url, second_url):
                http.current_urlopen()(post(url), timeout=2).close()
        assert first.calls[0][1] is first.calls[1][1]
        assert second.calls[0][1] is second.calls[1][1]
        assert first.calls[0][1] is not second.calls[0][1]


def test_redirect_is_not_followed(monkeypatch):
    monkeypatch.setattr(http.request, "getproxies", lambda: {})
    with server() as (host, url), http.pooled_http_session():
        with pytest.raises(error.HTTPError) as exc:
            http.current_urlopen()(post(url + "/redirect"), timeout=2)
        assert exc.value.code == 302
        assert exc.value.read()
        exc.value.close()
        assert [path for path, _ in host.calls] == ["/redirect"]


def test_dropped_post_is_not_automatically_replayed(monkeypatch):
    monkeypatch.setattr(http.request, "getproxies", lambda: {})
    with server() as (host, url), http.pooled_http_session():
        with pytest.raises(error.URLError):
            http.current_urlopen()(post(url + "/drop"), timeout=2)
        assert len(host.calls) == 1
        http.current_urlopen()(post(url), timeout=2).close()
        assert len(host.calls) == 2


def test_server_closed_connection_is_replaced(monkeypatch):
    monkeypatch.setattr(http.request, "getproxies", lambda: {})
    with server() as (host, url), http.pooled_http_session():
        http.current_urlopen()(post(url + "/close"), timeout=2).close()
        http.current_urlopen()(post(url), timeout=2).close()
        assert len(host.calls) == 2
        assert host.calls[0][1] is not host.calls[1][1]


def test_context_is_restored_after_failure(monkeypatch):
    monkeypatch.setattr(http.request, "getproxies", lambda: {})
    with pytest.raises(ValueError), http.pooled_http_session():
        raise ValueError("fixture")
    assert http.current_urlopen() is None


def test_read_timeout_does_not_replay_post(monkeypatch):
    monkeypatch.setattr(http.request, "getproxies", lambda: {})
    with server() as (host, url), http.pooled_http_session():
        with pytest.raises(TimeoutError):
            http.current_urlopen()(post(url + "/timeout"), timeout=0.05)
        assert len(host.calls) == 1


def test_https_reuses_verified_tls_connection(monkeypatch, tmp_path):
    cert = tmp_path / "cert.pem"
    key = tmp_path / "key.pem"
    subprocess.run(
        ["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes",
         "-keyout", str(key), "-out", str(cert), "-days", "1",
         "-subj", "/CN=localhost", "-addext", "subjectAltName=IP:127.0.0.1"],
        check=True, capture_output=True, timeout=10,
    )
    tls = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    tls.load_cert_chain(cert, key)
    trust = ssl.create_default_context(cafile=str(cert))
    monkeypatch.setattr(http.ssl, "create_default_context", lambda: trust)
    monkeypatch.setattr(http.request, "getproxies", lambda: {})
    with server(tls) as (host, url), http.pooled_http_session():
        for _ in range(3):
            http.current_urlopen()(post(url), timeout=2).close()
        assert len(host.calls) == 3
        assert len({id(conn) for _, conn in host.calls}) == 1
        assert all(isinstance(conn, ssl.SSLSocket) for _, conn in host.calls)


@pytest.mark.parametrize("scheme", ["http", "https", "all", "socks"])
def test_configured_proxy_is_rejected_without_exposing_credentials(monkeypatch, scheme):
    proxy = "http://fixture:private@proxy.invalid:3128"
    monkeypatch.setattr(http.request, "getproxies", lambda: {scheme: proxy})
    with pytest.raises(ValueError, match="Proxy configuration is not supported") as caught:
        with http.pooled_http_session():
            pytest.fail("A configured proxy must not be silently bypassed")
    assert "private" not in str(caught.value)
    assert "proxy.invalid" not in str(caught.value)
    assert http.current_urlopen() is None


def test_no_proxy_bypass_list_alone_allows_direct_connections(monkeypatch):
    monkeypatch.setattr(http.request, "getproxies", lambda: {"no": "localhost"})
    with http.pooled_http_session():
        assert http.current_urlopen() is not None


@pytest.mark.parametrize("number", [errno.EACCES, errno.ECONNREFUSED])
def test_connection_error_preserves_os_cause(monkeypatch, number):
    import urllib3.util.connection

    def fail(*args, **kwargs):
        raise OSError(number, "fixture connection failure")

    monkeypatch.setattr(http.request, "getproxies", lambda: {})
    monkeypatch.setattr(urllib3.util.connection, "create_connection", fail)
    with http.pooled_http_session():
        with pytest.raises(error.URLError) as caught:
            http.current_urlopen()(post("https://origin.invalid/"), timeout=1)
    assert type(caught.value.reason) is type(OSError(number, "fixture"))
    assert caught.value.reason.errno == number
    diagnostic = read_network_error(caught.value)
    assert diagnostic.cause_errno == errno.errorcode[number]


@pytest.mark.parametrize("number,attempts", [
    (errno.EACCES, 1), (errno.ECONNREFUSED, 3), (errno.ETIMEDOUT, 3),
])
def test_os_error_controls_application_retry_budget(monkeypatch, number, attempts):
    import urllib3.util.connection
    from telegram_shared.openai_api import OpenAIRequestError

    calls = []

    def fail(*args, **kwargs):
        calls.append(1)
        raise OSError(number, "fixture connection failure")

    monkeypatch.setattr(http.request, "getproxies", lambda: {})
    monkeypatch.setattr(urllib3.util.connection, "create_connection", fail)
    with http.pooled_http_session():
        with pytest.raises(OpenAIRequestError) as caught:
            post_responses(
                {}, "fixture", timeout_seconds=1, retry_attempts=3,
                retry_backoff_seconds=0, sleep_func=lambda _: None,
            )
    assert len(calls) == attempts
    assert caught.value.retryable is (attempts > 1)
    assert caught.value.cause_errno == errno.errorcode[number]
    assert caught.value.cause_type == type(OSError(number, "fixture")).__name__


@pytest.mark.parametrize("status,attempts", [(400, 1), (401, 1), (503, 3), (200, 3)])
@pytest.mark.parametrize("failure", ["timeout", "read_timeout", "protocol"])
def test_status_survives_error_body_read_failure(monkeypatch, status, attempts, failure):
    import urllib3
    from telegram_shared.openai_api import OpenAIRequestError

    responses = []

    class BrokenBody:
        reason = "fixture"
        headers = {"x-request-id": "fixture-status"}

        def __init__(self):
            self.status = status
            self.closed = False
            self.released = False

        def read(self, *args):
            if failure == "timeout":
                raise TimeoutError("fixture body timeout")
            if failure == "read_timeout":
                raise urllib3.exceptions.ReadTimeoutError(None, "/fixture", "fixture")
            raise urllib3.exceptions.ProtocolError("fixture truncated body")

        def close(self):
            self.closed = True

        def release_conn(self):
            self.released = True

    def send(*args, **kwargs):
        raw = BrokenBody()
        responses.append(raw)
        return raw

    monkeypatch.setattr(http.request, "getproxies", lambda: {})
    with http.pooled_http_session() as session:
        monkeypatch.setattr(session._direct, "request", send)
        with pytest.raises(OpenAIRequestError) as caught:
            post_responses(
                {}, "fixture", timeout_seconds=1, retry_attempts=3,
                retry_backoff_seconds=0, sleep_func=lambda _: None,
            )
    assert caught.value.status_code == (status if status >= 300 else None)
    assert caught.value.request_id == ("fixture-status" if status >= 300 else None)
    assert len(responses) == attempts
    assert all(raw.closed and raw.released for raw in responses)
