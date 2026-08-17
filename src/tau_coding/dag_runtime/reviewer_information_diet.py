"""Information diet for reviewer nodes.

A reviewer judges the artifact, not the producer's workspace. If a review-role
node's resolved inputs carry the producer's worktree/workspace location or raw
transcript, the reviewer is contaminated by the implementer's framing and (for
local skill reviewers) gains write-adjacent access to the thing it is supposed
to judge independently. This module scans a reviewer node's accepted inputs for
those payload shapes and fails closed before the adapter is dispatched.

Scope is deliberately narrow: it blocks the specific contamination channels —
workspace paths and transcripts — rather than attempting to allowlist every
legitimate artifact schema.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

REVIEWER_INPUT_DIET_CODE = "REVIEWER_INPUT_DIET_VIOLATION"

# Payload keys that hand a reviewer the producer's workspace or framing.
FORBIDDEN_REVIEWER_INPUT_KEYS = frozenset(
    {
        "worktree_path",
        "workspace_path",
        "producer_workspace",
        "transcript",
        "producer_transcript",
        "session_transcript",
    }
)


def is_reviewer_role(role: str | None) -> bool:
    return bool(role) and "review" in role.casefold()


def reviewer_diet_violation(accepted_inputs: tuple[Mapping[str, Any], ...]) -> str | None:
    """Return ``REVIEWER_INPUT_DIET_VIOLATION:<key>`` for the first forbidden
    key found anywhere in the accepted inputs, or None when the diet holds."""

    for item in accepted_inputs:
        key = _find_forbidden_key(item)
        if key is not None:
            return f"{REVIEWER_INPUT_DIET_CODE}:{key}"
    return None


def _find_forbidden_key(value: Any) -> str | None:
    if isinstance(value, Mapping):
        for key, child in value.items():
            if isinstance(key, str) and key.casefold() in FORBIDDEN_REVIEWER_INPUT_KEYS:
                return key
            found = _find_forbidden_key(child)
            if found is not None:
                return found
    elif isinstance(value, (list, tuple)):
        for child in value:
            found = _find_forbidden_key(child)
            if found is not None:
                return found
    return None


__all__ = [
    "FORBIDDEN_REVIEWER_INPUT_KEYS",
    "REVIEWER_INPUT_DIET_CODE",
    "is_reviewer_role",
    "reviewer_diet_violation",
]
