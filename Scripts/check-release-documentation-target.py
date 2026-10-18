#!/usr/bin/env python3
"""Require documentation deployment to target the latest exact release."""

from __future__ import annotations

import os
import sys
from typing import Any
from urllib.parse import quote

from release import GitHubClient, ReleaseError, parse_version, require_sha


def require_release(value: Any, version: str, description: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ReleaseError(f"{description} response was not an object")
    if value.get("tag_name") != version:
        raise ReleaseError(f"{description} does not use tag {version}")
    if value.get("draft") is not False:
        raise ReleaseError(f"{description} is still a draft")
    if value.get("prerelease") is not False:
        raise ReleaseError(f"{description} is a prerelease")
    return value


def validate_documentation_target(client: GitHubClient, version: str, certified_sha: str) -> None:
    parse_version(version)
    require_sha(certified_sha)

    encoded_version = quote(version, safe="")
    release = client.request("GET", f"/repos/{client.repository}/releases/tags/{encoded_version}")
    require_release(release, version, f"release {version}")

    latest = client.request("GET", f"/repos/{client.repository}/releases/latest")
    if not isinstance(latest, dict):
        raise ReleaseError("latest release response was not an object")
    latest_version = latest.get("tag_name")
    if not isinstance(latest_version, str):
        raise ReleaseError("latest release has no valid tag")
    require_release(latest, latest_version, "latest release")
    if latest_version != version:
        raise ReleaseError(f"documentation must target latest release {latest_version}, not {version}")

    tag_sha = client.resolve_tag(version)
    if tag_sha != certified_sha:
        raise ReleaseError(f"tag {version} resolves to {tag_sha}, not certified SHA {certified_sha}")


def main(arguments: list[str] | None = None) -> int:
    arguments = sys.argv[1:] if arguments is None else arguments
    if len(arguments) != 2 or not all(arguments):
        print(f"usage: {os.path.basename(sys.argv[0])} <version> <certified-sha>", file=sys.stderr)
        return 64
    repository = os.environ.get("GH_REPO", "")
    token = os.environ.get("GH_TOKEN", "")
    if not repository:
        raise ReleaseError("GH_REPO is required")
    if not token:
        raise ReleaseError("GH_TOKEN is required")
    client = GitHubClient(repository, token, os.environ.get("GITHUB_API_URL", "https://api.github.com"))
    validate_documentation_target(client, arguments[0], arguments[1])
    print(f"Documentation release target passed for {arguments[0]} at {arguments[1]}")
    return 0
if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except ReleaseError as error:
        print(f"error: {error}", file=sys.stderr)
        raise SystemExit(1)
