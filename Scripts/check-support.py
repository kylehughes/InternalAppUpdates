#!/usr/bin/env python3
"""Validate the declared support policy against the package and CI workflows."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import sys


ROOT = Path(__file__).resolve().parent.parent
CONFIGURATION = ROOT / "Config" / "support.json"
PACKAGE = ROOT / "Package.swift"
PYPROJECT = ROOT / "pyproject.toml"
WORKFLOWS = (
    ROOT / ".github" / "workflows" / "ci.yml",
    ROOT / ".github" / "workflows" / "support-audit.yml",
    ROOT / ".github" / "workflows" / "nightly.yml",
    ROOT / ".github" / "workflows" / "post-merge.yml",
)


def fail(message: str) -> None:
    print(f"error: {message}", file=sys.stderr)
    raise SystemExit(1)


def load_support() -> dict[str, object]:
    try:
        support = json.loads(CONFIGURATION.read_text())
    except (OSError, json.JSONDecodeError) as error:
        fail(f"cannot read {CONFIGURATION.relative_to(ROOT)}: {error}")
    if not isinstance(support, dict):
        fail("support configuration must be an object")
    return support


def require_string(mapping: object, key: str) -> str:
    if not isinstance(mapping, dict) or not isinstance(mapping.get(key), str):
        fail(f"support configuration requires string {key!r}")
    return mapping[key]


def validate(support: dict[str, object]) -> None:
    swift = support.get("swift")
    platforms = support.get("platforms")
    tools_version = require_string(swift, "toolsVersion")
    language_mode = require_string(swift, "languageMode")
    ios = require_string(platforms, "iOS")
    macos = require_string(platforms, "macOS")
    toolchains = support.get("appleToolchains")
    python_versions = support.get("pythonVersions")
    if not isinstance(toolchains, list) or not toolchains:
        fail("support configuration requires non-empty appleToolchains")
    if not isinstance(python_versions, list) or not all(isinstance(v, str) for v in python_versions):
        fail("support configuration requires string pythonVersions")
    if python_versions != ["3.11", "3.15"]:
        fail("pythonVersions must declare the 3.11 and 3.15 lanes")

    package = PACKAGE.read_text()
    expected_fragments = (
        f"// swift-tools-version: {tools_version}",
        f".iOS(.v{ios})",
        f".macOS(.v{macos})",
        f"swiftLanguageModes: [.v{language_mode}]",
    )
    for fragment in expected_fragments:
        if fragment not in package:
            fail(f"Package.swift does not match support configuration: {fragment}")

    seen_xcode: set[str] = set()
    for toolchain in toolchains:
        xcode = require_string(toolchain, "xcode")
        build = require_string(toolchain, "build")
        swift_version = require_string(toolchain, "swift")
        developer_directories = toolchain.get("developerDirectories") if isinstance(toolchain, dict) else None
        if xcode in seen_xcode:
            fail(f"duplicate Apple Xcode lane {xcode}")
        seen_xcode.add(xcode)
        if not re.fullmatch(r"\d+\.\d+", xcode) or not re.fullmatch(r"\d+[A-Z]\d+", build):
            fail(f"invalid Apple toolchain declaration for Xcode {xcode}")
        if (
            not re.fullmatch(r"\d+\.\d+\.\d+", swift_version)
            or not isinstance(developer_directories, list)
            or not developer_directories
            or not all(isinstance(directory, str) and directory.startswith("/Applications/") for directory in developer_directories)
        ):
            fail(f"invalid Apple toolchain declaration for Xcode {xcode}")

    pyproject = PYPROJECT.read_text()
    minimum_python = python_versions[0]
    if f'requires-python = ">={minimum_python}"' not in pyproject:
        fail(f"pyproject.toml does not declare Python {minimum_python} as its lower bound")
    for workflow in WORKFLOWS:
        if not workflow.exists():
            fail(f"missing workflow {workflow.relative_to(ROOT)}")
        body = workflow.read_text()
        if "Scripts/check-support.py" not in body:
            fail(f"{workflow.relative_to(ROOT)} does not validate support.json")
        if workflow.name in {"ci.yml", "support-audit.yml"}:
            if "fromJSON(needs.support.outputs.apple-matrix)" not in body:
                fail(f"{workflow.relative_to(ROOT)} does not consume the declared Apple matrix")
            if "fromJSON(needs.support.outputs.python-matrix).python" not in body:
                fail(f"{workflow.relative_to(ROOT)} does not consume the declared Python matrix")


def write_github_output(support: dict[str, object]) -> None:
    output = os.environ.get("GITHUB_OUTPUT")
    if not output:
        return
    Path(output).open("a").write(
        "apple-matrix="
        + json.dumps(
            {
                "include": [
                    {
                        "xcode": toolchain["xcode"],
                        "build": toolchain["build"],
                        "swift": toolchain["swift"],
                        "configuration": configuration,
                    }
                    for toolchain in support["appleToolchains"]
                    for configuration in ("Debug", "Release")
                ]
            },
            separators=(",", ":"),
        )
        + "\n"
        + "python-matrix=" + json.dumps({"python": support["pythonVersions"]}, separators=(",", ":")) + "\n"
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--github-output", action="store_true")
    arguments = parser.parse_args()
    support = load_support()
    validate(support)
    if arguments.github_output:
        write_github_output(support)


if __name__ == "__main__":
    main()
