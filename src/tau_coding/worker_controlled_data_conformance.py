"""Live worker and controlled-data denial/correction conformance receipt."""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from tau_coding.coding_worker_adapters import (
    OMP_WORK_ORDER_SCHEMA,
    write_omp_worker_receipt,
)
from tau_coding.itar_boundary import write_itar_access_preflight_receipt
from tau_coding.policy_profile import DATA_BOUNDARY_SCHEMA, POLICY_PROFILE_SCHEMA

WORKER_CONTROLLED_DATA_CONFORMANCE_SCHEMA = "tau.worker_controlled_data_conformance.v1"
LOCAL_WORKER_READINESS_RECEIPT_SCHEMA = "tau.local_worker_readiness_receipt.v1"
LOCAL_WORKER_EXECUTION_RECEIPT_SCHEMA = "tau.local_worker_execution_receipt.v1"
CONTROLLED_DATA_CORRECTION_ACTION_SCHEMA = "tau.controlled_data_correction_action.v1"
CONTROLLED_DATA_CORRECTION_VALIDATION_SCHEMA = "tau.controlled_data_correction_validation.v1"
RUN_ID = "worker-controlled-data-conformance-run"
DAG_ID = "worker-controlled-data-conformance-dag"
NODE_ID = "controlled-data-worker"
GOAL_HASH = "sha256:worker-controlled-data-conformance"
RESTRICTED_MARKER = "TAU_CONTROLLED_DATA_NEVER_LEAK_193"


def write_worker_controlled_data_conformance(
    output: Path,
    *,
    allow_live_worker: bool,
    allow_live_filesystem: bool,
) -> dict[str, Any]:
    """Exercise a live worker process and controlled-data denial/correction path."""

    if not allow_live_worker:
        raise RuntimeError("--allow-live-worker is required")
    if not allow_live_filesystem:
        raise RuntimeError("--allow-live-filesystem is required")
    resolved_output = output.expanduser().resolve()
    proof_dir = resolved_output.parent
    artifacts_dir = proof_dir / "artifacts"
    repo_dir = artifacts_dir / "worker-repo"
    receipt_dir = artifacts_dir / "receipts"
    controlled_dir = artifacts_dir / "controlled-data"
    unauthorized_dir = artifacts_dir / "unauthorized-outputs"
    correction_dir = artifacts_dir / "correction"
    for directory in (repo_dir, receipt_dir, controlled_dir, unauthorized_dir, correction_dir):
        directory.mkdir(parents=True, exist_ok=True)

    readiness_receipt = _write_worker_readiness(receipt_dir / "worker-readiness.json")
    work_order_path = artifacts_dir / "worker-work-order.json"
    worker_script_path = artifacts_dir / "local_worker.py"
    _write_json(work_order_path, _work_order(repo_dir=repo_dir))
    _write_worker_script(worker_script_path)
    worker_execution_receipt = _run_local_worker(
        script_path=worker_script_path,
        work_order_path=work_order_path,
        receipt_path=receipt_dir / "worker-execution.json",
    )
    worker_validation_receipt = write_omp_worker_receipt(
        work_order_path=work_order_path,
        result_path=repo_dir / "worker-result.json",
        output_path=receipt_dir / "worker-validation.json",
    )

    controlled_source = controlled_dir / "controlled-source.txt"
    controlled_source.write_text(
        (
            "Synthetic export-controlled engineering note. "
            f"Restricted marker: {RESTRICTED_MARKER}. "
            "Manufacturing process details require verified authorization.\n"
        ),
        encoding="utf-8",
    )
    policy_path = controlled_dir / "policy-profile.json"
    boundary_path = controlled_dir / "data-boundary.json"
    unauthorized_actor_path = controlled_dir / "unauthorized-actor.json"
    authorized_actor_path = controlled_dir / "authorized-actor.json"
    approval_path = controlled_dir / "approval-packet.json"
    unauthorized_output_path = unauthorized_dir / "denied-response.txt"
    unauthorized_output_path.write_text(
        "DENIED: controlled data requires verified human authorization.\n",
        encoding="utf-8",
    )
    _write_json(policy_path, _policy_profile())
    _write_json(boundary_path, _data_boundary())
    _write_json(unauthorized_actor_path, _actor_manifest(authorized=False))
    _write_json(authorized_actor_path, _actor_manifest(authorized=True))
    _write_json(approval_path, _approval_packet())

    denial_receipt = write_itar_access_preflight_receipt(
        actor_manifest_path=unauthorized_actor_path,
        data_boundary_path=boundary_path,
        receipt_path=receipt_dir / "controlled-data-denial.json",
        required_boundary="ITAR",
    )
    authorization_receipt = write_itar_access_preflight_receipt(
        actor_manifest_path=authorized_actor_path,
        data_boundary_path=boundary_path,
        receipt_path=receipt_dir / "correction-authorization.json",
        approval_packet_path=approval_path,
        required_boundary="ITAR",
    )
    correction_action_receipt = _write_correction_action(
        controlled_source=controlled_source,
        authorization_receipt=authorization_receipt,
        receipt_path=receipt_dir / "correction-action.json",
        corrected_artifact=correction_dir / "corrected-redacted-summary.txt",
    )
    worker_output = repo_dir / "reports" / "worker-output.json"
    correction_validation_receipt = _write_correction_validation(
        unauthorized_outputs=[unauthorized_output_path, worker_output],
        corrected_artifact=correction_dir / "corrected-redacted-summary.txt",
        receipt_path=receipt_dir / "correction-validation.json",
    )

    checks = {
        "worker_readiness_receipt_present": readiness_receipt.get("status") == "PASS"
        and readiness_receipt.get("process_executed") is True,
        "worker_execution_process_ran": worker_execution_receipt.get("status") == "PASS"
        and worker_execution_receipt.get("exit_code") == 0,
        "worker_result_artifact_validated": worker_validation_receipt.get("status") == "PASS",
        "controlled_data_denial_receipt_present": denial_receipt.get("status") == "BLOCKED"
        and bool(denial_receipt.get("alert_codes")),
        "correction_authorization_receipt_present": authorization_receipt.get("status") == "PASS",
        "correction_action_receipt_present": correction_action_receipt.get("status") == "PASS",
        "correction_validation_receipt_present": correction_validation_receipt.get("status")
        == "PASS",
        "no_restricted_data_leaked_into_unauthorized_outputs": correction_validation_receipt.get(
            "unauthorized_leak_count"
        )
        == 0,
    }
    failed_checks = [name for name, passed in checks.items() if passed is not True]
    payload = {
        "schema": WORKER_CONTROLLED_DATA_CONFORMANCE_SCHEMA,
        "status": "PASS" if not failed_checks else "BLOCKED",
        "mocked": False,
        "live": True,
        "provider_live": False,
        "output": str(resolved_output),
        "proof_dir": str(proof_dir),
        "artifacts_dir": str(artifacts_dir),
        "worker_readiness_receipt": str((receipt_dir / "worker-readiness.json").resolve()),
        "worker_execution_receipt": str((receipt_dir / "worker-execution.json").resolve()),
        "worker_result_artifact": str((repo_dir / "worker-result.json").resolve()),
        "worker_validation_receipt": str((receipt_dir / "worker-validation.json").resolve()),
        "controlled_data_denial_receipt": str(
            (receipt_dir / "controlled-data-denial.json").resolve()
        ),
        "correction_authorization_receipt": str(
            (receipt_dir / "correction-authorization.json").resolve()
        ),
        "correction_action_receipt": str((receipt_dir / "correction-action.json").resolve()),
        "correction_validation_receipt": str(
            (receipt_dir / "correction-validation.json").resolve()
        ),
        "unauthorized_outputs": [
            str(unauthorized_output_path.resolve()),
            str((repo_dir / "reports" / "worker-output.json").resolve()),
        ],
        "checks": checks,
        "failed_checks": failed_checks,
        "proof_scope": {
            "proves": [
                "Tau launched an available local Python worker subprocess.",
                "Tau validated the worker's structured result artifact through the "
                "existing OMP worker-result adapter.",
                "Tau denied controlled-data access for an unverified actor.",
                "Tau accepted a correction path only after a verified human approval "
                "receipt and validated that unauthorized outputs did not contain the "
                "restricted marker.",
            ],
            "does_not_prove": [
                "Provider/model semantic quality.",
                "ITAR legal sufficiency.",
                "Authorization to process real controlled technical data.",
                "That OMP or SciLLM provider workers were launched.",
                "Absence of every possible leak outside the enumerated unauthorized outputs.",
            ],
        },
        "checked_at": _now(),
    }
    _write_json(resolved_output, payload)
    return payload


def _write_worker_readiness(receipt_path: Path) -> dict[str, Any]:
    completed = subprocess.run(
        [sys.executable, "--version"],
        text=True,
        capture_output=True,
        timeout=10,
        check=False,
    )
    payload = {
        "schema": LOCAL_WORKER_READINESS_RECEIPT_SCHEMA,
        "status": "PASS" if completed.returncode == 0 else "BLOCKED",
        "mocked": False,
        "live": True,
        "provider_live": False,
        "worker_kind": "local-python-subprocess",
        "command": [sys.executable, "--version"],
        "process_executed": True,
        "exit_code": completed.returncode,
        "stdout": completed.stdout,
        "stderr": completed.stderr,
        "python_executable": sys.executable,
        "receipt_path": str(receipt_path.resolve()),
        "checked_at": _now(),
    }
    _write_json(receipt_path, payload)
    return payload


def _run_local_worker(
    *,
    script_path: Path,
    work_order_path: Path,
    receipt_path: Path,
) -> dict[str, Any]:
    stdout_path = receipt_path.with_suffix(receipt_path.suffix + ".stdout.txt")
    stderr_path = receipt_path.with_suffix(receipt_path.suffix + ".stderr.txt")
    completed = subprocess.run(
        [sys.executable, str(script_path), str(work_order_path)],
        text=True,
        capture_output=True,
        timeout=30,
        check=False,
    )
    stdout_path.write_text(completed.stdout, encoding="utf-8")
    stderr_path.write_text(completed.stderr, encoding="utf-8")
    payload = {
        "schema": LOCAL_WORKER_EXECUTION_RECEIPT_SCHEMA,
        "status": "PASS" if completed.returncode == 0 else "BLOCKED",
        "mocked": False,
        "live": True,
        "provider_live": False,
        "worker_kind": "local-python-subprocess",
        "command": [sys.executable, str(script_path), str(work_order_path)],
        "process_executed": True,
        "exit_code": completed.returncode,
        "stdout_path": str(stdout_path.resolve()),
        "stderr_path": str(stderr_path.resolve()),
        "work_order_path": str(work_order_path.resolve()),
        "work_order_sha256": _sha256_uri(work_order_path),
        "receipt_path": str(receipt_path.resolve()),
        "checked_at": _now(),
    }
    _write_json(receipt_path, payload)
    return payload


def _write_correction_action(
    *,
    controlled_source: Path,
    authorization_receipt: dict[str, Any],
    receipt_path: Path,
    corrected_artifact: Path,
) -> dict[str, Any]:
    authorized = authorization_receipt.get("status") == "PASS"
    corrected_artifact.parent.mkdir(parents=True, exist_ok=True)
    if authorized:
        corrected_artifact.write_text(
            (
                "Authorized correction produced a redacted synthetic summary. "
                "[CONTROLLED DATA REDACTED]\n"
            ),
            encoding="utf-8",
        )
    payload = {
        "schema": CONTROLLED_DATA_CORRECTION_ACTION_SCHEMA,
        "status": "PASS" if authorized and corrected_artifact.exists() else "BLOCKED",
        "mocked": False,
        "live": True,
        "provider_live": False,
        "authorization_status": authorization_receipt.get("status"),
        "authorization_receipt_path": authorization_receipt.get("receipt_path"),
        "controlled_source_sha256": _sha256_uri(controlled_source),
        "corrected_artifact": str(corrected_artifact.resolve()),
        "corrected_artifact_sha256": (
            _sha256_uri(corrected_artifact) if corrected_artifact.exists() else None
        ),
        "redaction_applied": authorized,
        "receipt_path": str(receipt_path.resolve()),
        "checked_at": _now(),
    }
    _write_json(receipt_path, payload)
    return payload


def _write_correction_validation(
    *,
    unauthorized_outputs: list[Path],
    corrected_artifact: Path,
    receipt_path: Path,
) -> dict[str, Any]:
    unauthorized_scans = [_scan_for_marker(path) for path in unauthorized_outputs]
    corrected_scan = _scan_for_marker(corrected_artifact)
    unauthorized_leaks = [scan for scan in unauthorized_scans if scan["contains_restricted_marker"]]
    redacted = corrected_artifact.exists() and (
        "[CONTROLLED DATA REDACTED]" in corrected_artifact.read_text(encoding="utf-8")
    )
    payload = {
        "schema": CONTROLLED_DATA_CORRECTION_VALIDATION_SCHEMA,
        "status": "PASS"
        if not unauthorized_leaks and corrected_artifact.exists() and not corrected_scan[
            "contains_restricted_marker"
        ]
        and redacted
        else "BLOCKED",
        "mocked": False,
        "live": True,
        "provider_live": False,
        "unauthorized_outputs": [str(path.resolve()) for path in unauthorized_outputs],
        "unauthorized_scans": unauthorized_scans,
        "unauthorized_leak_count": len(unauthorized_leaks),
        "corrected_artifact": str(corrected_artifact.resolve()),
        "corrected_artifact_scan": corrected_scan,
        "redaction_marker_present": redacted,
        "receipt_path": str(receipt_path.resolve()),
        "checked_at": _now(),
    }
    _write_json(receipt_path, payload)
    return payload


def _write_worker_script(script_path: Path) -> None:
    script_path.write_text(
        """
import json
import sys
from pathlib import Path

work_order_path = Path(sys.argv[1]).resolve()
work_order = json.loads(work_order_path.read_text(encoding="utf-8"))
repo = Path(work_order["repo"]).resolve()
(repo / "reports").mkdir(parents=True, exist_ok=True)
(repo / "logs").mkdir(parents=True, exist_ok=True)
(repo / "logs" / "worker.log").write_text(
    "local worker wrote structured output\\n",
    encoding="utf-8",
)
(repo / "reports" / "worker-output.json").write_text(
    json.dumps(
        {
            "schema": "tau.local_worker_output.v1",
            "status": "PASS",
            "summary": "worker emitted non-sensitive operational output",
        },
        indent=2,
        sort_keys=True,
    )
    + "\\n",
    encoding="utf-8",
)
result = {
    "schema": "tau.omp_worker_result.v1",
    "status": "PASS",
    "goal_hash": work_order["goal_hash"],
    "changed_files": ["reports/worker-output.json"],
    "artifacts": ["reports/worker-output.json", "logs/worker.log"],
    "tests_run": [
        {
            "name": "local-worker-script",
            "status": "PASS",
            "log_path": "logs/worker.log",
        }
    ],
    "findings": [],
    "next_recommended_route": "reviewer",
}
(repo / work_order["result_path"]).write_text(
    json.dumps(result, indent=2, sort_keys=True) + "\\n",
    encoding="utf-8",
)
print(json.dumps({"status": "PASS", "result_path": str(repo / work_order["result_path"])}))
""".lstrip(),
        encoding="utf-8",
    )


def _work_order(*, repo_dir: Path) -> dict[str, Any]:
    return {
        "schema": OMP_WORK_ORDER_SCHEMA,
        "dag_id": DAG_ID,
        "node_id": NODE_ID,
        "agent": "local-python-worker",
        "goal_hash": GOAL_HASH,
        "attempt": 1,
        "repo": str(repo_dir.resolve()),
        "allowed_paths": ["reports/**", "logs/**", "worker-result.json", "worker-receipt.json"],
        "forbidden_paths": ["controlled-data/**", "secrets/**"],
        "task": "Write a structured non-sensitive worker result.",
        "required_artifacts": ["reports/worker-output.json", "logs/worker.log"],
        "result_path": "worker-result.json",
        "receipt_path": "worker-receipt.json",
        "high_stakes": False,
        "execution_substrate": "local-low-risk",
        "model_provider_route": {
            "provider": "local-python-subprocess",
            "surface": "local_subprocess",
            "provider_live": False,
        },
    }


def _policy_profile() -> dict[str, Any]:
    return {
        "schema": POLICY_PROFILE_SCHEMA,
        "profile_id": "itar-controlled-data-demo",
        "default_decision": "deny",
        "requires_data_boundary": True,
        "network": {"default": "deny"},
        "providers": {"cloud_llm": "deny", "local_model": "allow_with_approval"},
        "research": {
            "external_search": "deny",
            "manual_sanitized_receipt": "allow_with_review",
        },
        "memory": {
            "read": "allow_with_review",
            "write": "allow_with_review",
            "requires_intent": True,
            "requires_evidence_case": True,
        },
        "github": {"public_mutation": "deny", "dry_run_projection": "allow"},
        "filesystem": {
            "write_allowlist": ["artifacts/**"],
            "read_denylist": ["controlled-data/raw/**"],
        },
    }


def _data_boundary() -> dict[str, Any]:
    return {
        "schema": DATA_BOUNDARY_SCHEMA,
        "classification": "ITAR",
        "export_controlled": True,
        "itar": True,
        "technical_data": True,
        "external_provider_allowed": False,
        "external_research_allowed": False,
        "public_repo_allowed": False,
        "foreign_person_access": "prohibited",
        "notes": ["synthetic controlled-data denial/correction demo"],
    }


def _actor_manifest(*, authorized: bool) -> dict[str, Any]:
    return {
        "schema": "tau.actor_access_manifest.v1",
        "actor_id": "human-export-officer" if authorized else "unverified-agent",
        "actor_type": "human" if authorized else "agent",
        "roles": ["approver"] if authorized else ["worker"],
        "trusted": authorized,
        "verified": authorized,
        "eligibility": {
            "us_person": "verified" if authorized else "unknown",
            "foreign_person": False,
            "export_control_training_current": authorized,
            "approved_for_boundary": ["ITAR"] if authorized else [],
        },
    }


def _approval_packet() -> dict[str, Any]:
    return {
        "schema": "tau.human_approval_packet.v1",
        "approved": True,
        "actor": {
            "id": "human-export-officer",
            "role": "export_control_officer",
        },
        "target": "synthetic-controlled-data-correction",
        "scope": "redacted correction artifact only",
        "checked_at": _now(),
    }


def _scan_for_marker(path: Path) -> dict[str, Any]:
    exists = path.exists()
    text = path.read_text(encoding="utf-8") if exists else ""
    return {
        "path": str(path.resolve()),
        "exists": exists,
        "sha256": _sha256_uri(path) if exists else None,
        "bytes": path.stat().st_size if exists else None,
        "contains_restricted_marker": RESTRICTED_MARKER in text,
    }


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _sha256_uri(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return f"sha256:{digest.hexdigest()}"


def _now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")
