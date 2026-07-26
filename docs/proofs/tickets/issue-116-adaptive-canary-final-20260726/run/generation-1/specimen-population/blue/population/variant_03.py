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
