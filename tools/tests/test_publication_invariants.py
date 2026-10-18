from __future__ import annotations

from dataclasses import FrozenInstanceError, replace
from pathlib import Path
import unittest

from internal_app_updates.models import (
    Feed, IPAInfo, Links, MetadataInspection, PublicationError, Release, VerifiedIPA, version_key,
)


class PublicationModelInvariantTests(unittest.TestCase):
    def release(self) -> Release:
        return Release("a" * 64, "1.0", "2", "16.0", "2026-01-01T00:00:00Z",
                       "2030-01-01T00:00:00Z", "https://updates.example/release/manifest.plist")

    def test_valid_values_are_immutable_and_round_trip_through_feed_json_shape(self):
        info = IPAInfo("example.app", "1", "2", "16", "a" * 64, "Example",
                       profile_expires_at="2030-01-01T00:00:00Z")
        release = self.release()
        feed = Feed("example.app", release)
        self.assertFalse(hasattr(info, "__dict__"))
        self.assertEqual(Feed.from_dict(feed.to_dict()), feed)
        self.assertEqual(release.key, ((1, 0, 0), (2, 0, 0)))
        self.assertEqual(Links("https://updates.example/apps///").to_dict(), {
            "installPageURL": "https://updates.example/apps/index.html",
            "feedURL": "https://updates.example/apps/feed.json",
        })
        with self.assertRaises(FrozenInstanceError):
            info.version = "3"

    def test_direct_and_replacement_construction_reject_invalid_values(self):
        with self.assertRaises(PublicationError):
            IPAInfo("example.app", "١", "2", "16", "a" * 64, "Example")
        with self.assertRaises(PublicationError):
            Release("A" * 64, "1", "2", "16", "2026-01-01T00:00:00Z",
                    "2030-01-01T00:00:00Z", "https://updates.example/manifest.plist")
        with self.assertRaises(PublicationError):
            replace(self.release(), expires_at="2020-01-01T00:00:00Z")

    def test_release_constraints_reject_the_same_bad_values_at_every_construction_boundary(self):
        valid = self.release()
        invalid = {
            "id": "A" * 64,
            "version": "1.٢",
            "build": "18446744073709551616",
            "minimum_os_version": True,
            "published_at": "2026-01-01T00:00:00+00:00",
            "expires_at": "2020-01-01T00:00:00Z",
            "manifest_url": "https://user:password@updates.example/manifest.plist",
            "release_notes": 1,
        }
        for field, value in invalid.items():
            with self.subTest(field=field):
                kwargs = {name: getattr(valid, name) for name in (
                    "id", "version", "build", "minimum_os_version", "published_at", "expires_at",
                    "manifest_url", "release_notes",
                )}
                kwargs[field] = value
                with self.assertRaises(PublicationError):
                    Release(**kwargs)
                with self.assertRaises(PublicationError):
                    replace(valid, **{field: value})
                encoded = Feed("example.app", valid).to_dict()
                wire_field = {"minimum_os_version": "minimumOSVersion", "published_at": "publishedAt",
                              "expires_at": "expiresAt", "manifest_url": "manifestURL",
                              "release_notes": "releaseNotes"}.get(field, field)
                encoded["release"][wire_field] = value
                with self.assertRaises(PublicationError):
                    Feed.from_dict(encoded)

    def test_ipa_constraints_and_output_detachment(self):
        valid = IPAInfo("example.app", "1", "2", "16", "a" * 64, "Example")
        invalid = {
            "bundle_identifier": "", "version": "1.٢", "build": "x", "minimum_os_version": False,
            "artifact_id": "a" * 63, "app_name": "", "profile_expires_at": "not-a-date",
            "legacy_app_name": 1, "extracted_app": Path("bad\0path"),
        }
        for field, value in invalid.items():
            with self.subTest(field=field):
                kwargs = {name: getattr(valid, name) for name in (
                    "bundle_identifier", "version", "build", "minimum_os_version", "artifact_id", "app_name",
                    "extracted_app", "profile_expires_at", "legacy_app_name",
                )}
                kwargs[field] = value
                with self.assertRaises(PublicationError):
                    IPAInfo(**kwargs)
                with self.assertRaises(PublicationError):
                    replace(valid, **{field: value})
        feed = Feed("example.app", self.release())
        document = feed.to_dict()
        document["release"]["version"] = "99"
        self.assertEqual(feed.release.version, "1.0")
        withdrawal = Feed("example.app", None)
        self.assertEqual(Feed.from_dict(withdrawal.to_dict()), withdrawal)

    def test_ordinary_reinitialization_subclassing_and_decoding_cannot_bypass_validation(self):
        info = IPAInfo("example.app", "1", "2", "16", "a" * 64, "Example")
        with self.assertRaises(PublicationError):
            info.__init__("example.app", "bad", "2", "16", "a" * 64, "Example")
        with self.assertRaises(TypeError):
            class InvalidIPAInfo(IPAInfo):
                pass
        document = Feed("example.app", self.release()).to_dict()
        document["release"]["unknown"] = True
        with self.assertRaises(PublicationError):
            Feed.from_dict(document)

    def test_version_date_and_url_boundary_matrix(self):
        release = self.release()
        for value in ("", "-1", "1.2.3.4", "1..2", "1.0\n", "１２", "1e2", "9" * 5000, True):
            for field in ("version", "build", "minimum_os_version"):
                with self.subTest(field=field, value=str(value)[:30]), self.assertRaises(PublicationError):
                    replace(release, **{field: value})
        self.assertEqual(version_key("18446744073709551615"), (2**64 - 1, 0, 0))
        self.assertEqual(version_key("0" * 5000 + "1.0"), (1, 0, 0))
        for value in (float("inf"), float("nan"), None, "2026-02-30T00:00:00Z",
                      "2026-01-01T24:00:00Z", "２０２６-01-01T00:00:00Z", "2026-01-01T00:00:00.0Z"):
            for field in ("published_at", "expires_at"):
                with self.subTest(field=field, value=value), self.assertRaises(PublicationError):
                    replace(release, **{field: value})
        with self.assertRaises(PublicationError):
            replace(release, expires_at=release.published_at)
        for url in ("http://updates.example/file", "https:///file", "https://user@updates.example/file",
                    "https://updates.example:0/file", "https://updates.example:65536/file",
                    "https://updates.example/file#", "https://updates.example/white space", "https://updates.example/\n"):
            with self.subTest(url=url), self.assertRaises(PublicationError):
                replace(release, manifest_url=url)
        self.assertEqual(replace(release, manifest_url="https://updates.example:443/file?token=x").manifest_url,
                         "https://updates.example:443/file?token=x")
        with self.assertRaises(PublicationError):
            Links("https://updates.example/?token=x")

    def test_feed_decoding_is_closed_and_nested_children_are_validated(self):
        for bundle in ("", None, 1, []):
            with self.subTest(bundle=bundle), self.assertRaises(PublicationError):
                Feed(bundle, self.release())
        with self.assertRaises(PublicationError):
            Feed("example.app", self.release().to_dict())
        feed = Feed("example.app", self.release())
        for value in (True, 2, "1", None):
            wire = feed.to_dict()
            wire["schemaVersion"] = value
            with self.subTest(schema=value), self.assertRaises(PublicationError):
                Feed.from_dict(wire)
        for field in feed.to_dict():
            wire = feed.to_dict()
            del wire[field]
            with self.subTest(missing=field), self.assertRaises(PublicationError):
                Feed.from_dict(wire)
        wire = feed.to_dict()
        wire["unknown"] = 1
        with self.assertRaises(PublicationError):
            Feed.from_dict(wire)
        wire = feed.to_dict()
        wire["release"]["releaseNotes"] = None
        with self.assertRaises(PublicationError):
            Feed.from_dict(wire)
        with self.assertRaises(FrozenInstanceError):
            feed.release.version = "99"

    def test_signing_outcome_variants_cannot_exchange_metadata(self):
        metadata = IPAInfo("example.app", "1", "2", "16", "a" * 64, "Example")
        verified = replace(metadata, profile_expires_at="2030-01-01T00:00:00Z")
        self.assertEqual(MetadataInspection(metadata).info, metadata)
        self.assertEqual(VerifiedIPA(verified).info, verified)
        with self.assertRaises(PublicationError):
            VerifiedIPA(metadata)
        with self.assertRaises(PublicationError):
            MetadataInspection(verified)
        with self.assertRaises(PublicationError):
            replace(VerifiedIPA(verified), info=metadata)
        with self.assertRaises(FrozenInstanceError):
            MetadataInspection(metadata).info.profile_expires_at = "2030-01-01T00:00:00Z"
