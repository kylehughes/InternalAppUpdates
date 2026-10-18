from __future__ import annotations

from contextlib import redirect_stdout
from copy import deepcopy
import hashlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from internal_app_updates import cli, publishing, verification
from internal_app_updates.deployment import publication_report, validate_local_files, validate_publication
from internal_app_updates.publishing import IPAInfo, PublicationError, publish, withdraw


class WithdrawalTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.site = self.root / "site"
        self.base = "https://updates.example/apps/"
        ipa = self.root / "App.ipa"
        ipa.write_bytes(b"signed fixture")
        digest = hashlib.sha256(ipa.read_bytes()).hexdigest()
        self.active = publish(ipa, self.site, self.base, verifier=lambda _: IPAInfo(
            "example.app", "1.0", "2", "16.0", digest, "Example",
            profile_expires_at="2030-01-01T00:00:00Z",
        ))
        self.old_feed = (self.site / "feed.json").read_bytes()
        self.assets = {str(path.relative_to(self.site)): path.read_bytes()
                       for path in (self.site / "releases").rglob("*") if path.is_file()}
        self.report_path = self.root / "withdrawal.json"

    def prepare(self):
        publication = withdraw(self.site, self.base)
        report = publication_report(self.site, publication, self.base)
        self.report_path.write_text(json.dumps(report))
        return report

    def test_report_updates_only_page_then_feed_and_preserves_assets(self):
        report = self.prepare()
        self.assertEqual(report["schemaVersion"], 1)
        self.assertIsNone(report["release"])
        self.assertEqual(report["deployment"]["schemaVersion"], 2)
        self.assertEqual(report["deployment"]["operation"], "withdraw")
        files = report["deployment"]["files"]
        self.assertEqual([entry["path"] for entry in files], ["index.html", "feed.json"])
        self.assertEqual([entry["contentType"] for entry in files], ["text/html", "application/json"])
        self.assertTrue(all(entry["cacheControl"] == "no-cache" for entry in files))
        for entry in files:
            contents = (self.site / entry["path"]).read_bytes()
            self.assertEqual(entry["sha256"], hashlib.sha256(contents).hexdigest())
            self.assertEqual(entry["size"], len(contents))
            self.assertEqual(entry["url"], self.base + entry["path"])
        for path, contents in self.assets.items():
            self.assertEqual((self.site / path).read_bytes(), contents)
        self.assertNotIn("itms-services:", (self.site / "index.html").read_text())
        self.assertIsNone(json.loads((self.site / "feed.json").read_bytes())["release"])
        validate_local_files(report)
        self.assertEqual(self.prepare(), report)

    def test_rejects_ambiguous_operations_and_wrong_file_contracts(self):
        original = self.prepare()
        variants = []
        for key, value in (("schemaVersion", 1), ("operation", "publish"), ("operation", None)):
            report = deepcopy(original)
            report["deployment"][key] = value
            variants.append(report)
        report = deepcopy(original)
        report["release"] = self.active["release"]
        variants.append(report)
        for files in ([], original["deployment"]["files"][::-1],
                      original["deployment"]["files"] + original["deployment"]["files"][:1]):
            report = deepcopy(original)
            report["deployment"]["files"] = files
            variants.append(report)
        for report in variants:
            with self.subTest(report=report), self.assertRaises(PublicationError):
                validate_publication(report)

    def test_report_rejects_old_page_and_changed_feed(self):
        old_page = (self.site / "index.html").read_bytes()
        document = withdraw(self.site, self.base)
        new_page = (self.site / "index.html").read_bytes()
        (self.site / "index.html").write_bytes(old_page)
        with self.assertRaisesRegex(PublicationError, "inconsistent"):
            publication_report(self.site, document, self.base)
        (self.site / "index.html").write_bytes(new_page)
        (self.site / "feed.json").write_bytes(self.old_feed)
        with self.assertRaisesRegex(PublicationError, "serialize"):
            publication_report(self.site, document, self.base)

    def test_retry_after_interrupted_local_withdrawal(self):
        atomic_write = publishing._atomic_write
        def interrupt_feed(path, contents):
            if path.name == "feed.json":
                raise OSError("interrupted")
            atomic_write(path, contents)
        with patch.object(publishing, "_atomic_write", side_effect=interrupt_feed):
            with self.assertRaises(OSError):
                withdraw(self.site, self.base)
        self.assertEqual((self.site / "feed.json").read_bytes(), self.old_feed)
        validate_local_files(self.prepare())

    def test_cli_json_produces_retryable_report(self):
        for arguments in (["withdraw", "--json"], ["--json", "withdraw"]):
            output = io.StringIO()
            with redirect_stdout(output):
                code = cli.main(arguments + ["--output", str(self.site), "--base-url", self.base])
            self.assertEqual(code, 0)
            report = json.loads(output.getvalue())
            validate_local_files(report)
            self.assertEqual(report, self.prepare())

    def test_cli_requires_report_base_url_before_mutation(self):
        output = io.StringIO()
        with redirect_stdout(output):
            code = cli.main(["withdraw", "--json", "--output", str(self.site)])
        self.assertEqual(code, 1)
        self.assertIn("--base-url", json.loads(output.getvalue())["error"])
        self.assertEqual((self.site / "feed.json").read_bytes(), self.old_feed)

    def test_local_withdrawal_without_report_remains_available(self):
        output = io.StringIO()
        with redirect_stdout(output):
            code = cli.main(["withdraw", "--output", str(self.site)])
        self.assertEqual(code, 0)
        self.assertEqual(output.getvalue(), "withdrawn\n")
        self.assertIsNone(json.loads((self.site / "feed.json").read_bytes())["release"])

    def responses(self, report, *, stale_feed=False, cache_control="no-cache"):
        responses = []
        for entry in report["deployment"]["files"]:
            contents = (self.site / entry["path"]).read_bytes()
            if stale_feed and entry["path"] == "feed.json":
                contents = self.old_feed
            response = MagicMock()
            response.__enter__.return_value = response
            response.status = 200
            response.getcode.return_value = 200
            response.headers = {"Content-Type": entry["contentType"], "Cache-Control": cache_control}
            response.read.side_effect = io.BytesIO(contents).read
            responses.append(response)
        return responses

    def test_verifier_checks_both_withdrawal_files_and_cli_reports_withdrawal(self):
        report = self.prepare()
        with patch.object(verification, "_open_url", side_effect=self.responses(report)) as opened:
            result = verification.verify_publication(self.report_path)
        self.assertEqual(opened.call_count, 2)
        self.assertTrue(result["verified"])
        self.assertIsNone(result["release"])
        self.assertEqual(result["filesVerified"], 2)
        output = io.StringIO()
        with patch.object(verification, "_open_url", side_effect=self.responses(report)), redirect_stdout(output):
            code = cli.main(["verify", "--publication", str(self.report_path)])
        self.assertEqual(code, 0)
        self.assertIn("verified withdrawal on host", output.getvalue())
        self.assertNotIn("install:", output.getvalue())

    def test_verifier_rejects_stale_feed_and_cached_withdrawal(self):
        report = self.prepare()
        for responses in (self.responses(report, stale_feed=True),
                          self.responses(report, cache_control="public, max-age=300")):
            with patch.object(verification, "_open_url", side_effect=responses), self.assertRaises(PublicationError):
                verification.verify_publication(self.report_path)
