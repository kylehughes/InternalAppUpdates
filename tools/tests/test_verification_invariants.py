from dataclasses import FrozenInstanceError, replace
import copy
import unittest

from internal_app_updates.models import Links, MAX_IPA_BYTES, PublicationError, Release
from internal_app_updates.verification import (
    ActiveVerification, WithdrawnVerification, _verification_from_dict,
)


class VerificationInvariantTests(unittest.TestCase):
    def setUp(self):
        self.links = Links("https://updates.example/app/")
        self.release = Release(
            "a" * 64, "1", "2", "16.0", "2026-01-01T00:00:00Z", "2030-01-01T00:00:00Z",
            self.links.base_url + "releases/" + "a" * 64 + "/manifest.plist",
        )
        self.active = ActiveVerification(self.release, self.links, 50)
        self.withdrawn = WithdrawnVerification(self.links, 20)

    def test_active_and_withdrawn_round_trip_preserve_public_shapes(self):
        for result, count in ((self.active, 5), (self.withdrawn, 2)):
            with self.subTest(result=type(result).__name__):
                wire = result.to_dict()
                self.assertEqual(set(wire), {"verified", "release", "links", "filesVerified", "bytesVerified"})
                self.assertIs(wire["verified"], True)
                self.assertEqual(wire["filesVerified"], count)
                self.assertEqual(_verification_from_dict(wire), result)
        self.assertIsNone(self.withdrawn.to_dict()["release"])

    def test_construction_and_replacement_reject_invalid_payloads_and_totals(self):
        for result, count in ((self.active, 5), (self.withdrawn, 2)):
            for total in (None, True, 0, count - 1, -1, 1.5, float("inf"), float("nan"), count * MAX_IPA_BYTES + 1):
                with self.subTest(result=type(result).__name__, total=total), self.assertRaises(PublicationError):
                    replace(result, bytes_verified=total)
            self.assertEqual(replace(result, bytes_verified=count).bytes_verified, count)
            self.assertEqual(replace(result, bytes_verified=count * MAX_IPA_BYTES).bytes_verified, count * MAX_IPA_BYTES)
            with self.assertRaises(PublicationError):
                replace(result, links=result.links.to_dict())
        for release in (None, self.release.to_dict()):
            with self.subTest(release=release), self.assertRaises(PublicationError):
                ActiveVerification(release, self.links, 50)
        with self.assertRaises(PublicationError):
            replace(self.active, links=Links("https://other.example/"))
        with self.assertRaises(TypeError):
            replace(self.withdrawn, release=self.release)

    def test_outcomes_and_nested_values_are_immutable_and_serialization_is_detached(self):
        for result in (self.active, self.withdrawn):
            with self.assertRaises(FrozenInstanceError):
                result.bytes_verified = 0
            with self.assertRaises(FrozenInstanceError):
                result.links.base_url = "http://other.example/"
            self.assertFalse(hasattr(result, "__dict__"))
            wire = result.to_dict()
            wire["links"]["feedURL"] = "http://other.example/"
            self.assertEqual(result.links, self.links)
        with self.assertRaises(FrozenInstanceError):
            self.active.release.id = "invalid"

    def test_decoding_rejects_outcome_presence_counts_links_and_metadata_mismatches(self):
        changes = [
            ("verified", False), ("verified", 1), ("release", None), ("filesVerified", 2),
            ("filesVerified", True), ("bytesVerified", 0), ("bytesVerified", float("nan")),
            ("links", {"feedURL": "http://updates.example/app/feed.json", "installPageURL": self.links.install_page_url}),
            ("links", {"feedURL": self.links.feed_url, "installPageURL": "https://other.example/index.html"}),
        ]
        for name, value in changes:
            wire = self.active.to_dict()
            wire[name] = value
            with self.subTest(name=name, value=value), self.assertRaises(PublicationError):
                _verification_from_dict(wire)
        for name in self.active.to_dict():
            wire = self.active.to_dict()
            del wire[name]
            with self.subTest(missing=name), self.assertRaises(PublicationError):
                _verification_from_dict(wire)
        wire = self.withdrawn.to_dict()
        wire["release"] = self.release.to_dict()
        with self.assertRaises(PublicationError):
            _verification_from_dict(wire)
        wire = copy.deepcopy(self.active.to_dict())
        wire["release"]["expiresAt"] = wire["release"]["publishedAt"]
        with self.assertRaises(PublicationError):
            _verification_from_dict(wire)


if __name__ == "__main__":
    unittest.main()
