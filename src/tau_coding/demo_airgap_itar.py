"""One-command synthetic airgap ITAR-style demo."""

from __future__ import annotations

import hashlib
import json
import shutil
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from tau_coding.airgap_no_egress import write_airgap_no_egress_receipt
from tau_coding.evidence_manifest import write_evidence_validation_receipt
from tau_coding.init_project import initialize_tau_project
from tau_coding.itar_contract import write_itar_contract_receipt
from tau_coding.local_provider_readiness import write_local_provider_readiness_receipt
from tau_coding.proof_index import build_proof_index
from tau_coding.run_status import build_run_status
from tau_coding.sparta_posture import write_sparta_posture_contract

DEMO_RECEIPT_SCHEMA = "tau.demo_airgap_itar_basic_receipt.v1"
GOAL_HASH = "sha256:airgap-itar-basic-synthetic-demo"


def run_demo_airgap_itar_basic(
    *,
    out: Path,
    provider_url: str = "http://127.0.0.1:4001",
    model: str = "local-kimi-k2.6",
    live_provider: bool = False,
    live_airgap_probe: bool = False,
) -> dict[str, Any]:
    """Create the synthetic external-review demo artifact bundle."""

    run_dir = out.expanduser().resolve()
    run_dir.mkdir(parents=True, exist_ok=True)
    initialize_tau_project(out_dir=run_dir, profile="itar-airgap", force=True)
    _copy(run_dir / ".tau" / "policy-profile.json", run_dir / "policy-profile.json")
    _copy(run_dir / ".tau" / "data-boundary.json", run_dir / "data-boundary.json")
    _copy(run_dir / ".tau" / "dag-template.json", run_dir / "dag-contract.json")
    clause_path = run_dir / "synthetic-contract-clause.txt"
    _write_synthetic_clause(clause_path)

    provider_receipt = write_local_provider_readiness_receipt(
        provider_url=provider_url,
        model=model,
        out=run_dir / "local-provider-readiness-receipt.json",
        airgap_mode=True,
        allow_unavailable_demo=not live_provider,
    )
    airgap_receipt = write_airgap_no_egress_receipt(
        out=run_dir / "airgap-no-egress-receipt.json",
        allowed_local_endpoints=["127.0.0.1:4001", "127.0.0.1:8601"],
        assume_no_egress_demo=not live_airgap_probe,
    )
    itar_receipt = write_itar_contract_receipt(
        clause=clause_path,
        policy_profile=run_dir / "policy-profile.json",
        data_boundary=run_dir / "data-boundary.json",
        out=run_dir / "itar-contract-receipt.json",
    )
    _write_evidence_manifest(run_dir)
    evidence_receipt = write_evidence_validation_receipt(
        manifest_path=run_dir / "evidence-manifest.json",
        receipt_path=run_dir / "evidence-validation-receipt.json",
    )
    _write_dag_receipt(run_dir, itar_receipt=itar_receipt)
    posture = write_sparta_posture_contract(
        run_dir=run_dir,
        out=run_dir / "sparta-posture-contract.json",
    )
    demo_receipt = _demo_receipt(
        run_dir=run_dir,
        provider_receipt=provider_receipt,
        airgap_receipt=airgap_receipt,
        evidence_receipt=evidence_receipt,
        proof_index_receipt=None,
        posture=posture,
        live_provider=live_provider,
        live_airgap_probe=live_airgap_probe,
    )
    _write_json(run_dir / "run-receipt.json", demo_receipt)
    proof_index_receipt = build_proof_index(
        run_dir,
        output_path=run_dir / "proof-index.jsonl",
        receipt_path=run_dir / "proof-index-receipt.json",
    )
    demo_receipt = _demo_receipt(
        run_dir=run_dir,
        provider_receipt=provider_receipt,
        airgap_receipt=airgap_receipt,
        evidence_receipt=evidence_receipt,
        proof_index_receipt=proof_index_receipt,
        posture=posture,
        live_provider=live_provider,
        live_airgap_probe=live_airgap_probe,
    )
    _write_json(run_dir / "run-receipt.json", demo_receipt)
    run_status = build_run_status(run_dir)
    _write_json(run_dir / "run-status.json", run_status)
    return demo_receipt


def _demo_receipt(
    *,
    run_dir: Path,
    provider_receipt: dict[str, Any],
    airgap_receipt: dict[str, Any],
    evidence_receipt: dict[str, Any],
    proof_index_receipt: dict[str, Any] | None,
    posture: dict[str, Any],
    live_provider: bool,
    live_airgap_probe: bool,
) -> dict[str, Any]:
    required_receipts = [provider_receipt, airgap_receipt, evidence_receipt]
    if proof_index_receipt is not None:
        required_receipts.append(proof_index_receipt)
    ok = all(receipt.get("ok") is True for receipt in required_receipts) and (
        posture.get("readiness", {}).get("status") == "NOT_SIGNOFF_READY"
    )
    return {
        "schema": DEMO_RECEIPT_SCHEMA,
        "ok": ok,
        "status": "PASS" if ok else "BLOCKED",
        "mocked": not (live_provider or live_airgap_probe),
        "live": True,
        "provider_live": bool(provider_receipt.get("provider_live")),
        "demo_verdict": posture.get("readiness", {}).get("status"),
        "gate": posture.get("readiness", {}).get("gate"),
        "top_blocker": _top_blocker_code(posture),
        "run_dir": str(run_dir),
        "sparta_posture_contract": str(run_dir / "sparta-posture-contract.json"),
        "receipts": {
            "local_provider": str(run_dir / "local-provider-readiness-receipt.json"),
            "airgap_no_egress": str(run_dir / "airgap-no-egress-receipt.json"),
            "itar_contract": str(run_dir / "itar-contract-receipt.json"),
            "evidence_validation": str(run_dir / "evidence-validation-receipt.json"),
            "proof_index": str(run_dir / "proof-index.jsonl"),
            "run_status": str(run_dir / "run-status.json"),
        },
        "non_claims": [
            "Synthetic data only.",
            "Does not prove ITAR compliance.",
            "Does not prove model approval.",
            "Does not prove airgap certification.",
            "Does not prove human approval.",
        ],
        "created_at": _utc_stamp(),
    }


def _write_evidence_manifest(run_dir: Path) -> None:
    items = []
    for kind, filename in (
        ("policy_profile", "policy-profile.json"),
        ("data_boundary", "data-boundary.json"),
        ("local_provider", "local-provider-readiness-receipt.json"),
        ("airgap_no_egress", "airgap-no-egress-receipt.json"),
        ("itar_contract", "itar-contract-receipt.json"),
    ):
        path = run_dir / filename
        items.append(
            {
                "kind": kind,
                "path": filename,
                "sha256": f"sha256:{_sha256(path)}",
            }
        )
    _write_json(
        run_dir / "evidence-manifest.json",
        {
            "schema": "tau.evidence_manifest.v1",
            "run_id": "airgap-itar-basic",
            "dag_id": "airgap-itar-basic",
            "goal_hash": GOAL_HASH,
            "items": items,
        },
    )


def _write_dag_receipt(run_dir: Path, *, itar_receipt: dict[str, Any]) -> None:
    _write_json(
        run_dir / "dag-receipt.json",
        {
            "schema": "tau.dag_receipt.v1",
            "ok": False,
            "status": "BLOCKED",
            "verdict": "approval_required",
            "mocked": False,
            "live": True,
            "provider_live": False,
            "dag_id": "airgap-itar-basic",
            "goal_hash": GOAL_HASH,
            "contract_path": str(run_dir / "dag-contract.json"),
            "failed_node": "human-export-control-review",
            "top_blocker": "human_export_control_review_required",
            "source_receipt": str(run_dir / "itar-contract-receipt.json"),
            "alert_codes": itar_receipt.get("alert_codes", []),
            "proof_scope": {
                "proves": [
                    "Tau assembled the synthetic airgap ITAR demo receipt chain.",
                    "Tau preserved a human approval blocker instead of claiming signoff.",
                ],
                "does_not_prove": [
                    "ITAR compliance.",
                    "Human approval.",
                    "Operational readiness.",
                    "Provider/model semantic quality.",
                ],
            },
        },
    )


def _write_synthetic_clause(path: Path) -> None:
    source = Path("examples/airgap-itar-basic/synthetic-contract-clause.txt")
    if source.exists():
        _copy(source, path)
        return
    path.write_text(
        "Synthetic Clause SC-001: design drawings, test procedures, "
        "manufacturing process notes, foreign-person access, and external release.\n",
        encoding="utf-8",
    )


def _copy(source: Path, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, target)


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _top_blocker_code(posture: dict[str, Any]) -> str | None:
    blockers = posture.get("top_blockers")
    if isinstance(blockers, list) and blockers:
        first = blockers[0]
        if isinstance(first, dict):
            code = first.get("code")
            return code if isinstance(code, str) else None
    return None


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _utc_stamp() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")
