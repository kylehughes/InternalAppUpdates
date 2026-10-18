from __future__ import annotations

import importlib.util
from pathlib import Path
import sys
import unittest


SCRIPTS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPTS))
import release  # noqa: E402

spec = importlib.util.spec_from_file_location(
    "check_release_documentation_target",
    SCRIPTS / "check-release-documentation-target.py",
)
assert spec is not None and spec.loader is not None
documentation_target = importlib.util.module_from_spec(spec)
spec.loader.exec_module(documentation_target)


VERSION = "1.2.3"
SHA = "a" * 40
OTHER_SHA = "b" * 40


class FakeClient:
    repository = "owner/repository"

    def __init__(self) -> None:
        self.release = self.release_value(VERSION)
        self.latest = self.release_value(VERSION)
        self.tag_sha = SHA
        self.api_error: release.APIError | None = None
        self.requests: list[tuple[str, str]] = []

    @staticmethod
    def release_value(version: str, *, draft: bool = False, prerelease: bool = False) -> dict:
        return {"tag_name": version, "draft": draft, "prerelease": prerelease}

    def request(self, method: str, path: str):
        self.requests.append((method, path))
        if self.api_error is not None:
            raise self.api_error
        if path.endswith("/releases/latest"):
            return self.latest
        if "/releases/tags/" in path:
            if self.release is None:
                raise release.APIError(method, path, "Not Found", 404)
            return self.release
        raise AssertionError(path)

    def resolve_tag(self, version: str) -> str:
        return self.tag_sha


class DocumentationTargetTests(unittest.TestCase):
    def test_accepts_latest_published_release_at_certified_sha(self):
        client = FakeClient()
        documentation_target.validate_documentation_target(client, VERSION, SHA)
        self.assertEqual(
            client.requests,
            [
                ("GET", f"/repos/{client.repository}/releases/tags/{VERSION}"),
                ("GET", f"/repos/{client.repository}/releases/latest"),
            ],
        )

    def test_latest_release_must_match_version(self):
        client = FakeClient()
        client.latest = client.release_value("1.2.4")
        with self.assertRaisesRegex(release.ReleaseError, "must target latest release 1.2.4"):
            documentation_target.validate_documentation_target(client, VERSION, SHA)

    def test_latest_release_must_be_published_and_not_prerelease(self):
        for field in ("draft", "prerelease"):
            with self.subTest(field=field):
                client = FakeClient()
                client.latest[field] = True
                with self.assertRaises(release.ReleaseError):
                    documentation_target.validate_documentation_target(client, VERSION, SHA)

    def test_release_must_not_be_draft(self):
        client = FakeClient()
        client.release = client.release_value(VERSION, draft=True)
        with self.assertRaisesRegex(release.ReleaseError, "still a draft"):
            documentation_target.validate_documentation_target(client, VERSION, SHA)

    def test_release_must_not_be_prerelease(self):
        client = FakeClient()
        client.release = client.release_value(VERSION, prerelease=True)
        with self.assertRaisesRegex(release.ReleaseError, "is a prerelease"):
            documentation_target.validate_documentation_target(client, VERSION, SHA)

    def test_tag_must_resolve_to_certified_sha(self):
        client = FakeClient()
        client.tag_sha = OTHER_SHA
        with self.assertRaisesRegex(release.ReleaseError, "not certified SHA"):
            documentation_target.validate_documentation_target(client, VERSION, SHA)

    def test_missing_release_fails_closed(self):
        client = FakeClient()
        client.release = None
        with self.assertRaises(release.APIError) as context:
            documentation_target.validate_documentation_target(client, VERSION, SHA)
        self.assertEqual(context.exception.status, 404)

    def test_api_error_fails_closed(self):
        client = FakeClient()
        client.api_error = release.APIError("GET", "releases", "service unavailable", 503)
        with self.assertRaises(release.APIError) as context:
            documentation_target.validate_documentation_target(client, VERSION, SHA)
        self.assertEqual(context.exception.status, 503)

    def test_version_must_be_canonical_stable_bare_semver(self):
        client = FakeClient()
        for version in ("v1.2.3", "01.2.3", "1.2", "1.2.3-beta"):
            with self.subTest(version=version), self.assertRaises(release.ReleaseError):
                documentation_target.validate_documentation_target(client, version, SHA)
        self.assertEqual(client.requests, [])

    def test_certified_sha_must_be_lowercase_full_hex(self):
        client = FakeClient()
        for sha in ("abc", "A" * 40, "g" * 40):
            with self.subTest(sha=sha), self.assertRaises(release.ReleaseError):
                documentation_target.validate_documentation_target(client, VERSION, sha)
        self.assertEqual(client.requests, [])


if __name__ == "__main__":
    unittest.main()
