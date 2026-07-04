"""Receipt helpers for Traycer."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from tau_coding.traycer.models import MONITOR_ALERT_SCHEMA


def utc_now_iso() -> str:
    """Return a stable UTC timestamp string."""

    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def sha256_file(path: Path) -> str:
    """Return the sha256 digest for a file."""

    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return f"sha256:{digest.hexdigest()}"


def stable_sha256_payload(payload: dict[str, Any]) -> str:
    """Return a sha256 digest for a canonical JSON payload."""

    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    return f"sha256:{hashlib.sha256(encoded).hexdigest()}"


def write_json(path: Path, payload: dict[str, Any]) -> None:
    """Write a JSON object with deterministic formatting."""

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def monitor_alert(
    *,
    run_id: str,
    index: int,
    observed_agent: str | None,
    severity: str,
    code: str,
    message: str,
    evidence_trace_ids: list[str] | None = None,
    deterministic: bool = True,
    recommended_action: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build one monitor alert with a stable content hash."""

    alert = {
        "schema": MONITOR_ALERT_SCHEMA,
        "run_id": run_id,
        "alert_id": f"alert-{index:04d}",
        "ts": utc_now_iso(),
        "observed_agent": observed_agent,
        "severity": severity,
        "violation": {
            "code": code,
            "message": message,
            "evidence_trace_ids": evidence_trace_ids or [],
            "deterministic": deterministic,
        },
        "recommended_action": recommended_action
        or {
            "type": "wait_for_human" if severity == "BLOCK" else "review",
            "reason": message,
        },
    }
    alert["alert_sha256"] = stable_sha256_payload(alert)
    return alert
