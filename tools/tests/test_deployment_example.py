from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from internal_app_updates.deployment import publication_report
from internal_app_updates.publishing import IPAInfo, publish, withdraw


ROOT = Path(__file__).resolve().parents[2]
UPLOADER = ROOT / "Examples" / "Deployment" / "upload-s3.py"
WRAPPER = ROOT / "Examples" / "Deployment" / "publish-s3.sh"


class S3DeploymentExampleTests(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary_directory.name)
        self.site = self.root / "site"
        self.ipa = self.root / "App.ipa"
        self.ipa.write_bytes(b"signed app fixture")
        digest = hashlib.sha256(self.ipa.read_bytes()).hexdigest()
        publication = publish(self.ipa, self.site, "https://updates.example/apps", verifier=lambda _: IPAInfo(
            "example.app", "1.0", "2", "16.0", digest, "Example",
            profile_expires_at="2030-01-01T00:00:00Z",
        ))
        self.report = self.root / "publication.json"
        self.report.write_text(json.dumps(publication_report(self.site, publication, "https://updates.example/apps")))
        self.log = self.root / "commands.jsonl"
        self.bin = self.root / "bin"
        self.bin.mkdir()
        self._command("aws", """#!/usr/bin/env python3
import json, os, sys
with open(os.environ['COMMAND_LOG'], 'a') as handle:
    handle.write(json.dumps(sys.argv[1:]) + '\\n')
if os.environ.get('FAIL_AT') and any(os.environ['FAIL_AT'] in argument for argument in sys.argv):
    raise SystemExit(9)
""")
        self._command("internal-app-updates", """#!/usr/bin/env python3
import json, os, sys
with open(os.environ['COMMAND_LOG'], 'a') as handle:
    handle.write(json.dumps(sys.argv[1:]) + '\\n')
if os.environ.get('PUBLISH_FAIL') and sys.argv[1:2] == ['publish']:
    print(json.dumps({'error': 'IPA signature is invalid'}))
    raise SystemExit(9)
if os.environ.get('VERIFY_FAIL'):
    raise SystemExit(10)
""")

    def tearDown(self):
        self.temporary_directory.cleanup()

    def _command(self, name: str, source: str) -> None:
        path = self.bin / name
        path.write_text(source)
        path.chmod(0o755)

    def run_uploader(self, destination: str = "s3://updates-bucket/internal/apps",
                     **overrides: str) -> subprocess.CompletedProcess[str]:
        environment = os.environ | {
            "COMMAND_LOG": str(self.log),
            "PATH": str(self.bin) + os.pathsep + os.environ["PATH"],
            "PYTHONPATH": str(ROOT / "tools") + os.pathsep + os.environ.get("PYTHONPATH", ""),
        } | overrides
        return subprocess.run(
            [sys.executable, str(UPLOADER), str(self.report), destination],
            text=True, capture_output=True, check=False, env=environment,
        )

    def run_wrapper(self, report: Path, output: Path, **overrides: str) -> subprocess.CompletedProcess[str]:
        environment = os.environ | {
            "COMMAND_LOG": str(self.log),
            "PATH": str(self.bin) + os.pathsep + os.environ["PATH"],
            "OUTPUT_DIRECTORY": str(output),
            "BASE_URL": "https://updates.example/apps",
            "S3_DESTINATION": "s3://updates-bucket/internal/apps",
            "PUBLICATION_REPORT": str(report),
            "INTERNAL_APP_UPDATES_PYTHON": sys.executable,
        } | overrides
        return subprocess.run(
            ["sh", str(WRAPPER), str(self.ipa)], text=True, capture_output=True,
            check=False, env=environment,
        )

    def commands(self) -> list[list[str]]:
        if not self.log.exists():
            return []
        return [json.loads(line) for line in self.log.read_text().splitlines()]

    def report_document(self) -> dict:
        return json.loads(self.report.read_text())

    def test_uploads_ordered_files_with_report_headers_then_verifies(self):
        result = self.run_uploader()
        self.assertEqual(result.returncode, 0, result.stderr)
        report = self.report_document()
        files = report["deployment"]["files"]
        uploads = self.commands()[:-1]
        self.assertEqual([command[3] for command in uploads], [
            f"s3://updates-bucket/internal/apps/{file['path']}" for file in files
        ])
        for command, file in zip(uploads, files):
            self.assertEqual(command[:3], ["s3", "cp", str(Path(report["deployment"]["directory"]) / file["path"])])
            self.assertEqual(command[4:], ["--content-type", file["contentType"], "--cache-control", file["cacheControl"], "--only-show-errors"])
        self.assertEqual(self.commands()[-1], ["verify", "--json", "--publication", str(self.report)])
        self.assertIn("Deployment verified.", result.stdout)
        self.assertIn(report["links"]["installPageURL"], result.stdout)

    def test_upload_failure_stops_before_feed_and_verification(self):
        result = self.run_uploader(FAIL_AT="release.json")
        self.assertNotEqual(result.returncode, 0)
        commands = self.commands()
        self.assertEqual(len(commands), 3)
        self.assertTrue(all(command[0:2] == ["s3", "cp"] for command in commands))
        self.assertFalse(any("feed.json" in command for command in commands))
        self.assertNotIn("Deployment verified.", result.stdout)

    def test_verify_failure_never_reports_deployment_success(self):
        result = self.run_uploader(VERIFY_FAIL="1")
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(len(self.commands()), 6)
        self.assertEqual(self.commands()[-1][0], "verify")
        self.assertNotIn("Deployment verified.", result.stdout)
        self.assertNotIn("install:", result.stdout)

    def test_changed_local_file_means_no_remote_writes(self):
        (self.site / "index.html").write_text("corrupted after report")
        result = self.run_uploader()
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.commands(), [])
        self.assertNotIn("Deployment verified.", result.stdout)

    def test_destination_rejects_empty_prefix_components_before_remote_writes(self):
        for destination in (
            "s3://updates-bucket//",
            "s3://updates-bucket//internal/apps",
            "s3://updates-bucket/internal/apps//",
        ):
            with self.subTest(destination=destination):
                self.log.unlink(missing_ok=True)
                result = self.run_uploader(destination)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("empty components", result.stderr)
                self.assertEqual(self.commands(), [])

        result = self.run_uploader("s3://updates-bucket/internal/apps/")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.commands()[0][3],
                         f"s3://updates-bucket/internal/apps/{self.report_document()['deployment']['files'][0]['path']}")

    def test_withdrawal_uploads_page_then_feed_and_preserves_release_assets(self):
        assets = {path: path.read_bytes() for path in (self.site / "releases").rglob("*") if path.is_file()}
        document = withdraw(self.site, "https://updates.example/apps")
        report = publication_report(self.site, document, "https://updates.example/apps")
        self.report.write_text(json.dumps(report))
        result = self.run_uploader()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual([command[3] for command in self.commands()[:-1]], [
            "s3://updates-bucket/internal/apps/index.html", "s3://updates-bucket/internal/apps/feed.json",
        ])
        self.assertTrue(all(command[7] == "no-cache" for command in self.commands()[:-1]))
        self.assertEqual(self.commands()[-1], ["verify", "--json", "--publication", str(self.report)])
        self.assertIn("Withdrawal verified.", result.stdout)
        self.assertNotIn("install:", result.stdout)
        for path, contents in assets.items():
            self.assertEqual(path.read_bytes(), contents)

    def test_withdrawal_failure_stops_then_retries_same_report(self):
        document = withdraw(self.site, "https://updates.example/apps")
        report = publication_report(self.site, document, "https://updates.example/apps")
        self.report.write_text(json.dumps(report))
        for failure in ("index.html", "feed.json"):
            with self.subTest(failure=failure):
                self.log.unlink(missing_ok=True)
                failed = self.run_uploader(FAIL_AT=failure)
                self.assertNotEqual(failed.returncode, 0)
                self.assertEqual(len(self.commands()), 1 if failure == "index.html" else 2)
                self.assertNotIn("Withdrawal verified.", failed.stdout)
                self.log.unlink()
                retried = self.run_uploader()
                self.assertEqual(retried.returncode, 0, retried.stderr)
                self.assertEqual(len(self.commands()), 3)
                self.assertEqual(self.report_document(), report)

    def test_withdrawal_verify_failure_never_reports_success(self):
        document = withdraw(self.site, "https://updates.example/apps")
        self.report.write_text(json.dumps(publication_report(self.site, document, "https://updates.example/apps")))
        result = self.run_uploader(VERIFY_FAIL="1")
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(len(self.commands()), 3)
        self.assertNotIn("Withdrawal verified.", result.stdout)
        self.assertNotIn("page:", result.stdout)

    def test_wrapper_creates_a_first_time_report_parent(self):
        report = self.root / "new" / "reports" / "publication.json"
        result = self.run_wrapper(report, self.root / "new-site")
        self.assertNotEqual(result.returncode, 0)  # The fake publish output is not a valid report.
        self.assertTrue(report.is_file())
        self.assertEqual(report.read_text(), "")
        self.assertEqual(self.commands(), [["publish", "--ipa", str(self.ipa), "--output", str((self.root / "new-site").resolve()), "--base-url", "https://updates.example/apps", "--json"]])

    def test_wrapper_preserves_existing_report_when_publish_fails(self):
        report = self.root / "reports" / "publication.json"
        report.parent.mkdir()
        report.write_text("previous report")
        result = self.run_wrapper(report, self.root / "site", PUBLISH_FAIL="1")
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(result.returncode, 9)
        self.assertIn("IPA signature is invalid", result.stderr)
        self.assertEqual(report.read_text(), "previous report")
        self.assertEqual(self.commands(), [["publish", "--ipa", str(self.ipa), "--output", str(self.site.resolve()), "--base-url", "https://updates.example/apps", "--json"]])

    def test_wrapper_rejects_report_path_inside_output_after_resolving_aliases(self):
        report = self.root / "site" / "report.json"
        result = self.run_wrapper(self.root / "." / "site" / "report.json", self.root / "site")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("outside OUTPUT_DIRECTORY", result.stderr)
        self.assertFalse(report.exists())
        self.assertEqual(self.commands(), [])


if __name__ == "__main__":
    unittest.main()
