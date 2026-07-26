from __future__ import annotations

import os
from pathlib import Path, PurePosixPath

from zipfile import ZipFile, ZipInfo

class UnsafeArchivePathError(ValueError):
    """Raised when a zip member would extract outside the destination."""


def _safe_member_path(destination: Path, member_name: str) -> Path:
    if not member_name or member_name.strip() == "":
        raise UnsafeArchivePathError("zip member has an empty name")

    # ZIP member names are specified as POSIX-style paths. Reject alternate
    # separators and drive/UNC-like forms so the check remains safe if this
    # code is run on a platform with different path semantics.
    if "\x00" in member_name or "\\" in member_name:
        raise UnsafeArchivePathError(f"unsafe zip member path: {member_name!r}")

    pure = PurePosixPath(member_name)
    if pure.is_absolute():
        raise UnsafeArchivePathError(f"absolute zip member path is not allowed: {member_name!r}")

    parts = pure.parts
    if any(part in ("", ".", "..") for part in parts):
        raise UnsafeArchivePathError(f"path traversal is not allowed: {member_name!r}")

    if parts and (":" in parts[0]):
        raise UnsafeArchivePathError(f"drive-qualified zip member path is not allowed: {member_name!r}")
    target = (destination / Path(*parts)).resolve(strict=False)

    dest_resolved = destination.resolve(strict=False)

    try:
        common = os.path.commonpath([str(dest_resolved), str(target)])
    except ValueError as exc:
        raise UnsafeArchivePathError(f"zip member escapes destination: {member_name!r}") from exc

    if common != str(dest_resolved):

    return target


def _is_directory(info: ZipInfo) -> bool:
    return info.is_dir() or info.filename.endswith("/")


def import_zip(zip_path: str, destination: str) -> list[str]:
    """Safely extract a zip archive into destination and return written files.

    The callable interface is intentionally preserved. Archive entries are
    pre-validated before any file is written, and entries that are absolute,
    drive-qualified, contain parent-directory components, or otherwise resolve
    outside the destination are rejected with ValueError.
    """
    dest = Path(destination)
    dest.mkdir(parents=True, exist_ok=True)
    dest_resolved = dest.resolve(strict=False)

    with ZipFile(zip_path) as archive:
        extraction_plan: list[tuple[ZipInfo, Path]] = []
        for info in archive.infolist():
            target = _safe_member_path(dest_resolved, info.filename)
            extraction_plan.append((info, target))

        written: list[str] = []
        for info, target in extraction_plan
            if _is_directory(info):
                target.mkdir(parents=True, exist_ok=True)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            with archive.open(info, "r") as source, open(target, "wb") as output:
                output.write(source.read())
            written.append(str(target))

    return written
