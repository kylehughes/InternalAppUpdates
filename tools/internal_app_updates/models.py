"""Immutable values used by the internal update publisher."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from functools import wraps
import json
from pathlib import Path
import math
import re
from typing import TypeVar
from urllib.parse import urlsplit


MAX_IPA_BYTES = 2 * 1024 * 1024 * 1024
MAX_FEED_BYTES = 256 * 1024


class PublicationError(RuntimeError):
    """The requested artifact cannot safely be published."""


def _unique_object_members(pairs: list[tuple[str, object]]) -> dict:
    result = {}
    for key, value in pairs:
        if key in result:
            raise PublicationError(f"JSON object has a duplicate member: {key}")
        result[key] = value
    return result


def load_feed_or_release_json(document: str | bytes | bytearray) -> object:
    """Decode a feed or immutable release document without discarding duplicate members."""
    return json.loads(document, object_pairs_hook=_unique_object_members)


_ID = re.compile(r"[0-9a-f]{64}\Z")
_DATE = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z\Z")


def version_key(value: str) -> tuple[int, int, int]:
    if type(value) is not str:
        raise PublicationError("version must be a string")
    parts = value.split(".")
    if not 1 <= len(parts) <= 3 or any(not part.isascii() or not part.isdecimal() for part in parts):
        raise PublicationError(f"version must be 1–3 numeric components: {value}")
    normalized = tuple(part.lstrip("0") or "0" for part in parts)
    if any(len(part) > 20 or (len(part) == 20 and part > "18446744073709551615") for part in normalized):
        raise PublicationError(f"version component exceeds UInt64: {value}")
    numbers = tuple(int(part) for part in normalized)
    return numbers + (0,) * (3 - len(parts))


def _canonical_date(value: str, field: str) -> datetime:
    if type(value) is not str or not _DATE.fullmatch(value):
        raise PublicationError(f"{field} must be a canonical UTC date")
    try:
        result = datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)
    except ValueError as error:
        raise PublicationError(f"{field} must be a canonical UTC date") from error
    if not math.isfinite(result.timestamp()):
        raise PublicationError(f"{field} must be finite")
    return result


def https_url(value: str) -> str:
    if type(value) is not str:
        raise PublicationError("URL must be a string")
    try:
        parsed = urlsplit(value)
        valid_port = parsed.port is None or 1 <= parsed.port <= 65535
        valid = (parsed.scheme == "https" and bool(parsed.hostname) and valid_port
                 and parsed.username is None and parsed.password is None
                 and "#" not in value
                 and not any(character.isspace() or ord(character) < 32 for character in value))
    except ValueError:
        valid = False
    if not valid:
        raise PublicationError("URL must be a credential-free HTTPS URL without a fragment")
    return value


def base_url(value: str) -> str:
    result = https_url(value)
    if "?" in result or "#" in result:
        raise PublicationError("base URL must be an HTTPS URL without credentials, query, or fragment")
    return result.rstrip("/")


Model = TypeVar("Model", bound=type)


def validated_model(cls: Model) -> Model:
    """Close ordinary construction routes for a frozen, slotted value object."""
    original_init = cls.__init__
    first_field = next(iter(cls.__dataclass_fields__))

    @wraps(original_init)
    def checked_init(self, *args, **kwargs):
        if hasattr(self, first_field):
            raise PublicationError(f"{cls.__name__} cannot be reinitialized")
        original_init(self, *args, **kwargs)

    def reject_subclass(subclass, **kwargs):
        raise TypeError(f"{cls.__name__} is final")

    cls.__init__ = checked_init
    cls.__init_subclass__ = classmethod(reject_subclass)
    return cls


@validated_model
@dataclass(frozen=True, slots=True)
class IPAInfo:
    bundle_identifier: str
    version: str
    build: str
    minimum_os_version: str | None
    artifact_id: str
    app_name: str
    extracted_app: Path | None = None
    profile_expires_at: str | None = None
    legacy_app_name: str | None = None

    def __post_init__(self) -> None:
        for field in ("bundle_identifier", "app_name"):
            if type(value := getattr(self, field)) is not str or not value:
                raise PublicationError(f"{field} must be a nonempty string")
        version_key(self.version)
        version_key(self.build)
        if self.minimum_os_version is not None:
            version_key(self.minimum_os_version)
        if type(self.artifact_id) is not str or not _ID.fullmatch(self.artifact_id):
            raise PublicationError("artifact_id must be a lowercase SHA-256 digest")
        if self.extracted_app is not None and (
                type(self.extracted_app) is not type(Path()) or "\0" in str(self.extracted_app)):
            raise PublicationError("extracted_app must be a path without NUL characters")
        if self.profile_expires_at is not None:
            _canonical_date(self.profile_expires_at, "profile_expires_at")
        if self.legacy_app_name is not None and (type(self.legacy_app_name) is not str or not self.legacy_app_name):
            raise PublicationError("legacy_app_name must be a nonempty string")


@validated_model
@dataclass(frozen=True, slots=True)
class MetadataInspection:
    """IPA bundle metadata read without relying on its signature."""
    info: IPAInfo

    def __post_init__(self) -> None:
        if type(self.info) is not IPAInfo or self.info.profile_expires_at is not None:
            raise PublicationError("metadata inspection must not claim signature verification")


@validated_model
@dataclass(frozen=True, slots=True)
class VerifiedIPA:
    """A signing result from the inspector or an explicitly trusted verifier."""
    info: IPAInfo

    def __post_init__(self) -> None:
        if type(self.info) is not IPAInfo or self.info.profile_expires_at is None:
            raise PublicationError("verified IPA requires a signing expiration")


@validated_model
@dataclass(frozen=True, slots=True)
class Release:
    id: str
    version: str
    build: str
    minimum_os_version: str
    published_at: str
    expires_at: str
    manifest_url: str
    release_notes: str | None = None

    def __post_init__(self) -> None:
        if type(self.id) is not str or not _ID.fullmatch(self.id):
            raise PublicationError("release id must be a lowercase SHA-256 digest")
        version_key(self.version)
        version_key(self.build)
        version_key(self.minimum_os_version)
        published = _canonical_date(self.published_at, "publishedAt")
        expires = _canonical_date(self.expires_at, "expiresAt")
        if published >= expires:
            raise PublicationError("release expiration must be after publication")
        https_url(self.manifest_url)
        if self.release_notes is not None and type(self.release_notes) is not str:
            raise PublicationError("releaseNotes must be a string")

    @property
    def key(self) -> tuple[tuple[int, int, int], tuple[int, int, int]]:
        return version_key(self.version), version_key(self.build)

    @classmethod
    def from_dict(cls, value: dict) -> Release:
        if type(value) is not dict:
            raise PublicationError("release must be an object")
        required = {"id", "version", "build", "minimumOSVersion", "publishedAt", "expiresAt", "manifestURL"}
        optional = {"releaseNotes"}
        if set(value) - required - optional or not required <= set(value):
            raise PublicationError("release has an unsupported or missing field")
        notes = value.get("releaseNotes")
        if "releaseNotes" in value and type(notes) is not str:
            raise PublicationError("releaseNotes must be a string")
        return cls(value["id"], value["version"], value["build"], value["minimumOSVersion"],
                   value["publishedAt"], value["expiresAt"], value["manifestURL"], notes)

    def to_dict(self) -> dict:
        result = {"id": self.id, "version": self.version, "build": self.build,
                  "minimumOSVersion": self.minimum_os_version, "publishedAt": self.published_at,
                  "expiresAt": self.expires_at, "manifestURL": self.manifest_url}
        if self.release_notes is not None:
            result["releaseNotes"] = self.release_notes
        return result


@validated_model
@dataclass(frozen=True, slots=True)
class Feed:
    bundle_identifier: str
    release: Release | None

    def __post_init__(self) -> None:
        if type(self.bundle_identifier) is not str or not self.bundle_identifier:
            raise PublicationError("bundleIdentifier must be a nonempty string")
        if self.release is not None and type(self.release) is not Release:
            raise PublicationError("release must be a Release or null")

    @classmethod
    def from_dict(cls, value: dict) -> Feed:
        if type(value) is not dict or set(value) != {"schemaVersion", "bundleIdentifier", "release"}:
            raise PublicationError("feed has an unsupported or missing field")
        if type(value["schemaVersion"]) is not int or value["schemaVersion"] != 1:
            raise PublicationError("unsupported feed schema version")
        release = value["release"]
        return cls(value["bundleIdentifier"], None if release is None else Release.from_dict(release))

    def to_dict(self) -> dict:
        return {"schemaVersion": 1, "bundleIdentifier": self.bundle_identifier,
                "release": None if self.release is None else self.release.to_dict()}


@validated_model
@dataclass(frozen=True, slots=True)
class Links:
    base_url: str

    def __post_init__(self) -> None:
        normalized = base_url(self.base_url)
        object.__setattr__(self, "base_url", normalized + "/")

    @property
    def install_page_url(self) -> str:
        return self.base_url + "index.html"

    @property
    def feed_url(self) -> str:
        return self.base_url + "feed.json"

    def to_dict(self) -> dict:
        return {"installPageURL": self.install_page_url, "feedURL": self.feed_url}
