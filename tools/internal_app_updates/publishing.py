"""Validate and publish signed IPA files as an immutable static update feed."""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import replace
from datetime import UTC, datetime
from html import escape
import hashlib
import json
import os
from pathlib import Path
import plistlib
import re
import shutil
import subprocess
import tempfile
from urllib.parse import quote
import zipfile
import zlib

from .models import (Feed, IPAInfo, Links, MAX_FEED_BYTES, MAX_IPA_BYTES,
                     MetadataInspection, PublicationError, Release, VerifiedIPA,
                     base_url, load_feed_or_release_json, version_key)

MAX_UNCOMPRESSED_BYTES = 4 * 1024 * 1024 * 1024


def _sha256(path: Path) -> str:
    with path.open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


def _safe_member(name: str) -> bool:
    p = Path(name)
    return not p.is_absolute() and ".." not in p.parts and "\\" not in name


def _validated_zip(ipa: Path) -> tuple[zipfile.ZipFile, str]:
    try:
        if not ipa.is_file() or ipa.suffix.lower() != ".ipa":
            raise PublicationError(f"IPA does not exist or is not an .ipa file: {ipa}")
        if ipa.stat().st_size > MAX_IPA_BYTES:
            raise PublicationError("IPA exceeds the 2 GiB safety limit")
        archive = zipfile.ZipFile(ipa)
    except PublicationError:
        raise
    except (OSError, EOFError, RuntimeError, NotImplementedError, zipfile.BadZipFile, zlib.error) as error:
        raise PublicationError(f"could not open IPA archive: {error}") from error
    try:
        total = 0
        apps: set[str] = set()
        for item in archive.infolist():
            if not _safe_member(item.filename):
                raise PublicationError("IPA contains an unsafe archive path")
            total += item.file_size
            if total > MAX_UNCOMPRESSED_BYTES:
                raise PublicationError("IPA exceeds the 4 GiB extracted safety limit")
            parts = item.filename.split("/")
            if len(parts) >= 3 and parts[0] == "Payload" and parts[1].endswith(".app"):
                apps.add("Payload/" + parts[1] + "/")
        if len(apps) != 1:
            raise PublicationError("IPA must contain exactly one top-level Payload/*.app")
        return archive, next(iter(apps))
    except PublicationError:
        archive.close()
        raise
    except (OSError, EOFError, RuntimeError, NotImplementedError, zipfile.BadZipFile, zlib.error) as error:
        archive.close()
        raise PublicationError(f"could not read IPA archive: {error}") from error
    except BaseException:
        archive.close()
        raise


def _extract_safely(archive: zipfile.ZipFile, target: Path) -> None:
    try:
        root = target.resolve()
        for item in archive.infolist():
            if item.is_dir():
                continue
            # Unix symlinks can lead extraction outside target.
            if (item.external_attr >> 16) & 0o170000 == 0o120000:
                raise PublicationError("IPA contains a symbolic link")
            destination = (target / item.filename).resolve()
            if not destination.is_relative_to(root):
                raise PublicationError("IPA extraction would escape its temporary directory")
            destination.parent.mkdir(parents=True, exist_ok=True)
            with archive.open(item) as source, destination.open("wb") as output:
                shutil.copyfileobj(source, output)
    except PublicationError:
        raise
    except (OSError, EOFError, RuntimeError, NotImplementedError, zipfile.BadZipFile, zlib.error) as error:
        raise PublicationError(f"could not extract IPA archive: {error}") from error


def _inspect_metadata(ipa: Path) -> MetadataInspection:
    archive, app_directory = _validated_zip(ipa)
    try:
        try:
            raw = archive.read(app_directory + "Info.plist")
            info = plistlib.loads(raw)
        except (KeyError, ValueError, plistlib.InvalidFileException) as error:
            raise PublicationError("IPA app has no readable Info.plist") from error
        except (OSError, EOFError, RuntimeError, NotImplementedError, zipfile.BadZipFile, zlib.error) as error:
            raise PublicationError(f"could not read IPA archive: {error}") from error
    finally:
        archive.close()
    required = ("CFBundleIdentifier", "CFBundleShortVersionString", "CFBundleVersion", "MinimumOSVersion")
    if not isinstance(info, dict) or any(not isinstance(info.get(key), str) or not info[key] for key in required):
        raise PublicationError("IPA Info.plist is missing required bundle metadata")
    app_bundle_name = app_directory.rsplit("/", 2)[1]
    preferred_name = next((value for key in ("CFBundleDisplayName", "CFBundleName")
                           if isinstance((value := info.get(key)), str) and value.strip()),
                          Path(app_bundle_name).stem)
    try:
        artifact_id = _sha256(ipa)
    except OSError as error:
        raise PublicationError(f"could not read IPA: {error}") from error
    return MetadataInspection(IPAInfo(info["CFBundleIdentifier"], info["CFBundleShortVersionString"], info["CFBundleVersion"],
                                      info["MinimumOSVersion"], artifact_id, preferred_name, legacy_app_name=app_bundle_name))


def _read_info(ipa: Path) -> IPAInfo:
    """Compatibility metadata adapter; it does not validate IPA signing."""
    return _inspect_metadata(ipa).info


def inspect_ipa(ipa: Path, *, verify_signature: bool = True) -> IPAInfo:
    """Read metadata and, by default, validate its macOS signing and provisioning."""
    inspection = _inspect_metadata(ipa)
    if not verify_signature:
        return inspection.info
    return _verify_metadata(ipa, inspection).info


def _verify_metadata(ipa: Path, inspection: MetadataInspection) -> VerifiedIPA:
    if os.uname().sysname != "Darwin":
        raise PublicationError("signature verification requires macOS (use inspect --no-verify only for metadata inspection)")
    archive, app_directory = _validated_zip(ipa)
    with tempfile.TemporaryDirectory(prefix="internal-app-updates-ipa-") as directory:
        target = Path(directory)
        try:
            _extract_safely(archive, target)
        finally:
            archive.close()
        app = target / app_directory.rstrip("/")
        expires_at = _verify_signing(app, inspection.info)
    return VerifiedIPA(replace(inspection.info, profile_expires_at=expires_at))


def _verified_ipa(ipa: Path) -> VerifiedIPA:
    return _verify_metadata(ipa, _inspect_metadata(ipa))


def _run(command: list[str]) -> subprocess.CompletedProcess[str]:
    try:
        result = subprocess.run(command, check=False, capture_output=True, text=True)
    except OSError as error:
        raise PublicationError(f"could not run {command[0]}: {error}") from error
    if result.returncode:
        raise PublicationError((result.stderr or result.stdout or f"{command[0]} failed").strip())
    return result


def _profile(path: Path) -> dict:
    if not path.is_file():
        raise PublicationError(f"missing embedded provisioning profile in {path.parent.name}")
    result = _run(["security", "cms", "-D", "-i", str(path)])
    try:
        profile = plistlib.loads(result.stdout.encode())
    except plistlib.InvalidFileException as error:
        raise PublicationError("embedded provisioning profile is not a readable CMS plist") from error
    if type(profile) is not dict:
        raise PublicationError("embedded provisioning profile is not a readable CMS plist")
    return profile


def _verify_signing(app: Path, info: IPAInfo) -> str:
    _run(["codesign", "--verify", "--deep", "--strict", str(app)])
    def entitlements_for(target: Path) -> dict:
        result = _run(["codesign", "-d", "--entitlements", ":-", str(target)])
        # codesign writes identity diagnostics to stderr and the plist to stdout.
        entitlement_output = result.stdout or result.stderr
        start = entitlement_output.find("<?xml")
        if start < 0:
            start = entitlement_output.find("<plist")
        try:
            entitlements = plistlib.loads(entitlement_output[start:].encode())
        except (ValueError, plistlib.InvalidFileException) as error:
            raise PublicationError("could not read signed application entitlements") from error
        if type(entitlements) is not dict:
            raise PublicationError("could not read signed application entitlements")
        return entitlements
    # Each nested app bundle has its own signing and profile contract. Enumerate bundles,
    # rather than only profiles, so a missing nested profile cannot be silently skipped.
    bundles = [app, *sorted((path for path in app.rglob("*") if path.is_dir() and path.suffix in {".app", ".appex"}), key=str)]
    device_sets: list[set[str]] = []
    expirations: list[datetime] = []
    for bundle in bundles:
        profile_path = bundle / "embedded.mobileprovision"
        bundle_info = info
        if bundle != app:
            try:
                extension = plistlib.loads((bundle / "Info.plist").read_bytes())
                if type(extension) is not dict:
                    raise PublicationError(f"extension {bundle.name} has no readable Info.plist")
                bundle_info = replace(info, bundle_identifier=extension["CFBundleIdentifier"])
            except (OSError, KeyError, plistlib.InvalidFileException, PublicationError):
                raise PublicationError(f"extension {bundle.name} has no readable Info.plist")
        entitlements = entitlements_for(bundle)
        profile = _profile(profile_path)
        expiry = profile.get("ExpirationDate")
        if not isinstance(expiry, datetime):
            raise PublicationError("embedded provisioning profile is expired or malformed")
        expiry = expiry.replace(tzinfo=UTC) if expiry.tzinfo is None else expiry.astimezone(UTC)
        if expiry <= datetime.now(UTC):
            raise PublicationError("embedded provisioning profile is expired or malformed")
        expirations.append(expiry)
        devices = profile.get("ProvisionedDevices")
        if (not isinstance(devices, list) or not devices
                or any(type(device) is not str or not device for device in devices)):
            raise PublicationError("embedded profile is not a development or ad-hoc device profile")
        device_sets.append(set(devices))
        profile_entitlements = profile.get("Entitlements", {})
        if type(profile_entitlements) is not dict:
            raise PublicationError("embedded profile lacks application identity entitlements")
        app_identifier = profile_entitlements.get("application-identifier")
        signed_identifier = entitlements.get("application-identifier")
        team = profile_entitlements.get("com.apple.developer.team-identifier")
        if (type(app_identifier) is not str or not app_identifier
                or type(team) is not str or not team):
            raise PublicationError("embedded profile lacks application identity entitlements")
        expected_signed_identifier = team + "." + bundle_info.bundle_identifier
        wildcard_prefix = app_identifier[:-1] if app_identifier.endswith("*") else None
        profile_matches_bundle = signed_identifier == expected_signed_identifier and (
            app_identifier == expected_signed_identifier or
            (wildcard_prefix is not None and wildcard_prefix.startswith(team + ".") and
             expected_signed_identifier.startswith(wildcard_prefix)))
        if not profile_matches_bundle:
            raise PublicationError("signed application identifier does not match embedded profile")
        if entitlements.get("com.apple.developer.team-identifier") != team:
            raise PublicationError("signed team identifier does not match embedded profile")
        certificates = profile.get("DeveloperCertificates")
        if not isinstance(certificates, list) or not all(isinstance(value, bytes) for value in certificates):
            raise PublicationError("embedded profile lacks developer certificates")
        with tempfile.TemporaryDirectory(prefix="internal-app-updates-cert-") as certificate_dir:
            prefix = str(Path(certificate_dir) / "certificate")
            _run(["codesign", "-d", "--extract-certificates=" + prefix, str(bundle)])
            leaf = Path(prefix + "0")
            if not leaf.is_file() or leaf.read_bytes() not in certificates:
                raise PublicationError("signing certificate is not present in embedded profile")
    if not device_sets or not set.intersection(*device_sets):
        raise PublicationError("embedded profiles have no eligible device in common")
    return min(expirations).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _base_url(value: str) -> str:
    return base_url(value)


def links_for_base_url(base_url: str) -> dict[str, str]:
    """Return the human installation page and machine update feed at an HTTPS base URL."""
    return Links(base_url).to_dict()


def publication_links(output: Path) -> dict[str, str]:
    """Read links from an active publication without modifying files or contacting its host."""
    output = Path(output)
    try:
        document = _read_feed(output / "feed.json")
    except PublicationError as error:
        raise PublicationError(f"cannot read links: {error}") from error
    release = document.release
    if release is None:
        raise PublicationError("cannot read links: no build is currently published")
    suffix = f"/releases/{release.id}/manifest.plist"
    if not release.manifest_url.endswith(suffix):
        raise PublicationError("cannot read links: manifest URL does not use the publisher's release layout")
    links = links_for_base_url(release.manifest_url[:-len(suffix)])
    if not (output / "index.html").is_file():
        raise PublicationError("cannot read links: the installation page is missing")
    return links


def _version_key(value: str) -> tuple[int, int, int]:
    return version_key(value)


def _release_history(output: Path) -> tuple[Release, ...]:
    releases = output / "releases"
    if not releases.is_dir():
        return ()
    history = []
    release_directories = {metadata.parent for metadata in releases.glob("*/release.json")}
    release_directories.update(
        path for path in releases.iterdir()
        if path.is_dir() and re.fullmatch(r"[0-9a-f]{64}", path.name)
    )
    for release_directory in sorted(release_directories):
        metadata = release_directory / "release.json"
        try:
            release = Release.from_dict(load_feed_or_release_json(metadata.read_text(encoding="utf-8")))
            if release.id != release_directory.name:
                raise PublicationError("release identity does not match its immutable directory")
        except (OSError, ValueError, json.JSONDecodeError, PublicationError) as error:
            raise PublicationError(f"invalid release history at {metadata}") from error
        history.append(release)
    return tuple(history)


def _feed_bytes(document: Feed) -> bytes:
    contents = json.dumps(document.to_dict(), sort_keys=True, indent=2).encode() + b"\n"
    if len(contents) > MAX_FEED_BYTES:
        raise PublicationError("feed.json exceeds the 256 KiB limit")
    return contents


@contextmanager
def _lock(output: Path):
    import fcntl
    output.mkdir(parents=True, exist_ok=True)
    with (output / ".publish.lock").open("a+") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        yield


def _atomic_write(path: Path, contents: bytes) -> None:
    temporary = path.with_name("." + path.name + ".tmp")
    temporary.write_bytes(contents)
    os.replace(temporary, path)


def _read_feed(path: Path) -> Feed:
    try:
        value = load_feed_or_release_json(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise PublicationError(f"could not read existing feed: {error}") from error
    return Feed.from_dict(value)


def _manifest(info: IPAInfo, package_url: str) -> bytes:
    return plistlib.dumps({"items": [{"assets": [{"kind": "software-package", "url": package_url}], "metadata": {
        "bundle-identifier": info.bundle_identifier, "bundle-version": info.build,
        "kind": "software", "title": info.app_name,
    }}]}, fmt=plistlib.FMT_XML, sort_keys=False)


def _manifest_is_canonical(contents: bytes, info: IPAInfo, package_url: str) -> bool:
    if contents == _manifest(info, package_url):
        return True
    return (info.legacy_app_name is not None
            and info.legacy_app_name != info.app_name
            and contents == _manifest(replace(info, app_name=info.legacy_app_name), package_url))


def _html(feed_url: str, release: Release | None) -> bytes:
    body = "<p>No build is currently published.</p>"
    if release is not None:
        notes = release.release_notes or ""
        body = (
            f"<p class=version>Version {escape(release.version)} &middot; Build {escape(release.build)}</p>"
            + (f"<p class=notes>{escape(notes)}</p>" if notes else "")
            + f"<a class=install href=\"itms-services://?action=download-manifest&amp;url={quote(release.manifest_url, safe='')}\">Install Build</a>"
            + "<p class=help>Open this page on your iPhone or iPad. iOS will ask you to confirm installation. "
            + "The build must be signed for your device.</p>"
        )
    return ("""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="color-scheme" content="light dark"><title>Internal App Updates</title>
<style>
:root { font: 17px/1.5 -apple-system, BlinkMacSystemFont, sans-serif; color-scheme: light dark; }
body { margin: 0; padding: 5rem 1.25rem; background: light-dark(#f3f5fa, #14161d); }
main { max-width: 30rem; margin: auto; padding: 2rem; border-radius: 1.5rem;
  background: light-dark(white, #222530); box-shadow: 0 1rem 4rem #18254b10; }
.icon { font-size: 2.5rem; color: light-dark(#2458d5, #8cabff); }
h1 { font-size: 2rem; letter-spacing: -.04em; margin: .5rem 0; }
.version { font-variant-numeric: tabular-nums; color: light-dark(#596275, #bbc1d0); }
.notes { white-space: pre-wrap; overflow-wrap: anywhere; }
a { color: light-dark(#2458d5, #8cabff); }
.install { display: block; text-align: center; margin: 1.75rem 0 1rem; padding: .85rem;
  border-radius: .8rem; background: #2458d5; color: white; text-decoration: none; font-weight: 600; }
.install:focus-visible { outline: 3px solid #ed9e43; outline-offset: 3px; }
.help, footer { font-size: .85rem; color: light-dark(#626a7b, #adb5c7); }
footer { margin-top: 2rem; }
</style></head><body><main><div class="icon" aria-hidden="true">&#8595;</div>
<h1>Internal App Updates</h1>""" + body
            + f"<footer><a href=\"{escape(feed_url, quote=True)}\">Update feed</a></footer>"
            + "</main></body></html>\n").encode()


def _publish(ipa: Path, output: Path, base_url: str, *, notes: str | None = None, verifier=inspect_ipa) -> Feed:
    """Publish a verified IPA, committing feed.json only after all immutable assets exist."""
    base_url = _base_url(base_url)
    output = Path(output)
    with _lock(output):
        snapshot_parent = Path(tempfile.mkdtemp(prefix=".snapshot-", dir=output))
        try:
            snapshot = snapshot_parent / "source.ipa"
            try:
                shutil.copy2(ipa, snapshot)
            except OSError as error:
                raise PublicationError(f"could not snapshot IPA: {error}") from error
            result = _verified_ipa(snapshot) if verifier is inspect_ipa else verifier(snapshot)
            if type(result) is VerifiedIPA:
                verified = result
            elif type(result) is IPAInfo and result.profile_expires_at is not None:
                # The injection point is a trusted compatibility boundary for existing callers.
                verified = VerifiedIPA(result)
            else:
                raise PublicationError("signature verifier must return IPAInfo")
            info = verified.info
            if info.profile_expires_at is None:
                raise PublicationError("IPA metadata inspection cannot be published without signature verification")
            if _sha256(snapshot) != info.artifact_id:
                raise PublicationError("signature verifier identity does not match the IPA snapshot")
            manifest_url = f"{base_url}/releases/{info.artifact_id}/manifest.plist"
            package_url = f"{base_url}/releases/{info.artifact_id}/app.ipa"
            release = Release(info.artifact_id, info.version, info.build, info.minimum_os_version or "",
                              datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
                              info.profile_expires_at, manifest_url, notes if notes else None)
            document = Feed(info.bundle_identifier, release)
            feed_contents = _feed_bytes(document)
            feed_path = output / "feed.json"
            existing = _read_feed(feed_path) if feed_path.exists() else None
            if existing and existing.bundle_identifier != info.bundle_identifier:
                raise PublicationError("feed belongs to a different bundle identifier")
            current = existing.release if existing else None
            history = _release_history(output)
            for prior in history:
                if prior.key == release.key and prior.id != info.artifact_id:
                    raise PublicationError("a different IPA already uses this version and build")
            if history and release.key < max(prior.key for prior in history):
                raise PublicationError("release is older than a previously published release")
            if current:
                if current.key == release.key and current.id != info.artifact_id:
                    raise PublicationError("a different IPA already uses this version and build")
                if release.key < current.key:
                    raise PublicationError("release is older than the currently published release")
                if current.id == info.artifact_id:
                    immutable = output / "releases" / info.artifact_id
                    if (not (immutable / "app.ipa").is_file() or _sha256(immutable / "app.ipa") != info.artifact_id
                            or not (immutable / "manifest.plist").is_file()):
                        raise PublicationError("feed references an incomplete immutable release")
                    try:
                        manifest_contents = (immutable / "manifest.plist").read_bytes()
                        saved_url = plistlib.loads(manifest_contents)["items"][0]["assets"][0]["url"]
                    except (OSError, ValueError, KeyError, IndexError, plistlib.InvalidFileException) as error:
                        raise PublicationError("feed references an unreadable immutable manifest") from error
                    if saved_url != package_url:
                        raise PublicationError("immutable release manifest conflicts with the requested base URL")
                    if not _manifest_is_canonical(manifest_contents, info, package_url):
                        raise PublicationError("immutable release manifest differs from the verified app")
                    try:
                        saved_release = Release.from_dict(load_feed_or_release_json((immutable / "release.json").read_bytes()))
                        if saved_release != current:
                            raise PublicationError("release metadata differs from the feed")
                    except (OSError, ValueError, PublicationError) as error:
                        raise PublicationError("feed references missing or inconsistent immutable release metadata") from error
                    if replace(release, published_at=current.published_at) != current:
                        raise PublicationError(
                            "active release metadata conflicts; retry with the original publication options"
                        )
                    return Feed(info.bundle_identifier, current)
            release_dir = output / "releases" / info.artifact_id
            releases = output / "releases"
            releases.mkdir(exist_ok=True)
            if release_dir.exists():
                try:
                    manifest_contents = (release_dir / "manifest.plist").read_bytes()
                    manifest = plistlib.loads(manifest_contents)
                    saved_url = manifest["items"][0]["assets"][0]["url"]
                except (OSError, ValueError, KeyError, IndexError, plistlib.InvalidFileException) as error:
                    raise PublicationError("immutable release directory has an unreadable manifest") from error
                if (not (release_dir / "app.ipa").is_file() or _sha256(release_dir / "app.ipa") != info.artifact_id
                        or saved_url != package_url):
                    raise PublicationError("immutable release directory conflicts with the requested URL or artifact")
                if not _manifest_is_canonical(manifest_contents, info, package_url):
                    raise PublicationError("immutable release manifest differs from the verified app")
                try:
                    saved_release = Release.from_dict(load_feed_or_release_json((release_dir / "release.json").read_bytes()))
                    if replace(release, published_at=saved_release.published_at) != saved_release:
                        raise ValueError("metadata differs")
                except (OSError, ValueError, PublicationError) as error:
                    raise PublicationError("immutable release metadata conflicts; retry with the original publication options") from error
                release = saved_release
                document = Feed(info.bundle_identifier, release)
                feed_contents = _feed_bytes(document)
            else:
                stage_parent = Path(tempfile.mkdtemp(prefix=".publish-", dir=output))
                try:
                    stage = stage_parent / info.artifact_id
                    stage.mkdir()
                    shutil.copy2(snapshot, stage / "app.ipa")
                    _atomic_write(stage / "manifest.plist", _manifest(info, package_url))
                    _atomic_write(stage / "release.json", json.dumps(release.to_dict(), sort_keys=True, indent=2).encode() + b"\n")
                    os.replace(stage, release_dir)
                finally:
                    shutil.rmtree(stage_parent, ignore_errors=True)
            _atomic_write(output / "index.html", _html(f"{base_url}/feed.json", release))
            # The feed is the commit point: clients only see the new artifact after it exists.
            _atomic_write(feed_path, feed_contents)
            return document
        finally:
            shutil.rmtree(snapshot_parent, ignore_errors=True)


def publish(ipa: Path, output: Path, base_url: str, *, notes: str | None = None, verifier=inspect_ipa) -> dict:
    """Publish a verified IPA, returning the established JSON-compatible shape."""
    return _publish(ipa, output, base_url, notes=notes, verifier=verifier).to_dict()


def _withdraw(output: Path, base_url: str | None = None) -> Feed:
    output = Path(output)
    url = _base_url(base_url) if base_url else "feed.json"
    with _lock(output):
        feed_path = output / "feed.json"
        if not feed_path.exists():
            raise PublicationError("cannot withdraw: feed.json does not exist")
        old = _read_feed(feed_path)
        if base_url is not None and old.release is not None:
            expected_manifest_url = f"{url}/releases/{old.release.id}/manifest.plist"
            if old.release.manifest_url != expected_manifest_url:
                raise PublicationError("active release belongs to a different base URL")
        document = Feed(old.bundle_identifier, None)
        _atomic_write(output / "index.html", _html(f"{url}/feed.json" if base_url else url, None))
        _atomic_write(feed_path, _feed_bytes(document))
        return document


def withdraw(output: Path, base_url: str | None = None) -> dict:
    """Withdraw the active release, returning the established JSON-compatible shape."""
    return _withdraw(output, base_url).to_dict()
