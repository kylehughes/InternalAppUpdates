#!/usr/bin/env python3
"""Run coverage and verify the report contains the Swift client sources."""

from __future__ import annotations

import json
from pathlib import Path
import shutil
import subprocess
import argparse


ROOT = Path(__file__).resolve().parent.parent
ARTIFACT = ROOT / "Artifacts" / "coverage.json"
# These files contain runtime behavior exercised by the package test suite. The
# remaining sources are declaration-only state/event types or a host-app SwiftUI
# view, which are not expected to emit executable coverage in this test target.
RUNTIME_SOURCES = {
    "Feed.swift",
    "JSONMemberValidator.swift",
    "Models.swift",
    "PositiveDuration.swift",
    "UpdateController.swift",
    "UpdateState.swift",
}


def validate_coverage(data: dict[str, object]) -> None:
    entries = [
        entry
        for unit in data.get("data", [])
        if isinstance(unit, dict)
        for entry in unit.get("files", [])
        if isinstance(entry, dict) and entry.get("filename")
    ]
    if not entries:
        raise SystemExit("error: coverage report contains no source files")
    summaries = {Path(str(entry["filename"])).name: entry.get("summary", {}) for entry in entries}
    missing = sorted(RUNTIME_SOURCES - summaries.keys())
    if missing:
        raise SystemExit("error: coverage report excludes runtime sources " + ", ".join(missing))
    uncovered = []
    for source in sorted(RUNTIME_SOURCES):
        lines = summaries[source].get("lines", {}) if isinstance(summaries[source], dict) else {}
        if not isinstance(lines, dict) or lines.get("count", 0) <= 0 or lines.get("covered", 0) <= 0:
            uncovered.append(source)
    if uncovered:
        raise SystemExit("error: coverage report records no covered runtime lines for " + ", ".join(uncovered))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--verify-report", type=Path)
    arguments = parser.parse_args()
    if arguments.verify_report:
        validate_coverage(json.loads(arguments.verify_report.read_text()))
        print(f"Coverage report is meaningful: {arguments.verify_report}")
        return
    if shutil.which("swift") is None:
        raise SystemExit("error: swift is required")
    subprocess.run(["swift", "test", "--enable-code-coverage"], cwd=ROOT, check=True)
    report = subprocess.check_output(["swift", "test", "--show-code-coverage-path"], cwd=ROOT, text=True).strip()
    report_path = Path(report)
    if not report_path.is_file():
        raise SystemExit(f"error: coverage report is missing: {report_path}")
    data = json.loads(report_path.read_text())
    validate_coverage(data)
    ARTIFACT.parent.mkdir(exist_ok=True)
    ARTIFACT.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n")
    print(f"Coverage report written to {ARTIFACT.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
