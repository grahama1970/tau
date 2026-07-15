"""Read packaged DAG viewer assets without requiring Node at runtime."""

from __future__ import annotations

import mimetypes
from dataclasses import dataclass
from importlib.resources import files
from pathlib import PurePosixPath


@dataclass(frozen=True, slots=True)
class StaticViewerFile:
    body: bytes
    content_type: str


def read_static_viewer_file(request_path: str) -> StaticViewerFile:
    relative = "index.html" if request_path in {"", "/"} else request_path.lstrip("/")
    parsed = PurePosixPath(relative)
    if parsed.is_absolute() or ".." in parsed.parts:
        raise RuntimeError("dag_viewer_static_path_invalid")
    root = files("tau_coding.dag_viewer").joinpath("static")
    resource = root.joinpath(*parsed.parts)
    if not resource.is_file():
        raise RuntimeError("dag_viewer_static_not_found")
    body = resource.read_bytes()
    content_type = mimetypes.guess_type(relative)[0] or "application/octet-stream"
    if content_type.startswith("text/") or content_type in {
        "application/javascript",
        "application/json",
    }:
        content_type = f"{content_type}; charset=utf-8"
    return StaticViewerFile(body=body, content_type=content_type)
