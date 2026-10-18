from __future__ import annotations

from contextlib import redirect_stdout
from io import StringIO
import json
from pathlib import Path
import unittest
from unittest.mock import patch

from internal_app_updates import cli


class CLITests(unittest.TestCase):
    def test_human_publish_output_includes_install_page_then_feed(self):
        output = StringIO()
        with redirect_stdout(output):
            cli._emit({
                "release": {"version": "1.2", "build": "3"},
                "links": {
                    "installPageURL": "https://updates.example/builds/index.html",
                    "feedURL": "https://updates.example/builds/feed.json",
                },
            }, False)
        self.assertEqual(output.getvalue().splitlines(), [
            "published 1.2 (3)",
            "install: https://updates.example/builds/index.html",
            "feed: https://updates.example/builds/feed.json",
        ])

    def test_publish_json_includes_links(self):
        output = StringIO()
        published = {"schemaVersion": 1, "bundleIdentifier": "com.example.app",
                     "release": {"version": "1.2", "build": "3"}}
        links = {"installPageURL": "https://updates.example/index.html",
                 "feedURL": "https://updates.example/feed.json"}
        report = {**published, "links": links, "deployment": {"schemaVersion": 1}}
        with patch.object(cli, "publish", return_value=published) as publish_mock, \
                patch.object(cli, "publication_report", return_value=report) as report_mock, \
                redirect_stdout(output):
            self.assertEqual(cli.main(["publish", "--json", "--ipa", "App.ipa", "--output", "site",
                                       "--base-url", "https://updates.example/"]), 0)
        self.assertEqual(publish_mock.call_count, 1)
        report_mock.assert_called_once_with(Path("site"), published, "https://updates.example/")
        self.assertEqual(json.loads(output.getvalue()), report)

    def test_links_human_output_does_not_publish(self):
        output = StringIO()
        links = {"installPageURL": "https://updates.example/index.html",
                 "feedURL": "https://updates.example/feed.json"}
        with patch.object(cli, "publication_links", return_value=links) as publication_links_mock, \
                patch.object(cli, "publish") as publish_mock, redirect_stdout(output):
            self.assertEqual(cli.main(["links", "--output", "site"]), 0)
        publication_links_mock.assert_called_once_with(Path("site"))
        publish_mock.assert_not_called()
        self.assertEqual(output.getvalue().splitlines(), [
            "install: https://updates.example/index.html",
            "feed: https://updates.example/feed.json",
        ])

    def test_links_json_works_before_and_after_subcommand(self):
        links = {"installPageURL": "https://updates.example/index.html",
                 "feedURL": "https://updates.example/feed.json"}
        for arguments in (("--json", "links", "--output", "site"),
                          ("links", "--json", "--output", "site")):
            output = StringIO()
            with patch.object(cli, "publication_links", return_value=links), redirect_stdout(output):
                self.assertEqual(cli.main(list(arguments)), 0)
            self.assertEqual(json.loads(output.getvalue()), links)

    def test_links_error_is_json_when_requested(self):
        output = StringIO()
        with patch.object(cli, "publication_links", side_effect=cli.PublicationError("no active publication")), \
                redirect_stdout(output):
            self.assertEqual(cli.main(["--json", "links", "--output", "site"]), 1)
        self.assertEqual(json.loads(output.getvalue()), {"error": "no active publication"})

    def test_verify_json_works_before_and_after_subcommand(self):
        result = {"verified": True, "release": {"version": "1", "build": "3"},
                  "links": {"installPageURL": "https://updates.example/index.html",
                            "feedURL": "https://updates.example/feed.json"},
                  "filesVerified": 5, "bytesVerified": 123}
        for arguments in (("--json", "verify", "--publication", "report.json", "--timeout", "9"),
                          ("verify", "--json", "--publication", "report.json", "--timeout", "9")):
            output = StringIO()
            with patch("internal_app_updates.verification.verify_publication", return_value=result) as verify, \
                    redirect_stdout(output):
                self.assertEqual(cli.main(list(arguments)), 0)
            verify.assert_called_once_with(Path("report.json"), timeout=9)
            self.assertEqual(json.loads(output.getvalue()), result)

    def test_failed_verification_is_nonzero_without_success_links(self):
        output = StringIO()
        with patch("internal_app_updates.verification.verify_publication", \
                   side_effect=cli.PublicationError("stale feed")), redirect_stdout(output):
            self.assertEqual(cli.main(["verify", "--publication", "report.json", "--json"]), 1)
        self.assertEqual(json.loads(output.getvalue()), {"error": "stale feed"})
