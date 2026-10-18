"""Immutable deployment-report contracts.

The public deployment module keeps accepting JSON-shaped dictionaries.  This
module is the boundary behind that adapter: once parsed, report processing
uses these value objects rather than unvalidated mappings.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re
from typing import Any, Mapping
from urllib.parse import urlsplit

from .models import MAX_IPA_BYTES, Feed, Links, PublicationError, base_url, https_url, validated_model


IMMUTABLE_CACHE_CONTROL = "public, max-age=31536000, immutable"
MUTABLE_CACHE_CONTROL = "no-cache"
_DIGEST = re.compile(r"[0-9a-f]{64}")


@validated_model
@dataclass(frozen=True, slots=True)
class FileSpec:
    path: str
    role: str
    content_type: str
    cache_control: str

    def __post_init__(self) -> None:
        if (not all(type(value) is str and value for value in
                    (self.path, self.role, self.content_type, self.cache_control))
                or self.path.startswith("/") or "\\" in self.path or "\0" in self.path
                or any(part in {"", ".", ".."} for part in self.path.split("/"))):
            raise PublicationError("invalid deployment file specification")
        filename = self.path
        if "/" in filename:
            match = re.fullmatch(r"releases/[0-9a-f]{64}/(app\.ipa|manifest\.plist|release\.json)", filename)
            if match is None:
                raise PublicationError("invalid deployment file path")
            filename = match.group(1)
        expected = {
            "app.ipa": ("release", "application/octet-stream", IMMUTABLE_CACHE_CONTROL),
            "manifest.plist": ("release", "text/xml", IMMUTABLE_CACHE_CONTROL),
            "release.json": ("release", "application/json", IMMUTABLE_CACHE_CONTROL),
            "index.html": ("install-page", "text/html", MUTABLE_CACHE_CONTROL),
            "feed.json": ("feed", "application/json", MUTABLE_CACHE_CONTROL),
        }
        if expected.get(filename) != (self.role, self.content_type, self.cache_control):
            raise PublicationError("invalid deployment file HTTP contract")


@validated_model
@dataclass(frozen=True, slots=True)
class DigestSize:
    sha256: str
    size: int

    def __post_init__(self) -> None:
        if type(self.sha256) is not str or _DIGEST.fullmatch(self.sha256) is None:
            raise PublicationError("invalid deployment checksum")
        if type(self.size) is not int or not 0 < self.size <= MAX_IPA_BYTES:
            raise PublicationError("invalid deployment byte size")


@validated_model
@dataclass(frozen=True, slots=True)
class FileEntry:
    path: str
    url: str
    role: str
    digest: DigestSize
    content_type: str
    cache_control: str

    def __post_init__(self) -> None:
        FileSpec(self.path, self.role, self.content_type, self.cache_control)
        if type(self.digest) is not DigestSize:
            raise PublicationError("invalid deployment file digest")
        base_url(self.url)
        if urlsplit(self.url).path != "/" + self.path and not urlsplit(self.url).path.endswith("/" + self.path):
            raise PublicationError("deployment file URL does not match its path")
        if self.role == "release":
            match = re.fullmatch(r"releases/([0-9a-f]{64})/(app\.ipa|manifest\.plist|release\.json)", self.path)
            if match is None:
                raise PublicationError("invalid immutable release file path")
            expected = {
                "app.ipa": ("application/octet-stream", IMMUTABLE_CACHE_CONTROL),
                "manifest.plist": ("text/xml", IMMUTABLE_CACHE_CONTROL),
                "release.json": ("application/json", IMMUTABLE_CACHE_CONTROL),
            }[match.group(2)]
            if (self.content_type, self.cache_control) != expected:
                raise PublicationError("invalid immutable release file contract")
            if match.group(2) == "app.ipa" and self.sha256 != match.group(1):
                raise PublicationError("IPA checksum does not match the release identity")
        elif (self.path, self.role, self.content_type, self.cache_control) not in {
            ("index.html", "install-page", "text/html", MUTABLE_CACHE_CONTROL),
            ("feed.json", "feed", "application/json", MUTABLE_CACHE_CONTROL),
        }:
            raise PublicationError("invalid mutable deployment file contract")

    @property
    def sha256(self) -> str:
        return self.digest.sha256

    @property
    def size(self) -> int:
        return self.digest.size

    @classmethod
    def from_dict(cls, value: object) -> FileEntry:
        if not isinstance(value, Mapping):
            raise PublicationError("deployment file must be an object")
        try:
            return cls(value["path"], value["url"], value["role"],
                       DigestSize(value["sha256"], value["size"]),
                       value["contentType"], value["cacheControl"])
        except (KeyError, TypeError) as error:
            raise PublicationError(f"invalid deployment file: {error}") from error

    def to_dict(self) -> dict[str, object]:
        return {"path": self.path, "url": self.url, "role": self.role,
                "sha256": self.sha256, "size": self.size,
                "contentType": self.content_type, "cacheControl": self.cache_control}


ACTIVE_FILE_SPECS = (
    FileSpec("app.ipa", "release", "application/octet-stream", IMMUTABLE_CACHE_CONTROL),
    FileSpec("manifest.plist", "release", "text/xml", IMMUTABLE_CACHE_CONTROL),
    FileSpec("release.json", "release", "application/json", IMMUTABLE_CACHE_CONTROL),
    FileSpec("index.html", "install-page", "text/html", MUTABLE_CACHE_CONTROL),
    FileSpec("feed.json", "feed", "application/json", MUTABLE_CACHE_CONTROL),
)
WITHDRAWAL_FILE_SPECS = ACTIVE_FILE_SPECS[-2:]


def file_specs(release_id: str | None) -> tuple[FileSpec, ...]:
    if release_id is None:
        return WITHDRAWAL_FILE_SPECS
    prefix = f"releases/{release_id}/"
    return tuple(FileSpec(prefix + spec.path, spec.role, spec.content_type, spec.cache_control)
                 for spec in ACTIVE_FILE_SPECS[:3]) + ACTIVE_FILE_SPECS[-2:]


def _directory(value: object) -> Path:
    if not isinstance(value, str) or "\0" in value or not Path(value).is_absolute():
        raise PublicationError("deployment directory must be absolute")
    return Path(value)


def _base(value: object) -> str:
    if not isinstance(value, str):
        raise PublicationError("deployment base URL must be an HTTPS URL")
    canonical = base_url(value) + "/"
    if value != canonical:
        raise PublicationError("deployment base URL must have one trailing slash")
    return canonical


def _files(value: object) -> tuple[FileEntry, ...]:
    if not isinstance(value, list):
        raise PublicationError("deployment files must be an array")
    return tuple(FileEntry.from_dict(entry) for entry in value)


def _validate_files(base: str, entries: tuple[FileEntry, ...], specs: tuple[FileSpec, ...]) -> None:
    if len(entries) != len(specs):
        raise PublicationError("deployment must describe all files required by its operation")
    for entry, spec in zip(entries, specs):
        if (entry.path != spec.path or entry.url != base + spec.path or entry.role != spec.role
                or entry.content_type != spec.content_type or entry.cache_control != spec.cache_control):
            raise PublicationError("deployment files, order, or HTTP metadata do not match its operation")


@validated_model
@dataclass(frozen=True, slots=True)
class ActiveDeployment:
    directory: Path
    base_url: str
    files: tuple[FileEntry, ...]

    def __post_init__(self) -> None:
        if type(self.directory) is not type(Path()) or not self.directory.is_absolute() or "\0" in str(self.directory):
            raise PublicationError("deployment directory must be absolute")
        _base(self.base_url)
        if type(self.files) is not tuple or not all(type(entry) is FileEntry for entry in self.files):
            raise PublicationError("deployment files must be immutable file entries")
        match = re.fullmatch(r"releases/([0-9a-f]{64})/app\.ipa", self.files[0].path) if self.files else None
        if match is None:
            raise PublicationError("active deployment requires a release IPA")
        release_id = match.group(1)
        _validate_files(self.base_url, self.files, file_specs(release_id))
        if self.files[0].sha256 != release_id:
            raise PublicationError("IPA checksum does not match the release identity")

    @classmethod
    def from_dict(cls, value: object) -> ActiveDeployment:
        if (not isinstance(value, Mapping) or type(value.get("schemaVersion")) is not int
                or value.get("schemaVersion") != 1
                or ("operation" in value and value["operation"] != "publish")):
            raise PublicationError("unsupported deployment schema")
        try:
            return cls(_directory(value["directory"]), _base(value["baseURL"]), _files(value["files"]))
        except KeyError as error:
            raise PublicationError(f"invalid deployment report: {error}") from error

    def to_dict(self) -> dict[str, object]:
        return {"schemaVersion": 1, "directory": str(self.directory), "baseURL": self.base_url,
                "files": [entry.to_dict() for entry in self.files]}


@validated_model
@dataclass(frozen=True, slots=True)
class WithdrawalDeployment:
    directory: Path
    base_url: str
    files: tuple[FileEntry, ...]

    def __post_init__(self) -> None:
        if type(self.directory) is not type(Path()) or not self.directory.is_absolute() or "\0" in str(self.directory):
            raise PublicationError("deployment directory must be absolute")
        _base(self.base_url)
        if type(self.files) is not tuple or not all(type(entry) is FileEntry for entry in self.files):
            raise PublicationError("deployment files must be immutable file entries")
        _validate_files(self.base_url, self.files, file_specs(None))

    @classmethod
    def from_dict(cls, value: object) -> WithdrawalDeployment:
        if (not isinstance(value, Mapping) or type(value.get("schemaVersion")) is not int or value.get("schemaVersion") != 2
                or value.get("operation") != "withdraw"):
            raise PublicationError("version 2 requires a withdrawal with release: null")
        try:
            return cls(_directory(value["directory"]), _base(value["baseURL"]), _files(value["files"]))
        except KeyError as error:
            raise PublicationError(f"invalid deployment report: {error}") from error

    def to_dict(self) -> dict[str, object]:
        return {"schemaVersion": 2, "operation": "withdraw", "directory": str(self.directory),
                "baseURL": self.base_url, "files": [entry.to_dict() for entry in self.files]}


def _feed(value: Mapping[str, Any]) -> Feed:
    try:
        release = value["release"]
        if isinstance(release, Mapping):
            release = {key: release[key] for key in ("id", "version", "build", "minimumOSVersion", "publishedAt", "expiresAt", "manifestURL") if key in release} | ({"releaseNotes": release["releaseNotes"]} if "releaseNotes" in release else {})
        return Feed.from_dict({"schemaVersion": value["schemaVersion"], "bundleIdentifier": value["bundleIdentifier"], "release": release})
    except KeyError as error:
        raise PublicationError(f"invalid deployment report: {error}") from error


def _links(value: object) -> Links:
    if not isinstance(value, Mapping):
        raise PublicationError("publication links must be an object")
    try:
        install = value["installPageURL"]
        feed = value["feedURL"]
        if type(install) is not str or type(feed) is not str or not install.endswith("index.html"):
            raise PublicationError("invalid publication links")
        result = Links(install[:-len("index.html")])
        if result.feed_url != feed:
            raise PublicationError("invalid publication links")
        return result
    except KeyError as error:
        raise PublicationError(f"invalid publication links: {error}") from error


@validated_model
@dataclass(frozen=True, slots=True)
class ActivePublicationReport:
    feed: Feed
    links: Links
    deployment: ActiveDeployment

    def __post_init__(self) -> None:
        if not isinstance(self.feed, Feed) or self.feed.release is None:
            raise PublicationError("version 1 requires an active release")
        if not isinstance(self.links, Links) or not isinstance(self.deployment, ActiveDeployment):
            raise PublicationError("invalid active publication report")
        release = self.feed.release
        base = self.deployment.base_url
        if self.links != Links(base):
            raise PublicationError("publication links do not match the deployment base URL")
        if release.manifest_url != base + f"releases/{release.id}/manifest.plist":
            raise PublicationError("manifest URL does not match the release")
        _validate_files(base, self.deployment.files, file_specs(release.id))
        if self.deployment.files[0].sha256 != release.id:
            raise PublicationError("IPA checksum does not match the release identity")

    @property
    def release(self):
        return self.feed.release

    @classmethod
    def from_dict(cls, value: object) -> ActivePublicationReport:
        if not isinstance(value, Mapping):
            raise PublicationError("deployment report must be an object")
        feed = _feed(value)
        if feed.release is None:
            raise PublicationError("version 1 requires an active release")
        try:
            return cls(feed, _links(value["links"]), ActiveDeployment.from_dict(value["deployment"]))
        except KeyError as error:
            raise PublicationError(f"invalid deployment report: {error}") from error

    def to_dict(self) -> dict[str, object]:
        return {**self.feed.to_dict(), "links": self.links.to_dict(), "deployment": self.deployment.to_dict()}


@validated_model
@dataclass(frozen=True, slots=True)
class WithdrawalPublicationReport:
    feed: Feed
    links: Links
    deployment: WithdrawalDeployment

    def __post_init__(self) -> None:
        if not isinstance(self.feed, Feed) or self.feed.release is not None:
            raise PublicationError("version 2 requires a withdrawal with release: null")
        if not isinstance(self.links, Links) or not isinstance(self.deployment, WithdrawalDeployment):
            raise PublicationError("invalid withdrawal publication report")
        if self.links != Links(self.deployment.base_url):
            raise PublicationError("publication links do not match the deployment base URL")
        _validate_files(self.deployment.base_url, self.deployment.files, file_specs(None))

    @property
    def release(self):
        return None

    @classmethod
    def from_dict(cls, value: object) -> WithdrawalPublicationReport:
        if not isinstance(value, Mapping):
            raise PublicationError("deployment report must be an object")
        feed = _feed(value)
        if feed.release is not None:
            raise PublicationError("version 2 requires a withdrawal with release: null")
        try:
            return cls(feed, _links(value["links"]), WithdrawalDeployment.from_dict(value["deployment"]))
        except KeyError as error:
            raise PublicationError(f"invalid deployment report: {error}") from error

    def to_dict(self) -> dict[str, object]:
        return {**self.feed.to_dict(), "links": self.links.to_dict(), "deployment": self.deployment.to_dict()}


PublicationReport = ActivePublicationReport | WithdrawalPublicationReport


def publication_from_dict(value: object) -> PublicationReport:
    if not isinstance(value, Mapping):
        raise PublicationError("deployment report must be an object")
    deployment = value.get("deployment")
    if not isinstance(deployment, Mapping):
        raise PublicationError("invalid deployment report: missing deployment")
    version = deployment.get("schemaVersion")
    if version == 1:
        return ActivePublicationReport.from_dict(value)
    if version == 2:
        return WithdrawalPublicationReport.from_dict(value)
    raise PublicationError("unsupported deployment schema")
