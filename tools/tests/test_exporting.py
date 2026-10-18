from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

from internal_app_updates.exporting import ExportError, export_archive


class ExportingTests(unittest.TestCase):
    def test_command_preserves_options_and_is_explicit_about_provisioning(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            archive = root / "App.xcarchive"
            archive.mkdir()
            options = root / "ExportOptions.plist"
            options.write_text("plist")
            commands = []
            def run(command, **kwargs):
                commands.append(command)
                Path(command[command.index("-exportPath") + 1]).mkdir()
                return type("Result", (), {"returncode": 0, "stderr": "", "stdout": ""})()
            export_archive(archive, options, root / "out", runner=run)
            self.assertNotIn("-allowProvisioningUpdates", commands[0])
            export_archive(archive, options, root / "out2", allow_provisioning_updates=True, runner=run)
            self.assertIn("-allowProvisioningUpdates", commands[1])
            self.assertIn("-exportOptionsPlist", commands[0])

    def test_does_not_overwrite_output(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            archive = root / "App.xcarchive"
            archive.mkdir()
            options = root / "ExportOptions.plist"
            options.write_text("plist")
            output = root / "out"
            output.mkdir()
            with self.assertRaises(ExportError):
                export_archive(archive, options, output)

    def test_rejects_a_missing_or_non_directory_export_result(self):
        for make_file in (False, True):
            with self.subTest(make_file=make_file), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                archive = root / "App.xcarchive"
                archive.mkdir()
                options = root / "ExportOptions.plist"
                options.write_text("plist")
                output = root / "out"

                def run(command, **kwargs):
                    if make_file:
                        output.write_text("not a directory")
                    return type("Result", (), {"returncode": 0})()

                with self.assertRaisesRegex(ExportError, "did not create an export directory"):
                    export_archive(archive, options, output, runner=run)

    def test_does_not_follow_an_existing_dangling_output_symlink(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            archive = root / "App.xcarchive"
            archive.mkdir()
            options = root / "ExportOptions.plist"
            options.write_text("plist")
            output = root / "out"
            output.symlink_to(root / "missing")

            def run(command, **kwargs):
                self.fail("existing output must be rejected before running xcodebuild")

            with self.assertRaisesRegex(ExportError, "refusing to overwrite"):
                export_archive(archive, options, output, runner=run)
