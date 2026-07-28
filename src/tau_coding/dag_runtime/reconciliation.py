"""Startup reconciliation for durable-but-unadmitted receipt evidence.

Contract 8.A1 and 8.A8.8 (docs/design/receipt-admission-contract.md): a file
without an admission row is re-admitted ONLY when a matching, valid
write-intent record exists in the sidecar; run, node, attempt, receipt kind
and digest all verify against that intent; and the attempt has not been
superseded by a later attempt for the same node. A bare file — no matching
intent, digest mismatch, or superseded attempt — is never sufficient: it is
quarantined under ``.orphaned/`` and reported, and the caller must treat the
run as BLOCKED rather than trusting the evidence.

Reconciliation runs before scheduler dispatch or resume. It writes its own
receipt through the durable admission primitive so its verdict cannot itself
become a silent absence.
"""

from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from tau_coding.dag_runtime.admission import write_durable_json
from tau_coding.dag_runtime.run_store import DagRunLease, SqliteDagRunStore
from tau_coding.dag_runtime.write_intent import read_sidecar

RECONCILIATION_RECEIPT_SCHEMA = "tau.startup_reconciliation_receipt.v1"


@dataclass
class ReconciliationOutcome:
    readmitted: list[dict[str, Any]] = field(default_factory=list)
    quarantined: list[dict[str, Any]] = field(default_factory=list)
    torn_tail: dict[str, Any] | None = None
    receipt_path: Path | None = None

    @property
    def clean(self) -> bool:
        return not self.quarantined


def _file_digest(path: Path) -> str:
    return f"sha256:{hashlib.sha256(path.read_bytes()).hexdigest()}"


def _quarantine(path: Path, reason: str, quarantine_dir: Path) -> dict[str, Any]:
    quarantine_dir.mkdir(parents=True, exist_ok=True)
    destination = quarantine_dir / path.name
    counter = 1
    while destination.exists():
        destination = quarantine_dir / f"{path.stem}.{counter}{path.suffix}"
        counter += 1
    os.replace(path, destination)
    return {"original": str(path), "quarantined_to": str(destination), "reason": reason}


def reconcile_startup(
    store: SqliteDagRunStore,
    lease: DagRunLease,
    *,
    receipts_root: Path,
    sidecar_path: Path,
) -> ReconciliationOutcome:
    """Classify every durable receipt file under ``receipts_root``.

    Admitted files (digest matches an admission row) are untouched. Files
    covered by a durable-stage sidecar intent whose identity and digest verify
    and whose attempt is current are re-admitted idempotently. Everything else
    is quarantined with a named reason.
    """

    outcome = ReconciliationOutcome()
    sidecar = read_sidecar(sidecar_path)
    if sidecar.torn_tail_reason is not None:
        outcome.torn_tail = {
            "reason": sidecar.torn_tail_reason,
            "bytes": sidecar.torn_tail_bytes,
        }

    admitted = {
        (row["node_id"], row["attempt_id"], row["receipt_kind"]): row["sha256"]
        for row in store.list_admissions(lease.run_id)
    }
    intents_by_target: dict[str, list[dict[str, Any]]] = {}
    for record in sidecar.records:
        target = record.get("target_path")
        if isinstance(target, str):
            intents_by_target.setdefault(target, []).append(record)

    latest_attempt: dict[str, str] = {}
    for stored in store.list_attempts(lease.run_id):
        latest_attempt[stored.identity.node_id] = stored.identity.attempt_id

    quarantine_dir = receipts_root / ".orphaned"
    for path in sorted(receipts_root.rglob("*.json")):
        if quarantine_dir in path.parents:
            continue
        # Control artifacts (this function's own receipt, quarantine metadata)
        # are dot-prefixed and are not node evidence; scanning them would make
        # the instrument quarantine its own output on the next pass.
        if path.name.startswith("."):
            continue
        digest = _file_digest(path)
        if digest in admitted.values():
            continue
        intents = intents_by_target.get(str(path), [])
        durable_intents = [r for r in intents if r.get("stage") == "S5"]
        if not durable_intents:
            outcome.quarantined.append(
                _quarantine(path, "no_matching_durable_intent", quarantine_dir)
            )
            continue
        intent = durable_intents[-1]
        expected_digest = (
            intent.get("extra", {}).get("sha256") if isinstance(intent.get("extra"), dict) else None
        )
        if expected_digest != digest:
            outcome.quarantined.append(
                _quarantine(path, "intent_digest_mismatch", quarantine_dir)
            )
            continue
        if intent.get("run_id") != lease.run_id:
            outcome.quarantined.append(_quarantine(path, "wrong_run", quarantine_dir))
            continue
        node_id = str(intent.get("node_id"))
        attempt_id = str(intent.get("attempt_id"))
        if latest_attempt.get(node_id) != attempt_id:
            outcome.quarantined.append(
                _quarantine(path, "attempt_superseded", quarantine_dir)
            )
            continue
        key = (node_id, attempt_id, str(intent.get("receipt_kind")))
        if key in admitted:
            if admitted[key] != digest:
                outcome.quarantined.append(
                    _quarantine(path, "conflicts_with_admitted_row", quarantine_dir)
                )
            continue
        record = store.admit_receipt(
            lease,
            attempt_id,
            receipt_kind=str(intent.get("receipt_kind")),
            sha256=digest,
            path=str(path),
            size_bytes=path.stat().st_size,
        )
        outcome.readmitted.append(
            {"path": str(path), "sha256": digest, "duplicate": record["duplicate"]}
        )

    receipt = {
        "schema": RECONCILIATION_RECEIPT_SCHEMA,
        "run_id": lease.run_id,
        "mocked": False,
        "live": True,
        "clean": outcome.clean,
        "readmitted": outcome.readmitted,
        "quarantined": outcome.quarantined,
        "sidecar_torn_tail": outcome.torn_tail,
    }
    result = write_durable_json(receipts_root / ".reconciliation-receipt.json", receipt)
    outcome.receipt_path = result.path
    return outcome
