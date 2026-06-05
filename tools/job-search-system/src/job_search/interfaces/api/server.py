from __future__ import annotations

import argparse
from http.server import BaseHTTPRequestHandler, HTTPServer
import ipaddress
import json
from pathlib import Path
import sys
from typing import Any
from urllib.parse import urlsplit

from job_search.config import load_runtime_settings
from job_search.interfaces.api.app import JobSearchApi


class JobSearchHttpHandler(BaseHTTPRequestHandler):
    api: JobSearchApi

    def do_GET(self) -> None:
        self._handle()

    def do_POST(self) -> None:
        self._handle()

    def log_message(self, format: str, *args: Any) -> None:
        return

    def _handle(self) -> None:
        if self.command in {"POST", "PUT", "PATCH", "DELETE"} and not self._is_allowed_browser_origin():
            self._send_json(
                status=403,
                payload={
                    "ok": False,
                    "error": {
                        "type": "ApiOriginRejectedError",
                        "message": "State-changing API requests from non-local browser origins are rejected.",
                    },
                },
            )
            return
        length = int(self.headers.get("Content-Length") or "0")
        if length > self.api.max_body_bytes:
            self._send_json(
                status=413,
                payload={
                    "ok": False,
                    "error": {
                        "type": "ApiRequestTooLargeError",
                        "message": f"Request body exceeds api_max_body_bytes={self.api.max_body_bytes}",
                    },
                },
            )
            return
        body = self.rfile.read(length) if length else b""
        response = self.api.dispatch(method=self.command, raw_path=self.path, body=body)
        self._send_json(status=response.status, payload=response.payload)

    def _is_allowed_browser_origin(self) -> bool:
        for header_name in ("Origin", "Referer"):
            raw_value = (self.headers.get(header_name) or "").strip()
            if raw_value and not _is_local_http_origin(raw_value):
                return False
        return True

    def _send_json(self, *, status: int, payload: dict[str, Any]) -> None:
        encoded = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="job-search-api")
    parser.add_argument("--config-path", required=True)
    parser.add_argument("--workspace-path", required=True)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--allow-unsafe-remote-bind", action="store_true")
    return parser


def validate_bind_host(host: str, *, allow_unsafe_remote_bind: bool) -> None:
    if allow_unsafe_remote_bind:
        return
    normalized = host.strip().lower()
    if normalized == "localhost":
        return
    try:
        address = ipaddress.ip_address(normalized)
    except ValueError:
        raise ValueError(
            "API-lite bind host must be loopback by default; use 127.0.0.1, ::1, localhost, or pass --allow-unsafe-remote-bind"
        ) from None
    if not address.is_loopback:
        raise ValueError(
            "API-lite bind host must be loopback by default; pass --allow-unsafe-remote-bind only for explicitly trusted environments"
        )


def validate_runtime_network_safety(*, allow_unsafe_remote_bind: bool, api_allow_local_file_sources: bool) -> None:
    if allow_unsafe_remote_bind and api_allow_local_file_sources:
        raise PermissionError(
            "API-lite cannot combine --allow-unsafe-remote-bind with api_allow_local_file_sources=true; "
            "remote clients must not be able to request local file ingestion."
        )


def _is_local_http_origin(value: str) -> bool:
    parsed = urlsplit(value)
    hostname = (parsed.hostname or "").strip().lower()
    if not hostname:
        return False
    if hostname == "localhost":
        return True
    try:
        return ipaddress.ip_address(hostname).is_loopback
    except ValueError:
        return False


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    validate_bind_host(args.host, allow_unsafe_remote_bind=args.allow_unsafe_remote_bind)
    runtime_settings = load_runtime_settings(Path(args.config_path))
    validate_runtime_network_safety(
        allow_unsafe_remote_bind=args.allow_unsafe_remote_bind,
        api_allow_local_file_sources=runtime_settings.api_allow_local_file_sources,
    )
    api = JobSearchApi(runtime_settings=runtime_settings, workspace_path=Path(args.workspace_path))
    handler = type("ConfiguredJobSearchHttpHandler", (JobSearchHttpHandler,), {"api": api})
    server = HTTPServer((args.host, args.port), handler)
    print(json.dumps({"status": "listening", "host": args.host, "port": server.server_port}, ensure_ascii=False), flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        return 0
    finally:
        server.server_close()
        api.close()
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (ValueError, KeyError, PermissionError, RuntimeError) as exc:
        print(json.dumps({"error": {"type": type(exc).__name__, "message": str(exc)}}, ensure_ascii=False), file=sys.stderr)
        raise SystemExit(2)
