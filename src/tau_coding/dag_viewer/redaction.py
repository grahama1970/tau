"""Recursive size-bounded redaction for browser-facing state."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

SENSITIVE_KEY = re.compile(
    r"(?:secret|token|password|credential|authorization|api_key|private_key|cookie|session_cookie)",
    re.IGNORECASE,
)


@dataclass(frozen=True, slots=True)
class RedactionResult:
    value: Any
    redacted: bool
    redacted_paths: tuple[str, ...]
    truncated: bool


def redact_for_viewer(value: Any) -> RedactionResult:
    paths: list[str] = []
    truncated = [False]
    projected = _walk(value, path="$", depth=0, paths=paths, truncated=truncated)
    if len(json.dumps(projected, separators=(",", ":")).encode()) > 5 * 1024 * 1024:
        raise RuntimeError("dag_viewer_projection_too_large")
    return RedactionResult(projected, bool(paths), tuple(paths), truncated[0])


def _walk(value: Any, *, path: str, depth: int, paths: list[str], truncated: list[bool]) -> Any:
    if depth > 12:
        truncated[0] = True
        return "[TRUNCATED:MAX_DEPTH]"
    if isinstance(value, dict):
        output: dict[str, Any] = {}
        for index, (key, item) in enumerate(value.items()):
            if index >= 1000:
                truncated[0] = True
                break
            child = f"{path}.{key}"
            if SENSITIVE_KEY.search(str(key)):
                output[str(key)] = "[REDACTED]"
                paths.append(child)
            else:
                output[str(key)] = _walk(
                    item, path=child, depth=depth + 1, paths=paths, truncated=truncated
                )
        return output
    if isinstance(value, (list, tuple)):
        if len(value) > 1000:
            truncated[0] = True
        return [
            _walk(item, path=f"{path}[{index}]", depth=depth + 1, paths=paths, truncated=truncated)
            for index, item in enumerate(value[:1000])
        ]
    if isinstance(value, str) and len(value) > 8192:
        truncated[0] = True
        return value[:8192] + "[TRUNCATED]"
    return value
