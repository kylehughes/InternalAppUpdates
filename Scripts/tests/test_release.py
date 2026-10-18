from __future__ import annotations

import argparse
import copy
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from urllib.request import Request


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import release  # noqa: E402


SHA = "a" * 40
OTHER_SHA = "b" * 40
REPOSITORY = "owner/repository"


class FakeGitHubClient:
    def __init__(self) -> None:
        self.release_values: list[dict] = []
        self.tag_values: list[dict] = []
        self.tag_shas: dict[str, str] = {}
        self.payloads: dict[str, bytes] = {}
        self.mutations: list[tuple[str, str | None]] = []
        self.create_outcome = "success"
        self.upload_outcomes: dict[str, str] = {}
        self.publish_outcome = "success"
        self.workflow_run_value: dict = {}

    def releases(self):
        return copy.deepcopy(self.release_values)

    def tags(self):
        return copy.deepcopy(self.tag_values)

    def resolve_tag(self, tag):
        return self.tag_shas[tag]

    def workflow_run(self, run_id):
        return copy.deepcopy(self.workflow_run_value)

    def bytes(self, url):
        return self.payloads[url]

    def create_release(self, body):
        self.mutations.append(("create", None))
        if self.create_outcome != "unknown":
            self.release_values.append(
                {
                    "id": 7,
                    "tag_name": body["tag_name"],
                    "target_commitish": body["target_commitish"],
                    "name": body["name"],
                    "draft": True,
                    "prerelease": False,
                    "body": body.get("body", "generated notes"),
                    "assets": [],
                    "upload_url": "https://uploads.example/releases/7/assets{?name}",
                }
            )
        if self.create_outcome != "success":
            raise release.APIError("POST", "releases", "lost response")

    def upload_asset(self, upload_url, name, media_type, data):
        self.mutations.append(("upload", name))
        outcome = self.upload_outcomes.get(name, "success")
        if outcome != "unknown":
            url = f"asset://{name}"
            self.release_values[0]["assets"].append({"name": name, "size": len(data), "url": url})
            self.payloads[url] = data
        if outcome != "success":
            raise release.APIError("POST", upload_url, "lost response")

    def publish_release(self, release_id):
        self.mutations.append(("publish", None))
        if self.publish_outcome != "unknown":
            self.release_values[0]["draft"] = False
            self.release_values[0]["html_url"] = "https://example.invalid/release"
            version = self.release_values[0]["tag_name"]
            self.tag_values = [{"name": version}]
            self.tag_shas[version] = self.release_values[0]["target_commitish"]
        if self.publish_outcome != "success":
            raise release.APIError("PATCH", "release", "lost response")


class ReleaseTests(unittest.TestCase):
    def make_candidate(self, root: Path, version: str = "1.2.3") -> tuple[Path, dict]:
        artifacts = root / "artifacts"
        artifacts.mkdir()
        (artifacts / f"internal_app_updates-{version}-py3-none-any.whl").write_bytes(b"wheel")
        (artifacts / f"internal_app_updates-{version}.tar.gz").write_bytes(b"sdist")
        (artifacts / f"InternalAppUpdates-{version}.doccarchive.zip").write_bytes(b"doccarchive")
        (artifacts / f"InternalAppUpdates-{version}-documentation.zip").write_bytes(b"documentation")
        manifest = release.build_manifest(artifacts, REPOSITORY, version, SHA)
        release.write_json(artifacts / release.MANIFEST_NAME, manifest)
        return artifacts, manifest

    def existing_release(self, version: str = "1.2.3", sha: str = SHA, draft: bool = False):
        return {
            "id": 7,
            "tag_name": version,
            "target_commitish": sha,
            "name": version,
            "draft": draft,
            "prerelease": False,
            "body": "notes",
            "assets": [],
            "upload_url": "https://uploads.example/releases/7/assets{?name}",
        }

    def test_versions_must_be_canonical_stable_bare_semver(self):
        self.assertEqual(release.parse_version("0.1.0"), (0, 1, 0))
        for value in ("v1.2.3", "01.2.3", "1.2", "1.2.3-beta", "1.2.3+build"):
            with self.subTest(value=value), self.assertRaises(release.ReleaseError):
                release.parse_version(value)

    def test_candidate_run_id_must_be_positive_numeric(self):
        self.assertEqual(release.positive_run_id("123"), 123)
        for value in ("", "0", "-1", "1.0", "abc"):
            with self.subTest(value=value), self.assertRaises(argparse.ArgumentTypeError):
                release.positive_run_id(value)

    def test_project_version_must_match(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "pyproject.toml").write_text('[project]\nversion = "1.2.3"\n', encoding="utf-8")
            release.validate_project_version(root, "1.2.3")
            with self.assertRaisesRegex(release.ReleaseError, "does not match"):
                release.validate_project_version(root, "1.2.4")

    def test_exact_checked_out_sha_may_remain_behind_reachable_main(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            subprocess.run(["git", "init", "-q", "-b", "main", root], check=True)
            subprocess.run(["git", "-C", root, "config", "user.name", "Release Test"], check=True)
            subprocess.run(["git", "-C", root, "config", "user.email", "release@example.invalid"], check=True)
            (root / "value").write_text("one", encoding="utf-8")
            subprocess.run(["git", "-C", root, "add", "value"], check=True)
            subprocess.run(["git", "-C", root, "commit", "-q", "-m", "one"], check=True)
            candidate = release.run_git(root, "rev-parse", "HEAD")
            (root / "value").write_text("two", encoding="utf-8")
            subprocess.run(["git", "-C", root, "commit", "-q", "-am", "two"], check=True)
            current_main = release.run_git(root, "rev-parse", "HEAD")
            subprocess.run(["git", "-C", root, "update-ref", "refs/remotes/origin/main", current_main], check=True)
            subprocess.run(["git", "-C", root, "checkout", "-q", "--detach", candidate], check=True)
            release.validate_source(root, candidate, "origin/main")

            with self.assertRaisesRegex(release.ReleaseError, "checked-out HEAD"):
                release.validate_source(root, current_main, "origin/main")

    def test_cross_origin_asset_redirect_drops_authorization(self):
        request = Request("https://api.github.com/repos/owner/repository/releases/assets/1")
        request.add_header("Authorization", "Bearer secret")
        redirected = release.CredentialSafeRedirectHandler().redirect_request(
            request,
            None,
            302,
            "Found",
            {},
            "https://objects.example.invalid/signed-asset",
        )
        self.assertIsNotNone(redirected)
        self.assertIsNone(redirected.get_header("Authorization"))

    def test_same_origin_redirect_preserves_authorization(self):
        request = Request("https://api.github.com/repos/owner/repository/releases/assets/1")
        request.add_header("Authorization", "Bearer secret")
        redirected = release.CredentialSafeRedirectHandler().redirect_request(
            request,
            None,
            302,
            "Found",
            {},
            "https://api.github.com/repos/owner/repository/releases/assets/2",
        )
        self.assertEqual(redirected.get_header("Authorization"), "Bearer secret")

    def test_candidate_requires_the_exact_release_asset_set(self):
        with tempfile.TemporaryDirectory() as directory:
            artifacts = Path(directory)
            (artifacts / "documentation.zip").write_bytes(b"docs")
            with self.assertRaisesRegex(release.ReleaseError, "asset set mismatch"):
                release.build_manifest(artifacts, REPOSITORY, "1.2.3", SHA)

    def test_retained_candidate_manifest_binds_repository_version_sha_and_bytes(self):
        with tempfile.TemporaryDirectory() as directory:
            artifacts, manifest = self.make_candidate(Path(directory))
            path = artifacts / release.MANIFEST_NAME
            self.assertEqual(release.load_manifest(path, artifacts, REPOSITORY, "1.2.3", SHA), manifest)
            with self.assertRaisesRegex(release.ReleaseError, "does not match"):
                release.load_manifest(path, artifacts, "other/repository", "1.2.3", SHA)
            (artifacts / manifest["files"][0]["name"]).write_bytes(b"different")
            with self.assertRaisesRegex(release.ReleaseError, "does not match"):
                release.load_manifest(path, artifacts, REPOSITORY, "1.2.3", SHA)

    def test_retained_candidate_source_must_be_completed_release_dispatch(self):
        client = FakeGitHubClient()
        client.workflow_run_value = {
            "head_sha": SHA,
            "head_branch": "main",
            "event": "workflow_dispatch",
            "status": "completed",
            "path": ".github/workflows/release.yml@refs/heads/main",
            "repository": {"full_name": REPOSITORY},
        }
        release.validate_candidate_run(client, 123, REPOSITORY, SHA)
        for key, value in (
            ("head_sha", OTHER_SHA),
            ("head_branch", "feature"),
            ("event", "pull_request"),
            ("status", "in_progress"),
            ("path", ".github/workflows/other.yml"),
            ("repository", {"full_name": "other/repository"}),
        ):
            with self.subTest(key=key):
                invalid = copy.deepcopy(client.workflow_run_value)
                invalid[key] = value
                client.workflow_run_value = invalid
                with self.assertRaisesRegex(release.ReleaseError, "not a completed Release workflow"):
                    release.validate_candidate_run(client, 123, REPOSITORY, SHA)
                client.workflow_run_value = {
                    "head_sha": SHA,
                    "head_branch": "main",
                    "event": "workflow_dispatch",
                    "status": "completed",
                    "path": ".github/workflows/release.yml",
                    "repository": {"full_name": REPOSITORY},
                }

    def test_version_must_advance_past_other_stable_versions(self):
        client = FakeGitHubClient()
        client.tag_values = [{"name": "1.2.4"}]
        client.tag_shas["1.2.4"] = OTHER_SHA
        with self.assertRaisesRegex(release.ReleaseError, "must be greater"):
            release.validate_remote(client, "1.2.3", SHA)

    def test_identical_published_release_is_a_retry(self):
        with tempfile.TemporaryDirectory() as directory:
            artifacts, manifest = self.make_candidate(Path(directory))
            client = FakeGitHubClient()
            current = self.existing_release()
            for entry in manifest["files"]:
                data = (artifacts / entry["name"]).read_bytes()
                url = f"asset://{entry['name']}"
                current["assets"].append({"name": entry["name"], "size": len(data), "url": url})
                client.payloads[url] = data
            client.release_values = [current]
            client.tag_values = [{"name": "1.2.3"}]
            client.tag_shas["1.2.3"] = SHA
            receipt = Path(directory) / "receipt.json"
            release.publish(client, artifacts, manifest, receipt, "notes")
            self.assertEqual(client.mutations, [])
            self.assertEqual(json.loads(receipt.read_text())["phase"], "published")

    def test_existing_tag_without_release_is_blocked(self):
        client = FakeGitHubClient()
        client.tag_values = [{"name": "1.2.3"}]
        client.tag_shas["1.2.3"] = SHA
        with self.assertRaisesRegex(release.ReleaseError, "bump pyproject.toml"):
            release.validate_remote(client, "1.2.3", SHA)

    def test_tag_at_different_sha_is_blocked(self):
        client = FakeGitHubClient()
        client.tag_values = [{"name": "1.2.3"}]
        client.tag_shas["1.2.3"] = OTHER_SHA
        client.release_values = [self.existing_release(sha=OTHER_SHA)]
        with self.assertRaisesRegex(release.ReleaseError, "not certified SHA"):
            release.validate_remote(client, "1.2.3", SHA)

    def test_asset_digest_mismatch_is_blocked(self):
        with tempfile.TemporaryDirectory() as directory:
            artifacts, manifest = self.make_candidate(Path(directory))
            client = FakeGitHubClient()
            current = self.existing_release(draft=True)
            entry = manifest["files"][0]
            current["assets"] = [{"name": entry["name"], "size": entry["size"], "url": "asset://wrong"}]
            client.payloads["asset://wrong"] = b"x" * entry["size"]
            client.release_values = [current]
            with self.assertRaisesRegex(release.ReleaseError, "bytes do not match"):
                release.validate_remote(client, "1.2.3", SHA, manifest)

    def test_lost_upload_response_reconciles_exact_bytes_and_completes(self):
        with tempfile.TemporaryDirectory() as directory:
            artifacts, manifest = self.make_candidate(Path(directory))
            client = FakeGitHubClient()
            client.create_outcome = "accepted_then_error"
            first_name = manifest["files"][0]["name"]
            client.upload_outcomes[first_name] = "accepted_then_error"
            receipt = Path(directory) / "receipt.json"
            release.publish(client, artifacts, manifest, receipt, None)
            self.assertEqual(json.loads(receipt.read_text())["phase"], "published")
            self.assertEqual([kind for kind, _ in client.mutations].count("upload"), 4)

    def test_unobservable_partial_upload_stops_with_pending_receipt(self):
        with tempfile.TemporaryDirectory() as directory:
            artifacts, manifest = self.make_candidate(Path(directory))
            client = FakeGitHubClient()
            client.upload_outcomes[manifest["files"][0]["name"]] = "unknown"
            receipt = Path(directory) / "receipt.json"
            with self.assertRaises(release.UnknownMutation):
                release.publish(client, artifacts, manifest, receipt, None)
            saved = json.loads(receipt.read_text())
            self.assertEqual(saved["phase"], "mutation-pending")
            self.assertEqual(saved["pendingMutation"]["kind"], "upload-asset")
            self.assertNotIn(("publish", None), client.mutations)

    def test_lost_publish_response_reconciles_published_release(self):
        with tempfile.TemporaryDirectory() as directory:
            artifacts, manifest = self.make_candidate(Path(directory))
            client = FakeGitHubClient()
            client.publish_outcome = "accepted_then_error"
            receipt = Path(directory) / "receipt.json"
            release.publish(client, artifacts, manifest, receipt, "notes")
            saved = json.loads(receipt.read_text())
            self.assertEqual(saved["phase"], "published")
            self.assertEqual(saved["releaseId"], 7)
            self.assertEqual([kind for kind, _ in client.mutations].count("publish"), 1)

    def test_validation_dry_run_makes_no_mutations(self):
        with tempfile.TemporaryDirectory() as directory:
            _, manifest = self.make_candidate(Path(directory))
            client = FakeGitHubClient()
            release.validate_remote(client, "1.2.3", SHA, manifest)
            self.assertEqual(client.mutations, [])


if __name__ == "__main__":
    unittest.main()
