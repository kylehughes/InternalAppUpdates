#!/usr/bin/env python3
"""Upload an Internal App Updates deployment report with the normal AWS CLI."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path, PurePosixPath
from urllib.parse import urlsplit

from internal_app_updates.deployment import load_publication, validate_local_files
from internal_app_updates.publishing import PublicationError


def destination_parts(destination: str) -> tuple[str, tuple[str, ...]]:
    """Return a bucket and a fixed prefix without accepting URI decorations."""
    parsed = urlsplit(destination)
    if (
        parsed.scheme != "s3"
        or not parsed.netloc
        or parsed.hostname != parsed.netloc
        or parsed.query
        or parsed.fragment
        or parsed.username is not None
        or parsed.password is not None
        or parsed.port is not None
    ):
        raise ValueError("S3 destination must be s3://bucket/optional-prefix without query or fragment")
    raw_prefix = parsed.path[1:] if parsed.path.startswith("/") else parsed.path
    if raw_prefix.startswith("/"):
        raise ValueError("S3 destination prefix must not contain empty components")
    if raw_prefix.endswith("/"):
        raw_prefix = raw_prefix[:-1]
    if raw_prefix and any(not part for part in raw_prefix.split("/")):
        raise ValueError("S3 destination prefix must not contain empty components")
    prefix = tuple(raw_prefix.split("/")) if raw_prefix else ()
    if any(part in {".", ".."} or "\\" in part for part in prefix):
        raise ValueError("S3 destination prefix must not contain traversal components")
    return parsed.netloc, prefix


def object_destination(bucket: str, prefix: tuple[str, ...], relative_path: str) -> str:
    path = PurePosixPath(relative_path)
    if path.is_absolute() or not path.parts or any(part in {".", ".."} for part in path.parts):
        raise ValueError(f"unsafe deployment path: {relative_path!r}")
    return "s3://" + "/".join((bucket, *prefix, *path.parts))


def upload(report_path: str, s3_destination: str) -> None:
    report = load_publication(Path(report_path))
    # Do this before the first remote write so a retry can use the same report.
    validate_local_files(report)
    bucket, prefix = destination_parts(s3_destination)

    for file in report["deployment"]["files"]:
        command = [
            "aws", "s3", "cp", str(Path(report["deployment"]["directory"]) / file["path"]),
            object_destination(bucket, prefix, file["path"]),
            "--content-type", file["contentType"],
            "--cache-control", file["cacheControl"],
            "--only-show-errors",
        ]
        subprocess.run(command, check=True)

    verification = subprocess.run(
        ["internal-app-updates", "verify", "--json", "--publication", report_path],
        check=False, capture_output=True, text=True,
    )
    if verification.returncode:
        raise RuntimeError(verification.stderr or verification.stdout or "verification failed")
    links = report["links"]
    if report["release"] is None:
        print("Withdrawal verified.")
        print(f"page: {links['installPageURL']}")
    else:
        print("Deployment verified.")
        print(f"install: {links['installPageURL']}")
    print(f"feed: {links['feedURL']}")


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print(f"usage: {Path(sys.argv[0]).name} REPORT.json s3://bucket/optional-prefix", file=sys.stderr)
        return 64
    try:
        upload(argv[0], argv[1])
    except (OSError, RuntimeError, subprocess.CalledProcessError, PublicationError, ValueError, KeyError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
