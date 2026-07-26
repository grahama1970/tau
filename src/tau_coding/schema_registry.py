"""Schema identifier parsing and compatibility checks for Tau payloads.

Inputs are schema strings in the canonical ``tau.<name>.v<N>`` form. Outputs
are parsed schema identifiers or deterministic version-skew errors that preserve
both the expected and actual schema versions for operators and callers.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

_SCHEMA_ID_RE = re.compile(r"^tau\.(?P<name>[a-z0-9_]+)\.v(?P<version>[1-9][0-9]*)$")


class SchemaVersionSkewError(ValueError):
    """Raised when a payload schema belongs to the right family but wrong version."""

    def __init__(self, *, expected: str, actual: str) -> None:
        self.expected = expected
        self.actual = actual
        super().__init__(f"schema version skew: expected={expected} actual={actual}")


@dataclass(frozen=True, slots=True)
class SchemaId:
    namespace: str
    name: str
    version: int

    @property
    def family(self) -> str:
        return f"{self.namespace}.{self.name}"


def parse_schema_id(schema: str) -> SchemaId:
    """Parse a canonical Tau schema id into name and integer version."""

    if not isinstance(schema, str):
        raise ValueError("schema must be a string")
    match = _SCHEMA_ID_RE.fullmatch(schema)
    if match is None:
        raise ValueError(f"schema must match tau.<name>.v<N>: {schema}")
    return SchemaId(namespace="tau", name=match.group("name"), version=int(match.group("version")))


def require_schema_compatible(actual: object, expected: str) -> None:
    """Accept only the expected schema version and report same-family skew clearly."""

    if not isinstance(actual, str):
        raise ValueError(f"schema must be {expected}")
    expected_id = parse_schema_id(expected)
    actual_id = parse_schema_id(actual)
    if actual_id == expected_id:
        return
    if actual_id.family == expected_id.family:
        raise SchemaVersionSkewError(expected=expected, actual=actual)
    raise ValueError(f"schema must be {expected}")


def require_schema_in(actual: object, accepted: set[str], *, latest: str) -> None:
    """Accept any explicitly supported schema, with same-family skew diagnostics."""

    if not accepted:
        raise ValueError("accepted schema set must not be empty")
    if not isinstance(actual, str):
        raise ValueError(f"schema must be {latest}")
    if actual in accepted:
        return
    latest_id = parse_schema_id(latest)
    actual_id = parse_schema_id(actual)
    if actual_id.family == latest_id.family:
        raise SchemaVersionSkewError(expected=latest, actual=actual)
    raise ValueError(f"schema must be {latest}")
