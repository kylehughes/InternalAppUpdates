#!/usr/bin/env python3
"""Validate and publish an exact, reproducible GitHub release candidate."""

from __future__ import annotations

import argparse
import hashlib
import json
import mimetypes
import os
from pathlib import Path
import re
import subprocess
import sys
import time
from typing import Any, Callable, Iterable
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode, urlsplit
from urllib.request import build_opener, HTTPRedirectHandler, Request, urlopen
import uuid


SEMVER = re.compile(r"(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\Z")
FULL_SHA = re.compile(r"[0-9a-f]{40}\Z")
MANIFEST_NAME = "release-candidate.json"


class ReleaseError(RuntimeError):
    """A release invariant was not satisfied."""


class APIError(ReleaseError):
    def __init__(self, method: str, url: str, detail: str, status: int | None = None):
        super().__init__(f"GitHub API {method} {url} failed: {detail}")
        self.status = status


class UnknownMutation(ReleaseError):
    """A write failed and its intended state could not be observed."""


def origin(url: str) -> tuple[str, str, int | None]:
    value = urlsplit(url)
    return value.scheme.lower(), (value.hostname or "").lower(), value.port


class CredentialSafeRedirectHandler(HTTPRedirectHandler):
    """Drop authorization when GitHub redirects an asset to signed storage."""

    def redirect_request(self, request, file_pointer, code, message, headers, new_url):  # type: ignore[no-untyped-def]
        redirected = super().redirect_request(request, file_pointer, code, message, headers, new_url)
        if redirected is not None and origin(request.full_url) != origin(new_url):
            redirected.remove_header("Authorization")
        return redirected


def parse_version(value: str) -> tuple[int, int, int]:
    match = SEMVER.fullmatch(value)
    if match is None:
        raise ReleaseError(f"version must be canonical stable bare SemVer: {value!r}")
    return tuple(int(part) for part in match.groups())  # type: ignore[return-value]


def require_sha(value: str) -> str:
    if FULL_SHA.fullmatch(value) is None:
        raise ReleaseError(f"release SHA must be lowercase 40-hex: {value!r}")
    return value


def positive_run_id(value: str) -> int:
    if not re.fullmatch(r"[1-9][0-9]*", value):
        raise argparse.ArgumentTypeError("candidate run ID must be a positive integer")
    return int(value)


def read_project_version(source: Path) -> str:
    import tomllib

    try:
        data = tomllib.loads((source / "pyproject.toml").read_text(encoding="utf-8"))
        version = data["project"]["version"]
    except (OSError, KeyError, TypeError, tomllib.TOMLDecodeError) as error:
        raise ReleaseError(f"could not read [project].version from pyproject.toml: {error}") from error
    if not isinstance(version, str):
        raise ReleaseError("[project].version must be a string")
    parse_version(version)
    return version


def validate_project_version(source: Path, version: str) -> None:
    project_version = read_project_version(source)
    if version != project_version:
        raise ReleaseError(f"dispatched version {version} does not match pyproject.toml version {project_version}")


def run_git(source: Path, *arguments: str) -> str:
    process = subprocess.run(
        ["git", "-C", str(source), *arguments],
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    if process.returncode:
        raise ReleaseError(f"git {' '.join(arguments)} failed: {process.stderr.strip()}")
    return process.stdout.strip()


def validate_source(source: Path, sha: str, main_ref: str) -> None:
    head = run_git(source, "rev-parse", "HEAD")
    if head != sha:
        raise ReleaseError(f"checked-out HEAD {head} does not equal certified SHA {sha}")
    process = subprocess.run(
        ["git", "-C", str(source), "merge-base", "--is-ancestor", sha, main_ref],
        check=False,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
    )
    if process.returncode != 0:
        raise ReleaseError(f"certified SHA {sha} is not reachable from {main_ref}")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def candidate_files(directory: Path) -> list[Path]:
    if not directory.is_dir():
        raise ReleaseError(f"artifact directory does not exist: {directory}")
    paths = sorted(
        path
        for path in directory.iterdir()
        if path.name != MANIFEST_NAME and path.is_file() and not path.is_symlink()
    )
    if not paths:
        raise ReleaseError("release candidate contains no assets")
    ignored = [path.name for path in directory.iterdir() if path.name != MANIFEST_NAME and path not in paths]
    if ignored:
        raise ReleaseError(f"release candidate contains unsupported entries: {', '.join(sorted(ignored))}")
    return paths


def build_manifest(directory: Path, repository: str, version: str, sha: str) -> dict[str, Any]:
    parse_version(version)
    require_sha(sha)
    expected_names = {
        f"internal_app_updates-{version}-py3-none-any.whl",
        f"internal_app_updates-{version}.tar.gz",
        f"InternalAppUpdates-{version}.doccarchive.zip",
        f"InternalAppUpdates-{version}-documentation.zip",
    }
    paths = candidate_files(directory)
    actual_names = {path.name for path in paths}
    if actual_names != expected_names:
        missing = ", ".join(sorted(expected_names - actual_names)) or "none"
        unexpected = ", ".join(sorted(actual_names - expected_names)) or "none"
        raise ReleaseError(f"release asset set mismatch (missing: {missing}; unexpected: {unexpected})")
    files = []
    for path in paths:
        media_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        files.append({"name": path.name, "size": path.stat().st_size, "sha256": sha256(path), "mediaType": media_type})
    return {"schemaVersion": 1, "repository": repository, "version": version, "sha": sha, "files": files}


def write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    temporary.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temporary.replace(path)


def load_manifest(path: Path, directory: Path, repository: str, version: str, sha: str) -> dict[str, Any]:
    try:
        manifest = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ReleaseError(f"could not read candidate manifest: {error}") from error
    expected = build_manifest(directory, repository, version, sha)
    if manifest != expected:
        raise ReleaseError("candidate manifest does not match the exact local artifact set and digests")
    return manifest


class GitHubClient:
    def __init__(self, repository: str, token: str, api_url: str = "https://api.github.com"):
        if repository.count("/") != 1 or any(not part for part in repository.split("/")):
            raise ReleaseError(f"repository must be owner/name: {repository!r}")
        if not token:
            raise ReleaseError("GITHUB_TOKEN is required")
        self.repository = repository
        self.token = token
        self.api_url = api_url.rstrip("/")

    def request(self, method: str, path_or_url: str, body: Any | None = None, content_type: str = "application/json") -> Any:
        url = path_or_url if path_or_url.startswith("https://") else f"{self.api_url}{path_or_url}"
        data = None
        if body is not None:
            data = body if isinstance(body, bytes) else json.dumps(body).encode("utf-8")
        request = Request(url, data=data, method=method)
        request.add_header("Accept", "application/vnd.github+json")
        request.add_header("Authorization", f"Bearer {self.token}")
        request.add_header("X-GitHub-Api-Version", "2022-11-28")
        if data is not None:
            request.add_header("Content-Type", content_type)
        try:
            with urlopen(request, timeout=30) as response:
                payload = response.read()
        except HTTPError as error:
            detail = error.read().decode("utf-8", errors="replace")
            raise APIError(method, url, detail, error.code) from error
        except (URLError, TimeoutError, OSError) as error:
            raise APIError(method, url, str(error)) from error
        if not payload:
            return None
        try:
            return json.loads(payload)
        except json.JSONDecodeError as error:
            raise APIError(method, url, "response was not JSON") from error

    def bytes(self, url: str) -> bytes:
        if not url.startswith(f"{self.api_url}/repos/{self.repository}/releases/assets/"):
            raise ReleaseError("remote asset API URL is outside the expected repository")
        request = Request(url, method="GET")
        request.add_header("Accept", "application/octet-stream")
        request.add_header("Authorization", f"Bearer {self.token}")
        try:
            with build_opener(CredentialSafeRedirectHandler()).open(request, timeout=60) as response:
                return response.read()
        except HTTPError as error:
            raise APIError("GET", url, error.reason, error.code) from error
        except (URLError, TimeoutError, OSError) as error:
            raise APIError("GET", url, str(error)) from error

    def list_all(self, resource: str) -> list[dict[str, Any]]:
        values: list[dict[str, Any]] = []
        page = 1
        while True:
            separator = "&" if "?" in resource else "?"
            batch = self.request("GET", f"/repos/{self.repository}/{resource}{separator}per_page=100&page={page}")
            if not isinstance(batch, list):
                raise ReleaseError(f"GitHub {resource} response was not a list")
            values.extend(batch)
            if len(batch) < 100:
                return values
            page += 1

    def releases(self) -> list[dict[str, Any]]:
        return self.list_all("releases")

    def tags(self) -> list[dict[str, Any]]:
        return self.list_all("tags")

    def workflow_run(self, run_id: int) -> dict[str, Any]:
        value = self.request("GET", f"/repos/{self.repository}/actions/runs/{run_id}")
        if not isinstance(value, dict):
            raise ReleaseError("GitHub workflow run response was not an object")
        return value

    def resolve_tag(self, tag: str) -> str:
        value = self.request("GET", f"/repos/{self.repository}/git/ref/tags/{quote(tag, safe='')}")
        object_value = value.get("object", {})
        seen: set[str] = set()
        while object_value.get("type") == "tag":
            object_sha = object_value.get("sha")
            if not isinstance(object_sha, str) or object_sha in seen:
                raise ReleaseError(f"tag {tag} has an invalid or cyclic annotated-tag chain")
            seen.add(object_sha)
            value = self.request("GET", f"/repos/{self.repository}/git/tags/{object_sha}")
            object_value = value.get("object", {})
        if object_value.get("type") != "commit" or FULL_SHA.fullmatch(str(object_value.get("sha", ""))) is None:
            raise ReleaseError(f"tag {tag} does not resolve to a commit")
        return object_value["sha"]

    def create_release(self, body: dict[str, Any]) -> Any:
        return self.request("POST", f"/repos/{self.repository}/releases", body)

    def upload_asset(self, upload_url: str, name: str, media_type: str, data: bytes) -> Any:
        base = upload_url.split("{", 1)[0]
        return self.request("POST", f"{base}?{urlencode({'name': name})}", data, media_type)

    def publish_release(self, release_id: int) -> Any:
        return self.request("PATCH", f"/repos/{self.repository}/releases/{release_id}", {"draft": False, "make_latest": "true"})


def one_release(releases: Iterable[dict[str, Any]], version: str) -> dict[str, Any] | None:
    matches = [release for release in releases if release.get("tag_name") == version]
    if len(matches) > 1:
        raise ReleaseError(f"multiple releases use tag {version}")
    return matches[0] if matches else None


def snapshot(client: GitHubClient, version: str) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any] | None]:
    releases = client.releases()
    tags = client.tags()
    return releases, tags, one_release(releases, version)


def assert_remote_identity(
    client: GitHubClient,
    version: str,
    sha: str,
    releases: list[dict[str, Any]],
    tags: list[dict[str, Any]],
    release: dict[str, Any] | None,
) -> None:
    candidate = parse_version(version)
    stable_names: set[str] = set()
    for value in [*(tag.get("name") for tag in tags), *(item.get("tag_name") for item in releases)]:
        if isinstance(value, str) and SEMVER.fullmatch(value):
            stable_names.add(value)
    for existing in stable_names:
        if existing != version and parse_version(existing) >= candidate:
            raise ReleaseError(f"version {version} must be greater than existing stable version {existing}")

    matching_tags = [tag for tag in tags if tag.get("name") == version]
    if len(matching_tags) > 1:
        raise ReleaseError(f"multiple tags use {version}")
    tag_sha = client.resolve_tag(version) if matching_tags else None
    if tag_sha is not None and tag_sha != sha:
        raise ReleaseError(f"tag {version} resolves to {tag_sha}, not certified SHA {sha}")
    if release is None:
        if tag_sha is not None:
            raise ReleaseError(
                f"tag {version} already exists without the matching release; bump pyproject.toml for the next release"
            )
        return
    if release.get("prerelease") is not False:
        raise ReleaseError(f"release {version} must not be a prerelease")
    if release.get("draft") is not True and release.get("draft") is not False:
        raise ReleaseError(f"release {version} has an invalid draft state")
    if tag_sha is None:
        if not release.get("draft"):
            raise ReleaseError(f"published release {version} has no tag")
        if release.get("target_commitish") != sha:
            raise ReleaseError(f"draft release {version} does not target certified SHA {sha}")
    if release.get("name") != version:
        raise ReleaseError(f"release title must exactly equal {version}")


def manifest_index(manifest: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {entry["name"]: entry for entry in manifest["files"]}


def verify_remote_assets(client: GitHubClient, release: dict[str, Any], manifest: dict[str, Any], complete: bool) -> set[str]:
    expected = manifest_index(manifest)
    assets = release.get("assets")
    if not isinstance(assets, list):
        raise ReleaseError("release assets response is malformed")
    names = [asset.get("name") for asset in assets]
    if any(not isinstance(name, str) or not name for name in names):
        raise ReleaseError("release contains an asset with an invalid name")
    if len(names) != len(set(names)):
        raise ReleaseError("release contains duplicate asset names")
    foreign = sorted(name for name in names if name not in expected)
    if foreign:
        raise ReleaseError(f"release contains foreign assets: {', '.join(str(name) for name in foreign)}")
    if complete and set(names) != set(expected):
        missing = sorted(set(expected) - set(names))
        raise ReleaseError(f"release is missing assets: {', '.join(missing)}")
    verified: set[str] = set()
    for asset in assets:
        name = asset["name"]
        entry = expected[name]
        if asset.get("size") != entry["size"]:
            raise ReleaseError(f"remote asset {name} has the wrong size")
        url = asset.get("url")
        if not isinstance(url, str):
            raise ReleaseError(f"remote asset {name} has no API download URL")
        payload = client.bytes(url)
        if len(payload) != entry["size"] or hashlib.sha256(payload).hexdigest() != entry["sha256"]:
            raise ReleaseError(f"remote asset {name} bytes do not match the certified digest")
        verified.add(name)
    return verified


def validate_remote(
    client: GitHubClient, version: str, sha: str, manifest: dict[str, Any] | None = None, notes: str | None = None
) -> dict[str, Any] | None:
    releases, tags, release = snapshot(client, version)
    assert_remote_identity(client, version, sha, releases, tags, release)
    if release is not None and notes is not None and release.get("body") != notes:
        raise ReleaseError("existing release notes do not match the explicitly supplied notes")
    if release is not None and manifest is not None:
        verify_remote_assets(client, release, manifest, complete=not release.get("draft", False))
    return release


def validate_candidate_run(client: GitHubClient, run_id: int, repository: str, sha: str) -> None:
    run = client.workflow_run(run_id)
    path = run.get("path")
    source_path = path.split("@", 1)[0] if isinstance(path, str) else None
    observed_repository = run.get("repository", {}).get("full_name") if isinstance(run.get("repository"), dict) else None
    expected = {
        "head_sha": sha,
        "head_branch": "main",
        "event": "workflow_dispatch",
        "status": "completed",
    }
    observed = {key: run.get(key) for key in expected}
    if observed != expected or source_path != ".github/workflows/release.yml" or observed_repository != repository:
        raise ReleaseError(
            f"candidate run {run_id} is not a completed Release workflow dispatch for {repository} at {sha}"
        )


def receipt_update(path: Path, receipt: dict[str, Any], phase: str, pending: dict[str, Any] | None = None) -> None:
    receipt["phase"] = phase
    receipt["pendingMutation"] = pending
    receipt["updatedAtUnix"] = int(time.time())
    write_json(path, receipt)


def reconcile(
    client: GitHubClient,
    version: str,
    sha: str,
    expected: Callable[[dict[str, Any] | None], bool] = lambda _: True,
    description: str = "remote release state",
    attempts: int = 5,
) -> dict[str, Any] | None:
    last_error: Exception | None = None
    for attempt in range(attempts):
        try:
            releases, tags, release = snapshot(client, version)
            assert_remote_identity(client, version, sha, releases, tags, release)
            if expected(release):
                return release
        except APIError as error:
            last_error = error
        if attempt + 1 < attempts:
            time.sleep(1)
    if last_error:
        raise UnknownMutation(f"could not observe {description} after a mutation: {last_error}") from last_error
    raise UnknownMutation(f"could not observe {description} after a mutation")


def publish(
    client: GitHubClient,
    directory: Path,
    manifest: dict[str, Any],
    receipt_path: Path,
    notes: str | None,
) -> None:
    version = manifest["version"]
    sha = manifest["sha"]
    receipt: dict[str, Any] = {
        "schemaVersion": 1,
        "operationId": str(uuid.uuid4()),
        "repository": manifest["repository"],
        "version": version,
        "sha": sha,
        "files": manifest["files"],
        "notes": (
            {"mode": "generated"}
            if notes is None
            else {"mode": "explicit", "sha256": hashlib.sha256(notes.encode("utf-8")).hexdigest()}
        ),
    }
    receipt_update(receipt_path, receipt, "planned")
    release = validate_remote(client, version, sha, manifest, notes)
    if release is not None and not release.get("draft"):
        verify_remote_assets(client, release, manifest, complete=True)
        receipt_update(receipt_path, receipt, "published")
        return

    if release is None:
        # Narrow the stale-read window immediately before the first write. A
        # conflicting create is still reconciled below rather than overwritten.
        release = validate_remote(client, version, sha, manifest, notes)
        if release is not None:
            raise ReleaseError("a release appeared before draft creation; rerun to reconcile it")
        pending = {"kind": "create-draft", "version": version, "sha": sha}
        receipt_update(receipt_path, receipt, "mutation-pending", pending)
        body: dict[str, Any] = {
            "tag_name": version,
            "target_commitish": sha,
            "name": version,
            "draft": True,
            "prerelease": False,
            "make_latest": "false",
        }
        if notes is None:
            body["generate_release_notes"] = True
        else:
            body["body"] = notes
        try:
            client.create_release(body)
        except APIError:
            pass
        release = reconcile(
            client,
            version,
            sha,
            expected=lambda value: value is not None and value.get("draft") is True,
            description="the exact draft release",
        )
        if notes is not None and release.get("body") != notes:
            raise ReleaseError("reconciled draft release notes do not match the intended notes")
        receipt_update(receipt_path, receipt, "draft-observed")

    verified = verify_remote_assets(client, release, manifest, complete=False)
    upload_url = release.get("upload_url")
    if not isinstance(upload_url, str):
        raise ReleaseError("draft release has no upload URL")
    expected = manifest_index(manifest)
    for name, entry in expected.items():
        if name in verified:
            continue
        release = reconcile(
            client,
            version,
            sha,
            expected=lambda value: value is not None and value.get("draft") is True,
            description="the unchanged draft before asset upload",
        )
        verified = verify_remote_assets(client, release, manifest, complete=False)
        if name in verified:
            continue
        upload_url = release.get("upload_url")
        if not isinstance(upload_url, str):
            raise ReleaseError("draft release has no upload URL")
        pending = {"kind": "upload-asset", "name": name, "sha256": entry["sha256"]}
        receipt_update(receipt_path, receipt, "mutation-pending", pending)
        data = (directory / name).read_bytes()
        try:
            client.upload_asset(upload_url, name, entry["mediaType"], data)
        except APIError:
            pass
        release = reconcile(
            client,
            version,
            sha,
            expected=lambda value: value is not None
            and value.get("draft") is True
            and name in {asset.get("name") for asset in value.get("assets", [])},
            description=f"uploaded asset {name}",
        )
        verified = verify_remote_assets(client, release, manifest, complete=False)
        if name not in verified:
            raise UnknownMutation(f"upload of {name} failed and exact remote bytes were not observed")
        receipt_update(receipt_path, receipt, "assets-partial")

    release = reconcile(
        client,
        version,
        sha,
        expected=lambda value: value is not None and value.get("draft") is True,
        description="the complete draft before publication",
    )
    verify_remote_assets(client, release, manifest, complete=True)
    if notes is not None and release.get("body") != notes:
        raise ReleaseError("release notes changed before publication")
    pending = {"kind": "publish-release", "releaseId": release.get("id")}
    receipt_update(receipt_path, receipt, "mutation-pending", pending)
    try:
        client.publish_release(int(release["id"]))
    except (APIError, KeyError, TypeError, ValueError):
        pass
    published = reconcile(
        client,
        version,
        sha,
        expected=lambda value: value is not None and value.get("draft") is False,
        description="the published release",
    )
    verify_remote_assets(client, published, manifest, complete=True)
    if notes is not None and published.get("body") != notes:
        raise ReleaseError("published release notes do not match the intended notes")
    receipt["releaseId"] = published.get("id")
    receipt["releaseUrl"] = published.get("html_url")
    receipt_update(receipt_path, receipt, "published")


def notes_from_arguments(arguments: argparse.Namespace) -> str | None:
    if arguments.notes is not None:
        return arguments.notes
    if arguments.notes_file is not None:
        try:
            return arguments.notes_file.read_text(encoding="utf-8")
        except OSError as error:
            raise ReleaseError(f"could not read release notes file: {error}") from error
    value = os.environ.get("RELEASE_NOTES")
    return value if value else None


def common_parser(parser: argparse.ArgumentParser, artifacts: bool) -> None:
    parser.add_argument("--repository", required=True)
    parser.add_argument("--version", required=True)
    parser.add_argument("--sha", required=True)
    parser.add_argument("--source", type=Path, default=Path.cwd())
    if artifacts:
        parser.add_argument("--artifacts", type=Path, required=True)
        parser.add_argument("--manifest", type=Path)


def make_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    preflight = subparsers.add_parser("preflight")
    common_parser(preflight, artifacts=False)
    manifest = subparsers.add_parser("create-manifest")
    common_parser(manifest, artifacts=True)
    validate_manifest = subparsers.add_parser("validate-manifest")
    common_parser(validate_manifest, artifacts=True)
    validate = subparsers.add_parser("validate")
    common_parser(validate, artifacts=True)
    validate_run = subparsers.add_parser("validate-run")
    common_parser(validate_run, artifacts=False)
    validate_run.add_argument("--candidate-run-id", type=positive_run_id, required=True)
    publish_parser = subparsers.add_parser("publish")
    common_parser(publish_parser, artifacts=True)
    publish_parser.add_argument("--receipt", type=Path, required=True)
    for current in (preflight, validate, publish_parser):
        notes = current.add_mutually_exclusive_group()
        notes.add_argument("--notes")
        notes.add_argument("--notes-file", type=Path)
    return parser


def main(argv: list[str] | None = None) -> int:
    arguments = make_parser().parse_args(argv)
    version = arguments.version
    sha = require_sha(arguments.sha)
    parse_version(version)
    validate_project_version(arguments.source, version)
    validate_source(arguments.source, sha, "origin/main")
    if arguments.command in {"create-manifest", "validate-manifest"}:
        output = arguments.manifest or arguments.artifacts / MANIFEST_NAME
        if arguments.command == "validate-manifest":
            load_manifest(output, arguments.artifacts, arguments.repository, version, sha)
            print(f"Validated {output} for {version} at {sha}")
            return 0
        manifest = build_manifest(arguments.artifacts, arguments.repository, version, sha)
        write_json(output, manifest)
        print(f"Created {output} for {version} at {sha}")
        return 0

    token = os.environ.get("GITHUB_TOKEN", "")
    client = GitHubClient(arguments.repository, token, os.environ.get("GITHUB_API_URL", "https://api.github.com"))
    if arguments.command == "validate-run":
        validate_candidate_run(client, arguments.candidate_run_id, arguments.repository, sha)
        print(f"Validated retained candidate source run {arguments.candidate_run_id}")
        return 0
    notes = notes_from_arguments(arguments)
    manifest = None
    if arguments.command in {"validate", "publish"}:
        manifest_path = arguments.manifest or arguments.artifacts / MANIFEST_NAME
        manifest = load_manifest(manifest_path, arguments.artifacts, arguments.repository, version, sha)
    if arguments.command in {"preflight", "validate"}:
        validate_remote(client, version, sha, manifest, notes)
        print(f"Release validation passed for {version} at {sha}")
        return 0
    publish(client, arguments.artifacts, manifest, arguments.receipt, notes)
    print(f"Published {version} at {sha}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except ReleaseError as error:
        print(f"error: {error}", file=sys.stderr)
        raise SystemExit(1)
