"""Recursive redaction for Tau storage and browser-facing state."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

SENSITIVE_KEY = re.compile(
    r"(?:secret|token|password|passphrase|credential|authorization|api_key|private_key|"
    r"access_key|client_secret|refresh_token|cookie|session_cookie)",
    re.IGNORECASE,
)
SECRET_VALUE_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (
        re.compile(
            r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----.*?-----END [A-Z0-9 ]*PRIVATE KEY-----",
            re.IGNORECASE | re.DOTALL,
        ),
        "[REDACTED:PRIVATE_KEY]",
    ),
    (
        re.compile(r"(?i)\b(authorization\s*:\s*(?:bearer|basic)\s+)([^\s,;&]+)"),
        r"\1[REDACTED]",
    ),
    (re.compile(r"(?i)\b(bearer\s+)([A-Za-z0-9._~+/=-]{8,})"), r"\1[REDACTED]"),
    (
        re.compile(
            r"(?i)\b((?:api[_-]?key|token|password|passwd|pwd|secret|credential|"
            r"authorization|cookie|session|private[_-]?key|access[_-]?key|"
            r"client[_-]?secret|refresh[_-]?token|passphrase)\s*[:=]\s*)"
            r"([^\s,;&\"'`]+)"
        ),
        r"\1[REDACTED]",
    ),
    (
        re.compile(r"(?i)(https?://)([^:/\s@]+):([^@\s/]+)@([A-Za-z0-9_.:-]+)"),
        r"\1[REDACTED]@\4",
    ),
    (re.compile(r"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b"), "[REDACTED:AWS_ACCESS_KEY]"),
    (re.compile(r"\bsk-[A-Za-z0-9_-]{16,}\b"), "[REDACTED:SECRET_TOKEN]"),
    (re.compile(r"\bsk-ant-[A-Za-z0-9_-]{16,}\b"), "[REDACTED:SECRET_TOKEN]"),
    (re.compile(r"\bgh[pousr]_[A-Za-z0-9_]{20,}\b"), "[REDACTED:GITHUB_TOKEN]"),
    (re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}\b"), "[REDACTED:SLACK_TOKEN]"),
)
RAW_OUTPUT_KEY = re.compile(
    r"^(?:stdout|stderr|pane_text|pane_capture|terminal_output|raw_output|"
    r"chain_of_thought|hidden_reasoning)$",
    re.IGNORECASE,
)
SAFE_USAGE_COUNTER_KEYS = {
    "input_tokens",
    "output_tokens",
    "cache_read_tokens",
    "cache_write_tokens",
    "cache_write_1h_tokens",
    "reasoning_tokens",
    "total_tokens",
}


@dataclass(frozen=True, slots=True)
class RedactionResult:
    value: Any
    redacted: bool
    redacted_paths: tuple[str, ...]
    truncated: bool


def redact_for_viewer(value: Any) -> RedactionResult:
    paths: list[str] = []
    truncated = [False]
    projected = _walk(
        value,
        path="$",
        depth=0,
        paths=paths,
        truncated=truncated,
        redact_raw_output=True,
        truncate_strings=True,
    )
    if len(json.dumps(projected, separators=(",", ":")).encode()) > 5 * 1024 * 1024:
        raise RuntimeError("dag_viewer_projection_too_large")
    return RedactionResult(projected, bool(paths), tuple(paths), truncated[0])


def redact_for_storage(value: Any) -> RedactionResult:
    """Redact secrets before durable storage while preserving useful output text."""

    paths: list[str] = []
    truncated = [False]
    redacted = _walk(
        value,
        path="$",
        depth=0,
        paths=paths,
        truncated=truncated,
        redact_raw_output=False,
        truncate_strings=False,
    )
    return RedactionResult(redacted, bool(paths), tuple(paths), truncated[0])


def redact_string_for_storage(value: str) -> str:
    """Redact secret-looking substrings in one string."""

    redacted, _changed = _redact_secret_substrings(value)
    return redacted


def _walk(
    value: Any,
    *,
    path: str,
    depth: int,
    paths: list[str],
    truncated: list[bool],
    redact_raw_output: bool,
    truncate_strings: bool,
) -> Any:
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
            if RAW_OUTPUT_KEY.fullmatch(str(key)):
                if redact_raw_output:
                    output[str(key)] = "[REDACTED:RAW_OUTPUT]"
                    paths.append(child)
                else:
                    output[str(key)] = _walk(
                        item,
                        path=child,
                        depth=depth + 1,
                        paths=paths,
                        truncated=truncated,
                        redact_raw_output=redact_raw_output,
                        truncate_strings=truncate_strings,
                    )
            elif SENSITIVE_KEY.search(str(key)) and str(key) not in SAFE_USAGE_COUNTER_KEYS:
                output[str(key)] = "[REDACTED]"
                paths.append(child)
            else:
                output[str(key)] = _walk(
                    item,
                    path=child,
                    depth=depth + 1,
                    paths=paths,
                    truncated=truncated,
                    redact_raw_output=redact_raw_output,
                    truncate_strings=truncate_strings,
                )
        return output
    if isinstance(value, (list, tuple)):
        if len(value) > 1000:
            truncated[0] = True
        return [
            _walk(
                item,
                path=f"{path}[{index}]",
                depth=depth + 1,
                paths=paths,
                truncated=truncated,
                redact_raw_output=redact_raw_output,
                truncate_strings=truncate_strings,
            )
            for index, item in enumerate(value[:1000])
        ]
    if isinstance(value, str):
        redacted, changed = _redact_secret_substrings(value)
        if changed:
            paths.append(path)
        if truncate_strings and len(redacted) > 8192:
            truncated[0] = True
            return redacted[:8192] + "[TRUNCATED]"
        return redacted
    return value


def _redact_secret_substrings(value: str) -> tuple[str, bool]:
    redacted = value
    for pattern, replacement in SECRET_VALUE_PATTERNS:
        redacted = pattern.sub(replacement, redacted)
    return redacted, redacted != value
