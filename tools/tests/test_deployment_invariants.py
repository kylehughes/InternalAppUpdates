from __future__ import annotations

from dataclasses import FrozenInstanceError, replace
from copy import deepcopy
import hashlib
from pathlib import Path
import tempfile
import unittest

from internal_app_updates.deployment import _publication_report
from internal_app_updates.deployment_models import (
    ActiveDeployment, ActivePublicationReport, DigestSize, FileEntry, FileSpec,
    WithdrawalDeployment, WithdrawalPublicationReport, publication_from_dict,
)
from internal_app_updates.models import Feed, Links, MAX_IPA_BYTES, PublicationError
from internal_app_updates.publishing import IPAInfo, publish, withdraw


class DeploymentInvariantTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        self.site = root / "site"
        ipa = root / "App.ipa"
        ipa.write_bytes(b"signed fixture")
        digest = hashlib.sha256(ipa.read_bytes()).hexdigest()
        self.base = "https://updates.example/apps/"
        document = publish(ipa, self.site, self.base, verifier=lambda _: IPAInfo(
            "example.app", "1.0", "2", "16.0", digest, "Example",
            profile_expires_at="2030-01-01T00:00:00Z",
        ))
        self.active = _publication_report(self.site, Feed.from_dict(document), self.base)
        withdrawn = withdraw(self.site, self.base)
        self.withdrawal = _publication_report(self.site, Feed.from_dict(withdrawn), self.base)

    def test_valid_active_and_withdrawal_reports_round_trip(self):
        self.assertEqual(publication_from_dict(self.active.to_dict()), self.active)
        self.assertIsInstance(self.withdrawal, WithdrawalPublicationReport)
        self.assertEqual(publication_from_dict(self.withdrawal.to_dict()), self.withdrawal)
        self.assertEqual(DigestSize("a" * 64, MAX_IPA_BYTES).size, MAX_IPA_BYTES)

    def test_file_spec_and_digest_constructors_and_replacements_reject_every_invalid_scalar(self):
        spec = FileSpec("release.json", "release", "application/json", "public, max-age=31536000, immutable")
        for path in ("/absolute", "release\\app.ipa", "release\0app.ipa", "./app.ipa", "a//b", "a/../b"):
            with self.subTest(route="constructor", path=path), self.assertRaises(PublicationError):
                FileSpec(path, "release", "application/json", "public, max-age=31536000, immutable")
            with self.subTest(route="replace", path=path), self.assertRaises(PublicationError):
                replace(spec, path=path)
        for role, content_type, cache in (
            ("unknown", "application/json", "no-cache"),
            ("feed", "text/html", "no-cache"),
            ("release", "application/json", "no-cache"),
        ):
            with self.subTest(route="constructor", role=role), self.assertRaises(PublicationError):
                FileSpec("x.json", role, content_type, cache)
            with self.subTest(route="replace", role=role), self.assertRaises(PublicationError):
                replace(spec, role=role, content_type=content_type, cache_control=cache)
        valid = DigestSize("a" * 64, 1)
        for digest in ("A" * 64, "a" * 63, 5, None):
            with self.subTest(digest=digest), self.assertRaises(PublicationError):
                DigestSize(digest, 1)
        for size in (0, -1, True, 1.0, MAX_IPA_BYTES + 1):
            with self.subTest(size=size), self.assertRaises(PublicationError):
                replace(valid, size=size)

    def test_file_spec_requires_concrete_release_names_but_keeps_generic_specs_valid(self):
        immutable = "public, max-age=31536000, immutable"
        for path, content_type in (("foo.ipa", "application/octet-stream"),
                                   ("app.ipa", "application/json")):
            with self.subTest(path=path, content_type=content_type), self.assertRaises(PublicationError):
                FileSpec(path, "release", content_type, immutable)
        self.assertEqual(FileSpec("app.ipa", "release", "application/octet-stream", immutable).path, "app.ipa")
        digest = "a" * 64
        self.assertEqual(
            FileSpec(f"releases/{digest}/release.json", "release", "application/json", immutable).path,
            f"releases/{digest}/release.json",
        )

    def test_file_entry_construction_and_replacement_rejects_each_contract_field(self):
        entry = self.active.deployment.files[0]
        variants = {
            "path": "releases/" + self.active.release.id + "/other.ipa",
            "url": self.base + "wrong.ipa",
            "role": "feed",
            "content_type": "text/xml",
            "cache_control": "no-cache",
            "digest": object(),
        }
        for field, value in variants.items():
            with self.subTest(route="replace", field=field), self.assertRaises(PublicationError):
                replace(entry, **{field: value})
            arguments = {
                "path": entry.path, "url": entry.url, "role": entry.role, "digest": entry.digest,
                "content_type": entry.content_type, "cache_control": entry.cache_control,
            }
            arguments[field] = value
            with self.subTest(route="constructor", field=field), self.assertRaises(PublicationError):
                FileEntry(**arguments)

    def test_deployment_constructors_and_replacements_reject_all_structure_categories(self):
        class MutablePath(type(Path())):
            pass

        for constructor, deployment in ((ActiveDeployment, self.active.deployment),
                                        (WithdrawalDeployment, self.withdrawal.deployment)):
            files = deployment.files
            variants = {
                "empty": (), "extra": files + (files[-1],), "reordered": tuple(reversed(files)),
                "wrong-child": (object(), *files[1:]), "list": list(files),
                "relative-directory": Path("relative"), "nul-directory": Path("/tmp/unsafe\0dir"),
                "subclass-directory": MutablePath("/tmp/site"),
                "http-base": "http://updates.example/", "unnormalized-base": self.base.rstrip("/"),
            }
            for label, value in variants.items():
                with self.subTest(model=constructor.__name__, label=label), self.assertRaises(PublicationError):
                    if label.endswith("directory"):
                        replace(deployment, directory=value)
                    elif label.endswith("base"):
                        replace(deployment, base_url=value)
                    else:
                        replace(deployment, files=value)

    def test_reports_reject_wrong_children_and_correlations_by_constructor_and_replacement(self):
        active = self.active
        withdrawal = self.withdrawal
        variants = (
            (ActivePublicationReport, (Feed(active.feed.bundle_identifier, None), active.links, active.deployment)),
            (WithdrawalPublicationReport, (active.feed, withdrawal.links, withdrawal.deployment)),
            (ActivePublicationReport, (active.feed, Links("https://updates.example/else/"), active.deployment)),
            (ActivePublicationReport, (active.feed, active.links, withdrawal.deployment)),
            (WithdrawalPublicationReport, (withdrawal.feed, withdrawal.links, active.deployment)),
            (ActivePublicationReport, (replace(active.feed, release=replace(active.release, manifest_url=self.base + "wrong.plist")), active.links, active.deployment)),
        )
        for model, arguments in variants:
            with self.subTest(model=model.__name__, arguments=arguments), self.assertRaises(PublicationError):
                model(*arguments)
        with self.assertRaises(PublicationError):
            changed = replace(active.deployment.files[0], digest=DigestSize("0" * 64, active.deployment.files[0].size))
            replace(active, deployment=replace(active.deployment, files=(changed, *active.deployment.files[1:])))

    def test_nested_values_are_immutable_and_replacements_revalidate(self):
        entry = self.active.deployment.files[0]
        with self.assertRaises(FrozenInstanceError):
            entry.digest.size = 3
        with self.assertRaises(FrozenInstanceError):
            self.active.feed.release.id = "0" * 64
        with self.assertRaises(PublicationError):
            replace(entry.digest, size=0)
        with self.assertRaises(PublicationError):
            changed = replace(entry, digest=DigestSize("0" * 64, entry.size))
            ActivePublicationReport(
                self.active.feed, self.active.links,
                replace(self.active.deployment, files=(changed, *self.active.deployment.files[1:])),
            )

    def test_constructors_reject_wrong_types_and_file_contracts(self):
        with self.assertRaises(PublicationError):
            FileSpec("../secret", "release", "text/plain", "no-cache")
        with self.assertRaises(PublicationError):
            DigestSize("A" * 64, 1)
        with self.assertRaises(PublicationError):
            FileEntry("feed.json", "https://updates.example/feed.json", "feed", "digest", "application/json", "no-cache")
        with self.assertRaises(PublicationError):
            ActiveDeployment(self.site, self.base, list(self.active.deployment.files))
        with self.assertRaises(PublicationError):
            WithdrawalPublicationReport(self.active.feed, Links(self.base), WithdrawalDeployment(
                self.site, self.base, self.active.deployment.files[-2:]))

    def test_decoding_rejects_correlated_mismatches(self):
        raw = self.active.to_dict()
        variants = []
        bad_links = {**raw, "links": {"installPageURL": self.base + "other.html", "feedURL": self.base + "feed.json"}}
        variants.append(bad_links)
        bad_manifest = {**raw, "release": {**raw["release"], "manifestURL": self.base + "manifest.plist"}}
        variants.append(bad_manifest)
        bad_file = {**raw, "deployment": {**raw["deployment"], "files": [
            {**raw["deployment"]["files"][0], "url": self.base + "other.ipa"},
            *raw["deployment"]["files"][1:],
        ]}}
        variants.append(bad_file)
        bad_digest = {**raw, "deployment": {**raw["deployment"], "files": [
            {**raw["deployment"]["files"][0], "sha256": "0" * 64},
            *raw["deployment"]["files"][1:],
        ]}}
        variants.append(bad_digest)
        bad_operation = {**raw, "deployment": {**raw["deployment"], "operation": "withdraw"}}
        variants.append(bad_operation)
        for value in variants:
            with self.subTest(value=value), self.assertRaises(PublicationError):
                publication_from_dict(value)

    def test_decoding_rejects_every_file_field_and_schema_operation_category(self):
        for report in (self.active.to_dict(), self.withdrawal.to_dict()):
            for schema in (True, 0, 3):
                invalid = deepcopy(report)
                invalid["deployment"]["schemaVersion"] = schema
                with self.subTest(kind="schema", report=report["deployment"]["schemaVersion"], schema=schema), self.assertRaises(PublicationError):
                    publication_from_dict(invalid)
        for operation in (None, "withdraw", "other", 1):
            invalid = self.active.to_dict()
            invalid["deployment"]["operation"] = operation
            with self.subTest(kind="active-operation", operation=operation), self.assertRaises(PublicationError):
                publication_from_dict(invalid)
        for operation in (None, "publish", True):
            invalid = self.withdrawal.to_dict()
            invalid["deployment"]["operation"] = operation
            with self.subTest(kind="withdrawal-operation", operation=operation), self.assertRaises(PublicationError):
                publication_from_dict(invalid)
        original = self.active.to_dict()
        mutations = {
            "path": "../outside", "url": "https://updates.example/wrong", "role": "feed",
            "sha256": "A" * 64, "size": True, "contentType": "text/plain", "cacheControl": "no-cache",
        }
        for field, value in mutations.items():
            invalid = deepcopy(original)
            invalid["deployment"]["files"][0][field] = value
            with self.subTest(field=field), self.assertRaises(PublicationError):
                publication_from_dict(invalid)
        for child in (None, [], "file"):
            invalid = deepcopy(original)
            invalid["deployment"]["files"][0] = child
            with self.subTest(child=child), self.assertRaises(PublicationError):
                publication_from_dict(invalid)

    def test_values_cannot_be_reinitialized_or_subclassed_and_dict_output_is_detached(self):
        entry = self.active.deployment.files[0]
        with self.assertRaises(PublicationError):
            entry.__init__(entry.path, entry.url, entry.role, entry.digest, entry.content_type, entry.cache_control)
        with self.assertRaises(TypeError):
            class DerivedFileEntry(FileEntry):
                pass
        output = self.active.to_dict()
        output["deployment"]["files"][0]["sha256"] = "0" * 64
        output["release"]["version"] = "99"
        self.assertNotEqual(output["deployment"]["files"][0]["sha256"], self.active.deployment.files[0].sha256)
        self.assertNotEqual(output["release"]["version"], self.active.release.version)

    def test_file_entry_direct_replacement_and_decoding_correlate_ipa_digest_and_query_free_url(self):
        entry = self.active.deployment.files[0]
        for changes in ({"digest": DigestSize("b" * 64, entry.size)},
                        {"url": entry.url + "?token=x"}, {"url": entry.url + "?"}):
            arguments = {name: getattr(entry, name) for name in
                         ("path", "url", "role", "digest", "content_type", "cache_control")}
            arguments.update(changes)
            with self.subTest(route="direct", changes=changes), self.assertRaises(PublicationError):
                FileEntry(**arguments)
            with self.subTest(route="replace", changes=changes), self.assertRaises(PublicationError):
                replace(entry, **changes)
            wire = entry.to_dict()
            if "digest" in changes:
                wire["sha256"] = changes["digest"].sha256
            else:
                wire["url"] = changes["url"]
            with self.subTest(route="decode", changes=changes), self.assertRaises(PublicationError):
                FileEntry.from_dict(wire)
