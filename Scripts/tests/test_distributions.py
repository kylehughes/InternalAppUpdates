"""Black-box checks for the Python release artifacts."""

from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys
import tempfile
import tomllib
import unittest


class DistributionBuildTests(unittest.TestCase):
    @unittest.skipUnless(
        os.environ.get("INTERNAL_APP_UPDATES_TEST_DISTRIBUTIONS") == "1",
        "set INTERNAL_APP_UPDATES_TEST_DISTRIBUTIONS=1 to build and install distribution artifacts",
    )
    def test_build_script_creates_only_installable_release_artifacts(self) -> None:
        repository = Path(__file__).resolve().parents[2]
        version = tomllib.loads((repository / "pyproject.toml").read_text(encoding="utf-8"))["project"]["version"]
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "distributions"
            subprocess.run(
                [str(repository / "Scripts/build-python-distributions.sh"), str(output)],
                check=True,
                cwd=repository,
                env={
                    "PATH": __import__("os").environ["PATH"],
                    "INTERNAL_APP_UPDATES_PYTHON": sys.executable,
                    "TMPDIR": directory,
                },
            )
            artifacts = sorted(path.name for path in output.iterdir())
            self.assertEqual(
                artifacts,
                [
                    f"internal_app_updates-{version}-py3-none-any.whl",
                    f"internal_app_updates-{version}.tar.gz",
                ],
            )


if __name__ == "__main__":
    unittest.main()
