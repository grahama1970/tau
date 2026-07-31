"""Durable workspace read sets, change signals, and stale-read reconciliation.

Tau does not try to trace every operating-system file read. This module records
the reads Tau owns or receives explicitly from adapters, compares those read
hashes with admitted workspace changes, and gives the scheduler a fail-closed
gate before a stale attempt can settle successful output.
"""

from __future__ import annotations

import os
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any

from tau_coding.dag_runtime.model import DagPlan, DagPlanNode, canonical_sha256

WORKSPACE_READ_SET_SCHEMA = "tau.workspace_read_set.v1"
WORKSPACE_CHANGE_SIGNAL_SCHEMA = "tau.workspace_change_signal.v1"
STALE_READ_RECONCILIATION_SCHEMA = "tau.stale_read_reconciliation.v1"
WORKSPACE_CHANGE_SCHEMA = "tau.workspace_change.v1"
STALE_READ_POLICIES = frozenset({"observe", "require_reconciliation", "block"})


def stale_read_policy(node: DagPlanNode) -> str:
    """Return the node policy, preserving legacy observe behavior by default."""

    source_extensions = node.source_extensions.to_value()
    policy = (
        source_extensions.get("stale_read_policy")
        if isinstance(source_extensions, Mapping)
        else None
    )
    if policy is None:
        return "observe"
    return str(policy) if str(policy) in STALE_READ_POLICIES else "block"


def initial_workspace_read_set(
    *,
    plan: DagPlan,
    node: DagPlanNode,
    run_id: str,
    attempt_id: str,
    attempt: int,
) -> dict[str, Any]:
    """Build the initial attempt read set from source bindings and static context."""

    entries: list[dict[str, Any]] = []
    for binding in node.source_bindings:
        entries.extend(
            _read_entries_from_value(binding.to_value(), observation_source="source_binding")
        )
    static_context = node.static_context.to_value()
    entries.extend(_read_entries_from_value(static_context, observation_source="static_context"))
    return workspace_read_set_payload(
        plan=plan,
        node=node,
        run_id=run_id,
        attempt_id=attempt_id,
        attempt=attempt,
        entries=entries,
        observation_source="initial_attempt_slice",
    )


def result_workspace_read_set(
    *,
    plan: DagPlan,
    node: DagPlanNode,
    run_id: str,
    attempt_id: str,
    attempt: int,
    result: Mapping[str, Any],
) -> dict[str, Any] | None:
    """Return an explicit adapter-provided read set, if present."""

    entries: list[dict[str, Any]] = []
    for key in ("workspace_read_set", "workspace_reads"):
        if key in result:
            entries.extend(_read_entries_from_value(result[key], observation_source="result"))
    accepted = result.get("accepted_output")
    if isinstance(accepted, Mapping):
        for key in ("workspace_read_set", "workspace_reads"):
            if key in accepted:
                entries.extend(
                    _read_entries_from_value(accepted[key], observation_source="accepted_output")
                )
    if not entries:
        return None
    return workspace_read_set_payload(
        plan=plan,
        node=node,
        run_id=run_id,
        attempt_id=attempt_id,
        attempt=attempt,
        entries=entries,
        observation_source="adapter_result",
    )


def workspace_read_set_payload(
    *,
    plan: DagPlan,
    node: DagPlanNode,
    run_id: str,
    attempt_id: str,
    attempt: int,
    entries: Iterable[Mapping[str, Any]],
    observation_source: str,
) -> dict[str, Any]:
    normalized = tuple(
        sorted(
            (
                _normalize_read_entry(entry, observation_source=observation_source)
                for entry in entries
            ),
            key=lambda item: (
                item["repository_id"],
                item["worktree_id"],
                item["path"],
                item.get("range") or "",
                item.get("symbol") or "",
            ),
        )
    )
    without_hash = {
        "schema": WORKSPACE_READ_SET_SCHEMA,
        "run_id": run_id,
        "plan_sha256": plan.plan_sha256,
        "node_id": node.node_id,
        "attempt_id": attempt_id,
        "attempt": attempt,
        "policy": stale_read_policy(node),
        "entries": list(normalized),
        "entry_count": len(normalized),
    }
    return {**without_hash, "read_set_sha256": canonical_sha256(without_hash)}


def workspace_changes_from_result(result: Mapping[str, Any]) -> tuple[dict[str, Any], ...]:
    """Extract admitted workspace changes from a node result."""

    changes: list[dict[str, Any]] = []
    for key in ("workspace_changes", "changed_files"):
        if key in result:
            changes.extend(_change_entries_from_value(result[key], source=key))
    accepted = result.get("accepted_output")
    if isinstance(accepted, Mapping):
        for key in ("workspace_changes", "changed_files"):
            if key in accepted:
                changes.extend(
                    _change_entries_from_value(accepted[key], source=f"accepted_output.{key}")
                )
    return tuple(changes)


def stale_read_reconciliations_from_result(
    result: Mapping[str, Any],
) -> tuple[dict[str, Any], ...]:
    value = result.get("stale_read_reconciliations")
    if value is None:
        value = result.get("stale_read_reconciliation")
    if value is None:
        return ()
    values = value if isinstance(value, list | tuple) else (value,)
    return tuple(dict(item) for item in values if isinstance(item, Mapping))


def _read_entries_from_value(value: object, *, observation_source: str) -> list[dict[str, Any]]:
    if isinstance(value, Mapping):
        schema = value.get("schema")
        if schema == WORKSPACE_READ_SET_SCHEMA and isinstance(value.get("entries"), list):
            return [
                _normalize_read_entry(item, observation_source=observation_source)
                for item in value["entries"]
                if isinstance(item, Mapping)
            ]
        if _looks_like_workspace_read(value):
            return [_normalize_read_entry(value, observation_source=observation_source)]
        reads = value.get("workspace_reads")
        read_set = value.get("workspace_read_set")
        entries: list[dict[str, Any]] = []
        if reads is not None:
            entries.extend(_read_entries_from_value(reads, observation_source=observation_source))
        if read_set is not None:
            entries.extend(
                _read_entries_from_value(read_set, observation_source=observation_source)
            )
        return entries
    if isinstance(value, list | tuple):
        entries: list[dict[str, Any]] = []
        for item in value:
            entries.extend(_read_entries_from_value(item, observation_source=observation_source))
        return entries
    return []


def _looks_like_workspace_read(value: Mapping[str, Any]) -> bool:
    return (
        value.get("schema") == "tau.workspace_read.v1"
        or value.get("kind") in {"input_file", "read_file", "source_file"}
        and ("content_sha256" in value or "sha256" in value or "blob_sha256" in value)
    )


def _normalize_read_entry(
    entry: Mapping[str, Any],
    *,
    observation_source: str,
) -> dict[str, Any]:
    sha = _sha(entry.get("blob_sha256") or entry.get("sha256") or entry.get("content_sha256"))
    path = _path(entry)
    repository_id = _non_empty_str(entry.get("repository_id")) or "default"
    worktree_id = _non_empty_str(entry.get("worktree_id")) or _worktree_id(entry)
    normalized: dict[str, Any] = {
        "repository_id": repository_id,
        "worktree_id": worktree_id,
        "path": path,
        "blob_sha256": sha,
        "observation_source": _non_empty_str(entry.get("observation_source")) or observation_source,
    }
    for optional in ("symbol", "range", "journal_sequence"):
        value = entry.get(optional)
        if value is not None:
            normalized[optional] = value
    return normalized


def _change_entries_from_value(value: object, *, source: str) -> list[dict[str, Any]]:
    values = value if isinstance(value, list | tuple) else (value,)
    changes: list[dict[str, Any]] = []
    for item in values:
        if not isinstance(item, Mapping):
            continue
        previous_sha = _sha(
            item.get("previous_sha256")
            or item.get("prior_sha256")
            or item.get("pre_sha256")
            or item.get("old_sha256")
        )
        new_sha = _sha(
            item.get("new_sha256")
            or item.get("post_sha256")
            or item.get("after_sha256")
            or item.get("sha256")
        )
        path = _path(item)
        if previous_sha == new_sha:
            continue
        changes.append(
            {
                "schema": WORKSPACE_CHANGE_SCHEMA,
                "repository_id": _non_empty_str(item.get("repository_id")) or "default",
                "worktree_id": _non_empty_str(item.get("worktree_id")) or _worktree_id(item),
                "path": path,
                "previous_sha256": previous_sha,
                "new_sha256": new_sha,
                "change_source": _non_empty_str(item.get("change_source")) or source,
                "admission_id": _non_empty_str(item.get("admission_id")),
            }
        )
    return changes


def _sha(value: object) -> str:
    if not isinstance(value, str) or not value.startswith("sha256:") or len(value) <= 7:
        raise RuntimeError("workspace_hash_invalid")
    return value


def _path(value: Mapping[str, Any]) -> str:
    raw = (
        value.get("path")
        or value.get("repo_relative_path")
        or value.get("declared_path")
        or value.get("file")
    )
    if not isinstance(raw, str) or not raw.strip():
        raise RuntimeError("workspace_path_missing")
    normalized = os.path.normpath(raw.strip())
    if os.path.isabs(normalized):
        root = _non_empty_str(value.get("worktree_root")) or _non_empty_str(
            value.get("repository_root")
        )
        if root:
            try:
                return os.path.relpath(Path(normalized).resolve(), Path(root).resolve())
            except ValueError as exc:
                raise RuntimeError("workspace_path_escape") from exc
        if value.get("anchor") == "filesystem_root" and value.get("kind") in {
            "input_file",
            "read_file",
            "source_file",
        }:
            return Path(normalized).name
        raise RuntimeError("workspace_path_escape")
    if normalized.startswith("../") or normalized == "..":
        raise RuntimeError("workspace_path_escape")
    return normalized


def _worktree_id(value: Mapping[str, Any]) -> str:
    root = value.get("worktree_root") or value.get("repository_root")
    if isinstance(root, str) and root.strip():
        return str(Path(root).expanduser().resolve())
    declared_path = value.get("declared_path")
    if (
        isinstance(declared_path, str)
        and declared_path.strip()
        and os.path.isabs(declared_path)
        and value.get("anchor") == "filesystem_root"
    ):
        return str(Path(declared_path).expanduser().resolve().parent)
    return "default"


def _non_empty_str(value: object) -> str | None:
    return value if isinstance(value, str) and value.strip() else None
