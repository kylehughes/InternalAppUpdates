"""Verify that a prepared publication is served correctly over HTTPS."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import http.client
import math
from pathlib import Path
import re
import urllib.error
import urllib.request

from .deployment import _load_publication, load_publication
from .deployment_models import FileEntry
from .models import Links, MAX_IPA_BYTES, PublicationError, Release, https_url, validated_model

_CHUNK_SIZE = 1024 * 1024
_CACHE_TOKEN = re.compile(r"[!#$%&'*+.^_`|~0-9A-Za-z-]+\Z")


def _validate_totals(value: int, files: int) -> None:
    if type(value) is not int or not files <= value <= files * MAX_IPA_BYTES:
        raise PublicationError("invalid verified byte total")


@validated_model
@dataclass(frozen=True, slots=True)
class ActiveVerification:
    """A successful hosted-byte check for an active offer, at the time of the check."""

    release: Release
    links: Links
    bytes_verified: int

    def __post_init__(self) -> None:
        if type(self.release) is not Release or type(self.links) is not Links:
            raise PublicationError("active verification requires a release and publication links")
        if self.release.manifest_url != self.links.base_url + f"releases/{self.release.id}/manifest.plist":
            raise PublicationError("verified release does not match publication links")
        _validate_totals(self.bytes_verified, 5)

    def to_dict(self) -> dict:
        return {"verified": True, "release": self.release.to_dict(), "links": self.links.to_dict(),
                "filesVerified": 5, "bytesVerified": self.bytes_verified}


@validated_model
@dataclass(frozen=True, slots=True)
class WithdrawnVerification:
    """A successful hosted-byte check for the withdrawal page and feed."""

    links: Links
    bytes_verified: int

    def __post_init__(self) -> None:
        if type(self.links) is not Links:
            raise PublicationError("withdrawn verification requires publication links")
        _validate_totals(self.bytes_verified, 2)

    def to_dict(self) -> dict:
        return {"verified": True, "release": None, "links": self.links.to_dict(),
                "filesVerified": 2, "bytesVerified": self.bytes_verified}


def _verification_from_dict(value: dict) -> ActiveVerification | WithdrawnVerification:
    """Decode a serialized result without treating it as signing or installation evidence."""
    try:
        if type(value) is not dict or value["verified"] is not True:
            raise PublicationError("verification outcome must be successful")
        raw_links = value["links"]
        if type(raw_links) is not dict or type(raw_links["feedURL"]) is not str:
            raise PublicationError("invalid verification links")
        feed_url = raw_links["feedURL"]
        if not feed_url.endswith("/feed.json"):
            raise PublicationError("invalid verification feed URL")
        links = Links(feed_url[:-len("feed.json")])
        if raw_links != links.to_dict():
            raise PublicationError("verification links do not share a base URL")
        release = value["release"]
        count = 2 if release is None else 5
        if type(value["filesVerified"]) is not int or value["filesVerified"] != count:
            raise PublicationError("verification file count does not match its outcome")
        if release is None:
            return WithdrawnVerification(links, value["bytesVerified"])
        return ActiveVerification(Release.from_dict(release), links, value["bytesVerified"])
    except (KeyError, TypeError, ValueError) as error:
        raise PublicationError(f"invalid verification result: {error}") from error


class _HTTPSRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Follow ordinary redirects while keeping every hop on safe HTTPS."""

    def redirect_request(self, request, file_pointer, code, message, headers, new_url):
        try:
            https_url(new_url)
        except PublicationError as error:
            raise PublicationError(f"redirect is not a credential-free HTTPS URL: {new_url}") from error
        return super().redirect_request(request, file_pointer, code, message, headers, new_url)


def _content_type(headers) -> str:
    return headers.get("Content-Type", "").partition(";")[0].strip().lower()


def _verify_content_type(path: str, expected: str, headers) -> None:
    actual = _content_type(headers)
    accepted = {expected.lower()}
    if expected.lower() == "text/xml":
        accepted.add("application/xml")
    if actual not in accepted:
        raise PublicationError(
            f"verification failed for {path}: expected Content-Type {expected}, got {actual or 'none'}"
        )


def _cache_directives(value: str) -> dict[str, str | None]:
    directives: dict[str, str | None] = {}
    items: list[str] = []
    start = 0
    quoted = False
    escaped = False
    for index, character in enumerate(value):
        if escaped:
            escaped = False
        elif quoted and character == "\\":
            escaped = True
        elif character == '"':
            quoted = not quoted
        elif character == "," and not quoted:
            items.append(value[start:index])
            start = index + 1
    if quoted or escaped:
        raise PublicationError("verification failed: malformed Cache-Control header")
    items.append(value[start:])

    for raw_item in items:
        item = raw_item.strip()
        if not item:
            continue
        name, separator, argument = item.partition("=")
        name = name.strip()
        argument = argument.strip()
        if _CACHE_TOKEN.fullmatch(name) is None:
            raise PublicationError("verification failed: malformed Cache-Control header")
        if separator:
            if not argument:
                raise PublicationError("verification failed: malformed Cache-Control header")
            if argument.startswith('"'):
                if not argument.endswith('"'):
                    raise PublicationError("verification failed: malformed Cache-Control header")
                decoded = []
                escaped = False
                for character in argument[1:-1]:
                    code_point = ord(character)
                    valid = (character in "\t " or 0x21 <= code_point <= 0x7E
                             or 0x80 <= code_point <= 0xFF)
                    if escaped:
                        if not valid:
                            raise PublicationError("verification failed: malformed Cache-Control header")
                        decoded.append(character)
                        escaped = False
                    elif character == "\\":
                        escaped = True
                    elif character == '"' or not valid:
                        raise PublicationError("verification failed: malformed Cache-Control header")
                    else:
                        decoded.append(character)
                if escaped:
                    raise PublicationError("verification failed: malformed Cache-Control header")
                argument = "".join(decoded)
            elif _CACHE_TOKEN.fullmatch(argument) is None:
                raise PublicationError("verification failed: malformed Cache-Control header")
        if name:
            if name.lower() in directives:
                raise PublicationError(f"verification failed: duplicate Cache-Control directive {name}")
            directives[name.lower()] = argument if separator else None
    return directives


def _verify_cache_policy(path: str, expected: str, headers) -> None:
    actual = (", ".join(headers.get_all("Cache-Control", [])) if hasattr(headers, "get_all")
              else headers.get("Cache-Control", ""))
    directives = _cache_directives(actual)
    if expected == "public, max-age=31536000, immutable":
        try:
            max_age = int(directives.get("max-age") or "")
            shared_max_age = int(directives.get("s-maxage", str(max_age)) or "")
        except ValueError:
            max_age = 0
            shared_max_age = 0
        valid = (
            directives.get("immutable", "qualified") is None
            and directives.get("public", "qualified") is None
            and max_age >= 31536000
            and shared_max_age >= 31536000
            and "private" not in directives
            and "no-store" not in directives
            and "no-cache" not in directives
        )
    elif expected == "no-cache":
        valid = (
            directives.get("no-store", "qualified") is None
            or directives.get("no-cache", "qualified") is None
            or (directives.get("max-age") == "0" and directives.get("must-revalidate", "qualified") is None
                and directives.get("s-maxage", "0") == "0")
        ) and directives.get("s-maxage", "0") == "0"
    else:
        valid = False
    if not valid:
        raise PublicationError(
            f"verification failed for {path}: Cache-Control does not satisfy {expected!r}: {actual or 'none'}"
        )


def _read_and_verify(response, file: FileEntry) -> int:
    path = file.path
    status = getattr(response, "status", None)
    if status is None:
        status = response.getcode()
    if status != 200:
        raise PublicationError(f"verification failed for {path}: HTTP {status}")
    _verify_content_type(path, file.content_type, response.headers)
    _verify_cache_policy(path, file.cache_control, response.headers)

    expected_size = file.size
    digest = hashlib.sha256()
    received = 0
    while received <= expected_size:
        chunk = response.read(min(_CHUNK_SIZE, expected_size - received + 1))
        if not chunk:
            break
        received += len(chunk)
        digest.update(chunk)
    if received != expected_size:
        qualifier = f"at least {received}" if received > expected_size else str(received)
        raise PublicationError(
            f"verification failed for {path}: expected {expected_size} bytes, received {qualifier}"
        )
    actual_digest = digest.hexdigest()
    if not re.fullmatch(r"[0-9a-f]{64}", actual_digest) or actual_digest != file.sha256.lower():
        raise PublicationError(f"verification failed for {path}: SHA-256 digest does not match")
    return received


def _open_url(request: urllib.request.Request, timeout: float):
    return urllib.request.build_opener(_HTTPSRedirectHandler()).open(request, timeout=timeout)


def _verify_publication(publication_path: Path, *, timeout: float = 30) -> ActiveVerification | WithdrawnVerification:
    """Download and verify every file described by a publication report."""
    try:
        valid_timeout = math.isfinite(timeout) and timeout > 0
    except (TypeError, ValueError):
        valid_timeout = False
    if not valid_timeout:
        raise PublicationError("verification timeout must be finite and greater than zero")
    publication = _load_publication(Path(publication_path))
    total = 0
    files = publication.deployment.files
    for file in files:
        request = urllib.request.Request(
            file.url,
            headers={"Accept-Encoding": "identity"},
            method="GET",
        )
        try:
            with _open_url(request, timeout) as response:
                total += _read_and_verify(response, file)
        except PublicationError:
            raise
        except urllib.error.HTTPError as error:
            raise PublicationError(
                f"verification failed for {file.path}: HTTP {error.code}"
            ) from error
        except (urllib.error.URLError, OSError, http.client.HTTPException) as error:
            reason = getattr(error, "reason", error)
            raise PublicationError(
                f"verification failed for {file.path}: could not download {file.url}: {reason}"
            ) from error
    if publication.release is None:
        return WithdrawnVerification(publication.links, total)
    return ActiveVerification(publication.release, publication.links, total)


def verify_publication(publication_path: Path, *, timeout: float = 30) -> dict:
    """Download and verify every file described by a publication report."""
    return _verify_publication(publication_path, timeout=timeout).to_dict()
