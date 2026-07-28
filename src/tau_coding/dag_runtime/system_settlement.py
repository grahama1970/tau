"""Scheduler-authored settlement receipts and the run-store failure state.

Contract 8.A5/8.A6 (docs/design/receipt-admission-contract.md): when a worker
cannot produce or admit its expected receipt, the node must still settle
fail-closed — but "BLOCKED requires a receipt whose admission just failed"
would recurse. The scheduler therefore authors a minimal ``system_settlement``
receipt through its own trusted path: durable write via the admission
primitive, then admission under the distinct ``system_settlement`` kind that
workers can never author.

If even that trusted path fails, the problem is storage, not the node: the
run enters RUN_STORE_FAILURE, recorded by an fsynced marker file (SQLite may
be unusable at that point), and dispatch must halt. ``run_store_failed``
is the guard the scheduler consults.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from tau_coding.dag_runtime.admission import write_durable_json
from tau_coding.dag_runtime.run_store import DagRunLease, SqliteDagRunStore

SYSTEM_SETTLEMENT_SCHEMA = "tau.system_settlement_receipt.v1"
SYSTEM_SETTLEMENT_KIND = "system_settlement"
RUN_STORE_FAILURE_MARKER = "run-store-failure.marker"


class RunStoreFailure(RuntimeError):
    """Raised when even the trusted settlement path cannot admit."""


def settle_with_system_receipt(
    store: SqliteDagRunStore,
    lease: DagRunLease,
    attempt_id: str,
    *,
    receipts_root: Path,
    node_id: str,
    reason_code: str,
    expected_receipt_kind: str,
    classification: str,
    run_dir: Path,
) -> dict[str, Any]:
    """Settle a node BLOCKED with a scheduler-authored receipt.

    On any failure inside this trusted path the run store is declared failed:
    the marker is written and ``RunStoreFailure`` raised — one node's problem
    must never be silently converted into an unrecorded absence.
    """

    payload = {
        "schema": SYSTEM_SETTLEMENT_SCHEMA,
        "receipt_kind": SYSTEM_SETTLEMENT_KIND,
        "verdict": "BLOCKED",
        "reason_code": reason_code,
        "expected_receipt_kind": expected_receipt_kind,
        "attempt_id": attempt_id,
        "node_id": node_id,
        "classification": classification,
    }
    target = receipts_root / node_id / f"{attempt_id}-system-settlement.json"
    try:
        written = write_durable_json(target, payload)
        return store.admit_receipt(
            lease,
            attempt_id,
            receipt_kind=SYSTEM_SETTLEMENT_KIND,
            sha256=written.sha256,
            path=str(written.path),
            size_bytes=written.size_bytes,
        )
    except Exception as exc:  # noqa: BLE001 - any trusted-path failure is storage-level
        marker = enter_run_store_failure(run_dir, reason=f"{reason_code}: {exc}")
        raise RunStoreFailure(f"trusted settlement path failed; marker at {marker}") from exc


def enter_run_store_failure(run_dir: Path, *, reason: str) -> Path:
    """Record storage-level failure with an fsynced marker file.

    Deliberately avoids SQLite and the JSON primitive's temp-rename dance is
    still safe here because the marker directory is the run dir itself; if
    even this write fails, the raised OSError reaches the operator — there is
    no quieter level left to fail into.
    """

    marker = run_dir / RUN_STORE_FAILURE_MARKER
    write_durable_json(marker, {"schema": "tau.run_store_failure.v1", "reason": reason})
    return marker


def run_store_failed(run_dir: Path) -> bool:
    """Dispatch guard: True when the run's store has been declared failed."""

    return (run_dir / RUN_STORE_FAILURE_MARKER).exists()


def assert_dispatch_allowed(run_dir: Path) -> None:
    if run_store_failed(run_dir):
        raise RunStoreFailure(
            f"dispatch refused: {run_dir / RUN_STORE_FAILURE_MARKER} present"
        )


__all__ = [
    "RUN_STORE_FAILURE_MARKER",
    "SYSTEM_SETTLEMENT_KIND",
    "SYSTEM_SETTLEMENT_SCHEMA",
    "RunStoreFailure",
    "assert_dispatch_allowed",
    "enter_run_store_failure",
    "run_store_failed",
    "settle_with_system_receipt",
]
