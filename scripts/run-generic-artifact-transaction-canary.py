#!/usr/bin/env python3
"""Run the live two-stage acceptance canary for Tau issue #71."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any

from tau_coding.generic_artifact_transaction import canonical_command_sha256
from tau_coding.generic_dag import run_generic_dag


def run_canary(
    *,
    output_dir: Path,
    reference: Path,
    model: str,
    approve_synthetic_continuation: bool,
    sequence_states: tuple[str, str] = ("visible-distinct-output", "visible-distinct-output"),
) -> dict[str, Any]:
    output_dir = output_dir.expanduser().resolve()
    reference = reference.expanduser().resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    if not reference.is_file():
        raise RuntimeError(f"reference image not found: {reference}")
    worker = (
        Path(__file__).resolve().parents[1]
        / "src/tau_coding/generic_artifact_canary_worker.py"
    )
    approval_path = output_dir / "stage-2-approval.json"
    continuation_marker = output_dir / "continuation-marker.json"
    continuation = [
        sys.executable,
        str(worker),
        "continue",
        "--marker",
        str(continuation_marker),
    ]
    spec_path = _write_spec(
        output_dir=output_dir,
        reference=reference,
        worker=worker,
        model=model,
        approval_path=approval_path,
        continuation=continuation,
        sequence_states=sequence_states,
    )
    first = run_generic_dag(spec_path=spec_path, resume=True)
    _require_first_rung(first, continuation_marker=continuation_marker)
    first_receipt_path = output_dir / "run-receipt-before-approval.json"
    _write_json(first_receipt_path, first)
    stage_2 = first["nodes"][1]
    if not approve_synthetic_continuation:
        return _summary(
            output_dir=output_dir,
            reference=reference,
            first=first,
            final=None,
            continuation_marker=continuation_marker,
            approval_path=approval_path,
            status="BLOCKED",
            verdict="APPROVAL_REQUIRED",
        )
    target = {
        "id": "generic-dag-transaction:issue-71-live-canary:tx-stage-2",
        "run_id": "issue-71-live-canary",
        "node_id": "stage-2",
        "transaction_id": "tx-stage-2",
        "accepted_manifest_sha256": stage_2["accepted_manifest_sha256"],
        "continuation_command_sha256": canonical_command_sha256(continuation),
    }
    _write_json(
        approval_path,
        {
            "schema": "tau.human_approval_packet.v1",
            "approved": True,
            "actor": {"id": "human:user-directed-canary", "auth_method": "manual"},
            "action": "generic_dag_transaction_continue",
            "target": target,
            "reason": "User explicitly required the full local acceptance canary.",
            "evidence": [str(first_receipt_path)],
            "nonce": "issue-71-live-canary-local-continuation",
            "signature": "DECLARED_MANUAL_TEST_AUTHORIZATION_NOT_CRYPTOGRAPHIC",
        },
    )
    producer_counts_before = {
        stage: (output_dir / f"{stage}-producer-count.txt").read_text(encoding="utf-8")
        for stage in ("stage-1", "stage-2")
    }
    final = run_generic_dag(spec_path=spec_path, resume=True)
    if final["status"] != "PASS" or final["provider_live"] is not True:
        raise RuntimeError(f"final canary did not pass provider-live: {final['verdict']}")
    for stage, before in producer_counts_before.items():
        after = (output_dir / f"{stage}-producer-count.txt").read_text(encoding="utf-8")
        if after != before:
            raise RuntimeError(f"resume reran producer for {stage}")
    if not continuation_marker.is_file():
        raise RuntimeError("approval-gated continuation marker missing")
    final_receipt_path = output_dir / "run-receipt-after-approval.json"
    _write_json(final_receipt_path, final)
    summary = _summary(
        output_dir=output_dir,
        reference=reference,
        first=first,
        final=final,
        continuation_marker=continuation_marker,
        approval_path=approval_path,
        status="PASS",
        verdict="PASS",
    )
    _write_json(output_dir / "canary-receipt.json", summary)
    return summary


def _write_spec(
    *,
    output_dir: Path,
    reference: Path,
    worker: Path,
    model: str,
    approval_path: Path,
    continuation: list[str],
    sequence_states: tuple[str, str],
) -> Path:
    nodes: list[dict[str, Any]] = []
    for stage_number in (1, 2):
        stage = f"stage-{stage_number}"
        sequence_contract = output_dir / f"{stage}-sequence-contract.json"
        _write_json(
            sequence_contract,
            {
                "schema": "tau.generic_sequence_contract.v1",
                "sequence_id": stage,
                "required_state": sequence_states[stage_number - 1],
                "must_differ_from_accepted_inputs": stage_number == 2,
            },
        )
        work_order = output_dir / f"{stage}-work-order.json"
        _write_json(
            work_order,
            {
                "task": "produce one admissible image artifact",
                "stage": stage,
                "immutable_reference": str(reference),
                "immutable_reference_sha256": _sha256(reference),
                "sequence_contract": str(sequence_contract),
                "sequence_contract_sha256": _sha256(sequence_contract),
            },
        )
        transaction: dict[str, Any] = {
            "schema": "tau.generic_artifact_transaction.v1",
            "transaction_id": f"tx-{stage}",
            "artifact_root": str(output_dir / "artifacts" / stage),
            "producer_id": f"{stage}-scillm-image-producer",
            "acceptance": {
                "require_provider_live_producer": True,
                "require_provider_live_reviewer": True,
                "require_output_change_after_revise": True,
                "require_distinct_from_accepted_inputs": stage_number == 2,
            },
            "reviewer": {
                "reviewer_id": f"{stage}-scillm-vlm-reviewer",
                "command": [
                    sys.executable,
                    str(worker),
                    "review",
                    "--model",
                    model,
                ],
                "timeout_seconds": 240,
            },
        }
        if stage_number == 2:
            transaction["continuation"] = {
                "command": continuation,
                "timeout_seconds": 30,
                "approval": {
                    "action": "generic_dag_transaction_continue",
                    "packet_path": str(approval_path),
                },
            }
        nodes.append(
            {
                "node_id": stage,
                "role": "live-image-transaction",
                "command": [
                    sys.executable,
                    str(worker),
                    "produce",
                    "--stage",
                    stage,
                    "--reference",
                    str(reference),
                    "--artifact-root",
                    str(output_dir / "artifacts" / stage),
                    "--receipt",
                    str(output_dir / f"{stage}-producer-receipt.json"),
                    "--work-order",
                    str(work_order),
                    "--counter",
                    str(output_dir / f"{stage}-producer-count.txt"),
                    "--sequence-contract",
                    str(sequence_contract),
                ],
                "depends_on": [] if stage_number == 1 else ["stage-1"],
                "receipt_path": str(output_dir / f"{stage}-producer-receipt.json"),
                "work_order_path": str(work_order),
                "timeout_seconds": 360,
                "max_attempts": 2,
                "transaction": transaction,
            }
        )
    spec_path = output_dir / "dag.json"
    _write_json(
        spec_path,
        {
            "schema": "tau.generic_dag_spec.v1",
            "run_id": "issue-71-live-canary",
            "run_dir": str(output_dir),
            "nodes": nodes,
        },
    )
    return spec_path


def _require_first_rung(receipt: dict[str, Any], *, continuation_marker: Path) -> None:
    if receipt["status"] != "BLOCKED" or receipt["verdict"] != "APPROVAL_REQUIRED":
        raise RuntimeError(f"first canary rung must stop for approval: {receipt['verdict']}")
    if receipt["provider_live"] is not True:
        raise RuntimeError("first canary rung must include live provider review")
    stage_1, stage_2 = receipt["nodes"]
    verdicts = [attempt["review_verdict"] for attempt in stage_1["attempts"]]
    if verdicts != ["REVISE", "PASS"]:
        raise RuntimeError(f"stage-1 must revise then pass, got {verdicts}")
    if stage_2["transaction_state"] != "APPROVAL_REQUIRED":
        raise RuntimeError("stage-2 must retain accepted state while awaiting approval")
    if stage_1["producer_provider_live"] is not True:
        raise RuntimeError("stage-1 accepted output must come from a live provider producer")
    if stage_2["producer_provider_live"] is not True:
        raise RuntimeError("stage-2 accepted output must come from a live provider producer")
    stage_1_accepted = stage_1["artifacts"][0]
    stage_2_accepted_manifest = _read_json(Path(stage_2["accepted_manifest_path"]))
    stage_2_accepted = stage_2_accepted_manifest["artifacts"][0]
    stage_2_attempt_context = _read_json(
        Path(stage_2["attempts"][0]["attempt_context_path"])
    )
    serialized_context = json.dumps(stage_2_attempt_context, sort_keys=True)
    if stage_1_accepted["path"] not in serialized_context:
        raise RuntimeError("stage-2 did not receive stage-1 accepted artifact")
    rejected_manifest = _read_json(Path(stage_1["attempts"][0]["candidate_manifest_path"]))
    rejected_path = str(rejected_manifest["artifacts"][0]["path"])
    if rejected_path in serialized_context:
        raise RuntimeError("rejected stage-1 artifact leaked into stage-2 context")
    if stage_1_accepted["sha256"] == stage_2_accepted["sha256"]:
        raise RuntimeError("stage-2 output duplicates the accepted stage-1 output")
    if continuation_marker.exists():
        raise RuntimeError("continuation executed before approval")


def _summary(
    *,
    output_dir: Path,
    reference: Path,
    first: dict[str, Any],
    final: dict[str, Any] | None,
    continuation_marker: Path,
    approval_path: Path,
    status: str,
    verdict: str,
) -> dict[str, Any]:
    authoritative = final or first
    return {
        "schema": "tau.generic_artifact_transaction_live_canary.v1",
        "ok": status == "PASS",
        "status": status,
        "verdict": verdict,
        "mocked": False,
        "live": True,
        "provider_live": authoritative["provider_live"],
        "model": authoritative["nodes"][0]["attempts"][0]["review_model"],
        "run_dir": str(output_dir),
        "reference": {"path": str(reference), "sha256": _sha256(reference)},
        "first_run": {
            "status": first["status"],
            "verdict": first["verdict"],
            "receipt": str(output_dir / "run-receipt-before-approval.json"),
        },
        "final_run": (
            {
                "status": final["status"],
                "verdict": final["verdict"],
                "receipt": str(output_dir / "run-receipt-after-approval.json"),
            }
            if final
            else None
        ),
        "stage_1_review_verdicts": [
            item["review_verdict"] for item in first["nodes"][0]["attempts"]
        ],
        "stage_1_accepted_manifest_sha256": first["nodes"][0][
            "accepted_manifest_sha256"
        ],
        "stage_2_accepted_manifest_sha256": first["nodes"][1][
            "accepted_manifest_sha256"
        ],
        "approval_packet": str(approval_path),
        "continuation_marker": str(continuation_marker),
        "claims": {
            "proves": [
                "Live Scillm image producers and a live Scillm VLM reviewer ran through Tau.",
                "Stage 1 revised then accepted a hash-bound image artifact.",
                "Stage 2 consumed only Stage 1's accepted manifest projection.",
                "The continuation did not execute before an exact approval binding.",
                "Resume did not rerun accepted producer work.",
            ],
            "does_not_prove": [
                "Provider or model semantic quality beyond this canary.",
                "Reviewer truthfulness or future route correctness.",
                "Cryptographic or legal authority of the declared test approval.",
                "Sandbox isolation or protection from direct filesystem discovery.",
            ],
        },
    }


def _read_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise RuntimeError(f"expected JSON object: {path}")
    return payload


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument(
        "--reference",
        type=Path,
        default=Path("docs/assets/tau-header.webp"),
    )
    parser.add_argument("--model", default="gpt-5.5")
    parser.add_argument("--sequence-state-1", default="visible-distinct-output")
    parser.add_argument("--sequence-state-2", default="visible-distinct-output")
    parser.add_argument("--approve-synthetic-continuation", action="store_true")
    args = parser.parse_args()
    receipt = run_canary(
        output_dir=args.out,
        reference=args.reference,
        model=args.model,
        approve_synthetic_continuation=args.approve_synthetic_continuation,
        sequence_states=(args.sequence_state_1, args.sequence_state_2),
    )
    print(json.dumps(receipt, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
