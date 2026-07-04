"""Traycer constants and small value objects."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

SUBAGENT_TRACE_SCHEMA = "tau.subagent_trace.v1"
EVIDENCE_CLAIM_SCHEMA = "tau.evidence_claim.v1"
MONITOR_ALERT_SCHEMA = "tau.monitor_alert.v1"
MONITOR_RECEIPT_SCHEMA = "tau.monitor_receipt.v1"
REQUIRED_EVIDENCE_SCHEMA = "tau.required_evidence.v1"

SEVERITY_RANK = {"WARN": 1, "REVIEW": 2, "REROUTE": 3, "BLOCK": 4}
STATUS_BY_SEVERITY = {
    "WARN": "PASS",
    "REVIEW": "REVIEW",
    "REROUTE": "REROUTE",
    "BLOCK": "BLOCKED",
}


@dataclass(frozen=True, slots=True)
class TraycerValidationOptions:
    """Inputs for one offline Traycer validation run."""

    trace_path: Path
    handoff_path: Path
    active_goal_hash: str
    receipt_path: Path
    required_evidence_path: Path | None = None
    start_handoff_path: Path | None = None
    advisory_final_handoff_evidence: bool = False
