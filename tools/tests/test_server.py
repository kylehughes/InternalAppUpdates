from __future__ import annotations

from http.client import HTTPConnection
import json
from pathlib import Path
import subprocess
from threading import Thread
import unittest
from unittest.mock import patch

from internal_app_updates import server
from internal_app_updates.server import configure_tailscale_serve, make_handler, normalize_mount_path, run
from http.server import ThreadingHTTPServer


class ServerTests(unittest.TestCase):
    def setUp(self) -> None:
        import tempfile

        self.temporary = tempfile.TemporaryDirectory()
        self.external = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        (self.root / "release.ipa").write_bytes(b"0123456789")
        (self.root / "feed.json").write_text('{"items": []}', encoding="utf-8")
        (self.root / "index.html").write_text("update feed", encoding="utf-8")
        (self.root / ".secret").write_text("no", encoding="utf-8")
        (self.root / "secret-link").symlink_to(self.root / ".secret")
        outside = Path(self.external.name) / "outside.ipa"
        outside.write_bytes(b"outside")
        (self.root / "outside-link").symlink_to(outside)
        (self.root / "folder").mkdir()
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(self.root))
        self.thread = Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()
        self.temporary.cleanup()
        self.external.cleanup()

    def request(self, method: str, path: str, headers: dict[str, str] | None = None):
        connection = HTTPConnection("127.0.0.1", self.server.server_address[1], timeout=2)
        connection.request(method, path, headers=headers or {})
        response = connection.getresponse()
        body = response.read()
        connection.close()
        return response, body

    def test_get_head_and_cache_rules(self) -> None:
        response, body = self.request("GET", "/release.ipa")
        self.assertEqual(response.status, 200)
        self.assertEqual(body, b"0123456789")
        self.assertEqual(response.getheader("Content-Type"), "application/octet-stream")
        self.assertIn("immutable", response.getheader("Cache-Control"))
        response, body = self.request("HEAD", "/feed.json")
        self.assertEqual(response.status, 200)
        self.assertEqual(body, b"")
        self.assertEqual(response.getheader("Cache-Control"), "no-cache")

    def test_range_etag_and_if_range(self) -> None:
        response, body = self.request("GET", "/release.ipa", {"Range": "bytes=2-5"})
        self.assertEqual((response.status, body), (206, b"2345"))
        self.assertEqual(response.getheader("Content-Range"), "bytes 2-5/10")
        tag = response.getheader("ETag")
        response, body = self.request("GET", "/release.ipa", {"If-None-Match": tag})
        self.assertEqual((response.status, body), (304, b""))
        response, body = self.request("GET", "/release.ipa", {"Range": "bytes=0-1", "If-Range": '"old"'})
        self.assertEqual((response.status, body), (200, b"0123456789"))
        response, _ = self.request("GET", "/release.ipa", {"Range": "bytes=99-100"})
        self.assertEqual(response.status, 416)

    def test_atomic_replacement_keeps_response_metadata_and_bytes_together(self) -> None:
        feed = self.root / "feed.json"
        previous = feed.read_bytes()
        replacement = self.root / "replacement.json"
        current = b'{"release": null, "bundleIdentifier": "com.example.app"}'
        replacement.write_bytes(current)
        etag = server._etag

        def replace_after_metadata(stat):
            replacement.replace(feed)
            return etag(stat)

        with patch.object(server, "_etag", side_effect=replace_after_metadata):
            response, body = self.request("GET", "/feed.json")
        self.assertEqual((response.status, body), (200, previous))
        self.assertEqual(int(response.getheader("Content-Length")), len(previous))
        response, body = self.request("GET", "/feed.json")
        self.assertEqual((response.status, body), (200, current))
        self.assertEqual(int(response.getheader("Content-Length")), len(current))

    def test_rejects_directory_hidden_and_encoded_escapes(self) -> None:
        for path in ("/folder/", "/.secret", "/secret-link", "/outside-link", "/%2esecret", "/%252esecret", "/%2e%2e/release.ipa"):
            response, _ = self.request("GET", path)
            self.assertEqual(response.status, 404, path)

    def test_accepts_preserved_mount_prefix(self) -> None:
        response, body = self.request("GET", "/internal-app-updates/release.ipa")
        self.assertEqual((response.status, body), (200, b"0123456789"))

    def test_root_and_mount_root_serve_index_without_listing_directories(self) -> None:
        for path in ("/", "/internal-app-updates", "/internal-app-updates/"):
            response, body = self.request("GET", path)
            self.assertEqual((response.status, body), (200, b"update feed"), path)
            self.assertEqual(response.getheader("Cache-Control"), "no-cache")
        response, _ = self.request("GET", "/folder")
        self.assertEqual(response.status, 404)

    def test_rejects_ambiguous_mount_paths_and_invalid_serve_arguments(self) -> None:
        for path in ("/feed?query", "/feed#fragment", "/feed\\path", "/feed%2fpath", "/feed\npath"):
            with self.assertRaises(ValueError, msg=path):
                normalize_mount_path(path)
        arguments = type("Args", (), {
            "directory": self.root, "port": 65_536, "tailscale": False, "bind": "127.0.0.1",
        })()
        self.assertEqual(run(arguments), 2)
        arguments.port = 0
        arguments.tailscale = True
        arguments.bind = "0.0.0.0"
        self.assertEqual(run(arguments), 2)
        arguments.bind = "127.0.0.2"
        self.assertEqual(run(arguments), 2)


class TailscaleTests(unittest.TestCase):
    def test_rejects_invalid_backend_ports_before_running_commands(self) -> None:
        for port in (0, -1, 65_536, True, "8765", 8765.0):
            with self.subTest(port=port):
                commands, runner = self.configured_runner({})
                with self.assertRaisesRegex(RuntimeError, "backend port"):
                    configure_tailscale_serve(binary="tailscale", port=port,
                                             mount_path="/app/", runner=runner)
                self.assertEqual(commands, [])

    def test_rejects_invalid_dns_names_before_configuring_a_route(self) -> None:
        for dns_name in ("", None, 123, "node/path", "node?query", "user@node", "node..", "-node", "node:443"):
            with self.subTest(dns_name=dns_name):
                commands = []

                def runner(command, **kwargs):
                    commands.append(command)
                    value = {} if command[1:4] == ["serve", "status", "--json"] else {
                        "Self": {"DNSName": dns_name},
                    }
                    return subprocess.CompletedProcess(command, 0, json.dumps(value), "")

                with self.assertRaisesRegex(RuntimeError, "DNS name"):
                    configure_tailscale_serve(binary="tailscale", port=8765,
                                             mount_path="/app/", runner=runner)
                self.assertEqual(commands, [["tailscale", "serve", "status", "--json"],
                                            ["tailscale", "status", "--json"]])

    def configured_runner(self, config):
        commands = []

        def runner(command, **kwargs):
            commands.append(command)
            value = config if command[1:4] == ["serve", "status", "--json"] else {
                "Self": {"DNSName": "node.tailnet.ts.net."},
            }
            return subprocess.CompletedProcess(command, 0, json.dumps(value), "")

        return commands, runner

    def test_refuses_funnel_without_altering_or_reusing_routes(self) -> None:
        for existing in ({}, {"/app/": {"Proxy": "http://127.0.0.1:8765"}}):
            for foreground in (False, True):
                with self.subTest(existing=existing, foreground=foreground):
                    config = {"Web": {"node:443": {"Handlers": existing}},
                              "AllowFunnel": {"node:443": True}}
                    if foreground:
                        config = {"Web": {}, "Foreground": {"session": config}}
                    commands, runner = self.configured_runner(config)
                    with self.assertRaisesRegex(RuntimeError, "Funnel"):
                        configure_tailscale_serve(binary="tailscale", port=8765,
                                                 mount_path="/app/", runner=runner)
                    self.assertEqual(commands, [["tailscale", "serve", "status", "--json"]])

    def test_checks_foreground_conflicts_even_with_background_web_configuration(self) -> None:
        for background in ({}, {"node:443": {"Handlers": {"/app/": {"Proxy": "http://127.0.0.1:8765"}}}}):
            with self.subTest(background=background):
                commands, runner = self.configured_runner({
                    "Web": background,
                    "Foreground": {"session": {"Web": {"node:443": {
                        "Handlers": {"/app/": {"Proxy": "http://127.0.0.1:9999"}},
                    }}}},
                })
                with self.assertRaisesRegex(RuntimeError, "different handler"):
                    configure_tailscale_serve(binary="tailscale", port=8765,
                                             mount_path="/app/", runner=runner)
                self.assertEqual(commands, [["tailscale", "serve", "status", "--json"]])

    def test_reuses_private_mount_and_ignores_unrelated_listeners_and_services(self) -> None:
        config = {
            "Web": {"node:443": {"Handlers": {"/app/": {"Proxy": "http://127.0.0.1:8765"}}},
                    "node:8443": {"Handlers": {"/app/": {"Proxy": "http://127.0.0.1:9999"}}}},
            "AllowFunnel": {"node:443": False, "node:8443": True},
            "Services": {"svc:other": {"Web": {"other:443": {"Handlers": {
                "/app/": {"Proxy": "http://127.0.0.1:9999"},
            }}}}},
        }
        commands, runner = self.configured_runner(config)
        self.assertEqual(configure_tailscale_serve(binary="tailscale", port=8765,
                                                 mount_path="/app/", runner=runner),
                         "https://node.tailnet.ts.net/app/")
        self.assertEqual(commands, [["tailscale", "serve", "status", "--json"],
                                    ["tailscale", "status", "--json"]])

    def test_configures_only_missing_mount_and_returns_private_url(self) -> None:
        commands: list[list[str]] = []

        def runner(command, **kwargs):
            commands.append(command)
            if command[1:4] == ["serve", "status", "--json"]:
                return subprocess.CompletedProcess(command, 0, "{}", "")
            if command[1:3] == ["status", "--json"]:
                return subprocess.CompletedProcess(command, 0, '{"Self": {"DNSName": "node.tailnet.ts.net."}}', "")
            return subprocess.CompletedProcess(command, 0, "", "")

        url = configure_tailscale_serve(binary="tailscale", port=8765, mount_path="internal-app-updates", runner=runner)
        self.assertEqual(url, "https://node.tailnet.ts.net/internal-app-updates/")
        self.assertIn(
            ["tailscale", "serve", "--bg", "--https=443", "--set-path=/internal-app-updates/", "http://127.0.0.1:8765"],
            commands,
        )

    def test_refuses_conflicting_mount(self) -> None:
        def runner(command, **kwargs):
            return subprocess.CompletedProcess(
                command,
                0,
                '{"Web": {"node:443": {"Handlers": {"/internal-app-updates/": {"Proxy": "http://127.0.0.1:9999"}}}}}',
                "",
            )

        with self.assertRaisesRegex(RuntimeError, "different handler"):
            configure_tailscale_serve(binary="tailscale", port=8765, mount_path="/internal-app-updates/", runner=runner)

    def test_normalizes_executable_and_command_failures(self) -> None:
        def missing(command, **kwargs):
            raise OSError("not found")

        with self.assertRaisesRegex(RuntimeError, "could not inspect"):
            configure_tailscale_serve(binary="tailscale", port=8765, mount_path="/internal-app-updates/", runner=missing)

        def failing(command, **kwargs):
            raise subprocess.CalledProcessError(1, command, stderr="serve failed")

        with self.assertRaisesRegex(RuntimeError, "could not inspect"):
            configure_tailscale_serve(binary="tailscale", port=8765, mount_path="/internal-app-updates/", runner=failing)
