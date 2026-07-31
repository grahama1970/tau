"""Non-mocked conformance receipt for Tau execution profiles."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from tau_coding.dag_runtime.compiler import compile_generic_dag_plan, write_dag_plan
from tau_coding.dag_runtime.execution_profile import (
    EXECUTION_PROFILE_IDS,
    evaluate_profile_revision,
)
from tau_coding.generic_dag import run_generic_dag

EXECUTION_PROFILE_CONFORMANCE_SCHEMA = "tau.execution_profile_conformance.v1"


def write_execution_profile_conformance(
    output: Path,
    *,
    allow_live_filesystem: bool,
) -> dict[str, Any]:
    if not allow_live_filesystem:
        raise RuntimeError("--allow-live-filesystem is required")
    resolved_output = output.expanduser().resolve()
    proof_dir = resolved_output.parent
    proof_dir.mkdir(parents=True, exist_ok=True)

    profile_runs: dict[str, dict[str, Any]] = {}
    plan_hashes: set[str] = set()
    for profile_id in EXECUTION_PROFILE_IDS:
        profile_dir = proof_dir / profile_id
        profile_dir.mkdir(parents=True, exist_ok=True)
        spec_path = profile_dir / "dag.json"
        plan_path = profile_dir / "plan.json"
        spec = _profile_spec(profile_dir, profile_id=profile_id)
        _write_json(spec_path, spec)
        compile_receipt = write_dag_plan(spec_path, output_path=plan_path)
        plan = compile_generic_dag_plan(spec, source_path=spec_path)
        run_receipt = run_generic_dag(spec_path=spec_path, resume=False)
        plan_payload = plan.to_payload()
        resolution = plan_payload["source_extensions"]["execution_profile_resolution"]
        profile_runs[profile_id] = {
            "spec_path": str(spec_path),
            "plan_path": str(plan_path),
            "plan_sha256": plan.plan_sha256,
            "compile_receipt": compile_receipt,
            "run_receipt_path": str(profile_dir / "run" / "run-receipt.json"),
            "run_status": run_receipt["status"],
            "run_verdict": run_receipt["verdict"],
            "scheduler": run_receipt["scheduler"],
            "run_execution_profile": run_receipt.get("execution_profile"),
            "dag_plan_sha256": run_receipt["dag_plan_sha256"],
            "resolution_sha256": resolution["resolution_sha256"],
            "resolved_controls_sha256": resolution["resolved_controls_sha256"],
            "resolved_controls": resolution["resolved_controls"],
        }
        plan_hashes.add(plan.plan_sha256)

    standard_resolution = json.loads(
        Path(profile_runs["standard"]["plan_path"]).read_text(encoding="utf-8")
    )["source_extensions"]["execution_profile_resolution"]
    downgrade = evaluate_profile_revision(
        standard_resolution,
        "interactive",
        approved_strengthening=False,
    )
    strengthening = evaluate_profile_revision(
        standard_resolution,
        "assurance",
        approved_strengthening=True,
    )
    checks = {
        "all_three_profiles_ran_same_scheduler": all(
            item["scheduler"] == "dag_plan_ready_queue" for item in profile_runs.values()
        ),
        "all_runs_passed": all(item["run_status"] == "PASS" for item in profile_runs.values()),
        "all_plan_hashes_distinct": len(plan_hashes) == len(EXECUTION_PROFILE_IDS),
        "compile_receipts_surface_profile": all(
            item["compile_receipt"]["execution_profile"]["profile_id"] == profile_id
            for profile_id, item in profile_runs.items()
        ),
        "run_receipts_bind_profiled_plan_hash": all(
            item["run_receipt_path"] and item["dag_plan_sha256"] == item["plan_sha256"]
            for item in profile_runs.values()
        ),
        "run_receipts_surface_profile": all(
            isinstance(item["run_execution_profile"], dict)
            and item["run_execution_profile"].get("profile_id") == profile_id
            for profile_id, item in profile_runs.items()
        ),
        "mid_run_downgrade_rejected": downgrade["verdict"] == "PROFILE_DOWNGRADE_REJECTED",
        "approved_strengthening_requires_new_plan": (
            strengthening["status"] == "PASS" and strengthening["new_plan_required"] is True
        ),
    }
    failed = [name for name, ok in checks.items() if ok is not True]
    payload = {
        "schema": EXECUTION_PROFILE_CONFORMANCE_SCHEMA,
        "status": "PASS" if not failed else "BLOCKED",
        "mocked": False,
        "live": True,
        "provider_live": False,
        "failed_checks": failed,
        "checks": checks,
        "profile_runs": profile_runs,
        "revision_checks": {
            "downgrade": downgrade,
            "approved_strengthening": strengthening,
        },
        "proof_scope": {
            "proves": [
                "All three execution profiles compile through the canonical DagPlan compiler.",
                "All three execution profiles run through the same dag_plan_ready_queue scheduler.",
                "Profile resolution is hash-bound in the compiled plan and surfaced by dag-plan.",
                (
                    "Profile downgrade is rejected and approved strengthening requires "
                    "a new plan path."
                ),
                "Generic DAG run receipts surface the selected profile.",
            ],
            "does_not_prove": [
                "Provider/model semantic quality.",
                "Compliance certification.",
                "Future optional evidence schemas that do not yet exist.",
            ],
        },
        "checked_at": datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
    }
    _write_json(resolved_output, payload)
    return payload


def _profile_spec(root: Path, *, profile_id: str) -> dict[str, Any]:
    receipt_path = root / "receipt.json"
    return {
        "schema": "tau.generic_dag_spec.v1",
        "run_id": f"execution-profile-{profile_id}",
        "run_dir": str(root / "run"),
        "execution_profile": profile_id,
        "nodes": [
            {
                "node_id": "worker",
                "role": "worker",
                "command": [
                    sys.executable,
                    "-c",
                    _receipt_writer_code(receipt_path, node_id="worker", profile_id=profile_id),
                ],
                "receipt_path": str(receipt_path),
                "timeout_seconds": 5,
                "max_attempts": 1,
            }
        ],
    }


def _receipt_writer_code(receipt_path: Path, *, node_id: str, profile_id: str) -> str:
    payload = {
        "schema": "tau.generic_dag_node_receipt.v1",
        "node_id": node_id,
        "status": "PASS",
        "verdict": "PASS",
        "mocked": False,
        "live": False,
        "provider_live": False,
        "accepted_output": {"profile_id": profile_id},
        "artifacts": [],
        "commands_run": ["python execution profile receipt writer"],
        "handoff_summary": f"{node_id} finished under {profile_id}",
        "errors": [],
        "policy_exceptions": [],
    }
    return (
        "import json; "
        "from pathlib import Path; "
        f"path = Path({str(receipt_path)!r}); "
        "path.parent.mkdir(parents=True, exist_ok=True); "
        f"path.write_text(json.dumps({payload!r}, sort_keys=True), encoding='utf-8')"
    )


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", required=True)
    parser.add_argument("--allow-live-filesystem", action="store_true")
    args = parser.parse_args()
    payload = write_execution_profile_conformance(
        Path(args.output),
        allow_live_filesystem=args.allow_live_filesystem,
    )
    print(json.dumps(payload, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
