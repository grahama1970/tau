#!/usr/bin/env python3
"""Run one inspectable crash/restart self-healing DAG canary."""

from __future__ import annotations

import argparse
import json
import os
import tempfile
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from tau_coding.battle_scillm import preflight_battle_scillm_auth
from tau_coding.dag_runtime.compiler import compile_generic_dag_plan
from tau_coding.dag_runtime.correction import (
    CorrectionActionIntent,
    CorrectionIncident,
    reduce_correction_projections,
    run_correction_transaction,
)
from tau_coding.dag_runtime.run_store import SqliteDagRunStore
from tau_coding.dag_runtime.scheduler import DagCorrectionRequest, run_dag_plan
from tau_coding.dag_viewer.source_artifact import write_dag_source_artifact
from tau_coding.security_capability import compile_capability_decision


class InjectedSmokeCrash(RuntimeError):
    pass


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--live-scillm-readiness", action="store_true")
    parser.add_argument("--scillm-url", default="http://127.0.0.1:4001")
    parser.add_argument("--model", default="gpt-5.5")
    args = parser.parse_args()

    run_dir = args.out.expanduser().resolve()
    run_dir.mkdir(parents=True, exist_ok=True)
    database = run_dir / "dag-run.sqlite3"
    if database.exists():
        raise RuntimeError(f"self_healing_smoke_output_exists:{database}")

    source = {
        "schema": "tau.generic_dag_spec.v1",
        "run_id": "self-healing-smoke",
        "run_dir": str(run_dir),
        "nodes": [
            {
                "node_id": "provider-review",
                "role": "provider-review",
                "command": ["self-healing-smoke-worker"],
                "depends_on": [],
                "accepted_context_from": [],
                "receipt_path": str(run_dir / "provider-review-receipt.json"),
                "timeout_seconds": 30,
                "max_attempts": 2,
            }
        ],
    }
    source_path = run_dir / "input-dag.json"
    _atomic_json(source_path, source)
    plan = compile_generic_dag_plan(source, source_path=source_path)
    write_dag_source_artifact(
        source_payload=source,
        source_schema=str(source["schema"]),
        source_path=source_path,
        run_dir=run_dir,
    )
    policy_sha256 = "sha256:self-healing-smoke-policy"
    capability_receipt = compile_capability_decision(
        dag_id=plan.plan_id,
        run_id="self-healing-smoke",
        goal_hash=plan.runtime_goal_hash,
        security_context={
            "security_context_sha256": "sha256:self-healing-smoke-context",
            "policy_profile": {"sha256": policy_sha256},
            "data_boundary": {"sha256": "sha256:self-healing-smoke-boundary"},
            "actor": {"actor_id": "human:smoke-operator"},
        },
        command_policy={
            "schema": "tau.command_spec_policy.v1",
            "allows_network": True,
            "allows_mutation": True,
            "capability_grant_ttl_seconds": 300,
            "capability_rules": [
                {
                    "capability": "provider.repair_auth",
                    "targets": ["refresh_local_provider_auth"],
                    "resource_scope": ["provider:scillm", "provider:local-fixture"],
                    "maximum_effect": {"max_repairs": 1},
                }
            ],
        },
        nodes=[
            {
                "node_id": "provider-review",
                "executor": "scheduler",
                "attempt": 1,
                "requested_capabilities": [
                    {
                        "capability": "provider.repair_auth",
                        "target": "refresh_local_provider_auth",
                        "resource_scope": [
                            "provider:scillm"
                            if args.live_scillm_readiness
                            else "provider:local-fixture"
                        ],
                        "maximum_effect": {"max_repairs": 1},
                    }
                ],
            }
        ],
        receipt_dir=run_dir / "security",
    )
    if capability_receipt["status"] != "PASS":
        raise RuntimeError("self_healing_smoke_capability_denied")
    capability_grant = capability_receipt["grants"][0]

    auth_state_path = run_dir / "auth-state.json"
    _atomic_json(auth_state_path, {"state": "EXPIRED", "effect_count": 0})
    node_calls = 0
    action_calls = 0
    verifier_calls = 0
    crash_injected = False

    def execute_node(*_args: object) -> dict[str, Any]:
        nonlocal node_calls
        node_calls += 1
        auth_state = _read_json(auth_state_path)
        if auth_state != {"state": "VALID", "effect_count": 1}:
            return {
                "node_id": "provider-review",
                "status": "BLOCKED",
                "verdict": "PROVIDER_AUTH_REQUIRED",
                "retryable": True,
                "correction_required": True,
                "errors": ["provider auth fixture is expired"],
            }
        receipt = {
            "schema": "tau.self_healing_smoke_node_receipt.v1",
            "status": "PASS",
            "auth_state": auth_state,
        }
        _atomic_json(run_dir / "provider-review-receipt.json", receipt)
        return {
            "node_id": "provider-review",
            "status": "PASS",
            "verdict": "PASS",
            "accepted_output": receipt,
        }

    def correction_handler(request: DagCorrectionRequest):  # type: ignore[no-untyped-def]
        incident = CorrectionIncident.create(
            run_id=request.attempt.run_id,
            dag_id=request.plan.plan_id,
            node_id=request.node.node_id,
            attempt=request.attempt.attempt,
            trigger="provider_auth_required",
            classification="RETRYABLE",
            goal_hash=request.plan.runtime_goal_hash,
            observed_state={"auth": "EXPIRED"},
        )
        intent = CorrectionActionIntent.create(
            incident=incident,
            capability="provider.repair_auth",
            action="refresh_local_provider_auth",
            target={"provider": "scillm" if args.live_scillm_readiness else "local-fixture"},
            policy_sha256=policy_sha256,
            capability_grant=capability_grant,
        )

        def apply_action(_intent: CorrectionActionIntent) -> dict[str, Any]:
            nonlocal action_calls
            action_calls += 1
            current = _read_json(auth_state_path)
            if current != {"state": "EXPIRED", "effect_count": 0}:
                raise RuntimeError("self_healing_smoke_effect_precondition_failed")
            _atomic_json(auth_state_path, {"state": "VALID", "effect_count": 1})
            provider_receipt = None
            if args.live_scillm_readiness:
                provider_receipt = preflight_battle_scillm_auth(
                    scillm_base_url=args.scillm_url,
                    model=args.model,
                    allow_repair=True,
                )
                _atomic_json(run_dir / "scillm-auth-readiness-receipt.json", provider_receipt)
            return {
                "auth_state": _read_json(auth_state_path),
                "provider_readiness": provider_receipt,
            }

        def verify_action(
            _intent: CorrectionActionIntent, action_receipt: Mapping[str, Any]
        ) -> dict[str, Any]:
            nonlocal verifier_calls
            verifier_calls += 1
            provider = action_receipt.get("result", {}).get("provider_readiness")
            provider_ok = not args.live_scillm_readiness or (
                isinstance(provider, dict) and provider.get("ok") is True
            )
            return {
                "verified": (
                    _read_json(auth_state_path) == {"state": "VALID", "effect_count": 1}
                    and provider_ok
                ),
                "auth_state_sha256": _sha256_json(_read_json(auth_state_path)),
                "provider_live": bool(args.live_scillm_readiness),
                "provider_ok": provider_ok,
            }

        def inject_crash(phase: str, _payload: Mapping[str, Any]) -> None:
            nonlocal crash_injected
            if phase == "after_applied" and not crash_injected:
                crash_injected = True
                raise InjectedSmokeCrash

        return run_correction_transaction(
            store=request.run_store,
            lease=request.lease,
            incident=incident,
            intent=intent,
            apply_action=apply_action,
            verify_action=verify_action,
            fault_injector=inject_crash,
        )

    first_run_crashed = False
    try:
        with SqliteDagRunStore(database) as store:
            run_dag_plan(
                plan,
                execute_node=execute_node,
                run_store=store,
                run_id="self-healing-smoke",
                lease_owner="self-healing-smoke-owner",
                correction_handler=correction_handler,
            )
    except InjectedSmokeCrash:
        first_run_crashed = True

    with SqliteDagRunStore(database) as store:
        result = run_dag_plan(
            plan,
            execute_node=execute_node,
            run_store=store,
            run_id="self-healing-smoke",
            lease_owner="self-healing-smoke-owner",
            correction_handler=correction_handler,
        )
        events = store.load_events("self-healing-smoke")
    corrections = reduce_correction_projections(events)
    provider_receipt_path = run_dir / "scillm-auth-readiness-receipt.json"
    provider_receipt = _read_json(provider_receipt_path) if provider_receipt_path.exists() else None
    status = (
        "PASS"
        if result.status == "PASS"
        and first_run_crashed
        and node_calls == 2
        and action_calls == 1
        and verifier_calls == 1
        and capability_receipt["status"] == "PASS"
        and capability_receipt["grant_count"] == 1
        and len(corrections) == 1
        and corrections[0].state == "VERIFIED"
        else "BLOCKED"
    )
    receipt = {
        "schema": "tau.self_healing_dag_smoke_receipt.v1",
        "status": status,
        "ok": status == "PASS",
        "mocked": False,
        "live": True,
        "provider_live": bool(args.live_scillm_readiness),
        "run_id": result.run_id,
        "run_status": result.status,
        "run_verdict": result.verdict,
        "crash_after_applied_observed": first_run_crashed,
        "node_call_count": node_calls,
        "correction_action_call_count": action_calls,
        "correction_verifier_call_count": verifier_calls,
        "effect_count": _read_json(auth_state_path)["effect_count"],
        "correction_state": corrections[0].state if corrections else None,
        "correction_incident_id": corrections[0].incident_id if corrections else None,
        "journal_sequence": events[-1]["seq"] if events else 0,
        "capability_decision_status": capability_receipt["status"],
        "capability_decision_receipt": capability_receipt["receipt_path"],
        "capability_grant_sha256": capability_grant["grant_sha256"],
        "provider_readiness": provider_receipt,
        "run_dir": str(run_dir),
        "viewer_command": ["tau", "dag-view", "--run-dir", str(run_dir)],
        "proof_scope": {
            "proves": [
                "Tau compiled the correction capability against a command policy and "
                "validated its hash, expiry, and run/DAG/node/attempt/goal bindings.",
                "Tau committed a correction intent before one local filesystem effect.",
                "Tau resumed after a crash following APPLIED without duplicating the effect.",
                "Tau verified the postcondition before releasing one original-node retry.",
            ],
            "does_not_prove": [
                "Provider/model semantic correctness.",
                "Every provider auth failure is automatically repairable.",
                "A live provider repair occurred when repair_attempted is false.",
                "The browser viewer is correct until separately verified by CDP.",
            ],
        },
    }
    _atomic_json(run_dir / "self-healing-smoke-receipt.json", receipt)
    print(json.dumps(receipt, indent=2, sort_keys=True))
    return 0 if status == "PASS" else 1


def _read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise RuntimeError(f"expected_json_object:{path}")
    return value


def _atomic_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(value, handle, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def _sha256_json(value: dict[str, Any]) -> str:
    from tau_coding.dag_runtime.model import canonical_sha256

    return canonical_sha256(value)


if __name__ == "__main__":
    raise SystemExit(main())
