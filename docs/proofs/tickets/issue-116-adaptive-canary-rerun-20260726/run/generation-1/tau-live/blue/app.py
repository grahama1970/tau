from __future__ import annotations

import os
import stat
from pathlib import Path, PurePosixPath, PureWindowsPath
from zipfile import ZipFile, ZipInfo


class UnsafeArchiveEntry(ValueError):
    """Raised when a zip member cannot be safely extracted."""


def _is_symlink(info: ZipInfo) -> bool:
    mode = (info.external_attr >> 16) & 0xFFFF
    return stat.S_IFMT(mode) == stat.S_IFLNK


def _safe_target_path(destination: Path