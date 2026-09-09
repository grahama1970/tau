#!/usr/bin/env python3
"""Prove a repaired Tau node reruns the same semantic node then advances (#344)."""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any

from tau_coding.dag_runtime.compiler import compile_generic_dag_plan
from tau_coding.dag_runtime.correction import (
    CorrectionActionIntent,
    CorrectionIncident,
    run_correction_transaction,
)
from tau_coding.dag_runtime.run_store import SqliteDagRunStore
from tau_coding.dag_runtime.scheduler import DagCorrectionRequest, run_dag_plan
from tau_coding.security_capability import capability_grant_sha256

GOAL_HASH = "sha256:tau-same-node-rerun-agentic-eval"
DAG_ID = "tau-same-node-rerun-agentic-eval"


class InjectedSchedulerCrash(RuntimeError):
    pass


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, default=Path.cwd())
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--run-root", type=Path)
    parser.add_argument("--timeout-seconds", type=int, default=180)
    args = parser.parse_args()

    repo = args.repo.expanduser().resolve()
    out = _resolve_out(repo, args.out)
    run_root = (
        args.run_root.expanduser().resolve()
        if args.run_root
        else Path(tempfile.mkdtemp(prefix="tau-same-node-rerun-"))
    )
    if run_root.exists():
        shutil.rmtree(run_root)
    run_root.mkdir(parents=True)
    proof_dir = run_root / "proof"
    proof_dir.mkdir()
    run_dir = run_root / "run"
    database = run_dir / "dag-run.sqlite3"
    state_path = run_root / "state.json"
    state_path.write_text(
        json.dumps(
            {
                "coder_fixed": False,
                "coder_calls": 0,
                "sibling_calls": 0,
                "reviewer_calls": 0,
                "repair_action_calls": 0,
                "repair_verify_calls": 0,
                "outputs": {},
            },
            sort_keys=True,
        )
    )
    plan = _plan(run_root)
    events: list[dict[str, Any]] = []

    open_crash = _run_scheduler(
        plan=plan,
        database=database,
        state_path=state_path,
        events=events,
        fault_point="after_repair_category_open",
        lease_owner="open-crash",
    )
    open_snapshot = _snapshot(database, plan.plan_id)
    open_reconciliation = _resolve_reconciliation(database, plan.plan_id, "after_open_crash")
    time.sleep(0.1)

    revalidating_crash = _run_scheduler(
        plan=plan,
        database=database,
        state_path=state_path,
        events=events,
        fault_point="after_repair_category_revalidating",
        lease_owner="revalidating-crash",
    )
    revalidating_snapshot = _snapshot(database, plan.plan_id)
    revalidating_reconciliation = _resolve_reconciliation(
        database,
        plan.plan_id,
        "after_revalidating_crash",
    )
    time.sleep(0.1)

    final_result = _run_scheduler(
        plan=plan,
        database=database,
        state_path=state_path,
        events=events,
        fault_point=None,
        lease_owner="final",
    )
    final_snapshot = _snapshot(database, plan.plan_id)
    state = _read_state(state_path)

    smoke = _run(
        [
            "python",
            "-m",
            "py_compile",
            "src/tau_coding/dag_runtime/correction.py",
            "src/tau_coding/dag_runtime/run_store.py",
            "src/tau_coding/dag_runtime/replay.py",
            "src/tau_coding/dag_runtime/scheduler.py",
            "scripts/agentic-eval-tau-same-node-rerun.py",
        ],
        cwd=repo,
        timeout=args.timeout_seconds,
    )

    categories = final_snapshot["repair_categories"]
    first_category = categories[0] if categories else {}
    result_by_node = {item.get("node_id"): item for item in final_result.node_results}
    sibling_output = result_by_node.get("sibling", {}).get("accepted_output") or {}
    coder_attempts = state.get("outputs", {}).get("coder_attempts", [])
    ledger_trace = _ledger_trace(final_snapshot["events"])

    checks = {
        "open_crash_recorded_open_category": open_crash == "InjectedSchedulerCrash"
        and [item.get("state") for item in open_snapshot["repair_categories"]] == ["OPEN"],
        "open_restart_safe": open_reconciliation.get("decision")
        in {"authorize_new_generation", "not_required"},
        "revalidating_crash_recorded_revalidating": revalidating_crash
        == "InjectedSchedulerCrash"
        and [item.get("state") for item in revalidating_snapshot["repair_categories"]]
        == ["REVALIDATING"],
        "revalidating_restart_safe": revalidating_reconciliation.get("decision")
        in {"authorize_new_generation", "not_required"},
        "final_pass": final_result.status == "PASS" and final_result.verdict == "PASS",
        "same_node_attempt_2_recorded": final_result.node_results
        and result_by_node.get("coder", {}).get("attempt_count") == 2
        and 2 in coder_attempts,
        "sibling_not_regenerated": state.get("sibling_calls") == 1,
        "sibling_output_byte_identical": sibling_output.get("payload_sha256")
        == state.get("outputs", {}).get("sibling_payload_sha256"),
        "reviewer_released_after_repair": state.get("reviewer_calls") == 1
        and result_by_node.get("reviewer", {}).get("status") == "PASS",
        "repair_closed": first_category.get("state") == "SAME_NODE_RERUN",
        "repair_has_resulting_attempt": bool(
            first_category.get("record", {}).get("resulting_attempt_id")
        ),
        "repair_action_once": state.get("repair_action_calls") == 1,
        "repair_verify_once": state.get("repair_verify_calls") == 1,
        "ledger_trace_complete": ledger_trace
        == ["OPEN", "REPAIRING", "REVALIDATING", "RESOLVED", "SAME_NODE_RERUN"],
        "py_compile_passed": smoke["exit_code"] == 0,
    }
    errors = [name for name, ok in checks.items() if not ok]
    receipt = {
        "schema": "tau.same_node_rerun_agentic_eval_proof.v1",
        "issue": 344,
        "ok": not errors,
        "status": "PASS" if not errors else "BLOCKED",
        "mocked": False,
        "live": True,
        "provider_live": False,
        "run_root": str(run_root),
        "receipt_dir": str(run_dir),
        "checks": checks,
        "errors": errors,
        "open_crash": _result_label(open_crash),
        "open_reconciliation": open_reconciliation,
        "open_repair_states": [item.get("state") for item in open_snapshot["repair_categories"]],
        "revalidating_crash": _result_label(revalidating_crash),
        "revalidating_reconciliation": revalidating_reconciliation,
        "revalidating_repair_states": [
            item.get("state") for item in revalidating_snapshot["repair_categories"]
        ],
        "final_status": final_result.status,
        "final_verdict": final_result.verdict,
        "node_attempts": {
            item.get("node_id"): item.get("attempt_count") for item in final_result.node_results
        },
        "node_call_counts": {
            "coder": state.get("coder_calls"),
            "sibling": state.get("sibling_calls"),
            "reviewer": state.get("reviewer_calls"),
            "repair_action": state.get("repair_action_calls"),
            "repair_verify": state.get("repair_verify_calls"),
        },
        "observed_edges": [list(item) for item in final_result.edge_states],
        "repair_categories": categories,
        "ledger_trace": ledger_trace,
        "sibling_output": sibling_output,
        "commands": {"py_compile": smoke},
        "proof_boundary": {
            "proves": "Live local Tau scheduler API with SqliteDagRunStore, durable repair "
            "category replay, deterministic correction evidence, same-node rerun, and fan-out "
            "sibling preservation.",
            "does_not_prove": "Provider/model semantic quality or browser-backed agents.",
        },
    }
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(receipt, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(receipt, indent=2, sort_keys=True))
    return 0 if not errors else 1


def _run_scheduler(
    *,
    plan: Any,
    database: Path,
    state_path: Path,
    events: list[dict[str, Any]],
    fault_point: str | None,
    lease_owner: str,
) -> Any:
    def execute_node(
        node: Any,
        accepted_inputs: tuple[dict[str, Any], ...],
        attempt: Any,
    ) -> dict[str, Any]:
        state = _read_state(state_path)
        state[f"{node.node_id}_calls"] = int(state.get(f"{node.node_id}_calls") or 0) + 1
        outputs = dict(state.get("outputs") or {})
        if node.node_id == "coder":
            attempts = list(outputs.get("coder_attempts") or [])
            attempts.append(attempt.attempt)
            outputs["coder_attempts"] = attempts
            if not state.get("coder_fixed"):
                _write_state(state_path, state | {"outputs": outputs})
                return {
                    "node_id": "coder",
                    "status": "BLOCKED",
                    "verdict": "PROVIDER_AUTH_REQUIRED",
                    "retryable": True,
                    "correction_required": True,
                    "repair_handler_id": "scheduler.correction_handler",
                    "repair_attempt_budget": 1,
                    "checkpoint_ref": "node_input_manifest",
                    "errors": ["fixture repair required"],
                    "failure": {
                        "schema": "tau.internal_failure.v1",
                        "original_code": "ADAPTER_EXECUTION_FAILED",
                        "classification_code": "tau_same_node_fixture_repair_required",
                    },
                }
            payload = {"source_node_id": "coder", "attempt": attempt.attempt, "fixed": True}
        elif node.node_id == "sibling":
            payload = {"source_node_id": "sibling", "value": "unaffected"}
            outputs["sibling_payload_sha256"] = _stable_sha(payload)
        else:
            payload = {
                "source_node_id": "reviewer",
                "accepted_from": [item.get("source_node_id") for item in accepted_inputs],
            }
        payload["payload_sha256"] = _stable_sha(payload)
        outputs[f"{node.node_id}_last"] = payload
        state["outputs"] = outputs
        _write_state(state_path, state)
        return {
            "node_id": node.node_id,
            "status": "PASS",
            "verdict": "PASS",
            "accepted_output": payload,
        }

    def correction_handler(request: DagCorrectionRequest):
        incident = CorrectionIncident.create(
            run_id=request.attempt.run_id,
            dag_id=request.plan.plan_id,
            node_id=request.node.node_id,
            attempt=request.attempt.attempt,
            trigger=str(request.result.get("verdict") or "node_failure"),
            classification="RETRYABLE",
            goal_hash=request.plan.runtime_goal_hash,
            observed_state=dict(request.result),
        )
        intent = CorrectionActionIntent.create(
            incident=incident,
            capability="provider.repair_auth",
            action="refresh_local_provider_auth",
            target={"provider": "local-fixture"},
            policy_sha256="sha256:policy",
            capability_grant=_grant(incident),
        )

        def apply_action(_intent: CorrectionActionIntent) -> dict[str, Any]:
            state = _read_state(state_path)
            state["repair_action_calls"] = int(state.get("repair_action_calls") or 0) + 1
            state["coder_fixed"] = True
            _write_state(state_path, state)
            return {"fixed": True, "repair_action_calls": state["repair_action_calls"]}

        def verify_action(
            _intent: CorrectionActionIntent, _receipt: dict[str, Any]
        ) -> dict[str, Any]:
            state = _read_state(state_path)
            state["repair_verify_calls"] = int(state.get("repair_verify_calls") or 0) + 1
            _write_state(state_path, state)
            return {"verified": state.get("coder_fixed") is True}

        return run_correction_transaction(
            store=request.run_store,
            lease=request.lease,
            incident=incident,
            intent=intent,
            apply_action=apply_action,
            verify_action=verify_action,
        )

    def inject(point: str, _payload: dict[str, Any]) -> None:
        if point == fault_point:
            raise InjectedSchedulerCrash(point)

    try:
        with SqliteDagRunStore(database) as store:
            return run_dag_plan(
                plan,
                execute_node=execute_node,
                run_store=store,
                run_id=plan.plan_id,
                lease_owner=lease_owner,
                allow_lease_takeover=True,
                lease_ttl_seconds=0.05,
                max_concurrency=1,
                event_sink=events.append,
                correction_handler=correction_handler,
                fault_injector=inject if fault_point else None,
            )
    except InjectedSchedulerCrash as exc:
        return type(exc).__name__


def _plan(root: Path) -> Any:
    return compile_generic_dag_plan(
        {
            "schema": "tau.generic_dag_spec.v1",
            "run_id": DAG_ID,
            "run_dir": str(root / "run"),
            "nodes": [
                _node(root, "coder", max_attempts=2),
                _node(root, "sibling", max_attempts=1),
                _node(root, "reviewer", depends_on=["coder", "sibling"], max_attempts=1),
            ],
        },
        source_path=root / "dag.json",
    )


def _node(
    root: Path, node_id: str, *, depends_on: list[str] | None = None, max_attempts: int
) -> dict[str, Any]:
    return {
        "node_id": node_id,
        "role": node_id,
        "command": ["true"],
        "depends_on": depends_on or [],
        "accepted_context_from": depends_on or [],
        "receipt_path": str(root / f"{node_id}.json"),
        "timeout_seconds": 1,
        "max_attempts": max_attempts,
    }


def _grant(incident: CorrectionIncident) -> dict[str, Any]:
    grant: dict[str, Any] = {
        "schema": "tau.capability_grant.v1",
        "grant_id": f"grant:{incident.incident_id}",
        "request_sha256": "sha256:request",
        "run_id": incident.run_id,
        "dag_id": incident.dag_id,
        "node_id": incident.node_id,
        "attempt": incident.attempt,
        "actor_id": "human:operator",
        "goal_hash": incident.goal_hash,
        "security_context_sha256": "sha256:security-context",
        "policy_profile_sha256": "sha256:policy",
        "data_boundary_sha256": "sha256:boundary",
        "capability": "provider.repair_auth",
        "target": "refresh_local_provider_auth",
        "resource_scope": ["provider:local-fixture"],
        "maximum_effect": {"max_repairs": 1},
        "issued_at": "2026-07-16T00:00:00Z",
        "expires_at": "2099-01-01T00:00:00Z",
        "granting_authority": "tau.command_spec_policy.v1",
    }
    grant["grant_sha256"] = capability_grant_sha256(grant)
    return grant


def _snapshot(database: Path, run_id: str) -> dict[str, Any]:
    with SqliteDagRunStore(database) as store:
        events = [dict(item) for item in store.load_events(run_id)]
    from tau_coding.dag_runtime.correction import reduce_repair_category_projections

    repairs = reduce_repair_category_projections(tuple(events))
    return {
        "events": events,
        "repair_categories": [
            {
                "repair_id": item.repair_id,
                "state": item.state,
                "journal_sequence": item.journal_sequence,
                "record": item.record,
                "transitions": list(item.transitions),
            }
            for item in repairs
        ],
    }


def _resolve_reconciliation(database: Path, run_id: str, reason: str) -> dict[str, Any]:
    with SqliteDagRunStore(database) as store:
        pending = store.reconciliation_required_runs()
        if not any(item.run_id == run_id for item in pending):
            return {"decision": "not_required"}
        return store.resolve_reconciliation_required_run(
            run_id=run_id,
            decision="authorize_new_generation",
            operator_id="agentic-eval-operator",
            reason=reason,
        )


def _ledger_trace(events: list[dict[str, Any]]) -> list[str]:
    return [
        str(item.get("payload", {}).get("repair", {}).get("state"))
        for item in events
        if item.get("event_type") == "repair_category_state_committed"
    ]


def _result_label(value: Any) -> str:
    if isinstance(value, str):
        return value
    return str(getattr(value, "status", type(value).__name__))


def _run(command: list[str], *, cwd: Path, timeout: int) -> dict[str, Any]:
    proc = subprocess.run(
        command,
        cwd=cwd,
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )
    return {
        "command": command,
        "cwd": str(cwd),
        "exit_code": proc.returncode,
        "stdout": proc.stdout,
        "stderr": proc.stderr,
    }


def _stable_sha(value: dict[str, Any]) -> str:
    from tau_coding.dag_runtime.model import canonical_sha256

    return canonical_sha256(value)


def _read_state(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_state(path: Path, state: dict[str, Any]) -> None:
    path.write_text(json.dumps(state, sort_keys=True) + "\n", encoding="utf-8")


def _resolve_out(repo: Path, out: Path) -> Path:
    expanded = out.expanduser()
    return expanded.resolve() if expanded.is_absolute() else (repo / expanded).resolve()


if __name__ == "__main__":
    raise SystemExit(main())
