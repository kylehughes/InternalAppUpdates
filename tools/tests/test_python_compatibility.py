from __future__ import annotations

from dataclasses import MISSING, fields
import hashlib
import inspect
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from internal_app_updates import deployment, verification
from internal_app_updates.deployment import (
    load_publication,
    publication_report,
    validate_local_files,
    validate_publication,
)
from internal_app_updates.publishing import (
    IPAInfo,
    inspect_ipa,
    links_for_base_url,
    publication_links,
    publish,
    withdraw,
)
from internal_app_updates.verification import verify_publication


class _Response:
    def __init__(self, contents: bytes, headers: dict[str, str]):
        self._contents = io.BytesIO(contents)
        self.status = 200
        self.headers = headers

    def __enter__(self):
        return self

    def __exit__(self, *arguments):
        return False

    def read(self, size: int = -1) -> bytes:
        return self._contents.read(size)


class PythonCompatibilityTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.ipa = self.root / "App.ipa"
        self.ipa.write_bytes(b"legacy signed IPA fixture")
        self.artifact_id = hashlib.sha256(self.ipa.read_bytes()).hexdigest()
        self.site = self.root / "site"
        self.base_url = "https://updates.example/apps"

    def legacy_info(self, version: str = "1.0", build: str = "1") -> IPAInfo:
        return IPAInfo("example.app", version, build, "16.0", self.artifact_id, "Example",
                       profile_expires_at="2035-01-01T00:00:00Z")

    def publish_legacy_release(self) -> dict:
        return publish(self.ipa, self.site, self.base_url, verifier=lambda _: self.legacy_info())

    def test_public_imports_keep_baseline_parameter_names_and_defaults(self):
        self.assertIs(deployment.links_for_base_url, links_for_base_url)
        self.assertIs(deployment.IPAInfo, IPAInfo)
        self.assertIs(verification.load_publication, load_publication)
        self.assertEqual(deployment.MAX_IPA_BYTES, 2 * 1024 * 1024 * 1024)
        self.assertEqual(deployment.IMMUTABLE_CACHE_CONTROL, "public, max-age=31536000, immutable")
        self.assertEqual(deployment.MUTABLE_CACHE_CONTROL, "no-cache")
        parameters = {function: inspect.signature(function).parameters for function in (
            inspect_ipa, publish, withdraw, links_for_base_url, publication_links,
            publication_report, load_publication, validate_publication, validate_local_files,
            verify_publication,
        )}
        self.assertEqual(list(parameters[inspect_ipa]), ["ipa", "verify_signature"])
        self.assertEqual(parameters[inspect_ipa]["verify_signature"].kind, inspect.Parameter.KEYWORD_ONLY)
        self.assertIs(parameters[inspect_ipa]["verify_signature"].default, True)
        self.assertEqual(list(parameters[publish]), ["ipa", "output", "base_url", "notes", "verifier"])
        self.assertEqual(parameters[publish]["notes"].kind, inspect.Parameter.KEYWORD_ONLY)
        self.assertIsNone(parameters[publish]["notes"].default)
        self.assertIs(parameters[publish]["verifier"].default, inspect_ipa)
        self.assertEqual(list(parameters[withdraw]), ["output", "base_url"])
        self.assertIsNone(parameters[withdraw]["base_url"].default)
        self.assertEqual(list(parameters[links_for_base_url]), ["base_url"])
        self.assertEqual(list(parameters[publication_links]), ["output"])
        self.assertEqual(list(parameters[publication_report]), ["output", "publication", "base_url"])
        self.assertEqual(list(parameters[load_publication]), ["path"])
        self.assertEqual(list(parameters[validate_publication]), ["report"])
        self.assertEqual(list(parameters[validate_local_files]), ["report"])
        self.assertEqual(list(parameters[verify_publication]), ["publication_path", "timeout"])
        self.assertEqual(parameters[verify_publication]["timeout"].kind, inspect.Parameter.KEYWORD_ONLY)
        self.assertEqual(parameters[verify_publication]["timeout"].default, 30)

    def test_ipainfo_keeps_legacy_dataclass_constructor_fields_and_defaults(self):
        actual = [(field.name, field.default) for field in fields(IPAInfo)]
        self.assertEqual([name for name, _ in actual], [
            "bundle_identifier", "version", "build", "minimum_os_version", "artifact_id", "app_name",
            "extracted_app", "profile_expires_at", "legacy_app_name",
        ])
        self.assertTrue(all(default is MISSING for _, default in actual[:6]))
        self.assertEqual([default for _, default in actual[6:]], [None, None, None])
        info = self.legacy_info()
        self.assertEqual(info.extracted_app, None)
        self.assertEqual(info.profile_expires_at, "2035-01-01T00:00:00Z")
        self.assertEqual(info.legacy_app_name, None)

    def test_legacy_publish_withdraw_and_links_keep_dict_outputs_and_history(self):
        publication = self.publish_legacy_release()
        self.assertIs(type(publication), dict)
        self.assertEqual(publication["bundleIdentifier"], "example.app")
        self.assertEqual(publication["release"]["id"], self.artifact_id)
        self.assertEqual(links_for_base_url(self.base_url), {
            "installPageURL": self.base_url + "/index.html",
            "feedURL": self.base_url + "/feed.json",
        })
        self.assertEqual(publication_links(self.site), links_for_base_url(self.base_url))

        publication["release"]["version"] = "mutated-return-value"
        saved_release = json.loads((self.site / "releases" / self.artifact_id / "release.json").read_text())
        self.assertEqual(saved_release["version"], "1.0")
        withdrawn = withdraw(self.site, self.base_url)
        self.assertIs(type(withdrawn), dict)
        self.assertEqual(withdrawn, {"schemaVersion": 1, "bundleIdentifier": "example.app", "release": None})
        self.assertEqual(json.loads((self.site / "releases" / self.artifact_id / "release.json").read_text()), saved_release)

    def test_legacy_report_round_trip_preserves_adapter_fields_and_is_mutable(self):
        publication = self.publish_legacy_release()
        report = publication_report(self.site, publication, self.base_url)
        report["adapterMetadata"] = {"owner": "deployment-script", "nested": {"enabled": True}}
        report["release"]["adapterMetadata"] = {"origin": "legacy-client"}
        report["deployment"]["adapterMetadata"] = {"attempt": 2}
        report_path = self.root / "publication.json"
        report_path.write_text(json.dumps(report))

        validate_publication(report)
        validate_local_files(report)
        loaded = load_publication(report_path)
        self.assertIs(type(loaded), dict)
        self.assertEqual(loaded, report)
        loaded["release"]["adapterMetadata"]["origin"] = "caller-change"
        self.assertEqual(json.loads(report_path.read_text())["release"]["adapterMetadata"]["origin"], "legacy-client")

    def test_legacy_verification_returns_a_detached_dict_shape(self):
        publication = self.publish_legacy_release()
        report = publication_report(self.site, publication, self.base_url)
        report_path = self.root / "publication.json"
        report_path.write_text(json.dumps(report))
        responses = []
        for entry in report["deployment"]["files"]:
            responses.append(_Response(
                (self.site / entry["path"]).read_bytes(),
                {"Content-Type": entry["contentType"], "Cache-Control": entry["cacheControl"]},
            ))
        with patch("internal_app_updates.verification._open_url", side_effect=responses):
            result = verify_publication(report_path)
        self.assertIs(type(result), dict)
        self.assertEqual(result["release"], publication["release"])
        self.assertEqual(result["links"], links_for_base_url(self.base_url))
        self.assertEqual(result["filesVerified"], 5)
        self.assertEqual(result["bytesVerified"], sum(entry["size"] for entry in report["deployment"]["files"]))
        result["release"]["version"] = "caller-change"
        self.assertEqual(publication["release"]["version"], "1.0")


if __name__ == "__main__":
    unittest.main()
