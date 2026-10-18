"""Static update-feed server and optional private Tailscale Serve integration."""

from __future__ import annotations

import argparse
import hashlib
import json
import mimetypes
import os
from pathlib import Path
import re
import shutil
import shlex
import subprocess
import sys
from http import HTTPStatus
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Callable
from urllib.parse import unquote_to_bytes, urlsplit


_MAX_REQUEST_LINE = 8_192
_RANGE = re.compile(r"bytes=(?P<start>\d*)-(?P<end>\d*)$")
_TAILSCALE_APP = "/Applications/Tailscale.app/Contents/MacOS/Tailscale"


def register_parser(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    """Register the ``serve`` command on the application's top-level parser."""
    parser = subparsers.add_parser("serve", help="serve an update feed over local HTTP")
    parser.add_argument("--directory", type=Path, required=True, help="published feed directory")
    parser.add_argument("--bind", default="127.0.0.1", help="address to bind (default: 127.0.0.1)")
    parser.add_argument("--port", type=int, default=8765, help="TCP port (default: 8765)")
    parser.add_argument("--tailscale", action="store_true", help="publish privately with Tailscale Serve")
    parser.add_argument("--mount-path", default="/internal-app-updates/", help="Tailscale HTTPS mount path")
    parser.add_argument("--tailscale-bin", help="path to the tailscale executable")
    parser.set_defaults(func=run)


def normalize_mount_path(value: str) -> str:
    """Return a safe, slash-delimited Serve mount path."""
    if (not value or any(character in value for character in "?#\\%")
            or any(ord(character) < 32 or ord(character) == 127 for character in value)):
        raise ValueError("mount path must be an unescaped URL path without a query or fragment")
    if not value.startswith("/"):
        value = "/" + value
    if not value.endswith("/"):
        value += "/"
    if "//" in value or any(part in {"", ".", ".."} for part in value.split("/")[1:-1]):
        raise ValueError("mount path must contain ordinary path segments")
    return value


def _decode_path(path: str) -> list[str] | None:
    """Decode repeatedly so encoded and double-encoded escapes cannot bypass checks."""
    try:
        current = path.encode("ascii", "strict")
    except UnicodeEncodeError:
        return None
    for _ in range(4):
        if b"%" not in current:
            break
        try:
            decoded = unquote_to_bytes(current.decode("ascii", "strict"))
        except UnicodeDecodeError:
            return None
        if decoded == current:
            break
        current = decoded
    try:
        text = current.decode("utf-8", "strict")
    except UnicodeDecodeError:
        return None
    parts = text.split("/")
    if any(part in {"", ".", ".."} or part.startswith(".") for part in parts):
        return None
    return parts


def _content_type(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix == ".ipa":
        return "application/octet-stream"
    if suffix == ".plist":
        return "text/xml; charset=utf-8"
    if suffix == ".json":
        return "application/json"
    if suffix in {".html", ".htm"}:
        return "text/html; charset=utf-8"
    return mimetypes.guess_type(path.name)[0] or "application/octet-stream"


def _etag(stat: os.stat_result) -> str:
    value = f"{stat.st_mtime_ns}:{stat.st_size}".encode()
    return '"' + hashlib.sha256(value).hexdigest()[:24] + '"'


def _cache_control(path: Path) -> str:
    name = path.name.lower()
    if "feed" in name or name.startswith("index."):
        return "no-cache"
    return "public, max-age=31536000, immutable"


def make_handler(root: Path, mount_path: str = "/internal-app-updates/") -> type[SimpleHTTPRequestHandler]:
    """Create a constrained static-file handler rooted at *root*."""
    root = root.resolve()
    mount_path = normalize_mount_path(mount_path)
    mount_without_slash = mount_path.rstrip("/")

    class UpdateHandler(SimpleHTTPRequestHandler):
        server_version = "InternalAppUpdates/1"
        sys_version = ""

        def log_message(self, format: str, *args: object) -> None:
            # The command prints its own useful startup information. Avoid request noise.
            return

        def handle_one_request(self) -> None:
            self.raw_requestline = self.rfile.readline(_MAX_REQUEST_LINE + 1)
            if len(self.raw_requestline) > _MAX_REQUEST_LINE:
                self.requestline = self.request_version = self.command = ""
                self.send_error(HTTPStatus.REQUEST_URI_TOO_LONG)
                return
            if not self.raw_requestline:
                self.close_connection = True
                return
            if not self.parse_request():
                return
            method = getattr(self, "do_" + self.command, None)
            if method is None:
                self.send_error(HTTPStatus.NOT_IMPLEMENTED, "Unsupported method")
                return
            method()
            self.wfile.flush()

        def do_GET(self) -> None:
            self._serve_file(include_body=True)

        def do_HEAD(self) -> None:
            self._serve_file(include_body=False)

        def _file_for_request(self) -> Path | None:
            parsed = urlsplit(self.path)
            raw_path = parsed.path
            # Serve currently strips the mount path for reverse proxies, but accepting
            # either form keeps this backend compatible with proxy versions that retain it.
            if raw_path == mount_without_slash:
                raw_path = ""
            elif raw_path.startswith(mount_path):
                raw_path = raw_path[len(mount_path) :]
            elif raw_path.startswith("/"):
                raw_path = raw_path[1:]
            else:
                return None
            # A feed's base URL is its index document. Only this explicit index
            # mapping is allowed; other directory paths remain 404 rather than
            # becoming directory listings.
            parts = ["index.html"] if raw_path in {"", "/"} else _decode_path(raw_path)
            if parts is None:
                return None
            candidate = root.joinpath(*parts)
            try:
                resolved = candidate.resolve(strict=True)
                relative = resolved.relative_to(root)
            except (FileNotFoundError, OSError, ValueError):
                return None
            if any(part.startswith(".") for part in relative.parts):
                return None
            return resolved if resolved.is_file() else None

        def _serve_file(self, *, include_body: bool) -> None:
            file_path = self._file_for_request()
            if file_path is None:
                self.send_error(HTTPStatus.NOT_FOUND)
                return
            try:
                source = file_path.open("rb")
            except OSError:
                self.send_error(HTTPStatus.NOT_FOUND)
                return
            with source:
                # Publication replaces mutable files atomically. Keep metadata and
                # bytes tied to the same open file across that replacement.
                stat = os.fstat(source.fileno())
                tag = _etag(stat)
                if self._matches_etag(self.headers.get("If-None-Match"), tag):
                    self.send_response(HTTPStatus.NOT_MODIFIED)
                    self.send_header("ETag", tag)
                    self.send_header("Cache-Control", _cache_control(file_path))
                    self.end_headers()
                    return

                start, end, status = 0, stat.st_size - 1, HTTPStatus.OK
                range_header = self.headers.get("Range")
                if range_header and self._if_range_matches(tag):
                    range_result = self._parse_range(range_header, stat.st_size)
                    if range_result is None:
                        self.send_response(HTTPStatus.REQUESTED_RANGE_NOT_SATISFIABLE)
                        self.send_header("Content-Range", f"bytes */{stat.st_size}")
                        self.end_headers()
                        return
                    start, end = range_result
                    status = HTTPStatus.PARTIAL_CONTENT

                self.send_response(status)
                self.send_header("Content-Type", _content_type(file_path))
                self.send_header("Content-Length", str(max(0, end - start + 1)))
                self.send_header("Accept-Ranges", "bytes")
                self.send_header("ETag", tag)
                self.send_header("Cache-Control", _cache_control(file_path))
                if status == HTTPStatus.PARTIAL_CONTENT:
                    self.send_header("Content-Range", f"bytes {start}-{end}/{stat.st_size}")
                self.end_headers()
                if include_body and stat.st_size:
                    source.seek(start)
                    remaining = end - start + 1
                    while remaining:
                        chunk = source.read(min(64 * 1024, remaining))
                        if not chunk:
                            break
                        self.wfile.write(chunk)
                        remaining -= len(chunk)

        @staticmethod
        def _matches_etag(value: str | None, tag: str) -> bool:
            return value is not None and (value.strip() == "*" or tag in (item.strip() for item in value.split(",")))

        def _if_range_matches(self, tag: str) -> bool:
            value = self.headers.get("If-Range")
            return value is None or value.strip() == tag

        @staticmethod
        def _parse_range(value: str, size: int) -> tuple[int, int] | None:
            match = _RANGE.fullmatch(value.strip())
            if not match or size == 0:
                return None
            start_text, end_text = match.group("start"), match.group("end")
            if not start_text and not end_text:
                return None
            if not start_text:
                length = int(end_text)
                if length == 0:
                    return None
                return max(0, size - length), size - 1
            start = int(start_text)
            if start >= size:
                return None
            end = int(end_text) if end_text else size - 1
            if end < start:
                return None
            return start, min(end, size - 1)

    return UpdateHandler


def find_tailscale(binary: str | None) -> str:
    if binary:
        return binary
    return shutil.which("tailscale") or (_TAILSCALE_APP if Path(_TAILSCALE_APP).is_file() else "")


def _run_tailscale(binary: str, arguments: list[str], runner: Callable[..., subprocess.CompletedProcess[str]]) -> str:
    try:
        completed = runner([binary, *arguments], check=True, capture_output=True, text=True)
    except OSError as error:
        raise RuntimeError(f"could not run tailscale: {error}") from error
    except subprocess.CalledProcessError as error:
        detail = (error.stderr or error.stdout or "tailscale command failed").strip()
        raise RuntimeError(detail) from error
    return completed.stdout


def _existing_handlers(config: Any, mount_path: str) -> list[Any]:
    """Find this node's matching HTTPS handlers, including foreground sessions."""
    result = []
    if isinstance(config, dict):
        web = config.get("Web")
        if isinstance(web, dict):
            for endpoint, listener in web.items():
                if str(endpoint).endswith(":443"):
                    result.extend(_existing_handlers(listener, mount_path))
        handlers = config.get("Handlers")
        if isinstance(handlers, dict) and mount_path in handlers:
            result.append(handlers[mount_path])
        for key, value in config.items():
            # Other listeners and separately addressed services are not this mount.
            if key not in {"Web", "Services", "Handlers"}:
                result.extend(_existing_handlers(value, mount_path))
    elif isinstance(config, list):
        for value in config:
            result.extend(_existing_handlers(value, mount_path))
    return result


def _has_funnel_on_https(config: Any) -> bool:
    if isinstance(config, dict):
        funnel = config.get("AllowFunnel")
        if isinstance(funnel, dict) and any(
            str(endpoint).endswith(":443") and enabled is True
            for endpoint, enabled in funnel.items()
        ):
            return True
        return any(_has_funnel_on_https(value) for key, value in config.items()
                   if key not in {"Web", "Services", "Handlers"})
    if isinstance(config, list):
        return any(_has_funnel_on_https(value) for value in config)
    return False


def configure_tailscale_serve(
    *, binary: str, port: int, mount_path: str, runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run
) -> str:
    """Install only this mount's Serve proxy, refusing a conflicting existing mount."""
    if type(port) is not int or not 1 <= port <= 65_535:
        raise RuntimeError("Tailscale backend port must be between 1 and 65535")
    mount_path = normalize_mount_path(mount_path)
    target = f"http://127.0.0.1:{port}"
    try:
        status = json.loads(_run_tailscale(binary, ["serve", "status", "--json"], runner))
    except (RuntimeError, json.JSONDecodeError) as error:
        raise RuntimeError("could not inspect existing Tailscale Serve configuration") from error
    if _has_funnel_on_https(status):
        raise RuntimeError("Tailscale Funnel is enabled on HTTPS port 443; refusing to alter or reuse its routes")
    existing = _existing_handlers(status, mount_path)
    if any(not isinstance(handler, dict) or handler.get("Proxy") != target for handler in existing):
        raise RuntimeError(f"Tailscale Serve already has a different handler at {mount_path}")
    base_url = _tailscale_base_url(binary, runner)
    if existing:
        return base_url + mount_path
    _run_tailscale(
        binary,
        ["serve", "--bg", "--https=443", f"--set-path={mount_path}", target],
        runner,
    )
    return base_url + mount_path


def _tailscale_base_url(binary: str, runner: Callable[..., subprocess.CompletedProcess[str]]) -> str:
    try:
        status = json.loads(_run_tailscale(binary, ["status", "--json"], runner))
        dns_name = status["Self"]["DNSName"]
        if not isinstance(dns_name, str):
            raise ValueError("DNS name must be a string")
        dns_name = dns_name.removesuffix(".")
        if len(dns_name) > 253 or any(
            not re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?", label)
            for label in dns_name.split(".")
        ):
            raise ValueError("DNS name must contain valid labels")
    except (KeyError, TypeError, ValueError, RuntimeError) as error:
        raise RuntimeError("could not determine this node's Tailscale DNS name") from error
    return f"https://{dns_name}"


def _is_tailscale_backend_bind(value: str) -> bool:
    """Return whether Serve's fixed IPv4 loopback proxy can reach this listener."""
    return value in {"127.0.0.1", "localhost"}


def run(args: argparse.Namespace) -> int:
    if not 0 <= args.port <= 65_535:
        print("port must be between 0 and 65535", file=sys.stderr)
        return 2
    if args.tailscale and not _is_tailscale_backend_bind(args.bind):
        print("--tailscale requires --bind to be localhost or an IPv4 loopback address", file=sys.stderr)
        return 2
    directory = args.directory.resolve()
    if not directory.is_dir():
        print(f"not a directory: {directory}", file=sys.stderr)
        return 2
    try:
        mount_path = normalize_mount_path(args.mount_path)
    except ValueError as error:
        print(str(error), file=sys.stderr)
        return 2
    handler = make_handler(directory, mount_path)
    try:
        server = ThreadingHTTPServer((args.bind, args.port), handler)
    except OSError as error:
        print(f"could not listen on {args.bind}:{args.port}: {error}", file=sys.stderr)
        return 1
    try:
        port = server.server_address[1]
        print(f"Serving {directory} at http://{args.bind}:{port}/")
        if args.tailscale:
            binary = find_tailscale(args.tailscale_bin)
            if not binary:
                print("tailscale executable not found", file=sys.stderr)
                return 1
            try:
                base_url = configure_tailscale_serve(binary=binary, port=port, mount_path=mount_path)
            except RuntimeError as error:
                print(str(error), file=sys.stderr)
                return 1
            print(f"Private update URL: {base_url}")
            removal = shlex.join([binary, "serve", "--https=443", f"--set-path={mount_path}", "off"])
            print(f"To remove this persistent route: {removal}")
        server.serve_forever()
    except KeyboardInterrupt:
        return 0
    finally:
        server.server_close()
    return 0
