"""Tamper-evident, replayable run ledger (tau.run_ledger.v1).

A security officer must be able to REPLAY a Tau run and VERIFY the record was
not edited. This module builds an append-only hash chain over a run's ordered
entries (journal events + admitted receipts + agentic-eval scores) and verifies
it by recomputation, naming the first tampered index.

Design (grahama1970/tau#327): the DEFAULT tier is a simple prev-hash chain
(git / Certificate-Transparency style) -- most runs need tamper-EVIDENCE, not a
full Merkle transparency log. The security-officer tier (Merkle checkpoint +
proof-of-inclusion) layers on top and is intentionally out of scope for this
default core.

Pure stdlib (hashlib/json) so any layer can build/verify a ledger without Tau's
runtime deps. Entry hashing is over canonical JSON, bound to the run's
goal_hash, so a chain from one run cannot be spliced into another.
"""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

RUN_LEDGER_SCHEMA = "tau.run_ledger.v1"
RUN_LEDGER_TRACE_SCHEMA = "tau.run_ledger_trace.v1"
AGENTIC_EVAL_RECEIPT_SCHEMA = "tau.agentic_eval_receipt.v1"
ARTIFACT_DIGEST_SCHEMA = "tau.run_ledger_artifact_digest.v1"
GENESIS = "GENESIS"


def _canonical(obj: Any) -> str:
    """Deterministic JSON for hashing (stable key order, no incidental spacing)."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def _entry_hash(prev_hash: str, payload: dict[str, Any], goal_hash: str) -> str:
    material = f"{prev_hash}\n{goal_hash}\n{_canonical(payload)}"
    return "sha256:" + hashlib.sha256(material.encode("utf-8")).hexdigest()


def admit_agentic_eval(report: dict[str, Any]) -> dict[str, Any]:
    """Project an /agentic-evals report (agentic_evals.report.v2) into an admitted
    ledger evidence entry, preserving readiness, per-case outcomes, and the
    mocked/live proof boundary. This is why the ledger also serves /agentic-evals
    and /triage-error: the score that proves a fix becomes an auditable row.
    """
    cases = []
    for case in report.get("cases", report.get("results", [])) or []:
        outcomes = [t.get("outcome") for t in case.get("trials", []) if isinstance(t, dict)]
        cases.append(
            {
                "name": case.get("name"),
                "type": case.get("type"),
                "real_world": bool(case.get("real_world")),
                "outcomes": outcomes,
                "passed": bool(outcomes) and all(o == "PASS" for o in outcomes),
            }
        )
    return {
        "schema": AGENTIC_EVAL_RECEIPT_SCHEMA,
        "kind": "agentic_eval",
        "skill": report.get("skill"),
        "readiness": report.get("readiness"),
        "live": bool(report.get("live")),
        "mocked": bool(report.get("mocked")),
        "trial_count": report.get("trial_count"),
        "cases": cases,
    }


def build_ledger(
    entries: list[dict[str, Any]],
    *,
    goal_hash: str,
    run_id: str,
    dag_id: str | None = None,
) -> dict[str, Any]:
    """Build a tamper-evident tau.run_ledger.v1 from ordered entries.

    Each entry is wrapped with a monotonic seq and an entry_hash chained to the
    prior entry_hash and the run's goal_hash. entries are opaque payloads
    (journal events, admitted receipts, admit_agentic_eval outputs, ...).
    """
    chained: list[dict[str, Any]] = []
    prev = GENESIS
    for seq, payload in enumerate(entries):
        wrapped = {"seq": seq, "payload": payload}
        h = _entry_hash(prev, wrapped, goal_hash)
        wrapped["prev_hash"] = prev
        wrapped["entry_hash"] = h
        chained.append(wrapped)
        prev = h
    ledger = {
        "schema": RUN_LEDGER_SCHEMA,
        "run_id": run_id,
        "dag_id": dag_id,
        "goal_hash": goal_hash,
        "built_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "entry_count": len(chained),
        "head_hash": prev,
        "entries": chained,
    }
    ledger["trace"] = _trace_from_chained_entries(chained)
    return ledger


def build_run_ledger_from_run_dir(
    run_dir: Path,
    *,
    agentic_eval_reports: Sequence[Path] = (),
    output_path: Path | None = None,
) -> dict[str, Any]:
    """Build and optionally write a ledger for an existing Tau DAG run directory."""
    resolved_run_dir = run_dir.expanduser().resolve()
    receipt_path = resolved_run_dir / "dag-receipt.json"
    receipt = _read_json_object(receipt_path)
    goal_hash = str(receipt.get("active_goal_hash") or receipt.get("goal_hash") or "")
    if not goal_hash:
        raise ValueError("dag receipt is missing active_goal_hash")
    entries: list[dict[str, Any]] = []
    for event in receipt.get("scheduler_events", []) or []:
        if isinstance(event, dict):
            entries.append({"kind": "scheduler_event", **event})
    for dispatch in receipt.get("dispatches", []) or []:
        if isinstance(dispatch, dict):
            entries.append(
                {
                    "kind": "dispatch_receipt",
                    "schema": dispatch.get("schema"),
                    "selected_agent": dispatch.get("selected_agent"),
                    "status": dispatch.get("status"),
                    "stop_reason": dispatch.get("stop_reason"),
                    "mocked": dispatch.get("mocked"),
                    "live": dispatch.get("live"),
                }
            )
    entries.extend(_artifact_digest_entries(receipt, resolved_run_dir))
    for path in agentic_eval_reports:
        entries.append(admit_agentic_eval(_read_json_object(path.expanduser().resolve())))
    ledger = build_ledger(
        entries,
        goal_hash=goal_hash,
        run_id=str(receipt.get("dag_id") or receipt.get("run_id") or resolved_run_dir.name),
        dag_id=str(receipt.get("dag_id")) if receipt.get("dag_id") is not None else None,
    )
    ledger["source_run_dir"] = str(resolved_run_dir)
    ledger["source_receipt_path"] = str(receipt_path)
    if output_path is not None:
        write_ledger(output_path, ledger)
    return ledger


def _artifact_digest_entries(receipt: dict[str, Any], run_dir: Path) -> list[dict[str, Any]]:
    """Project receipt artifacts into digest rows a security engineer can trace.

    The ledger is built before its final `run_ledger` pointer is written back to
    `dag-receipt.json`, so the receipt itself is deliberately excluded: hashing
    it would make every default ledger stale the moment the pointer is attached.
    """
    candidates: list[str] = []
    progress_path = receipt.get("progress_path")
    if isinstance(progress_path, str):
        candidates.append(progress_path)
    artifacts = receipt.get("artifacts")
    if isinstance(artifacts, list):
        candidates.extend(str(path) for path in artifacts if isinstance(path, str))
    seen: set[Path] = set()
    entries: list[dict[str, Any]] = []
    for raw_path in candidates:
        path = Path(raw_path).expanduser()
        if not path.is_absolute():
            path = run_dir / path
        try:
            resolved = path.resolve()
        except OSError:
            resolved = path.absolute()
        if (
            resolved in seen
            or resolved.name.startswith("run-ledger")
            or resolved.name == "dag-receipt.json"
        ):
            continue
        seen.add(resolved)
        relative_path: str | None
        try:
            relative_path = str(resolved.relative_to(run_dir))
        except ValueError:
            relative_path = None
        if not resolved.is_file():
            entries.append(
                {
                    "schema": ARTIFACT_DIGEST_SCHEMA,
                    "kind": "artifact_missing",
                    "path": str(resolved),
                    "relative_path": relative_path,
                }
            )
            continue
        data = resolved.read_bytes()
        entries.append(
            {
                "schema": ARTIFACT_DIGEST_SCHEMA,
                "kind": "artifact_digest",
                "path": str(resolved),
                "relative_path": relative_path,
                "bytes": len(data),
                "sha256": "sha256:" + hashlib.sha256(data).hexdigest(),
                "json_schema": _json_schema(resolved),
            }
        )
    return entries


def _json_schema(path: Path) -> str | None:
    if path.suffix.lower() != ".json":
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        return None
    if isinstance(payload, dict) and isinstance(payload.get("schema"), str):
        return payload["schema"]
    return None


def _trace_from_chained_entries(entries: list[dict[str, Any]]) -> dict[str, Any]:
    payloads = [
        entry.get("payload") for entry in entries if isinstance(entry.get("payload"), dict)
    ]
    kinds = Counter(
        str(payload.get("kind") or payload.get("schema") or "unknown") for payload in payloads
    )
    node_attempts: dict[tuple[str, str], dict[str, Any]] = {}
    artifact_rows: list[dict[str, Any]] = []
    agentic_eval_rows: list[dict[str, Any]] = []
    timeline: list[dict[str, Any]] = []
    for entry in entries:
        payload = entry.get("payload") if isinstance(entry.get("payload"), dict) else {}
        seq = entry.get("seq")
        kind = str(payload.get("kind") or payload.get("schema") or "unknown")
        event = payload.get("event") or payload.get("event_type")
        node_id = payload.get("node_id") or payload.get("selected_agent") or payload.get("node")
        attempt = payload.get("attempt") or payload.get("attempt_number")
        status = payload.get("status") or payload.get("verdict") or payload.get("readiness")
        timeline.append(
            {
                "seq": seq,
                "kind": kind,
                "event": event if isinstance(event, str) else None,
                "node_id": node_id if isinstance(node_id, str) else None,
                "attempt": attempt,
                "status": status if isinstance(status, str) else None,
            }
        )
        if isinstance(node_id, str) and node_id:
            key = (node_id, str(attempt or "unknown"))
            row = node_attempts.setdefault(
                key,
                {"node_id": node_id, "attempt": attempt, "events": [], "latest_status": None},
            )
            if isinstance(event, str):
                row["events"].append(event)
            if isinstance(status, str):
                row["latest_status"] = status
        if payload.get("schema") == ARTIFACT_DIGEST_SCHEMA:
            artifact_rows.append(
                {
                    "seq": seq,
                    "kind": kind,
                    "path": payload.get("relative_path") or payload.get("path"),
                    "sha256": payload.get("sha256"),
                    "bytes": payload.get("bytes"),
                    "json_schema": payload.get("json_schema"),
                }
            )
        if payload.get("schema") == AGENTIC_EVAL_RECEIPT_SCHEMA:
            agentic_eval_rows.append(
                {
                    "seq": seq,
                    "skill": payload.get("skill"),
                    "readiness": payload.get("readiness"),
                    "live": payload.get("live"),
                    "mocked": payload.get("mocked"),
                    "trial_count": payload.get("trial_count"),
                }
            )
    return {
        "schema": RUN_LEDGER_TRACE_SCHEMA,
        "entry_count": len(entries),
        "entry_kind_counts": {key: kinds[key] for key in sorted(kinds)},
        "timeline": timeline,
        "node_attempts": sorted(
            node_attempts.values(), key=lambda item: (str(item["node_id"]), str(item["attempt"]))
        ),
        "artifact_count": len(artifact_rows),
        "artifact_digests": artifact_rows,
        "agentic_eval_count": len(agentic_eval_rows),
        "agentic_evals": agentic_eval_rows,
    }


def write_ledger(path: Path, ledger: dict[str, Any]) -> None:
    resolved = path.expanduser().resolve()
    resolved.parent.mkdir(parents=True, exist_ok=True)
    resolved.write_text(json.dumps(ledger, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def read_ledger(path: Path) -> dict[str, Any]:
    return _read_json_object(path.expanduser().resolve())


def verify_ledger_file(path: Path) -> dict[str, Any]:
    resolved = path.expanduser().resolve()
    result = verify_ledger(read_ledger(resolved))
    return {**result, "ledger_path": str(resolved)}


def _read_json_object(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"expected JSON object: {path}")
    return payload


def verify_ledger(ledger: dict[str, Any]) -> dict[str, Any]:
    """Recompute the chain. Returns {ok, first_bad_index, reason, head_hash}.

    ok is False and first_bad_index names the earliest entry whose recomputed
    hash, prev linkage, or sequence does not match -- the officer's tamper check.
    """
    if ledger.get("schema") != RUN_LEDGER_SCHEMA:
        return {"ok": False, "first_bad_index": None, "reason": "wrong_schema"}
    goal_hash = ledger.get("goal_hash", "")
    prev = GENESIS
    for idx, entry in enumerate(ledger.get("entries", [])):
        if entry.get("seq") != idx:
            return {"ok": False, "first_bad_index": idx, "reason": "sequence_break"}
        if entry.get("prev_hash") != prev:
            return {"ok": False, "first_bad_index": idx, "reason": "prev_hash_mismatch"}
        recomputed = _entry_hash(
            prev, {"seq": entry.get("seq"), "payload": entry.get("payload")}, goal_hash
        )
        if entry.get("entry_hash") != recomputed:
            return {"ok": False, "first_bad_index": idx, "reason": "entry_hash_mismatch"}
        prev = entry["entry_hash"]
    if prev != ledger.get("head_hash"):
        return {
            "ok": False,
            "first_bad_index": len(ledger.get("entries", [])),
            "reason": "head_hash_mismatch",
        }
    if "trace" in ledger and ledger.get("trace") != _trace_from_chained_entries(
        list(ledger.get("entries", []))
    ):
        return {
            "ok": False,
            "first_bad_index": None,
            "reason": "trace_mismatch",
            "head_hash": prev,
        }
    return {"ok": True, "first_bad_index": None, "reason": None, "head_hash": prev}
