"""Execution boundary for Tau's packaged workflows."""

from __future__ import annotations

import json
import sys
import threading
import time
import webbrowser
from pathlib import Path
from typing import Any

from tau_coding.dag_viewer.server import RunningDagViewerServer, create_dag_viewer_server
from tau_coding.generic_dag import run_generic_dag
from tau_coding.workflows.catalog import get_workflow
from tau_coding.workflows.contracts import WORKFLOW_RUN_RECEIPT_SCHEMA
from tau_coding.workflows.materialize import (
    MaterializedWorkflow,
    materialize_approved_release_bundle,
    materialize_repository_evidence_map,
    materialize_repository_readiness,
    materialize_tau_operator_reference,
)


def run_approved_release_bundle_workflow(
    *,
    repo_path: Path,
    human_goal: str,
    publish_path: Path,
    run_dir: Path,
    open_viewer: bool,
    browser_open: bool,
    viewer_hold_seconds: float | None,
    force_terminal_failure: bool = False,
    simulate_publish_verification_failure: bool = False,
    step_delay_seconds: float = 0.0,
) -> dict[str, object]:
    materialized = materialize_approved_release_bundle(
        definition=get_workflow("approved-release-bundle"),
        repo_path=repo_path,
        human_goal=human_goal,
        publish_path=publish_path,
        run_dir=run_dir,
        force_terminal_failure=force_terminal_failure,
        simulate_publish_verification_failure=simulate_publish_verification_failure,
        step_delay_seconds=step_delay_seconds,
    )
    return _run_materialized_workflow(
        materialized=materialized,
        result_filename="approved-release-bundle.json",
        open_viewer=open_viewer,
        browser_open=browser_open,
        viewer_hold_seconds=viewer_hold_seconds,
    )


def approve_approved_release_bundle(*, run_dir: Path) -> dict[str, object]:
    resolved = run_dir.expanduser().resolve()
    gate_path = (
        resolved
        / "transactions"
        / "publish-approved-release"
        / "approval-gate-receipt.json"
    )
    gate = _read_object(gate_path, "approval gate receipt")
    target = gate.get("expected_target")
    if not isinstance(target, dict):
        raise RuntimeError("approval gate receipt has no exact expected_target")
    packet = {
        "schema": "tau.human_approval_packet.v1",
        "approved": True,
        "actor": {"id": "human:tau-operator", "auth_method": "manual"},
        "action": "generic_dag_transaction_continue",
        "target": target,
        "reason": "Human approved the exact accepted release bundle for publication.",
        "evidence": [str(gate_path)],
        "nonce": f"approved-release:{target.get('accepted_manifest_sha256')}",
        "signature": "declared-manual-approval",
    }
    packet_path = resolved / "input" / "approval.json"
    packet_path.write_text(json.dumps(packet, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return {
        "schema": "tau.workflow_approval_receipt.v1",
        "status": "PASS",
        "ok": True,
        "workflow_id": "approved-release-bundle",
        "run_dir": str(resolved),
        "approval_packet_path": str(packet_path),
        "target": target,
    }


def resume_approved_release_bundle(*, run_dir: Path) -> dict[str, object]:
    resolved = run_dir.expanduser().resolve()
    request_path = resolved / "input" / "approved-release-request.json"
    request = _read_object(request_path, "approved release request")
    goal = request.get("goal")
    if not isinstance(goal, dict):
        raise RuntimeError("approved release request goal is missing")
    definition = get_workflow("approved-release-bundle")
    materialized = MaterializedWorkflow(
        definition=definition,
        request_path=request_path,
        source_dag_path=resolved / "workflow" / "dag.json",
        run_dir=resolved,
        run_id=str(_read_object(resolved / "workflow" / "dag.json", "workflow DAG")["run_id"]),
        goal=goal,
    )
    dag_receipt = run_generic_dag(spec_path=materialized.source_dag_path, resume=True)
    return _write_workflow_receipt(
        materialized,
        dag_receipt,
        result_filename="approved-release-bundle.json",
        viewer=None,
    )


def run_repository_evidence_map_workflow(
    *,
    repo_path: Path,
    human_goal: str,
    require_tests: bool,
    run_dir: Path,
    open_viewer: bool,
    browser_open: bool,
    viewer_hold_seconds: float | None,
    step_delay_seconds: float = 0.0,
) -> dict[str, object]:
    materialized = materialize_repository_evidence_map(
        definition=get_workflow("repository-evidence-map"),
        repo_path=repo_path,
        human_goal=human_goal,
        require_tests=require_tests,
        run_dir=run_dir,
        step_delay_seconds=step_delay_seconds,
    )
    return _run_materialized_workflow(
        materialized=materialized,
        result_filename="repository-evidence-map.json",
        open_viewer=open_viewer,
        browser_open=browser_open,
        viewer_hold_seconds=viewer_hold_seconds,
    )


def run_repository_readiness_workflow(
    *,
    repo_path: Path,
    human_goal: str,
    require_clean: bool,
    run_dir: Path,
    open_viewer: bool,
    browser_open: bool,
    viewer_hold_seconds: float | None,
    step_delay_seconds: float = 0.0,
) -> dict[str, object]:
    definition = get_workflow("repository-readiness")
    materialized = materialize_repository_readiness(
        definition=definition,
        repo_path=repo_path,
        human_goal=human_goal,
        require_clean=require_clean,
        run_dir=run_dir,
        step_delay_seconds=step_delay_seconds,
    )
    return _run_materialized_workflow(
        materialized=materialized,
        result_filename="repository-readiness.json",
        open_viewer=open_viewer,
        browser_open=browser_open,
        viewer_hold_seconds=viewer_hold_seconds,
    )


def run_tau_operator_reference_workflow(
    *,
    repo_path: Path,
    required_workflow: str,
    run_dir: Path,
    open_viewer: bool,
    browser_open: bool,
    viewer_hold_seconds: float | None,
    step_delay_seconds: float = 0.0,
) -> dict[str, object]:
    definition = get_workflow("tau-operator-reference")
    materialized = materialize_tau_operator_reference(
        definition=definition,
        repo_path=repo_path,
        required_workflow=required_workflow,
        run_dir=run_dir,
        step_delay_seconds=step_delay_seconds,
    )
    return _run_materialized_workflow(
        materialized=materialized,
        result_filename="tau-operator-reference.json",
        open_viewer=open_viewer,
        browser_open=browser_open,
        viewer_hold_seconds=viewer_hold_seconds,
    )


def _run_materialized_workflow(
    *,
    materialized: MaterializedWorkflow,
    result_filename: str,
    open_viewer: bool,
    browser_open: bool,
    viewer_hold_seconds: float | None,
) -> dict[str, object]:
    if not open_viewer:
        dag_receipt = run_generic_dag(spec_path=materialized.source_dag_path)
        return _write_workflow_receipt(
            materialized,
            dag_receipt,
            result_filename=result_filename,
            viewer=None,
        )

    outcome: dict[str, Any] = {}
    failure: list[BaseException] = []

    def run_workflow() -> None:
        try:
            outcome.update(run_generic_dag(spec_path=materialized.source_dag_path))
        except BaseException as exc:  # pragma: no cover - forwarded across thread boundary
            failure.append(exc)

    workflow_thread = threading.Thread(target=run_workflow, name="tau-workflow", daemon=True)
    workflow_thread.start()
    viewer = _wait_for_viewer(materialized.run_dir, workflow_thread, failure)
    viewer_thread = threading.Thread(target=viewer.serve_forever, name="tau-viewer", daemon=True)
    viewer_thread.start()
    if browser_open:
        webbrowser.open(viewer.url)
    workflow_thread.join()
    if failure:
        viewer.shutdown()
        viewer_thread.join(timeout=5)
        raise RuntimeError(f"{materialized.definition.workflow_id} workflow failed: {failure[0]}")
    receipt = _write_workflow_receipt(
        materialized,
        outcome,
        result_filename=result_filename,
        viewer=viewer,
    )
    try:
        if viewer_hold_seconds is not None:
            if viewer_hold_seconds < 0:
                raise RuntimeError("viewer_hold_seconds must be non-negative")
            time.sleep(viewer_hold_seconds)
        elif sys.stdin.isatty():
            while True:
                time.sleep(1)
    except KeyboardInterrupt:
        pass
    finally:
        viewer.shutdown()
        viewer_thread.join(timeout=5)
    return receipt


def _wait_for_viewer(
    run_dir: Path,
    workflow_thread: threading.Thread,
    failure: list[BaseException],
) -> RunningDagViewerServer:
    deadline = time.monotonic() + 10
    last_error: Exception | None = None
    while time.monotonic() < deadline:
        if failure:
            raise RuntimeError(f"packaged workflow failed: {failure[0]}")
        try:
            return create_dag_viewer_server(run_dir=run_dir, host="127.0.0.1", port=0)
        except (OSError, RuntimeError) as exc:
            last_error = exc
            time.sleep(0.05)
    raise RuntimeError(f"packaged workflow viewer did not become ready: {last_error}")


def _write_workflow_receipt(
    materialized: MaterializedWorkflow,
    dag_receipt: dict[str, Any],
    *,
    result_filename: str,
    viewer: RunningDagViewerServer | None,
) -> dict[str, object]:
    result_path = materialized.run_dir / "results" / result_filename
    result: dict[str, Any] | None = None
    if result_path.is_file():
        payload = json.loads(result_path.read_text(encoding="utf-8"))
        result = payload if isinstance(payload, dict) else None
    ok = dag_receipt.get("ok") is True and result is not None
    receipt: dict[str, object] = {
        "schema": WORKFLOW_RUN_RECEIPT_SCHEMA,
        "status": "PASS" if ok else "BLOCKED",
        "ok": ok,
        "mocked": False,
        "live": True,
        "provider_live": False,
        "workflow_id": materialized.definition.workflow_id,
        "workflow_version": materialized.definition.workflow_version,
        "goal": materialized.goal,
        "source_dag_path": str(materialized.source_dag_path),
        "run_dir": str(materialized.run_dir),
        "run_receipt_path": str(materialized.run_dir / "run-receipt.json"),
        "result": result,
        "viewer": {
            "command": ["tau", "dag-view", "--run-dir", str(materialized.run_dir)],
            "url": viewer.url if viewer is not None else None,
        },
        "proof_scope": {
            "proves": [
                f"Tau executed the packaged {materialized.definition.workflow_id} workflow.",
                "The requested repository inputs and goal are bound into the materialized DAG.",
            ],
            "does_not_prove": [
                "The repository test suite passes.",
                "Provider or model quality.",
                "Production deployment readiness.",
            ],
        },
    }
    path = materialized.run_dir / "workflow-receipt.json"
    path.write_text(json.dumps(receipt, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return receipt


def _read_object(path: Path, label: str) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"{label} is unavailable: {exc}") from exc
    if not isinstance(payload, dict):
        raise RuntimeError(f"{label} must be an object")
    return payload
