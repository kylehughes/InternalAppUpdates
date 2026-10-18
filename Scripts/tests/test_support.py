from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys
import unittest


ROOT = Path(__file__).resolve().parents[2]


class SupportConfigurationTests(unittest.TestCase):
    def test_support_configuration_matches_package_and_workflows(self) -> None:
        result = subprocess.run(
            [sys.executable, "Scripts/check-support.py"],
            cwd=ROOT,
            text=True,
            capture_output=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_declares_verified_apple_and_python_lanes(self) -> None:
        support = json.loads((ROOT / "Config" / "support.json").read_text())
        self.assertEqual(
            [(lane["xcode"], lane["build"], lane["swift"]) for lane in support["appleToolchains"]],
            [("26.5", "17F42", "6.3.2"), ("26.6", "17F113", "6.3.3")],
        )
        self.assertEqual(support["pythonVersions"], ["3.11", "3.15"])

    def test_primary_workflows_consume_the_declared_python_matrix(self) -> None:
        expected = "fromJSON(needs.support.outputs.python-matrix).python"
        for name in ("ci.yml", "support-audit.yml"):
            self.assertIn(expected, (ROOT / ".github" / "workflows" / name).read_text())
