"""Retention and archive helpers for local Tau DAG run directories."""

from __future__ import annotations

import hashlib
import json
import shutil
import tarfile
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

DAG_RETENTION_RECEIPT_SCHEMA = "tau.dag_retention_receipt.v1"
DAG_RETENTION_ARCHIVE_MANIFEST_SCHEMA = "tau.dag_retention_archive_manifest.v1"
RUN_MARKERS = (
    "dag-run.sqlite3",
    "run-receipt.json",
    "dag-receipt.json",
    "dag-progress.json",
)


@dataclass(frozen=True, slots=True)
class RetentionCandidate:
    path: Path
    observed_mtime: float


def expire_dag_run_directories(
    *,
    root: Path,
    archive_dir: Path,
    keep_count: int | None = None,
    older_than_days: float | None = None,
    dry_run: bool = False,
    receipt_path: Path | None = None,
    now: float | None = None,
) -> dict[str, Any]:
    """Archive and remove local run directories selected by count and/or age."""

    if keep_count is None and older_than_days is None:
        raise RuntimeError("retention requires --keep-count or --older-than-days")
    if keep_count is not None and keep_count < 0:
        raise RuntimeError("keep_count must be non-negative")
    if older_than_days is not None and older_than_days < 0:
        raise RuntimeError("older_than_days must be non-negative")
    resolved_root = root.expanduser().resolve()
    resolved_archive_dir = archive_dir.expanduser().resolve()
    candidates = _discover_retention_candidates(resolved_root)
    selected = _select_expired_candidates(
        candidates,
        keep_count=keep_count,
        older_than_days=older_than_days,
        now=now or time.time(),
    )
    retained = [item for item in candidates if item not in selected]
    expired: list[dict[str, Any]] = []
    for candidate in selected:
        record: dict[str, Any] = {
            "run_dir": str(candidate.path),
            "observed_mtime": _iso_from_timestamp(candidate.observed_mtime),
        }
        if not dry_run:
            archive = _archive_run_dir(candidate.path, archive_dir=resolved_archive_dir)
            shutil.rmtree(candidate.path)
            record.update(archive)
            record["removed"] = True
        else:
            record["removed"] = False
        expired.append(record)
    receipt = {
        "schema": DAG_RETENTION_RECEIPT_SCHEMA,
        "ok": True,
        "status": "PASS",
        "mocked": False,
        "live": True,
        "root": str(resolved_root),
        "archive_dir": str(resolved_archive_dir),
        "dry_run": dry_run,
        "policy": {
            "keep_count": keep_count,
            "older_than_days": older_than_days,
        },
        "candidate_count": len(candidates),
        "expired_count": len(expired),
        "retained_count": len(retained),
        "expired_runs": expired,
        "retained_runs": [
            {
                "run_dir": str(item.path),
                "observed_mtime": _iso_from_timestamp(item.observed_mtime),
            }
            for item in retained
        ],
        "proof_scope": {
            "proves": [
                "Tau can discover local run directories under a configured root.",
                "Tau archives each selected run before deleting the source directory.",
                "Tau records archive hashes and retained run directories in a receipt.",
            ],
            "does_not_prove": [
                "Remote Herdr workspace cleanup.",
                "Semantic correctness of archived provider/model outputs.",
                "Scheduled retention policy installation.",
            ],
        },
        "timestamp": _utc_stamp(),
    }
    if receipt_path is not None:
        _write_json(receipt_path.expanduser().resolve(), receipt)
    return receipt


def _discover_retention_candidates(root: Path) -> list[RetentionCandidate]:
    if not root.exists():
        return []
    if not root.is_dir():
        raise RuntimeError(f"retention root is not a directory: {root}")
    candidates = [
        RetentionCandidate(path=path, observed_mtime=_run_dir_mtime(path))
        for path in root.iterdir()
        if path.is_dir() and _is_run_dir(path)
    ]
    return sorted(candidates, key=lambda item: (item.observed_mtime, str(item.path)))


def _select_expired_candidates(
    candidates: list[RetentionCandidate],
    *,
    keep_count: int | None,
    older_than_days: float | None,
    now: float,
) -> list[RetentionCandidate]:
    selected = set(candidates)
    if keep_count is not None:
        sorted_candidates = sorted(
            candidates,
            key=lambda item: (item.observed_mtime, str(item.path)),
            reverse=True,
        )
        retained_by_count = set(sorted_candidates[:keep_count])
        selected &= set(candidates) - retained_by_count
    if older_than_days is not None:
        cutoff = now - older_than_days * 24 * 60 * 60
        selected &= {item for item in candidates if item.observed_mtime < cutoff}
    return sorted(selected, key=lambda item: (item.observed_mtime, str(item.path)))


def _archive_run_dir(run_dir: Path, *, archive_dir: Path) -> dict[str, Any]:
    archive_dir.mkdir(parents=True, exist_ok=True)
    archive_path = archive_dir / f"{run_dir.name}.tar.gz"
    manifest_path = archive_dir / f"{run_dir.name}.manifest.json"
    with tarfile.open(archive_path, "w:gz") as archive:
        archive.add(run_dir, arcname=run_dir.name)
    manifest = {
        "schema": DAG_RETENTION_ARCHIVE_MANIFEST_SCHEMA,
        "run_dir_name": run_dir.name,
        "source_run_dir": str(run_dir),
        "archive_path": str(archive_path),
        "archive_sha256": _file_sha256(archive_path),
        "journal_sha256": _optional_file_sha256(run_dir / "dag-run.sqlite3"),
        "event_journal_sha256": _optional_file_sha256(run_dir / "events.jsonl"),
        "receipt_sha256": _optional_file_sha256(run_dir / "run-receipt.json")
        or _optional_file_sha256(run_dir / "dag-receipt.json"),
        "archived_at": _utc_stamp(),
    }
    _write_json(manifest_path, manifest)
    return {
        "archive_path": str(archive_path),
        "archive_sha256": manifest["archive_sha256"],
        "archive_manifest_path": str(manifest_path),
        "archive_manifest_sha256": _file_sha256(manifest_path),
    }


def _is_run_dir(path: Path) -> bool:
    return any((path / marker).exists() for marker in RUN_MARKERS)


def _run_dir_mtime(path: Path) -> float:
    markers = [path / marker for marker in RUN_MARKERS if (path / marker).exists()]
    if not markers:
        return path.stat().st_mtime
    return max(marker.stat().st_mtime for marker in markers)


def _file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _optional_file_sha256(path: Path) -> str | None:
    return _file_sha256(path) if path.is_file() else None


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _utc_stamp() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _iso_from_timestamp(value: float) -> str:
    return datetime.fromtimestamp(value, UTC).isoformat().replace("+00:00", "Z")
