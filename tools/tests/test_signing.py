from __future__ import annotations

from datetime import datetime
from pathlib import Path
import plistlib
import subprocess
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch
import zipfile

from internal_app_updates import publishing
from internal_app_updates.publishing import PublicationError, inspect_ipa


class SigningInspectionTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)

    def write_ipa(self, *, extension: bool = False, extension_profile: bool = True) -> Path:
        ipa = self.root / "App.ipa"
        app_info = {
            "CFBundleIdentifier": "example.app",
            "CFBundleShortVersionString": "1.0",
            "CFBundleVersion": "1",
            "MinimumOSVersion": "16.0",
        }
        with zipfile.ZipFile(ipa, "w") as archive:
            archive.writestr("Payload/App.app/Info.plist", plistlib.dumps(app_info))
            archive.writestr("Payload/App.app/embedded.mobileprovision", b"profile")
            if extension:
                archive.writestr("Payload/App.app/PlugIns/Widget.appex/Info.plist", plistlib.dumps({
                    "CFBundleIdentifier": "example.app.widget",
                }))
                if extension_profile:
                    archive.writestr("Payload/App.app/PlugIns/Widget.appex/embedded.mobileprovision", b"profile")
        return ipa

    def command_runner(self, *, expiry: datetime = datetime(2040, 1, 2, 3, 4, 5),
                       signed_identifier: str = "TEAM.example.app", verify_returncode: int = 0,
                       signed_team: str = "TEAM", profile_identifier: str = "TEAM.example.app",
                       profile_team: str = "TEAM", devices=None,
                       verify_stderr: str = "", os_failure: bool = False):
        commands: list[list[str]] = []
        profile = plistlib.dumps({
            "ExpirationDate": expiry,
            "ProvisionedDevices": ["device-1"] if devices is None else devices,
            "Entitlements": {
                "application-identifier": profile_identifier,
                "com.apple.developer.team-identifier": profile_team,
            },
            "DeveloperCertificates": [b"certificate"],
        }).decode()

        def run(command, **kwargs):
            commands.append(command)
            if command[:2] == ["codesign", "--verify"]:
                if os_failure:
                    raise OSError("codesign unavailable")
                return subprocess.CompletedProcess(command, verify_returncode, "", verify_stderr)
            if command[:4] == ["codesign", "-d", "--entitlements", ":-"]:
                target = Path(command[-1])
                identifier = signed_identifier
                if target.suffix == ".appex":
                    identifier = "TEAM.example.app.widget"
                return subprocess.CompletedProcess(command, 0, plistlib.dumps({
                    "application-identifier": identifier,
                    "com.apple.developer.team-identifier": signed_team,
                }).decode(), "")
            if command[:3] == ["security", "cms", "-D"]:
                return subprocess.CompletedProcess(command, 0, profile, "")
            if command[:3] == ["codesign", "-d", "--extract-certificates=unused"]:
                raise AssertionError("unexpected certificate output path")
            if command[:2] == ["codesign", "-d"] and command[2].startswith("--extract-certificates="):
                Path(command[2].split("=", 1)[1] + "0").write_bytes(b"certificate")
                return subprocess.CompletedProcess(command, 0, "", "")
            raise AssertionError(f"unexpected signing command: {command}")

        return commands, run

    def inspect_with_runner(self, ipa: Path, **runner_arguments):
        commands, run = self.command_runner(**runner_arguments)
        with patch.object(publishing.os, "uname", return_value=SimpleNamespace(sysname="Darwin")), \
                patch.object(publishing.subprocess, "run", side_effect=run):
            return inspect_ipa(ipa), commands

    def test_metadata_only_inspection_never_runs_signing_commands(self):
        ipa = self.write_ipa()
        with patch.object(publishing.os, "uname", return_value=SimpleNamespace(sysname="Linux")), \
                patch.object(publishing.subprocess, "run") as run:
            info = inspect_ipa(ipa, verify_signature=False)
        self.assertEqual(info.bundle_identifier, "example.app")
        self.assertIsNone(info.profile_expires_at)
        run.assert_not_called()

    def test_verified_inspection_requires_macos(self):
        with patch.object(publishing.os, "uname", return_value=SimpleNamespace(sysname="Linux")):
            with self.assertRaisesRegex(PublicationError, "requires macOS"):
                inspect_ipa(self.write_ipa())

    def test_verified_inspection_extracts_the_ipa_and_returns_profile_expiry(self):
        info, commands = self.inspect_with_runner(self.write_ipa())
        self.assertEqual(info.profile_expires_at, "2040-01-02T03:04:05Z")
        self.assertEqual(commands[0][:4], ["codesign", "--verify", "--deep", "--strict"])
        self.assertTrue(any(command[:4] == ["codesign", "-d", "--entitlements", ":-"] for command in commands))
        self.assertTrue(any(command[:3] == ["security", "cms", "-D"] for command in commands))
        self.assertTrue(any(command[:2] == ["codesign", "-d"] and command[2].startswith("--extract-certificates=")
                            for command in commands))

    def test_codesign_failure_is_reported_before_profile_processing(self):
        ipa = self.write_ipa()
        with self.assertRaisesRegex(PublicationError, "signature is invalid"):
            self.inspect_with_runner(ipa, verify_returncode=1, verify_stderr="signature is invalid")

    def test_codesign_os_failure_is_a_structured_error(self):
        with self.assertRaisesRegex(PublicationError, "could not run codesign: codesign unavailable"):
            self.inspect_with_runner(self.write_ipa(), os_failure=True)

    def test_expired_profile_is_rejected(self):
        with self.assertRaisesRegex(PublicationError, "profile is expired"):
            self.inspect_with_runner(self.write_ipa(), expiry=datetime(2000, 1, 1))

    def test_signed_identifier_mismatch_is_rejected(self):
        with self.assertRaisesRegex(PublicationError, "identifier does not match"):
            self.inspect_with_runner(self.write_ipa(), signed_identifier="TEAM.other.app")

    def test_malformed_profile_identity_and_device_values_are_rejected(self):
        cases = (
            {
                "signed_identifier": ".example.app", "signed_team": "",
                "profile_identifier": ".example.app", "profile_team": "",
            },
            {"devices": [True]},
        )
        for arguments in cases:
            with self.subTest(arguments=arguments), self.assertRaises(PublicationError):
                self.inspect_with_runner(self.write_ipa(), **arguments)

        info, _ = self.inspect_with_runner(self.write_ipa(), devices=["device-1"])
        self.assertEqual(info.bundle_identifier, "example.app")

    def test_nested_bundle_without_a_profile_is_rejected(self):
        with self.assertRaisesRegex(PublicationError, "missing embedded provisioning profile in Widget.appex"):
            self.inspect_with_runner(self.write_ipa(extension=True, extension_profile=False))


if __name__ == "__main__":
    unittest.main()
