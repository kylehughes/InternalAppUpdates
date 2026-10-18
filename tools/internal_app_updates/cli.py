"""Command line interface for publishing development and ad-hoc iOS builds."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

from .exporting import ExportError, export_archive
from .deployment import publication_report
from .publishing import (
    PublicationError,
    inspect_ipa,
    publication_links,
    publish,
    withdraw,
)


def _notes(args: argparse.Namespace) -> str | None:
    if args.notes and args.notes_file:
        raise PublicationError("use either --notes or --notes-file, not both")
    if args.notes_file:
        try:
            return Path(args.notes_file).read_text(encoding="utf-8")
        except OSError as error:
            raise PublicationError(f"could not read notes file: {error}") from error
    return args.notes


def _emit(value: object, as_json: bool) -> None:
    if as_json:
        print(json.dumps(value, sort_keys=True))
    elif isinstance(value, dict) and value.get("verified") is True:
        if value["release"] is None:
            print("verified withdrawal on host")
            print(f"page: {value['links']['installPageURL']}")
        else:
            print(f"verified {value['release']['version']} ({value['release']['build']}) on host")
            print(f"install: {value['links']['installPageURL']}")
        print(f"feed: {value['links']['feedURL']}")
    elif isinstance(value, dict) and "release" in value:
        release = value["release"]
        if release is None:
            print("withdrawn")
        else:
            print(f"published {release['version']} ({release['build']})")
            links = value.get("links")
            if links:
                print(f"install: {links['installPageURL']}")
                print(f"feed: {links['feedURL']}")
    elif isinstance(value, dict) and {"installPageURL", "feedURL"} <= value.keys():
        print(f"install: {value['installPageURL']}")
        print(f"feed: {value['feedURL']}")
    else:
        print(value)


def _publish(args: argparse.Namespace) -> dict:
    output = Path(args.output)
    publication = publish(Path(args.ipa), output, args.base_url, notes=_notes(args))
    return publication_report(output, publication, args.base_url)


def _verify(args: argparse.Namespace) -> dict:
    from .verification import verify_publication
    return verify_publication(Path(args.publication), timeout=args.timeout)


def _inspect(args: argparse.Namespace) -> dict:
    info = inspect_ipa(Path(args.ipa), verify_signature=not args.no_verify)
    return {"bundleIdentifier": info.bundle_identifier, "version": info.version, "build": info.build,
            "minimumOSVersion": info.minimum_os_version, "id": info.artifact_id,
            "signatureValidated": not args.no_verify}


def _export(args: argparse.Namespace) -> dict:
    output = export_archive(Path(args.archive), Path(args.export_options), Path(args.output),
                            allow_provisioning_updates=args.allow_provisioning_updates)
    return {"output": str(output)}


def _withdraw(args: argparse.Namespace) -> dict:
    if args.json and not args.base_url:
        raise PublicationError("withdraw --json requires --base-url to prepare a deployment report")
    output = Path(args.output)
    publication = withdraw(output, args.base_url)
    return publication_report(output, publication, args.base_url) if args.base_url else publication


def _links(args: argparse.Namespace) -> dict:
    return publication_links(Path(args.output))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="internal-app-updates", description="Publish signed iOS builds with install links and HTTPS update feeds.")
    parser.add_argument("--json", action="store_true", help="write structured JSON for artifact commands to standard output")
    commands = parser.add_subparsers(dest="command", required=True)
    publish_parser = commands.add_parser("publish", help="publish a signed IPA with an install page and update feed")
    publish_parser.add_argument("--json", action="store_true", default=argparse.SUPPRESS,
                                help="write a publication report with links and ordered deployment files")
    publish_parser.add_argument("--ipa", required=True)
    publish_parser.add_argument("--output", required=True)
    publish_parser.add_argument("--base-url", required=True, help="externally reachable HTTPS base URL")
    group = publish_parser.add_mutually_exclusive_group()
    group.add_argument("--notes")
    group.add_argument("--notes-file")
    publish_parser.set_defaults(func=_publish)
    inspect_parser = commands.add_parser("inspect", help="inspect and verify an IPA")
    inspect_parser.add_argument("--json", action="store_true", default=argparse.SUPPRESS, help=argparse.SUPPRESS)
    inspect_parser.add_argument("--ipa", required=True)
    inspect_parser.add_argument("--no-verify", action="store_true", help="read metadata without signature verification")
    inspect_parser.set_defaults(func=_inspect)
    export_parser = commands.add_parser("export", help="export an existing .xcarchive with xcodebuild")
    export_parser.add_argument("--json", action="store_true", default=argparse.SUPPRESS, help=argparse.SUPPRESS)
    export_parser.add_argument("--archive", required=True)
    export_parser.add_argument("--export-options", required=True)
    export_parser.add_argument("--output", required=True)
    export_parser.add_argument("--allow-provisioning-updates", action="store_true",
                               help="allow Xcode to contact Apple and update provisioning")
    export_parser.set_defaults(func=_export)
    withdraw_parser = commands.add_parser("withdraw", help="remove the current release from a feed")
    withdraw_parser.add_argument("--json", action="store_true", default=argparse.SUPPRESS,
                                 help="write a withdrawal deployment report; requires --base-url")
    withdraw_parser.add_argument("--output", required=True)
    withdraw_parser.add_argument("--base-url", help="externally reachable HTTPS base URL for the deployment report")
    withdraw_parser.set_defaults(func=_withdraw)
    links_parser = commands.add_parser("links", help="show URLs for the published install page and update feed",
                                      description="Read an existing publication's URLs without publishing again.")
    links_parser.add_argument("--json", action="store_true", default=argparse.SUPPRESS,
                              help="write installPageURL and feedURL as JSON")
    links_parser.add_argument("--output", required=True, help="directory containing the published site")
    links_parser.set_defaults(func=_links)
    verify_parser = commands.add_parser("verify", help="verify hosted files against a saved publication report")
    verify_parser.add_argument("--json", action="store_true", default=argparse.SUPPRESS,
                               help="write structured verification results")
    verify_parser.add_argument("--publication", required=True, help="JSON saved from publish --json or withdraw --json")
    verify_parser.add_argument("--timeout", type=float, default=30,
                               help="network operation timeout in seconds (default: 30)")
    verify_parser.set_defaults(func=_verify)
    from . import server
    server.register_parser(commands)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        result = args.func(args)
    except (PublicationError, ExportError) as error:
        if args.json:
            print(json.dumps({"error": str(error)}))
        else:
            print(f"error: {error}", file=sys.stderr)
        return 1
    if isinstance(result, int):
        return result
    _emit(result, args.json)
    return 0
