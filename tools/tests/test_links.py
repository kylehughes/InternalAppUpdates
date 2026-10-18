from __future__ import annotations

import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from internal_app_updates.publishing import (
    IPAInfo, PublicationError, links_for_base_url, publication_links, publish, withdraw,
)


class PublicationLinksTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.root = Path(self.directory.name)
        self.site = self.root / "site"
        self.ipa = self.root / "app.ipa"
        self.ipa.write_bytes(b"signed artifact fixture")
        self.digest = hashlib.sha256(self.ipa.read_bytes()).hexdigest()

    def tearDown(self):
        self.directory.cleanup()

    def publish(self, base_url="https://updates.example:8443/apps/demo%20app/"):
        return publish(self.ipa, self.site, base_url, verifier=lambda _: IPAInfo(
            "example.app", "1.0", "2", "16.0", self.digest, "Example",
            profile_expires_at="2030-01-01T00:00:00Z",
        ))

    def snapshot(self):
        return {str(p.relative_to(self.site)): (p.read_bytes(), p.stat().st_mtime_ns)
                for p in self.site.rglob("*") if p.is_file()}

    def test_reads_existing_publication_without_new_metadata_or_mutation(self):
        document = self.publish()
        before = self.snapshot()
        self.assertEqual(publication_links(self.site), {
            "installPageURL": "https://updates.example:8443/apps/demo%20app/index.html",
            "feedURL": "https://updates.example:8443/apps/demo%20app/feed.json",
        })
        self.assertEqual(self.snapshot(), before)
        self.assertNotIn("links", document)
        self.assertEqual(json.loads((self.site / "feed.json").read_text()), document)

    def test_retrieved_links_follow_current_release(self):
        self.publish("https://first.example/demo")
        self.ipa.write_bytes(b"new signed artifact fixture")
        digest = hashlib.sha256(self.ipa.read_bytes()).hexdigest()
        publish(self.ipa, self.site, "https://second.example/demo", verifier=lambda _: IPAInfo(
            "example.app", "1.0", "3", "16.0", digest, "Example",
            profile_expires_at="2030-01-01T00:00:00Z",
        ))
        self.assertEqual(publication_links(self.site)["installPageURL"], "https://second.example/demo/index.html")

    def test_missing_publication_does_not_create_output(self):
        with self.assertRaises(PublicationError):
            publication_links(self.site)
        self.assertFalse(self.site.exists())

    def test_withdrawn_publication_has_no_current_install_offer(self):
        self.publish()
        withdraw(self.site)
        before = self.snapshot()
        with self.assertRaisesRegex(PublicationError, "no build is currently published"):
            publication_links(self.site)
        self.assertEqual(self.snapshot(), before)

    def test_missing_install_page_is_reported(self):
        self.publish()
        (self.site / "index.html").unlink()
        with self.assertRaisesRegex(PublicationError, "installation page is missing"):
            publication_links(self.site)

    def test_malformed_feed_cannot_produce_plausible_links(self):
        valid = self.publish()
        malformed = [
            {**valid, "schemaVersion": 99},
            {"schemaVersion": 1},
            {**valid, "release": []},
            {**valid, "release": {**valid["release"], "id": "../bad"}},
            {**valid, "release": {**valid["release"], "manifestURL": "https://updates.example/elsewhere.plist"}},
            {**valid, "release": {**valid["release"], "manifestURL":
                f"http://updates.example/releases/{self.digest}/manifest.plist"}},
        ]
        for document in malformed:
            with self.subTest(document=document):
                (self.site / "feed.json").write_text(json.dumps(document))
                with self.assertRaises(PublicationError):
                    publication_links(self.site)

    def test_base_url_links_preserve_host_mount_and_normalize_slashes(self):
        self.assertEqual(links_for_base_url("https://updates.example///"), {
            "installPageURL": "https://updates.example/index.html",
            "feedURL": "https://updates.example/feed.json",
        })
        for base_url in ("http://updates.example", "https://user:secret@updates.example", "https://updates.example/?token=x"):
            with self.subTest(base_url=base_url), self.assertRaises(PublicationError):
                links_for_base_url(base_url)
