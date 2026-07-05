from pathlib import Path, PurePosixPath
from zipfile import ZipFile, ZipInfo


class UnsafeArchiveEntry(ValueError):
    """Raised when a zip member would escape the import destination."""


def _is_symlink(info: ZipInfo) -> bool:
    # Unix file type bits are stored in the upper 16 bits of external_attr.
    return ((info.external_attr >> 16) & 0o170000) == 0o120000


def _safe_target(destination: Path, member_name: str) -> Path:
    # Zip member names are specified with forward slashes. Treat backslashes as
    # separators too so Windows-style traversal cannot bypass validation.
    normalized_name = member_name.replace("\\", "/")
    member_path = PurePosixPath(normalized_name)

    if member_path.is_absolute():
        raise UnsafeArchiveEntry(f"absolute zip path rejected: {member_name!r}")

    if any(part in ("", ".", "..") for part in member_path.parts):
        raise UnsafeArchiveEntry(f"unsafe zip path rejected: {member_name!r}")

    target = destination.joinpath(*member_path.parts)
    resolved_destination = destination.resolve()
    resolved_target_parent = target.parent.resolve()

    if resolved_target_parent != resolved_destination and resolved_destination not in resolved_target_parent.parents:
        raise UnsafeArchiveEntry(f"zip path escapes destination: {member_name!r}")

    return target


def import_zip(zip_path: str, destination: str) -> list[str]:
    dest = Path(destination)
    dest.mkdir(parents=True, exist_ok=True)
    dest = dest.resolve()

    written: list[str] = []
    with ZipFile(zip_path) as archive:
        for info in archive.infolist():
            if _is_symlink(info):
                raise UnsafeArchiveEntry(f"symlink zip entry rejected: {info.filename!r}")

            target = _safe_target(dest, info.filename)

            if info.is_dir():
                target.mkdir(parents=True, exist_ok=True)
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
