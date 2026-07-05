from __future__ import annotations

import os
import stat
from pathlib import Path, PureWindowsPath
from zipfile import ZipFile, ZipInfo


class UnsafeArchiveError(ValueError):
    """Raised when an archive member would extract outside the destination."""


def _safe_member_parts(name: str) -> list[str]:
    """Return safe relative path components for a zip member name.

    Zip files are expected to use POSIX-style '/' separators, but this also
    treats backslashes as separators so Windows-style traversal cannot bypass
    validation on any platform.
    """
    if not isinstance(name, str) or not name:
        raise UnsafeArchiveError("archive member has an empty name")

    candidate = name.replace("\\", "/")
    candidate = candidate.rstrip("/")
    if not candidate:
        raise UnsafeArchiveError(f"archive member {name!r} has no file name")

    # Reject absolute paths and Windows drive/UNC paths even when running on POSIX.
    if candidate.startswith("/") or PureWindowsPath(name).is_absolute() or PureWindowsPath(name).drive:
        raise UnsafeArchiveError(f"archive member {name!r} is absolute")

    parts = candidate.split("/")
    if any(part in ("", ".", "..") for part in parts):
        raise UnsafeArchiveError(f"archive member {name!r} contains an unsafe path segment")

    return parts


def _is_symlink(info: ZipInfo) -> bool:
    mode = (info.external_attr >> 16) & 0o170000
    return stat.S_ISLNK(mode)


def _safe_target(destination: Path, parts: list[str]) -> Path:
    base = destination.resolve(strict=False)
    target = base.joinpath(*parts).resolve(strict=False)
    try:
        common = os.path.commonpath([str(base), str(target)])
    except ValueError as exc:
        raise UnsafeArchiveError("archive member targets a different filesystem root") from exc
    if common != str(base):
        raise UnsafeArchiveError("archive member would extract outside destination")
    return target


def import_zip(zip_path: str, destination: str) -> list[str]:
    """Safely extract a zip archive into destination.

    The function preserves the original public contract by returning the list of
    paths written/created, while preventing Zip Slip path traversal, absolute
    paths, Windows drive paths, and symlink entries.
    """
    dest = Path(destination)
    dest.mkdir(parents=True, exist_ok=True)

    written: list[str] = []
    with ZipFile(zip_path) as archive:
        for info in archive.infolist():
            if _is_symlink(info):
                raise UnsafeArchiveError(f"archive member {info.filename!r} is a symlink")

            parts = _safe_member_parts(info.filename)
            target = _safe_target(dest, parts)

            if info.is_dir():
                target.mkdir(parents=True, exist_ok=True)
                written.append(str(target))
                continue

            target.parent.mkdir(parents=True, exist_ok=True)
            with archive.open(info, "r") as src, target.open("wb") as dst:
                while True:
                    chunk = src.read(1024 * 1024)
                    if not chunk:
                        break
                    dst.write(chunk)
            written.append(str(target))

    return written
