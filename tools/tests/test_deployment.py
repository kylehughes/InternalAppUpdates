from __future__ import annotations

from contextlib import redirect_stdout
from copy import deepcopy
import hashlib
from io import StringIO
import json
import plistlib
from datetime import UTC, datetime
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from internal_app_updates import cli
from internal_app_updates.deployment import (
    load_publication, publication_report, validate_local_files, validate_publication,
)
from internal_app_updates.publishing import IPAInfo, PublicationError, publish
from internal_app_updates import publishing


class DeploymentTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.site = self.root / "site with spaces"
        self.ipa = self.root / "App.ipa"
        self.ipa.write_bytes(b"signed bytes supplied by test verifier")
        self.release_id = hashlib.sha256(self.ipa.read_bytes()).hexdigest()
        self.info = IPAInfo("com.example.App", "1.0", "3", "16.0", self.release_id, "App",
                            profile_expires_at="2030-01-01T00:00:00Z")
        self.base = "https://updates.example/apps/test/"
        self.publication = publish(self.ipa, self.site, self.base, verifier=lambda _: self.info)
        self.report = publication_report(self.site, self.publication, self.base)
        self.report_path = self.root / "publication.json"
        self.report_path.write_text(json.dumps(self.report))

    def test_report_describes_exact_files_and_order_without_changing_feed(self):
        self.assertEqual(json.loads((self.site / "feed.json").read_text()), self.publication)
        files = self.report["deployment"]["files"]
        self.assertEqual([entry["path"] for entry in files], [
            f"releases/{self.release_id}/app.ipa", f"releases/{self.release_id}/manifest.plist",
            f"releases/{self.release_id}/release.json", "index.html", "feed.json",
        ])
        for entry in files:
            contents = (self.site / entry["path"]).read_bytes()
            self.assertEqual(entry["size"], len(contents))
            self.assertEqual(entry["sha256"], hashlib.sha256(contents).hexdigest())
            self.assertEqual(entry["url"], self.base + entry["path"])
        self.assertEqual(self.report["deployment"]["directory"], str(self.site.resolve()))
        validate_local_files(self.report)

    def test_repeated_publication_has_same_report(self):
        repeated = publish(self.ipa, self.site, self.base, verifier=lambda _: self.info)
        self.assertEqual(publication_report(self.site, repeated, self.base), self.report)

    def test_cli_adds_report_without_altering_feed_schema(self):
        output = StringIO()
        with patch.object(cli, "publish", return_value=self.publication), redirect_stdout(output):
            result = cli.main(["publish", "--json", "--ipa", str(self.ipa), "--output", str(self.site),
                               "--base-url", self.base])
        self.assertEqual(result, 0)
        self.assertEqual(json.loads(output.getvalue()), self.report)
        self.assertNotIn("deployment", json.loads((self.site / "feed.json").read_text()))

    def test_local_feed_advance_prevents_reporting_mixed_publication(self):
        feed = deepcopy(self.publication)
        feed["release"]["build"] = "4"
        (self.site / "feed.json").write_text(json.dumps(feed))
        with self.assertRaisesRegex(PublicationError, "serialize"):
            publication_report(self.site, self.publication, self.base)

    def test_load_report_does_not_need_local_artifacts(self):
        self.report["deployment"]["directory"] = "/not/available/on/this/machine"
        self.report_path.write_text(json.dumps(self.report))
        self.assertEqual(load_publication(self.report_path), self.report)

    def test_active_reports_still_ignore_additional_object_fields(self):
        report = deepcopy(self.report)
        report["deployment"]["operation"] = "publish"
        report["deployment"]["customMetadata"] = {"pipeline": "internal"}
        report["deployment"]["files"][0]["customMetadata"] = "retained"
        validate_publication(report)
        validate_local_files(report)

    def test_changed_missing_or_escaping_files_prevent_upload_preflight(self):
        path = self.site / "index.html"
        for mode in ("changed", "missing", "escape"):
            with self.subTest(mode=mode):
                if path.exists() or path.is_symlink():
                    path.unlink()
                if mode == "changed":
                    path.write_text("new bytes")
                elif mode == "escape":
                    outside = self.root / "outside.html"
                    outside.write_text("outside")
                    path.symlink_to(outside)
                with self.assertRaises(PublicationError):
                    validate_local_files(self.report)

    def test_malformed_or_unsupported_reports_fail(self):
        variants = []
        for field, value in (("schemaVersion", 2), ("baseURL", "http://updates.example/"),
                             ("directory", "relative/path"), ("files", [])):
            report = deepcopy(self.report)
            report["deployment"][field] = value
            variants.append(report)
        for field, value in (("path", "../secret"), ("url", "https://other.example/app.ipa"),
                             ("size", True), ("size", -1), ("sha256", "a" * 64),
                             ("contentType", "text/plain"), ("role", "feed")):
            report = deepcopy(self.report)
            report["deployment"]["files"][0][field] = value
            variants.append(report)
        report = deepcopy(self.report)
        report["deployment"]["files"].reverse()
        variants.append(report)
        for report in variants + [None, [], {}, {"schemaVersion": 1, "release": None}]:
            with self.subTest(report=report), self.assertRaises(PublicationError):
                validate_publication(report)

    def test_report_read_errors_are_actionable(self):
        for contents in ("not JSON", "x" * (256 * 1024 + 1)):
            self.report_path.write_text(contents)
            with self.assertRaises(PublicationError):
                load_publication(self.report_path)
        self.report_path.unlink()
        with self.assertRaises(PublicationError):
            load_publication(self.report_path)

    def test_report_read_rejects_duplicate_expected_byte_members(self):
        document = json.dumps(self.report)
        digest = self.report["deployment"]["files"][1]["sha256"]
        duplicate = document.replace(
            f'"sha256": "{digest}"',
            f'"sha256":"{"0" * 64}","sha\\u0032\\u0035\\u0036":"{digest}"',
            1,
        )
        self.report_path.write_text(duplicate)
        with self.assertRaisesRegex(PublicationError, "duplicate member: sha256"):
            load_publication(self.report_path)

        self.report_path.write_text(document)
        self.assertEqual(load_publication(self.report_path), self.report)

    def test_reports_do_not_bless_edited_generated_artifacts(self):
        prefix = self.site / "releases" / self.release_id
        variants = (prefix / "manifest.plist", prefix / "release.json", self.site / "index.html")
        for path in variants:
            with self.subTest(path=path):
                original = path.read_bytes()
                if path.suffix == ".plist":
                    manifest = plistlib.loads(original)
                    manifest["items"][0]["metadata"]["bundle-identifier"] = "another.app"
                    path.write_bytes(plistlib.dumps(manifest))
                elif path.suffix == ".json":
                    metadata = json.loads(original)
                    metadata["releaseNotes"] = "edited"
                    path.write_text(json.dumps(metadata))
                else:
                    path.write_text("an unrelated installation page")
                with self.assertRaisesRegex(PublicationError, "inconsistent"):
                    publication_report(self.site, self.publication, self.base)
                path.write_bytes(original)

    def test_report_rejects_duplicate_members_in_immutable_release_metadata(self):
        path = self.site / "releases" / self.release_id / "release.json"
        original = path.read_bytes()
        path.write_text(
            original.decode().replace('"version": "1.0"', '"version":"1.0","vers\\u0069on":"9"')
        )
        with self.assertRaisesRegex(PublicationError, "generated deployment files are inconsistent"):
            publication_report(self.site, self.publication, self.base)
        path.write_bytes(original)

    def test_repeating_publish_rejects_modified_manifest(self):
        path = self.site / "releases" / self.release_id / "manifest.plist"
        manifest = plistlib.loads(path.read_bytes())
        manifest["items"][0]["metadata"]["title"] = "Changed app"
        path.write_bytes(plistlib.dumps(manifest))
        with self.assertRaisesRegex(PublicationError, "verified app"):
            publish(self.ipa, self.site, self.base, verifier=lambda _: self.info)

    def test_interrupted_publish_recovers_original_release_metadata(self):
        site = self.root / "interrupted"
        atomic_write = publishing._atomic_write
        def interrupt_feed(path, contents):
            if path.name == "feed.json":
                raise OSError("interrupted")
            atomic_write(path, contents)
        with patch.object(publishing, "_atomic_write", side_effect=interrupt_feed), \
                patch.object(publishing, "datetime") as clock:
            clock.now.return_value = datetime(2024, 1, 1, tzinfo=UTC)
            with self.assertRaises(OSError):
                publish(self.ipa, site, self.base, verifier=lambda _: self.info)
        original = json.loads((site / "releases" / self.release_id / "release.json").read_text())
        recovered = publish(self.ipa, site, self.base, verifier=lambda _: self.info)
        self.assertEqual(recovered["release"], original)
        report = publication_report(site, recovered, self.base)
        validate_local_files(report)
