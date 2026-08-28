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
from datetime import UTC, datetime
from typing import Any

RUN_LEDGER_SCHEMA = "tau.run_ledger.v1"
AGENTIC_EVAL_RECEIPT_SCHEMA = "tau.agentic_eval_receipt.v1"
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
        cases.append({
            "name": case.get("name"),
            "type": case.get("type"),
            "real_world": bool(case.get("real_world")),
            "outcomes": outcomes,
            "passed": bool(outcomes) and all(o == "PASS" for o in outcomes),
        })
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
    return {
        "schema": RUN_LEDGER_SCHEMA,
        "run_id": run_id,
        "dag_id": dag_id,
        "goal_hash": goal_hash,
        "built_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "entry_count": len(chained),
        "head_hash": prev,
        "entries": chained,
    }


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
        recomputed = _entry_hash(prev, {"seq": entry.get("seq"), "payload": entry.get("payload")}, goal_hash)
        if entry.get("entry_hash") != recomputed:
            return {"ok": False, "first_bad_index": idx, "reason": "entry_hash_mismatch"}
        prev = entry["entry_hash"]
    if prev != ledger.get("head_hash"):
        return {"ok": False, "first_bad_index": len(ledger.get("entries", [])), "reason": "head_hash_mismatch"}
    return {"ok": True, "first_bad_index": None, "reason": None, "head_hash": prev}
