"""Prepare and validate immutable deployment reports."""

from __future__ import annotations

import hashlib
from pathlib import Path
import plistlib

from .deployment_models import (
    ActiveDeployment, ActivePublicationReport, DigestSize, FileEntry, WithdrawalDeployment,
    WithdrawalPublicationReport, file_specs, publication_from_dict, IMMUTABLE_CACHE_CONTROL,
    MUTABLE_CACHE_CONTROL,
)
from .models import (Feed, Links, MAX_IPA_BYTES, PublicationError, Release,
                     base_url as canonical_base_url, load_feed_or_release_json)
from .publishing import IPAInfo, _html, _lock, _manifest, _read_feed, links_for_base_url


MAX_REPORT_BYTES = 256 * 1024


def _digest(path: Path) -> DigestSize:
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
            size += len(chunk)
    return DigestSize(digest.hexdigest(), size)


def _local_file(directory: Path, relative_path: str) -> Path:
    path = directory / relative_path
    if not path.resolve().is_relative_to(directory.resolve()) or not path.is_file():
        raise PublicationError(f"missing or unsafe deployment file: {relative_path}")
    return path


def _validate_generated_files(directory: Path, feed: Feed, base: str) -> None:
    release = feed.release
    try:
        if _local_file(directory, "index.html").read_bytes() != _html(base + "feed.json", release):
            raise ValueError("installation page differs from the publication")
        if release is None:
            return
        prefix = f"releases/{release.id}"
        metadata = Release.from_dict(load_feed_or_release_json(
            _local_file(directory, f"{prefix}/release.json").read_bytes()
        ))
        if metadata != release:
            raise ValueError("release metadata differs from the feed")
        manifest = plistlib.loads(_local_file(directory, f"{prefix}/manifest.plist").read_bytes())
        title = manifest["items"][0]["metadata"]["title"]
        if not isinstance(title, str) or not title:
            raise ValueError("manifest title is missing")
        info = IPAInfo(feed.bundle_identifier, release.version, release.build,
                       release.minimum_os_version, release.id, title)
        if manifest != plistlib.loads(_manifest(info, base + prefix + "/app.ipa")):
            raise ValueError("installation manifest differs from the publication")
    except (PublicationError, ValueError, KeyError, TypeError, IndexError, plistlib.InvalidFileException) as error:
        raise PublicationError(f"generated deployment files are inconsistent: {error}") from error


def _deployment(directory: Path, feed: Feed, base: str):
    release_id = feed.release.id if feed.release is not None else None
    entries = tuple(FileEntry(spec.path, base + spec.path, spec.role,
                              _digest(_local_file(directory, spec.path)), spec.content_type,
                              spec.cache_control) for spec in file_specs(release_id))
    return WithdrawalDeployment(directory, base, entries) if release_id is None else ActiveDeployment(directory, base, entries)


def _publication_report(output: Path, feed: Feed, base: str):
    """Build a typed report from an already validated feed and HTTPS base URL."""
    directory = Path(output).resolve()
    try:
        with _lock(directory):
            if _read_feed(directory / "feed.json") != feed:
                raise PublicationError("local publication changed before its report was prepared; serialize deployments")
            _validate_generated_files(directory, feed, base)
            deployment = _deployment(directory, feed, base)
    except (OSError, TypeError) as error:
        raise PublicationError(f"could not prepare deployment report: {error}") from error
    return (WithdrawalPublicationReport(feed, Links(base), deployment) if feed.release is None
            else ActivePublicationReport(feed, Links(base), deployment))


def publication_report(output: Path, publication: dict, base_url: str) -> dict:
    """Describe exact prepared bytes, without uploading or changing the hosted feed."""
    try:
        feed = Feed.from_dict(publication)
        return _publication_report(output, feed, canonical_base_url(base_url) + "/").to_dict()
    except (TypeError, ValueError, KeyError) as error:
        raise PublicationError(f"could not prepare deployment report: {error}") from error


def _read_report(path: Path) -> dict:
    """Read one bounded JSON report snapshot for public and typed adapters."""
    try:
        with Path(path).open("rb") as handle:
            data = handle.read(MAX_REPORT_BYTES + 1)
        if len(data) > MAX_REPORT_BYTES:
            raise PublicationError("deployment report exceeds 256 KiB")
        value = load_feed_or_release_json(data)
        if not isinstance(value, dict):
            raise PublicationError("deployment report must be an object")
        return value
    except PublicationError:
        raise
    except (OSError, ValueError, TypeError) as error:
        raise PublicationError(f"could not read deployment report: {error}") from error


def _load_publication(path: Path):
    """Read a report into its immutable, validated representation."""
    return publication_from_dict(_read_report(path))


def validate_publication(report: dict) -> None:
    """Validate a JSON-shaped report before a consumer uses its files."""
    try:
        publication_from_dict(report)
    except PublicationError as error:
        raise PublicationError(f"invalid deployment report: {error}") from error


def load_publication(path: Path) -> dict:
    """Read an expected report while preserving optional adapter metadata."""
    raw = _read_report(path)
    publication_from_dict(raw)
    return raw


def _validate_local_files(report) -> None:
    directory = report.deployment.directory
    try:
        for entry in report.deployment.files:
            if _digest(_local_file(directory, entry.path)) != entry.digest:
                raise PublicationError(f"local file changed since publication: {entry.path}")
    except OSError as error:
        raise PublicationError(f"could not validate local deployment files: {error}") from error


def validate_local_files(report: dict) -> None:
    """Reject changed or missing local files before a deployment makes its first write."""
    _validate_local_files(publication_from_dict(report))
