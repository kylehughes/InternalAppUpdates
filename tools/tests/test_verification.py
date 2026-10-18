from __future__ import annotations

import hashlib
import http.client
import io
import json
from decimal import Decimal
from fractions import Fraction
from pathlib import Path
import tempfile
import unittest
import urllib.error
import urllib.request
from unittest.mock import patch

from internal_app_updates.publishing import PublicationError
from internal_app_updates import verification


IMMUTABLE = "public, max-age=31536000, immutable"
MUTABLE = "no-cache"


class FakeResponse:
    def __init__(self, contents: bytes, *, status: int = 200, content_type: str,
                 cache_control: str, maximum_read: int | None = None):
        self._stream = io.BytesIO(contents)
        self.status = status
        self.headers = {"Content-Type": content_type, "Cache-Control": cache_control}
        self.maximum_read = maximum_read
        self.read_sizes: list[int] = []

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def getcode(self):
        return self.status

    def read(self, size=-1):
        self.read_sizes.append(size)
        if self.maximum_read is not None and (size < 0 or size > self.maximum_read):
            raise AssertionError(f"unbounded response read: {size}")
        return self._stream.read(size)


class VerificationTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.root = Path(self.directory.name)
        self.contents = [b"ipa", b"manifest", b"release", b"index", b"feed"]
        release_id = hashlib.sha256(self.contents[0]).hexdigest()
        paths = [
            f"releases/{release_id}/app.ipa",
            f"releases/{release_id}/manifest.plist",
            f"releases/{release_id}/release.json",
            "index.html",
            "feed.json",
        ]
        content_types = [
            "application/octet-stream", "text/xml", "application/json",
            "text/html", "application/json",
        ]
        roles = ["release", "release", "release", "install-page", "feed"]
        files = []
        for path, contents, content_type, role in zip(paths, self.contents, content_types, roles):
            files.append({
                "path": path,
                "url": f"https://updates.example/{path}",
                "role": role,
                "sha256": hashlib.sha256(contents).hexdigest(),
                "size": len(contents),
                "contentType": content_type,
                "cacheControl": IMMUTABLE if role == "release" else MUTABLE,
            })
        self.release = {
            "id": release_id, "version": "1.2", "build": "3", "minimumOSVersion": "16.0",
            "publishedAt": "2026-01-01T00:00:00Z", "expiresAt": "2030-01-01T00:00:00Z",
            "manifestURL": f"https://updates.example/releases/{release_id}/manifest.plist",
        }
        self.links = {
            "installPageURL": "https://updates.example/index.html",
            "feedURL": "https://updates.example/feed.json",
        }
        self.publication = {
            "schemaVersion": 1,
            "bundleIdentifier": "example.app",
            "release": self.release,
            "links": self.links,
            "deployment": {
                "schemaVersion": 1,
                "directory": str(self.root.resolve()),
                "baseURL": "https://updates.example/",
                "files": files,
            },
        }

    def tearDown(self):
        self.directory.cleanup()

    def responses(self, contents=None):
        values = contents or self.contents
        result = []
        for file, contents in zip(self.publication["deployment"]["files"], values):
            result.append(FakeResponse(
                contents,
                content_type=file["contentType"],
                cache_control=file["cacheControl"],
            ))
        return result

    def write_publication(self):
        path = self.root / "publication.json"
        path.write_text(json.dumps(self.publication))
        return path

    def verify(self, responses):
        self.write_publication()
        iterator = iter(responses)
        with patch.object(verification, "_open_url", side_effect=lambda request, timeout: next(iterator)):
            return verification.verify_publication(self.root / "publication.json")

    def test_verifies_every_file_and_returns_release_links_and_totals(self):
        responses = self.responses()
        result = self.verify(responses)
        self.assertEqual(result, {
            "verified": True,
            "release": self.release,
            "links": self.links,
            "filesVerified": 5,
            "bytesVerified": sum(map(len, self.contents)),
        })

    def test_requests_identity_encoding_without_authentication_or_cache_busting(self):
        requests = []
        responses = iter(self.responses())

        def open_url(request, timeout):
            requests.append(request)
            self.assertEqual(timeout, 9)
            return next(responses)

        self.write_publication()
        with patch.object(verification, "_open_url", side_effect=open_url):
            verification.verify_publication(self.root / "publication.json", timeout=9)
        self.assertEqual([request.full_url for request in requests], [
            file["url"] for file in self.publication["deployment"]["files"]
        ])
        for request in requests:
            self.assertEqual(request.get_header("Accept-encoding"), "identity")
            self.assertIsNone(request.get_header("Authorization"))

    def test_rejects_malformed_publication_report(self):
        path = self.root / "publication.json"
        path.write_text(json.dumps({"deployment": {"schemaVersion": 2}}))
        with self.assertRaises(PublicationError):
            verification.verify_publication(path)

    def test_rejects_stale_feed(self):
        responses = self.responses([*self.contents[:-1], b"old!"])
        with self.assertRaisesRegex(PublicationError, "feed.json: SHA-256"):
            self.verify(responses)

    def test_rejects_invalid_timeouts_before_networking(self):
        for timeout in (None, "3", complex(1, 0), 0, -1, float("inf"), float("nan")):
            with self.subTest(timeout=timeout), \
                    patch.object(verification, "_load_publication") as load, \
                    self.assertRaisesRegex(PublicationError, "timeout"):
                verification.verify_publication(self.root / "publication.json", timeout=timeout)
            load.assert_not_called()

        for timeout in (True, Decimal("1.5"), Fraction(3, 2)):
            with self.subTest(timeout=timeout), \
                    patch.object(verification, "_load_publication", side_effect=PublicationError("loaded")), \
                    self.assertRaisesRegex(PublicationError, "loaded"):
                verification.verify_publication(self.root / "publication.json", timeout=timeout)

    def test_rejects_short_and_oversized_ipa(self):
        for ipa in (b"ip", b"ipa extra"):
            with self.subTest(ipa=ipa), self.assertRaisesRegex(PublicationError, "app.ipa: expected"):
                self.verify(self.responses([ipa, *self.contents[1:]]))

    def test_rejects_corrupt_ipa_of_expected_size(self):
        with self.assertRaisesRegex(PublicationError, "app.ipa: SHA-256"):
            self.verify(self.responses([b"bad", *self.contents[1:]]))

    def test_accepts_xml_alias_and_content_type_parameters(self):
        responses = self.responses()
        responses[1].headers["Content-Type"] = "application/xml; charset=utf-8"
        responses[3].headers["Content-Type"] = "text/html; charset=UTF-8"
        self.verify(responses)

    def test_rejects_wrong_content_type(self):
        responses = self.responses()
        responses[0].headers["Content-Type"] = "text/html"
        with self.assertRaisesRegex(PublicationError, "Content-Type"):
            self.verify(responses)

    def test_enforces_immutable_and_mutable_cache_policies(self):
        invalid = [
            (0, "public, max-age=31536000"),
            (0, "public, max-age=31536000, immutable, no-cache"),
            (0, "private, max-age=31536000, immutable"),
            (0, "public, max-age=1, immutable"),
            (0, "public=x, max-age=31536000, immutable"),
            (0, "public, max-age=31536000, immutable=ignored"),
            (0, "public, max-age=31536000, immutable, s-maxage=60"),
            (3, "public, max-age=60"),
            (4, 'no-cache="Set-Cookie"'),
            (4, "max-age=0"),
            (4, "max-age=0, must-revalidate, s-maxage=60"),
            (4, "no-store=ignored"),
            (4, "no-cache, max-age=0, max-age=60"),
            (4, "no-cache, s-maxage=60"),
            (4, "no-store, s-maxage=60"),
            (0, 'public, max-age="31536000, immutable'),
            (0, 'public, max-age=31536000", immutable'),
            (0, 'public, max-age=31536000, immutable, extension="\x7f"'),
        ]
        for index, cache_control in invalid:
            with self.subTest(cache_control=cache_control):
                responses = self.responses()
                responses[index].headers["Cache-Control"] = cache_control
                with self.assertRaisesRegex(PublicationError, "Cache-Control"):
                    self.verify(responses)

        for cache_control in ("no-store", "no-cache", "max-age=0, must-revalidate"):
            with self.subTest(cache_control=cache_control):
                responses = self.responses()
                responses[4].headers["Cache-Control"] = cache_control
                self.verify(responses)

        for cache_control in (
            "public,, max-age=31536000, immutable",
            'public, max-age=31536000, immutable, extension="a,b"',
            'public, max-age=31536000, immutable, extension="a\\\"b"',
        ):
            with self.subTest(cache_control=cache_control):
                responses = self.responses()
                responses[0].headers["Cache-Control"] = cache_control
                self.verify(responses)

    def test_rejects_non_200_and_http_errors(self):
        responses = self.responses()
        responses[0].status = 206
        with self.assertRaisesRegex(PublicationError, "HTTP 206"):
            self.verify(responses)

        error = urllib.error.HTTPError("https://updates.example/feed.json", 404, "missing", {}, None)
        self.write_publication()
        with patch.object(verification, "_open_url", side_effect=error):
            with self.assertRaisesRegex(PublicationError, "HTTP 404"):
                verification.verify_publication(self.root / "publication.json")

    def test_redirect_handler_rejects_downgrades_and_credentials(self):
        handler = verification._HTTPSRedirectHandler()
        request = urllib.request.Request("https://updates.example/source")
        for target in ("http://updates.example/file", "https://user@updates.example/file",
                       "https://updates.example:0/file", "https://updates.example/file#fragment",
                       "https://updates.example/white space"):
            with self.subTest(target=target), self.assertRaisesRegex(PublicationError, "redirect"):
                handler.redirect_request(request, None, 302, "Found", {}, target)

    def test_incomplete_http_stream_reports_a_publication_error(self):
        response = self.responses()[0]
        with patch.object(response, "read", side_effect=http.client.IncompleteRead(b"i", 2)):
            with self.assertRaisesRegex(PublicationError, "could not download"):
                self.verify([response])

    def test_redirect_handler_allows_https(self):
        handler = verification._HTTPSRedirectHandler()
        request = urllib.request.Request("https://updates.example/source")
        with patch.object(urllib.request.HTTPRedirectHandler, "redirect_request", return_value="followed"):
            self.assertEqual(
                handler.redirect_request(request, None, 302, "Found", {}, "https://cdn.example/file"),
                "followed",
            )

    def test_streams_large_files_with_a_bounded_read(self):
        contents = b"x" * (verification._CHUNK_SIZE * 2 + 17)
        file = self.publication["deployment"]["files"][0]
        file["size"] = len(contents)
        old_id = self.release["id"]
        file["sha256"] = hashlib.sha256(contents).hexdigest()
        self.release["id"] = file["sha256"]
        self.release["manifestURL"] = self.release["manifestURL"].replace(old_id, file["sha256"])
        for entry in self.publication["deployment"]["files"][:3]:
            entry["path"] = entry["path"].replace(old_id, file["sha256"])
            entry["url"] = entry["url"].replace(old_id, file["sha256"])
        response = FakeResponse(
            contents,
            content_type=file["contentType"],
            cache_control=file["cacheControl"],
            maximum_read=verification._CHUNK_SIZE,
        )
        self.verify([response, *self.responses()[1:]])
        self.assertGreater(len(response.read_sizes), 2)
        self.assertNotIn(-1, response.read_sizes)


if __name__ == "__main__":
    unittest.main()
