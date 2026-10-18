"""Export an Xcode archive without changing Xcode account state implicitly."""

from __future__ import annotations

from pathlib import Path
import subprocess


class ExportError(RuntimeError):
    """An archive could not be exported."""


def export_archive(
    archive: Path,
    export_options: Path,
    output: Path,
    *,
    allow_provisioning_updates: bool = False,
    runner=subprocess.run,
) -> Path:
    """Run ``xcodebuild -exportArchive`` and return the newly-created output path."""
    archive = Path(archive)
    export_options = Path(export_options)
    output = Path(output)
    if not archive.is_dir() or archive.suffix != ".xcarchive":
        raise ExportError(f"archive is not an .xcarchive directory: {archive}")
    if not export_options.is_file():
        raise ExportError(f"export options file does not exist: {export_options}")
    if output.exists() or output.is_symlink():
        raise ExportError(f"refusing to overwrite output: {output}")
    if output.parent.exists() and not output.parent.is_dir():
        raise ExportError(f"output parent is not a directory: {output.parent}")

    output.parent.mkdir(parents=True, exist_ok=True)
    command = [
        "xcodebuild", "-exportArchive", "-archivePath", str(archive),
        "-exportOptionsPlist", str(export_options), "-exportPath", str(output),
    ]
    if allow_provisioning_updates:
        command.append("-allowProvisioningUpdates")
    try:
        completed = runner(command, check=False, capture_output=True, text=True)
    except OSError as error:
        raise ExportError(f"could not run xcodebuild: {error}") from error
    if completed.returncode:
        detail = (completed.stderr or completed.stdout or "xcodebuild failed").strip()
        raise ExportError(detail)
    if not output.is_dir():
        raise ExportError(f"xcodebuild did not create an export directory: {output}")
    return output
