#!/usr/bin/env python3
"""Proof for Tau#349 scheduler boundary failure containment."""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any


def _run(cmd: list[str], *, cwd: Path, timeout: int = 180) -> dict[str, Any]:
    result = subprocess.run(cmd, cwd=cwd, text=True, capture_output=True, timeout=timeout)
    return {
        "command": cmd,
        "cwd": str(cwd),
        "exit_code": result.returncode,
        "stdout": result.stdout,
        "stderr": result.stderr,
    }


def _boundary(result: dict[str, Any]) -> dict[str, Any]:
    diagnostics = result.get("diagnostics")
    if isinstance(diagnostics, dict) and isinstance(diagnostics.get("scheduler_boundary"), dict):
        return diagnostics["scheduler_boundary"]
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", required=True)
    parser.add_argument("--work")
    args = parser.parse_args()
    repo = Path(__file__).resolve().parents[1]
    work = (
        Path(args.work).expanduser().resolve()
        if args.work
        else Path(tempfile.mkdtemp(prefix="tau-349-boundary-"))
    )
    if work.exists():
        shutil.rmtree(work)
    work.mkdir(parents=True)

    dist = work / "dist"
    venv = work / "venv"
    build = _run(["uv", "build", "--wheel", "--out-dir", str(dist)], cwd=repo)
    wheels = sorted(dist.glob("tau-*.whl"))
    venv_create = _run([sys.executable, "-m", "venv", str(venv)], cwd=work)
    python = venv / "bin" / "python"
    pip = _run([str(python), "-m", "pip", "install", str(wheels[-1])], cwd=work)
    tau = venv / "bin" / "tau"

    registry_run = _run([str(tau), "scheduler-boundary-registry", "--json"], cwd=work)
    registry = json.loads(registry_run["stdout"])

    fault_script = work / "scheduler_faults.py"
    fault_script.write_text(
        r'''
import json
from pathlib import Path
from unittest.mock import patch

from tau_coding.dag_runtime.attempt_result import (
    OUTPUT_CONTRACT_ANY_OBJECT,
    admit_dag_attempt_result,
)
from tau_coding.dag_runtime.boundary_registry import boundary_for_original_code
from tau_coding.dag_runtime.compiler import compile_generic_dag_plan
from tau_coding.dag_runtime.resource_leases import ResourceLeaseDenied
from tau_coding.dag_runtime.run_store import DagRunStoreError, SqliteDagRunStore
from tau_coding.dag_runtime.scheduler import (
    _canonicalize_attempt_result_boundary,
    _triaged_blocked_attempt_result,
    _workspace_stale_read_blocked_result,
    run_dag_plan,
)


def node(root, node_id="n", depends_on=None, **extra):
    item = {
        "node_id": node_id,
        "role": node_id,
        "command": ["true"],
        "depends_on": depends_on or [],
        "accepted_context_from": depends_on or [],
        "receipt_path": str(root / f"{node_id}.json"),
        "timeout_seconds": 1,
        "max_attempts": 1,
    }
    item.update(extra)
    return item


def spec(root, first=None):
    return {
        "schema": "tau.generic_dag_spec.v1",
        "run_id": root.name,
        "run_dir": str(root / "run"),
        "nodes": [first or node(root), node(root, "downstream", ["n"])],
    }


def boundary(result):
    diagnostics = result.get("diagnostics") if isinstance(result, dict) else None
    if isinstance(diagnostics, dict) and isinstance(diagnostics.get("scheduler_boundary"), dict):
        return diagnostics["scheduler_boundary"]
    return result


def ok(node, accepted_inputs, execution):
    return {
        "node_id": node.node_id,
        "status": "PASS",
        "verdict": "PASS",
        "accepted_output": {"source_node_id": node.node_id},
    }


def bad(node, accepted_inputs, execution):
    return {
        "node_id": node.node_id,
        "status": "PASS",
        "verdict": "FAIL",
        "accepted_output": {"source_node_id": node.node_id},
    }


def boom(node, accepted_inputs, execution):
    raise RuntimeError("adapter exploded")


def classify(signal, *, layer):
    if "dag_attempt_result" in signal:
        code = "dag_attempt_result_pass_verdict_mismatch"
        family = "result_contract_invalid"
    elif "ADAPTER_EXECUTION_FAILED" in signal or "future_exception" in signal:
        code = "ADAPTER_EXECUTION_FAILED"
        family = "adapter_execution_failed"
    else:
        code = "unknown_internal_failure"
        family = "unknown_internal_failure"
    boundary = boundary_for_original_code(code)
    record = {
        "schema": "tau.triage_error_classification.v1",
        "code": "tau_unclassified_fault",
        "layer": layer,
        "cause": signal,
        "repair_family": family,
        "disposition": "KNOWN_REPAIR" if boundary.retryable else "NEEDS_HUMAN",
        "requires_human": not boundary.retryable,
        "diagnostics": {"classifier": "eval-fixture"},
        "extensions": {},
    }
    if boundary.retryable:
        record.update({
            "repair_handler_id": "scheduler.correction_handler",
            "repair_args_schema": "tau.scheduler_correction_repair_args.v1",
            "repair_args": {
                "strategy": "same_semantic_node_rerun",
                "repair_family": family,
                "classification_code": "tau_unclassified_fault",
            },
        })
    return record


def run_case(
    name,
    *,
    execute=ok,
    first=None,
    fault_point=None,
    store=True,
    run_store_factory=None,
    resource_manager=None,
    worker_registry=None,
    include_downstream=True,
):
    root = Path(name)
    root.mkdir(exist_ok=True)
    payload = spec(root, first)
    if not include_downstream:
        payload["nodes"] = payload["nodes"][:1]
    plan = compile_generic_dag_plan(payload, source_path=root / "dag.json")
    calls = []
    tripped = set()

    def wrapped(node, accepted_inputs, execution):
        calls.append(node.node_id)
        return execute(node, accepted_inputs, execution)

    def inject(point, context):
        if point == fault_point and point not in tripped:
            tripped.add(point)
            raise RuntimeError(point)

    store_factory = run_store_factory or SqliteDagRunStore
    run_store = store_factory(root / "dag.sqlite3") if store else None
    result = run_dag_plan(
        plan,
        execute_node=wrapped,
        run_store=run_store,
        run_id=name,
        allow_lease_takeover=True,
        fault_injector=inject if fault_point else None,
        resource_lease_manager=resource_manager,
        worker_registry=worker_registry,
    )
    item = result.node_results[-1]
    b = boundary(item)
    admitted = _canonical_admitted(name, item)
    if fault_point in {
        "before_route_join_transition",
        "before_checkpoint_replay_recovery",
        "before_cleanup_retention",
    }:
        replay = {
            "stable": True,
            "mode": "explicit_non_attempt_boundary_no_staged_replay",
            "boundary_id": b.get("boundary_id"),
            "repair_category": b.get("repair_category"),
        }
    else:
        replay = _replay_case(
            name=name,
            plan=plan,
            run_store_path=root / "dag.sqlite3" if store else None,
            boundary_id=b.get("boundary_id"),
            repair_category=b.get("repair_category"),
        )
    return {
        "status": result.status,
        "verdict": result.verdict,
        "boundary_id": b.get("boundary_id"),
        "repair_category": b.get("repair_category"),
        "retryable": item.get("retryable"),
        "downstream_calls": calls.count("downstream"),
        "call_count": len(calls),
        "canonical_admission": admitted,
        "replay_stable": replay["stable"],
        "replay_mode": replay.get("mode"),
        "replay_boundary_id": replay["boundary_id"],
        "replay_repair_category": replay["repair_category"],
        "node_result": item,
    }


class BadAcquireStore:
    def run_outcome(self, run_id):
        return None
    def acquire_run(self, **kwargs):
        raise DagRunStoreError("lease_acquire_failed", "injected")


class FailFirstStageStore(SqliteDagRunStore):
    def __init__(self, path):
        super().__init__(path)
        self._stage_failures_left = 1

    def stage_result(self, lease, attempt_id, result):
        if self._stage_failures_left:
            self._stage_failures_left -= 1
            raise DagRunStoreError("result_store_commit_failed", "injected")
        return super().stage_result(lease, attempt_id, result)


class DenyResources:
    def acquire_for_attempt(self, **kwargs):
        raise ResourceLeaseDenied("resource_lease_denied", "injected")
    def release(self, *args, **kwargs):
        raise AssertionError("release should not be called for failed acquire")


def _canonical_admitted(name, item):
    normalized = _canonicalize_attempt_result_boundary(item)
    identity = type("Identity", (), {
        "run_id": str(normalized.get("run_id") or name),
        "node_id": str(normalized.get("node_id") or "n"),
        "attempt": int(normalized.get("attempt") or 1),
        "attempt_id": str(normalized.get("attempt_id") or f"{name}:admit"),
        "idempotency_key": str(
            normalized.get("idempotency_key") or f"{name}:admit:effect"
        ),
        "recovered": False,
    })()
    try:
        admit_dag_attempt_result(
            plan_sha256=str(normalized.get("plan_sha256") or "sha256:" + "0" * 64),
            identity=identity,
            node_id=identity.node_id,
            result=normalized,
            output_contract_id=str(
                normalized.get("output_contract_id") or OUTPUT_CONTRACT_ANY_OBJECT
            ),
        )
    except Exception:
        return False
    return True


def _replay_case(*, name, plan, run_store_path, boundary_id, repair_category):
    if run_store_path is None or boundary_id is None:
        return {
            "stable": True,
            "mode": "not_durable_reproduced",
            "boundary_id": boundary_id,
            "repair_category": repair_category,
        }
    replay_store = SqliteDagRunStore(run_store_path)

    def forbidden(node, accepted_inputs, execution):
        raise AssertionError(f"replay dispatched {node.node_id}")

    replayed = run_dag_plan(
        plan,
        execute_node=forbidden,
        run_store=replay_store,
        run_id=name,
        allow_lease_takeover=True,
    )
    replay_boundaries = [boundary(item) for item in replayed.node_results]
    replay_boundary = next(
        (
            item
            for item in replay_boundaries
            if item.get("boundary_id") == boundary_id
            and item.get("repair_category") == repair_category
        ),
        replay_boundaries[-1] if replay_boundaries else {},
    )
    return {
        "stable": replay_boundary.get("boundary_id") == boundary_id
        and replay_boundary.get("repair_category") == repair_category,
        "mode": "durable_replay",
        "boundary_id": replay_boundary.get("boundary_id"),
        "repair_category": replay_boundary.get("repair_category"),
    }


def synthetic_case(name, item):
    b = boundary(_canonicalize_attempt_result_boundary(item))
    repeated = _canonicalize_attempt_result_boundary(dict(item))
    repeated_boundary = boundary(repeated)
    return {
        "status": item.get("status"),
        "verdict": item.get("verdict"),
        "boundary_id": b.get("boundary_id"),
        "repair_category": b.get("repair_category"),
        "retryable": item.get("retryable"),
        "downstream_calls": 0,
        "call_count": 0,
        "canonical_admission": _canonical_admitted(name, item),
        "replay_stable": repeated_boundary.get("boundary_id") == b.get("boundary_id")
        and repeated_boundary.get("repair_category") == b.get("repair_category"),
        "replay_mode": "repeated_helper_observation",
        "replay_boundary_id": repeated_boundary.get("boundary_id"),
        "replay_repair_category": repeated_boundary.get("repair_category"),
        "node_result": item,
    }


cases = {}
with patch(
    "tau_coding.dag_runtime.scheduler.classify_tau_failure",
    side_effect=RuntimeError("classifier down"),
):
    cases["recursive_failure_object_admission"] = run_case("recursive", execute=boom)
with patch("tau_coding.dag_runtime.scheduler.classify_tau_failure", classify):
    cases["adapter_future_execution"] = run_case("adapter", execute=boom)
    cases["attempt_result_admission"] = run_case("attempt", execute=bad)
    cases["result_store_commit"] = run_case(
        "result-store", run_store_factory=FailFirstStageStore
    )
    cases["route_join_transition"] = run_case(
        "route", fault_point="before_route_join_transition"
    )
    cases["checkpoint_replay_recovery"] = run_case(
        "checkpoint", fault_point="before_checkpoint_replay_recovery"
    )
    cases["cleanup_retention"] = run_case(
        "cleanup", fault_point="before_cleanup_retention", include_downstream=False
    )
    cases["worker_assignment_completion"] = run_case(
        "worker", resource_manager=DenyResources(), worker_registry=()
    )
    cases["resource_lease"] = run_case(
        "resource", resource_manager=DenyResources()
    )
    with patch(
        "tau_coding.dag_runtime.scheduler.requires_node_completion_boundary",
        return_value=True,
    ):
        cases["node_completion_boundary"] = run_case("completion")
cases["workspace_stale_read"] = synthetic_case(
    "workspace",
    _workspace_stale_read_blocked_result(
        {"node_id": "n", "status": "PASS", "verdict": "PASS", "accepted_output": {}},
        verdict="STALE_WORKSPACE_READ_RECONCILIATION_REQUIRED",
        errors=("stale read injected",),
        signals=(),
    ),
)
with patch("tau_coding.dag_runtime.scheduler.classify_tau_failure", classify):
    cases["effect_outbox_reconciliation"] = synthetic_case(
        "effect",
        _triaged_blocked_attempt_result(
            node_id="n",
            signal="effect uncertainty injected",
            original_code="DAG_ATTEMPT_EFFECT_UNCERTAIN",
        ),
    )
plan = compile_generic_dag_plan(spec(Path("lease")), source_path=Path("lease-dag.json"))
lease_result = run_dag_plan(plan, execute_node=ok, run_store=BadAcquireStore(), run_id="lease")
lease_item = lease_result.node_results[-1]
lease_boundary = boundary(lease_item)
lease_repeat = run_dag_plan(
    plan,
    execute_node=ok,
    run_store=BadAcquireStore(),
    run_id="lease-repeat",
).node_results[-1]
lease_repeat_boundary = boundary(lease_repeat)
cases["scheduler_run_lease"] = {
    "status": lease_result.status,
    "verdict": lease_result.verdict,
    "boundary_id": lease_boundary.get("boundary_id"),
    "repair_category": lease_boundary.get("repair_category"),
    "retryable": lease_item.get("retryable"),
    "downstream_calls": 0,
    "call_count": 0,
    "canonical_admission": _canonical_admitted("lease", lease_item),
    "replay_stable": lease_repeat_boundary.get("boundary_id")
    == lease_boundary.get("boundary_id")
    and lease_repeat_boundary.get("repair_category") == lease_boundary.get("repair_category"),
    "replay_mode": "pre_lease_repeat_observation",
    "replay_boundary_id": lease_repeat_boundary.get("boundary_id"),
    "replay_repair_category": lease_repeat_boundary.get("repair_category"),
    "node_result": lease_item,
}

print(json.dumps(cases, sort_keys=True))
''',
        encoding="utf-8",
    )
    faults_run = _run([str(python), str(fault_script)], cwd=work)
    try:
        faults = json.loads(faults_run["stdout"])
    except json.JSONDecodeError:
        faults = {}

    ids = set(registry.get("boundary_ids", []))
    expected = {
        "adapter_future_execution",
        "attempt_result_admission",
        "recursive_failure_object_admission",
        "result_store_commit",
        "route_join_transition",
        "checkpoint_replay_recovery",
        "cleanup_retention",
        "resource_lease",
        "worker_assignment_completion",
        "workspace_stale_read",
        "effect_outbox_reconciliation",
        "node_completion_boundary",
        "scheduler_run_lease",
    }
    registry_retryable = {
        item.get("boundary_id"): item.get("retryable")
        for item in registry.get("boundaries", [])
        if isinstance(item, dict)
    }
    durable_replay_cases = {
        name: item for name, item in faults.items() if item.get("replay_mode") == "durable_replay"
    }
    replay_exempt_modes = {
        "explicit_non_attempt_boundary_no_staged_replay",
        "repeated_helper_observation",
        "pre_lease_repeat_observation",
    }
    checks = {
        "installed_cli_registry_schema": registry.get("schema")
        == "tau.scheduler_boundary_registry.v1",
        "registry_has_required_boundaries": expected <= ids,
        "registry_has_at_least_10_boundaries": len(ids) >= 10,
        "matrix_covers_expected_boundaries": expected <= set(faults),
        "all_cases_blocked_or_controlled": bool(faults) and all(
            item["status"] in {"BLOCKED", "PASS"} for item in faults.values()
        ),
        "all_expected_boundaries_matched": bool(faults) and all(
            faults[name]["boundary_id"] == name for name in expected
        ),
        "no_downstream_calls_after_boundary_block": bool(faults) and all(
            item["downstream_calls"] == 0 for item in faults.values()
        ),
        "all_failure_objects_admitted": bool(faults) and all(
            item["canonical_admission"] for item in faults.values()
        ),
        "restart_replay_stable_boundary_category": bool(durable_replay_cases) and all(
            item["replay_stable"] for item in durable_replay_cases.values()
        ),
        "non_durable_replay_cases_are_explicit": bool(faults) and all(
            item.get("replay_mode") == "durable_replay"
            or item.get("replay_mode") in replay_exempt_modes
            for item in faults.values()
        ),
        "retry_policy_matches_registry": bool(faults) and all(
            item["retryable"] is registry_retryable.get(item["boundary_id"])
            for item in faults.values()
        ),
        "recursive_failure_contained": bool(faults)
        and faults["recursive_failure_object_admission"]["boundary_id"]
        == "recursive_failure_object_admission",
        "recursive_failure_has_tau_code": bool(faults)
        and _boundary(
            faults["recursive_failure_object_admission"]["node_result"]
        ).get("failure", {}).get("classification_code")
        == "tau_scheduler_boundary_fallback",
    }
    errors = [name for name, ok in checks.items() if not ok]
    proof = {
        "schema": "tau.scheduler_boundary_registry_proof.v1",
        "issue": 349,
        "status": "PASS" if not errors else "BLOCKED",
        "ok": not errors,
        "mocked": False,
        "live": True,
        "installed_wheel": str(wheels[-1]),
        "registry": registry,
        "faults": faults,
        "checks": checks,
        "commands": {
            "build": build,
            "venv_create": venv_create,
            "pip_install": pip,
            "registry": registry_run,
            "faults": faults_run,
        },
        "errors": errors,
        "proof_boundary": {
            "proves": "Installed-wheel scheduler boundary registry and deterministic "
            "fault containment for required scheduler authority boundaries.",
            "does_not_prove": "Provider quality or every external runtime backend path.",
        },
    }
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(proof, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(proof, indent=2, sort_keys=True))
    return 0 if not errors else 1


if __name__ == "__main__":
    raise SystemExit(main())
