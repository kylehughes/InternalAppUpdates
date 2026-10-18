from __future__ import annotations

from contextlib import redirect_stdout
from dataclasses import replace
from io import StringIO
import json
import hashlib
from html import unescape
from pathlib import Path
import plistlib
import re
import tempfile
import unittest
from urllib.parse import parse_qs, urlsplit
import zipfile

from internal_app_updates import cli
from internal_app_updates.publishing import IPAInfo, PublicationError, _manifest, _read_feed, _read_info, publish, withdraw


class PublishingTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.root = Path(self.directory.name)
        self.ipa = self.root / "app.ipa"
        with zipfile.ZipFile(self.ipa, "w") as archive:
            archive.writestr("Payload/App.app/", b"")
            archive.writestr("Payload/App.app/Info.plist", plistlib.dumps({
                "CFBundleIdentifier": "example.app", "CFBundleShortVersionString": "1.2", "CFBundleVersion": "3",
            }))

    def tearDown(self):
        self.directory.cleanup()

    def info(self, version="1.2", build="3", identifier=None):
        identifier = identifier or hashlib.sha256(self.ipa.read_bytes()).hexdigest()
        return IPAInfo("example.app", version, build, "15.0", identifier, "App", profile_expires_at="2030-01-01T00:00:00Z")

    def write_ipa(self, name, metadata, *, resource=None, compression=zipfile.ZIP_STORED):
        ipa = self.root / name
        info_contents = plistlib.dumps({
            "CFBundleIdentifier": "example.app", "CFBundleShortVersionString": "1.2",
            "CFBundleVersion": "3", "MinimumOSVersion": "15.0", **metadata,
        })
        with zipfile.ZipFile(ipa, "w", compression=compression) as archive:
            archive.writestr("Payload/Folder.app/Info.plist", info_contents)
            if resource is not None:
                archive.writestr("Payload/Folder.app/resource.bin", resource)
        return ipa, info_contents

    def corrupt_member(self, ipa, member):
        with zipfile.ZipFile(ipa) as archive:
            item = archive.getinfo(member)
            offset = item.header_offset
        contents = bytearray(ipa.read_bytes())
        name_length = int.from_bytes(contents[offset + 26:offset + 28], "little")
        extra_length = int.from_bytes(contents[offset + 28:offset + 30], "little")
        data_offset = offset + 30 + name_length + extra_length
        contents[data_offset + max(0, item.compress_size // 2)] ^= 0xFF
        ipa.write_bytes(contents)

    def test_malformed_metadata_is_rejected(self):
        bad = self.root / "bad.ipa"
        with zipfile.ZipFile(bad, "w") as archive:
            archive.writestr("Payload/A.app/", b"")
            archive.writestr("Payload/A.app/Info.plist", plistlib.dumps({"CFBundleIdentifier": "a"}))
        with self.assertRaises(PublicationError):
            _read_info(bad)

    def test_reads_realistic_ipa_without_explicit_app_directory_entry(self):
        ipa = self.root / "no-directory-entry.ipa"
        with zipfile.ZipFile(ipa, "w") as archive:
            archive.writestr("Payload/NoEntry.app/Info.plist", plistlib.dumps({
                "CFBundleIdentifier": "example.app", "CFBundleShortVersionString": "1", "CFBundleVersion": "2",
                "MinimumOSVersion": "15.0",
            }))
        info = _read_info(ipa)
        self.assertEqual(info.bundle_identifier, "example.app")
        self.assertEqual(info.minimum_os_version, "15.0")

    def test_non_dictionary_metadata_is_a_structured_inspect_error(self):
        for value in ([], "not bundle metadata", 1):
            with self.subTest(value=value):
                ipa = self.root / "invalid-root.ipa"
                with zipfile.ZipFile(ipa, "w") as archive:
                    archive.writestr("Payload/App.app/Info.plist", plistlib.dumps(value))
                output = StringIO()
                with redirect_stdout(output):
                    result = cli.main(["inspect", "--json", "--no-verify", "--ipa", str(ipa)])
                self.assertEqual(result, 1)
                self.assertIn("bundle metadata", json.loads(output.getvalue())["error"])

    def test_manifest_title_prefers_display_name_then_bundle_name_then_folder_stem(self):
        cases = (
            ({"CFBundleDisplayName": "Display Name", "CFBundleName": "Bundle Name"}, "Display Name"),
            ({"CFBundleDisplayName": "", "CFBundleName": "Bundle Name"}, "Bundle Name"),
            ({}, "Folder"),
        )
        for index, (metadata, expected) in enumerate(cases):
            with self.subTest(metadata=metadata):
                ipa, _ = self.write_ipa(f"title-{index}.ipa", metadata)
                info = _read_info(ipa)
                self.assertEqual(info.app_name, expected)
                self.assertEqual(info.legacy_app_name, "Folder.app")
                manifest = plistlib.loads(_manifest(info, "https://updates.example/app.ipa"))
                self.assertEqual(manifest["items"][0]["metadata"]["title"], expected)

    def test_corrupt_info_plist_is_a_structured_inspect_error(self):
        ipa, _ = self.write_ipa("corrupt-info.ipa", {})
        self.corrupt_member(ipa, "Payload/Folder.app/Info.plist")
        output = StringIO()
        with redirect_stdout(output):
            result = cli.main(["inspect", "--json", "--no-verify", "--ipa", str(ipa)])
        self.assertEqual(result, 1)
        self.assertIn("IPA archive", json.loads(output.getvalue())["error"])

    def test_bad_deflate_during_extraction_is_a_structured_publish_error(self):
        ipa, _ = self.write_ipa("corrupt-resource.ipa", {}, resource=b"unique resource contents" * 100,
                                compression=zipfile.ZIP_DEFLATED)
        self.corrupt_member(ipa, "Payload/Folder.app/resource.bin")
        output = StringIO()
        with redirect_stdout(output):
            result = cli.main(["publish", "--json", "--ipa", str(ipa),
                               "--output", str(self.root / "corrupt-site"),
                               "--base-url", "https://updates.example"])
        self.assertEqual(result, 1)
        self.assertIn("extract IPA archive", json.loads(output.getvalue())["error"])

    def test_failure_before_assets_does_not_advance_feed(self):
        output = self.root / "site"
        output.mkdir()
        (output / "feed.json").write_text('{"old": true}')
        with self.assertRaises(PublicationError):
            publish(self.ipa, output, "https://updates.example", verifier=lambda _: (_ for _ in ()).throw(PublicationError("bad")))
        self.assertEqual((output / "feed.json").read_text(), '{"old": true}')

    def test_metadata_only_inspection_cannot_be_published(self):
        output = self.root / "site"
        with self.assertRaisesRegex(PublicationError, "signature verifier"):
            publish(self.ipa, output, "https://updates.example", verifier=lambda _: IPAInfo(
                "example.app", "1", "2", "16", hashlib.sha256(self.ipa.read_bytes()).hexdigest(), "Example",
            ))
        self.assertFalse((output / "feed.json").exists())
        self.assertFalse((output / "releases").exists())

    def test_idempotence_and_version_conflict(self):
        output = self.root / "site"
        first = publish(self.ipa, output, "https://updates.example", verifier=lambda _: self.info())
        repeated = publish(self.ipa, output, "https://updates.example", verifier=lambda _: self.info())
        self.assertEqual(first, repeated)
        self.ipa.write_bytes(b"different signed artifact")
        with self.assertRaisesRegex(PublicationError, "different IPA already uses"):
            publish(self.ipa, output, "https://updates.example", verifier=lambda _: self.info("1.2.0", "3.0"))

    def test_idempotent_publish_requires_the_original_release_notes(self):
        output = self.root / "site"
        original = publish(
            self.ipa, output, "https://updates.example", notes="Original notes", verifier=lambda _: self.info()
        )

        self.assertEqual(
            publish(
                self.ipa, output, "https://updates.example", notes="Original notes", verifier=lambda _: self.info()
            ),
            original,
        )
        with self.assertRaisesRegex(PublicationError, "original publication options"):
            publish(self.ipa, output, "https://updates.example", notes="Changed notes", verifier=lambda _: self.info())
        self.assertEqual(json.loads((output / "feed.json").read_text()), original)

    def test_legacy_folder_title_is_accepted_for_idempotence_and_recovery_without_rewrite(self):
        ipa, _ = self.write_ipa("legacy.ipa", {"CFBundleDisplayName": "Modern Name"})
        output = self.root / "legacy-site"
        verifier = lambda snapshot: replace(_read_info(snapshot), profile_expires_at="2030-01-01T00:00:00Z")
        document = publish(ipa, output, "https://updates.example", verifier=verifier)
        info = _read_info(ipa)
        release_dir = output / "releases" / document["release"]["id"]
        package_url = f"https://updates.example/releases/{info.artifact_id}/app.ipa"
        legacy_manifest = _manifest(replace(info, app_name="Folder.app"), package_url)
        manifest_path = release_dir / "manifest.plist"
        manifest_path.write_bytes(legacy_manifest)

        self.assertEqual(publish(ipa, output, "https://updates.example", verifier=verifier), document)
        self.assertEqual(manifest_path.read_bytes(), legacy_manifest)

        (output / "feed.json").unlink()
        recovered = publish(ipa, output, "https://updates.example", verifier=verifier)
        self.assertEqual(recovered, document)
        self.assertEqual(manifest_path.read_bytes(), legacy_manifest)

    def test_legacy_compatibility_does_not_accept_a_tampered_title(self):
        ipa, _ = self.write_ipa("tampered.ipa", {"CFBundleDisplayName": "Modern Name"})
        output = self.root / "tampered-site"
        verifier = lambda snapshot: replace(_read_info(snapshot), profile_expires_at="2030-01-01T00:00:00Z")
        document = publish(ipa, output, "https://updates.example", verifier=verifier)
        manifest_path = output / "releases" / document["release"]["id"] / "manifest.plist"
        manifest = plistlib.loads(manifest_path.read_bytes())
        manifest["items"][0]["metadata"]["title"] = "Tampered.app"
        manifest_path.write_bytes(plistlib.dumps(manifest, fmt=plistlib.FMT_XML, sort_keys=False))

        with self.assertRaisesRegex(PublicationError, "manifest differs"):
            publish(ipa, output, "https://updates.example", verifier=verifier)

    def test_rejects_older_release(self):
        output = self.root / "site"
        publish(self.ipa, output, "https://updates.example", verifier=lambda _: self.info("2.0", "1"))
        self.ipa.write_bytes(b"older signed artifact")
        with self.assertRaisesRegex(PublicationError, "older than a previously"):
            publish(self.ipa, output, "https://updates.example", verifier=lambda _: self.info("1.9", "99"))

    def test_idempotent_publish_rejects_a_changed_base_url(self):
        output = self.root / "site"
        publish(self.ipa, output, "https://updates.example", verifier=lambda _: self.info())
        with self.assertRaises(PublicationError):
            publish(self.ipa, output, "https://other.example", verifier=lambda _: self.info())

    def test_idempotent_publish_rejects_missing_or_changed_release_metadata(self):
        output = self.root / "site"
        publication = publish(self.ipa, output, "https://updates.example", verifier=lambda _: self.info())
        metadata = output / "releases" / publication["release"]["id"] / "release.json"
        original = metadata.read_bytes()
        for value in (None, b"not JSON", json.dumps({**publication["release"], "releaseNotes": "changed"}).encode()):
            with self.subTest(value=value):
                if value is None:
                    metadata.unlink()
                else:
                    metadata.write_bytes(value)
                with self.assertRaises(PublicationError):
                    publish(self.ipa, output, "https://updates.example", verifier=lambda _: self.info())
                self.assertEqual(json.loads((output / "feed.json").read_text()), publication)
                metadata.write_bytes(original)

    def test_feed_and_release_json_reject_duplicate_members_before_model_decoding(self):
        output = self.root / "site"
        publication = publish(self.ipa, output, "https://updates.example", verifier=lambda _: self.info())
        feed_path = output / "feed.json"
        release_path = output / "releases" / publication["release"]["id"] / "release.json"
        original_feed = feed_path.read_bytes()
        original_release = release_path.read_bytes()

        feed_path.write_text(
            '{"schemaVersion":1,"bundleIdentifier":"example.app",'
            '"bundleIdent\\u0069fier":"other.app","release":null}'
        )
        with self.assertRaisesRegex(PublicationError, "duplicate member"):
            _read_feed(feed_path)

        feed_path.write_bytes(original_feed)
        release_path.write_text(
            original_release.decode().replace('"version": "1.2"', '"version":"1.2","vers\\u0069on":"9"')
        )
        with self.assertRaisesRegex(PublicationError, "invalid release history"):
            publish(self.ipa, output, "https://updates.example", verifier=lambda _: self.info())

        release_path.write_bytes(original_release)
        self.assertEqual(_read_feed(feed_path).to_dict(), publication)

    def test_raw_feed_rejection_preserves_invalid_json_and_release_notes_contracts(self):
        path = self.root / "feed.json"
        path.write_text('{')
        with self.assertRaisesRegex(PublicationError, "could not read existing feed"):
            _read_feed(path)

        path.write_bytes(b"\xff")
        with self.assertRaisesRegex(PublicationError, "could not read existing feed"):
            _read_feed(path)

        path.write_text(
            '{"schemaVersion":1,"bundleIdentifier":"example.app","release":{'
            '"id":"' + "a" * 64 + '","version":"1","build":"2","minimumOSVersion":"16",'
            '"publishedAt":"2026-01-01T00:00:00Z","expiresAt":"2030-01-01T00:00:00Z",'
            '"manifestURL":"https://updates.example/manifest.plist","releaseNotes":null}}'
        )
        with self.assertRaisesRegex(PublicationError, "releaseNotes must be a string"):
            _read_feed(path)

    def test_publishes_the_snapshot_verified_by_the_verifier(self):
        output = self.root / "site"
        original = self.ipa.read_bytes()
        digest = hashlib.sha256(original).hexdigest()
        def verifier(snapshot):
            self.ipa.write_bytes(b"source changed after snapshot")
            return self.info(identifier=digest)
        publish(self.ipa, output, "https://updates.example", verifier=verifier)
        self.assertEqual((output / "releases" / digest / "app.ipa").read_bytes(), original)

    def test_rejects_invalid_version_before_first_release(self):
        with self.assertRaises(PublicationError):
            publish(self.ipa, self.root / "site", "https://updates.example", verifier=lambda _: self.info(version="1.٢"))

    def test_withdraw_bad_base_url_leaves_feed_unchanged(self):
        output = self.root / "site"
        publish(self.ipa, output, "https://updates.example", verifier=lambda _: self.info())
        before = (output / "feed.json").read_bytes()
        with self.assertRaises(PublicationError):
            withdraw(output, "http://not-secure.example")
        self.assertEqual((output / "feed.json").read_bytes(), before)

    def test_withdraw_requires_the_active_publication_base_url(self):
        output = self.root / "site"
        publish(self.ipa, output, "https://updates.example/app", verifier=lambda _: self.info())
        before = {path.relative_to(output): path.read_bytes() for path in output.rglob("*") if path.is_file()}

        with self.assertRaisesRegex(PublicationError, "different base URL"):
            withdraw(output, "https://other.example/app")
        self.assertEqual(
            {path.relative_to(output): path.read_bytes() for path in output.rglob("*") if path.is_file()}, before
        )
        self.assertIsNone(withdraw(output, "https://updates.example/app")["release"])

    def test_manifest_and_html_urls_are_escaped(self):
        output = self.root / "site"
        document = publish(self.ipa, output, "https://updates.example/base", notes="<script>", verifier=lambda _: self.info())
        html = (output / "index.html").read_text()
        links = re.findall(r'href="(itms-services:[^"]+)"', html)
        self.assertEqual(len(links), 1)
        query = parse_qs(urlsplit(unescape(links[0])).query)
        self.assertEqual(query["action"], ["download-manifest"])
        self.assertEqual(query["url"], [document["release"]["manifestURL"]])
        self.assertNotIn("<script>", html)
        manifest = plistlib.loads((output / "releases" / document["release"]["id"] / "manifest.plist").read_bytes())
        package_url = manifest["items"][0]["assets"][0]["url"]
        self.assertTrue(package_url.endswith("/app.ipa"))

    def test_withdrawal_preserves_version_history(self):
        output = self.root / "site"
        publish(self.ipa, output, "https://updates.example", verifier=lambda _: self.info("2", "1"))
        withdraw(output)
        self.ipa.write_bytes(b"different signed artifact")
        with self.assertRaisesRegex(PublicationError, "different IPA already uses"):
            publish(self.ipa, output, "https://updates.example", verifier=lambda _: self.info("2.0", "1.0"))
        with self.assertRaisesRegex(PublicationError, "older than a previously"):
            publish(self.ipa, output, "https://updates.example", verifier=lambda _: self.info("1", "99"))
        self.assertIsNone(json.loads((output / "feed.json").read_text())["release"])

    def test_incomplete_release_history_cannot_enable_version_reuse(self):
        output = self.root / "site"
        first = publish(self.ipa, output, "https://updates.example", verifier=lambda _: self.info("2", "1"))
        withdraw(output)
        metadata = output / "releases" / first["release"]["id"] / "release.json"
        original_metadata = metadata.read_bytes()
        metadata.unlink()
        self.ipa.write_bytes(b"different signed artifact")

        with self.assertRaisesRegex(PublicationError, "invalid release history"):
            publish(self.ipa, output, "https://updates.example", verifier=lambda _: self.info("2.0", "1.0"))
        self.assertIsNone(json.loads((output / "feed.json").read_text())["release"])

        metadata.write_bytes(original_metadata)
        newer = publish(self.ipa, output, "https://updates.example", verifier=lambda _: self.info("3", "1"))
        self.assertEqual(newer["release"]["version"], "3")

    def test_oversized_feed_does_not_create_release_history(self):
        output = self.root / "site"
        with self.assertRaises(PublicationError):
            publish(self.ipa, output, "https://updates.example", notes="x" * (256 * 1024), verifier=lambda _: self.info())
        self.assertFalse((output / "feed.json").exists())
        self.assertFalse((output / "releases").exists())

    def test_invalid_base_url_is_rejected_before_writing(self):
        for url in ("https://host/?", "https://host/#", "https://@host/", "https://:443/", "https://host:bad/", "https://host/a b"):
            with self.subTest(url=url), self.assertRaises(PublicationError):
                publish(self.ipa, self.root / "site", url, verifier=lambda _: self.info())
        self.assertFalse((self.root / "site").exists())
