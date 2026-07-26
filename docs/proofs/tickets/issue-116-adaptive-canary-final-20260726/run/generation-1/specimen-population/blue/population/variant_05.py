from pathlib import Path
from zipfile import ZipFile, ZipInfo
import os


class UnsafeArchivePath(ValueError):
    """Raised when an archive member would escape the destination."""


def _is_unsafe_member_name(name: str) -> bool:
    """Return True if a zip member name is absolute or contains traversal."""
    if not name or name.startswith(("/", "\\")):
        return True

    # Reject Windows drive/UNC style paths even when running on POSIX.
    if len(name) >= 2 and name[1] == ":":
        return True
    if name.startswith(("//", "\\\\")):
        return True

    normalized = name.replace("\\", "/")
    parts = [part for part in normalized.split("/") if part not in ("", ".")]
    return any(part == ".." for part in parts)


def _safe_target(destination: Path, member_name: str) -> Path:
    """Resolve the output path and ensure it remains inside destination."""
    if _is_unsafe_member_name(member_name):
        raise UnsafeArchivePath(f"unsafe archive member path: {member_name!r}")

    dest_resolved = destination.resolve()
    target = (dest_resolved / member_name).resolve()

    try:
        target.relative_to(dest_resolved)
    except ValueError as exc:
        raise UnsafeArchivePath(f"archive member escapes destination: {member_name!r}") from exc

    return target


def _is_symlink(info: ZipInfo) -> bool:
    """Detect Unix symlink entries encoded in zip external attributes."""
    mode = (info.external_attr >> 16) & 0o170000
    return mode == 0o120000 (


def import_zip(zip_path: str, destination: str) -> list[str]:
    """
    Safely extract a zip archive into destination.

    Defenses:
    - rejects absolute paths, drive paths, UNC paths, and '..' traversal
    - verifies resolved output paths stay under the destination directory
    - rejects symlink entries to avoid link-based escapes
    - creates parent directories only after validation
    """
    dest = Path(destination)
    dest.mkdir(parents=True, exist_ok=True)
    dest_resolved = dest.resolve()

    written: list[str] = []
    with ZipFile(zip_path) as archive:
        for info in archive.infolist():
            name = info.filename
            target = _safe_target(dest_resolved, name)

            if _is_symlink(info):
                raise UnsafeArchivePath(f"archive member is a symlink: {name!r}")

            if info.is_dir() or name.endswith(("/", "\\")):
                target.mkdir(parents=True, exist_ok=True)
                continue

            target.parent.mkdir(parents=True, exist_ok=True)
            with archive.open(info, "r") as source, open(target, "wb") as output:
                while True:
                    chunk = source.read(1024 * 1024)
                    if not chunk:
                        break
                    output.write(chunk)
            written.append(str(target))

    return written
