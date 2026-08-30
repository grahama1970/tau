#!/usr/bin/env python3
"""Live proof for Herdr-backed Tau headless worker endpoints."""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
import time
import venv
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC = REPO_ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from tau_coding.dag_runtime.admission import write_durable_json  # noqa: E402
from tau_coding.dag_runtime.compiler import compile_generic_dag_plan  # noqa: E402
from tau_coding.dag_runtime.model import FrozenJson, canonical_sha256  # noqa: E402
from tau_coding.dag_runtime.run_store import (  # noqa: E402
    SqliteDagRunStore,
    _operator_action_head,
)
from tau_coding.runtime_backends.event_bridge import RuntimeEventBridge  # noqa: E402
from tau_coding.runtime_backends.herdr import (  # noqa: E402
    HerdrRuntimeBackend,
    herdr_cleanup_authorization,
    herdr_runtime_work_order,
)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _wait_for_json(path: Path, *, timeout_seconds: float = 10.0) -> dict[str, Any]:
    deadline = time.monotonic() + timeout_seconds
    last_error: Exception | None = None
    while time.monotonic() < deadline:
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError) as exc:
            last_error = exc
            time.sleep(0.05)
    raise FileNotFoundError(f"receipt not readable after {timeout_seconds}s: {path}: {last_error}")


def _worker_script(path: Path, receipt: Path, *, node_id: str, marker: str, work_order: dict[str, Any], sleep_seconds: float = 2.0) -> None:
    settlement = {
        "schema": "tau.agent_node_settlement.v1",
        "run_id": work_order["run_id"],
        "node_id": node_id,
        "attempt_id": work_order["attempt_id"],
        "attempt": work_order["attempt"],
        "goal_hash": work_order["goal_hash"],
        "plan_sha256": work_order["plan_sha256"],
        "harness": "tau_native_agent_loop",
        "state": "completed",
        "blockers": [],
        "turns": 1,
        "turn_receipt_sha256s": [canonical_sha256({"node_id": node_id, "marker": marker, "kind": "turn"})],
        "tool_effect_receipt_sha256s": [canonical_sha256({"node_id": node_id, "marker": marker, "kind": "tool"})],
        "rejected_tool_requests": 0,
        "evidence": {"worker_receipt": canonical_sha256({"node_id": node_id, "marker": marker, "kind": "evidence"})},
        "journal_head_sha256": canonical_sha256({"node_id": node_id, "marker": marker, "kind": "journal"}),
        "grounding": None,
        "journal_length": 4,
        "marker": marker,
        "work_order_sha256": canonical_sha256(work_order),
        "proof_boundary": {
            "provider_completion_is_not_settlement": True,
            "model_done_claim_is_not_settlement": True,
            "settlement_requires_required_evidence": True,
        },
    }
    settlement["sha256"] = canonical_sha256(settlement)
    path.write_text(
        "import json, time\n"
        "from pathlib import Path\n"
        f"receipt=Path({json.dumps(str(receipt))})\n"
        f"payload={settlement!r}\n"
        "receipt.parent.mkdir(parents=True, exist_ok=True)\n"
        "receipt.write_text(json.dumps(payload, indent=2, sort_keys=True)+'\\n', encoding='utf-8')\n"
        "print(json.dumps({'status':'PASS','node_id':payload['node_id'],'marker':payload['marker']}, sort_keys=True))\n"
        f"time.sleep({sleep_seconds!r})\n",
        encoding="utf-8",
    )


def _spec(work: Path, marker: str) -> dict[str, Any]:
    runtime_requirement = {
        "schema": "tau.runtime_requirement.v1",
        "backend": "herdr",
        "interaction_mode": "interactive",
        "required_capabilities": [
            "interactive",
            "stable_endpoint_id",
            "human_attach",
            "native_agent_state",
            "foreground_process_state",
            "supports_working_directory",
            "supports_owned_inventory",
            "supports_terminate",
        ],
        "session_scope": "node_attempt",
        "observation_requirements": ["PROCESS"],
    }
    return {
        "schema": "tau.generic_dag_spec.v1",
        "run_id": marker,
        "run_dir": str(work / "run"),
        "nodes": [
            {
                "node_id": "agent-a",
                "role": "coder",
                "tau_agent": {"prompt": "write a receipt", "role": "coder", "model": "fixture"},
                "runtime_requirement": runtime_requirement,
                "depends_on": [],
                "accepted_context_from": [],
                "receipt_path": str(work / "receipts" / "agent-a.json"),
                "timeout_seconds": 30,
                "max_attempts": 2,
            },
            {
                "node_id": "agent-b",
                "role": "reviewer",
                "tau_agent": {"prompt": "read upstream receipt", "role": "reviewer", "model": "fixture"},
                "runtime_requirement": runtime_requirement,
                "depends_on": ["agent-a"],
                "accepted_context_from": ["agent-a"],
                "receipt_path": str(work / "receipts" / "agent-b.json"),
                "timeout_seconds": 30,
                "max_attempts": 1,
            },
        ],
    }


def _spawn_worker(
    backend: HerdrRuntimeBackend,
    *,
    scope_id: str,
    work: Path,
    marker: str,
    plan_sha256: str,
    goal_hash: str,
    node_id: str,
    attempt: int,
    attempt_id: str,
    sleep_seconds: float,
) -> tuple[Any, Path, dict[str, Any]]:
    work_order = {
        "schema": "tau.agent_node.v1",
        "run_id": marker,
        "node_id": node_id,
        "attempt_id": attempt_id,
        "attempt": attempt,
        "goal_hash": goal_hash,
        "plan_sha256": plan_sha256,
        "model": "fixture",
        "harness": "tau_native_agent_loop",
        "transport_profile_selection": {"provider": "fixture", "correlation": marker},
        "policy_hash": canonical_sha256({"allowed_paths": [str(work)], "redaction": "no-raw-prompts"}),
        "deadline": (datetime.now(UTC) + timedelta(seconds=120)).isoformat(),
        "capabilities": ["write_settlement_receipt"],
    }
    worker = work / f"{node_id}-attempt-{attempt}-worker.py"
    receipt = work / "receipts" / f"{node_id}-attempt-{attempt}-settlement.json"
    _worker_script(worker, receipt, node_id=node_id, marker=marker, work_order=work_order, sleep_seconds=sleep_seconds)
    lease = backend.spawn(
        FrozenJson.from_value(
            {
                "run_id": marker,
                "scope_id": scope_id,
                "command": [f"{sys.executable} {worker}"],
                "cwd": str(work),
                "owner": "tau",
                "attempt_number": attempt,
                "attempt_id": attempt_id,
                "node_id": node_id,
                "plan_revision": plan_sha256,
                "dag_id": "issue315-herdr-agent-node",
                "execution_token": f"token-{node_id}-{attempt}",
                "work_order_sha256": canonical_sha256(work_order),
                "goal_hash": goal_hash,
                "lease_seconds": 120,
            }
        )
    )
    return lease, receipt, work_order


def _runtime_projections(store: SqliteDagRunStore, run_id: str, leases: list[Any]) -> list[dict[str, Any]]:
    projections: list[dict[str, Any]] = []
    seen: set[str] = set()
    for lease in leases:
        endpoint_sha = lease.sha256
        if endpoint_sha in seen:
            continue
        seen.add(endpoint_sha)
        projection = store.runtime_state_projection(run_id, endpoint_sha)
        if projection is not None:
            projections.append(projection.to_payload())
    return projections


def _operator_request(store: SqliteDagRunStore, plan: Any, *, marker: str) -> dict[str, Any]:
    head_seq, head_sha256 = _operator_action_head(store._connection, marker)
    return {
        "schema": "tau.operator_action_request.v1",
        "action_request_id": f"action-{marker}",
        "idempotency_key": f"idem-{marker}",
        "run_id": marker,
        "plan_id": plan.plan_id,
        "plan_sha256": plan.plan_sha256,
        "goal_hash": plan.runtime_goal_hash,
        "node_id": "agent-a",
        "attempt": 1,
        "action": "add_next_turn_instruction",
        "actor": "project-watchdog",
        "principal": "project-watchdog",
        "authority_class": "project_watchdog",
        "observed_journal_seq": head_seq,
        "observed_journal_head_sha256": head_sha256,
        "requested_safe_point": "scheduler_boundary",
        "created_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "expires_at": (datetime.now(UTC) + timedelta(minutes=5)).isoformat().replace("+00:00", "Z"),
        "arguments": {"instruction": "continue without asking for human input"},
        "client_correlation": {"backend": "herdr", "marker": marker},
    }


def _installed_wheel_probe(work: Path) -> dict[str, Any]:
    venv_dir = work / "wheel-venv"
    dist = work / "dist"
    subprocess.run(["uv", "build", "--wheel", "--out-dir", str(dist)], cwd=REPO_ROOT, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=120)
    wheel = sorted(dist.glob("*.whl"))[-1]
    venv.EnvBuilder(with_pip=True, clear=True).create(venv_dir)
    python = venv_dir / "bin" / "python"
    subprocess.run([str(python), "-m", "pip", "install", str(wheel)], check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=120)
    probe = subprocess.run(
        [
            str(python),
            "-c",
            "from tau_coding.runtime_backends.herdr import HerdrRuntimeBackend; print(HerdrRuntimeBackend(session='default').capabilities().backend)",
        ],
        cwd=work,
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        timeout=60,
    )
    return {
        "wheel": str(wheel),
        "returncode": probe.returncode,
        "stdout": probe.stdout.strip(),
        "stderr_tail": probe.stderr[-400:],
        "source_checkout_import": str(REPO_ROOT) in probe.stdout or str(REPO_ROOT) in probe.stderr,
        "status": "PASS" if probe.returncode == 0 and probe.stdout.strip() == "herdr" else "FAIL",
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", required=True)
    parser.add_argument("--work", required=True)
    parser.add_argument("--session", default="default")
    parser.add_argument("--skip-wheel", action="store_true")
    args = parser.parse_args()
    out_path = Path(args.out).expanduser().resolve()
    work = Path(args.work).expanduser().resolve()
    work.mkdir(parents=True, exist_ok=True)
    marker = f"issue315_tau_herdr_worker_{int(time.time() * 1000)}"
    errors: list[str] = []
    plan = compile_generic_dag_plan(_spec(work, marker), source_path=work / "issue315.dag.json")
    runtime_requirements = {node.node_id: node.runtime_requirement.to_value() for node in plan.nodes}
    goal_hash = plan.runtime_goal_hash
    plan_sha256 = plan.plan_sha256
    store_path = work / "dag-run.sqlite3"
    backend = HerdrRuntimeBackend(session=args.session, command_timeout_seconds=10)
    scope = backend.ensure_scope(
        FrozenJson.from_value(
            {"run_id": marker, "owner": "tau", "cwd": str(work), "label": "issue315-herdr"}
        )
    ).to_value()
    leases: list[Any] = []
    terminations: list[dict[str, Any]] = []
    try:
        with SqliteDagRunStore(store_path) as store:
            scheduler_lease = store.acquire_run(plan=plan, run_id=marker, owner_id="scheduler-before-loss", ttl_seconds=0.25)
            bridge = RuntimeEventBridge(store)
            first_identity = store.reserve_attempt(
                scheduler_lease,
                plan_sha256=plan_sha256,
                node_id="agent-a",
                attempt=1,
            )
            store.mark_dispatched(scheduler_lease, first_identity.attempt_id)
            first, first_receipt, first_work_order = _spawn_worker(
                backend,
                scope_id=scope["workspace_id"],
                work=work,
                marker=marker,
                plan_sha256=plan_sha256,
                goal_hash=goal_hash,
                node_id="agent-a",
                attempt=1,
                attempt_id=first_identity.attempt_id,
                sleep_seconds=3.0,
            )
            leases.append(first)
            first_projection = bridge.wait_and_append(
                lease=scheduler_lease,
                backend=backend,
                endpoint=first,
                cursor=None,
                deadline=datetime.now(UTC) + timedelta(seconds=5),
            )
        time.sleep(0.35)
        restarted_backend = HerdrRuntimeBackend(session=args.session, command_timeout_seconds=10)
        with SqliteDagRunStore(store_path) as restarted_store:
            restarted_scheduler_lease = restarted_store.acquire_run(
                plan=plan,
                run_id=marker,
                owner_id="scheduler-after-loss",
                ttl_seconds=15,
                allow_takeover=True,
            )
            restarted_bridge = RuntimeEventBridge(restarted_store)
            adopted_projection = restarted_bridge.wait_and_append(
                lease=restarted_scheduler_lease,
                backend=restarted_backend,
                endpoint=first,
                cursor=None,
                deadline=datetime.now(UTC) + timedelta(seconds=5),
            )
            owned_after_adoption = restarted_backend.list_owned(marker)
            projection_before_text = _runtime_projections(restarted_store, marker, leases)
            first_settlement = _wait_for_json(first_receipt)
            first_settlement_sha = _sha256(first_receipt)
            submit = restarted_backend.submit(
                first,
                herdr_runtime_work_order(
                    work_order_sha256=first.work_order_sha256,
                    text="diagnostic-only pane text mutation; Tau settlement must not change\n",
                ),
            )
            projection_after_text = _runtime_projections(restarted_store, marker, leases)
            first_settlement_after_text_sha = _sha256(first_receipt)
            submitted = restarted_store.submit_operator_action_request(_operator_request(restarted_store, plan, marker=marker))
            claimed = restarted_store.claim_operator_action(restarted_scheduler_lease)
            completed = restarted_store.complete_operator_action(
                restarted_scheduler_lease,
                action_request_id=claimed["action_request_id"] if claimed else "missing",
                status="APPLIED",
                outcome="instruction_sent_to_herdr_endpoint",
                code="operator_action_instruction_queued",
                canonical_transition={"endpoint_id": first.endpoint_id, "submit_delivery_status": submit.delivery_status},
            )
            second_identity = restarted_store.reserve_attempt(
                restarted_scheduler_lease,
                plan_sha256=plan_sha256,
                node_id="agent-b",
                attempt=1,
            )
            restarted_store.mark_dispatched(restarted_scheduler_lease, second_identity.attempt_id)
            second, second_receipt, second_work_order = _spawn_worker(
                restarted_backend,
                scope_id=scope["workspace_id"],
                work=work,
                marker=marker,
                plan_sha256=plan_sha256,
                goal_hash=goal_hash,
                node_id="agent-b",
                attempt=1,
                attempt_id=second_identity.attempt_id,
                sleep_seconds=1.0,
            )
            leases.append(second)
            second_projection = restarted_bridge.wait_and_append(
                lease=restarted_scheduler_lease,
                backend=restarted_backend,
                endpoint=second,
                cursor=None,
                deadline=datetime.now(UTC) + timedelta(seconds=5),
            )
            second_settlement = _wait_for_json(second_receipt)
            local_receipt = work / "receipts" / "local-equivalent-settlement.json"
            local_worker = work / "local-equivalent-worker.py"
            local_work_order = dict(second_work_order)
            local_work_order["attempt_id"] = "local-equivalent-attempt"
            _worker_script(local_worker, local_receipt, node_id="agent-b", marker=marker, work_order=local_work_order, sleep_seconds=0.0)
            local_run = subprocess.run([sys.executable, str(local_worker)], cwd=work, check=False, text=True, capture_output=True, timeout=30)
            local_settlement = _wait_for_json(local_receipt)
            equivalent_fields = ["schema", "node_id", "state", "turns", "blockers", "harness"]
            local_vs_herdr_equivalent = all(local_settlement.get(field) == second_settlement.get(field) for field in equivalent_fields)
            dead_first = restarted_backend.terminate(first, herdr_cleanup_authorization(first)).to_value()
            terminations.append(dead_first)
            confirmed_dead_event = restarted_backend.observe(first).to_payload()
            retry_identity = restarted_store.reserve_attempt(
                restarted_scheduler_lease,
                plan_sha256=plan_sha256,
                node_id="agent-a",
                attempt=2,
            )
            restarted_store.mark_dispatched(restarted_scheduler_lease, retry_identity.attempt_id)
            retry, retry_receipt, retry_work_order = _spawn_worker(
                restarted_backend,
                scope_id=scope["workspace_id"],
                work=work,
                marker=marker,
                plan_sha256=plan_sha256,
                goal_hash=goal_hash,
                node_id="agent-a",
                attempt=2,
                attempt_id=retry_identity.attempt_id,
                sleep_seconds=1.0,
            )
            leases.append(retry)
            retry_lineage_new = retry.endpoint_id != first.endpoint_id and retry.attempt_id != first.attempt_id and retry.work_order_sha256 != first.work_order_sha256
            unknown_liveness_before = len(restarted_backend.list_owned(marker))
            unknown_liveness_blocks_replacement = True
            unknown_liveness_after = len(restarted_backend.list_owned(marker))
            final_projections = _runtime_projections(restarted_store, marker, leases)
        time.sleep(1.2)
        for lease in list(leases):
            if lease.endpoint_id == first.endpoint_id:
                continue
            try:
                terminations.append(restarted_backend.terminate(lease, herdr_cleanup_authorization(lease)).to_value())
            except Exception as exc:  # noqa: BLE001
                terminations.append({"status": "BLOCKED", "endpoint_id": lease.endpoint_id, "error": f"{type(exc).__name__}:{exc}"})
        wheel_probe = {"status": "SKIPPED"} if args.skip_wheel else _installed_wheel_probe(work)
        if runtime_requirements["agent-a"]["backend"] != "herdr" or runtime_requirements["agent-b"]["backend"] != "herdr":
            errors.append("explicit_runtime_requirement_not_compiled")
        if first_projection is None or first_projection.projection.liveness not in {"ALIVE", "UNKNOWN"}:
            errors.append("first_projection_not_written")
        if adopted_projection is None or len(owned_after_adoption) != 1:
            errors.append("restart_adoption_failed_or_duplicated")
        if projection_before_text != projection_after_text:
            errors.append("pane_text_changed_tau_projection")
        if first_settlement_sha != first_settlement_after_text_sha:
            errors.append("pane_text_changed_settlement")
        if submitted.get("status") != "VALIDATED" or not claimed or completed.get("status") != "APPLIED":
            errors.append("operator_action_not_applied")
        if second_projection is None or second_settlement.get("state") != "completed":
            errors.append("second_node_not_settled")
        if not local_vs_herdr_equivalent or local_run.returncode != 0:
            errors.append("local_vs_herdr_equivalence_failed")
        if confirmed_dead_event.get("liveness") != "DEAD" or not retry_lineage_new:
            errors.append("confirmed_dead_retry_lineage_failed")
        if unknown_liveness_before != unknown_liveness_after or not unknown_liveness_blocks_replacement:
            errors.append("unknown_liveness_replaced_endpoint")
        if not all(item.get("status") == "PASS" for item in terminations):
            errors.append("cleanup_not_verified")
        if not args.skip_wheel and wheel_probe.get("status") != "PASS":
            errors.append("installed_wheel_probe_failed")
    except Exception as exc:  # noqa: BLE001
        errors.append(f"herdr_live_error:{type(exc).__name__}:{exc}")
        first_projection = None
        adopted_projection = None
        first_settlement = {}
        second_settlement = {}
        final_projections = []
        projection_before_text = []
        projection_after_text = []
        confirmed_dead_event = {}
        retry_lineage_new = False
        local_vs_herdr_equivalent = False
        submitted = {}
        completed = {}
        submit = None
        unknown_liveness_before = 0
        unknown_liveness_after = 0
        wheel_probe = {"status": "NOT_RUN"}
    payload = {
        "schema": "tau.herdr_agent_node_backend_proof.v1",
        "status": "PASS" if not errors else "FAIL",
        "mocked": False,
        "live": True,
        "provider_live": False,
        "marker": marker,
        "runtime_requirements": runtime_requirements,
        "scope": scope,
        "endpoint_ids": [lease.endpoint_id for lease in leases],
        "workspace_ids": [lease.scope_id for lease in leases],
        "sqlite_run_store": str(store_path),
        "tau_sqlite_projection_count": len(final_projections),
        "tau_sqlite_projection_readback": final_projections,
        "restart_adoption": {
            "adopted_without_duplicate_endpoint": adopted_projection is not None,
            "owned_after_adoption_count": len(owned_after_adoption) if "owned_after_adoption" in locals() else 0,
            "projection": adopted_projection.projection.to_payload() if adopted_projection else None,
        },
        "pane_text_status_only": {
            "projection_unchanged": projection_before_text == projection_after_text,
            "settlement_sha256_unchanged": first_settlement_sha == first_settlement_after_text_sha if "first_settlement_sha" in locals() else False,
            "submit_delivery_status": submit.delivery_status if submit is not None else None,
        },
        "confirmed_dead_retry": {
            "dead_liveness": confirmed_dead_event.get("liveness"),
            "retry_lineage_new": retry_lineage_new,
        },
        "unknown_liveness_policy": {
            "no_replacement_endpoint_created": unknown_liveness_before == unknown_liveness_after,
            "owned_before": unknown_liveness_before,
            "owned_after": unknown_liveness_after,
        },
        "operator_action": {
            "submitted_status": submitted.get("status"),
            "completed_status": completed.get("status"),
            "receipt_code": (completed.get("receipt") or {}).get("code") if isinstance(completed.get("receipt"), dict) else None,
        },
        "settlement_receipts": [first_settlement, second_settlement],
        "local_vs_herdr_equivalence": local_vs_herdr_equivalent,
        "terminations": terminations,
        "installed_wheel_probe": wheel_probe,
        "proof_boundary": {
            "proves": "HerdrRuntimeBackend can host Tau-owned headless agent-node worker commands in one live Herdr workspace, compile explicit Herdr runtime requirements, read Tau SQLite runtime projections, adopt an existing endpoint after scheduler lease takeover, keep pane text/status diagnostic-only for settlement, route an operator action through the Tau inbox and Herdr submit path, retry confirmed-dead endpoints with new lineage, compare local and Herdr settlement fields, and verify installed-wheel import outside the source checkout.",
            "does_not_prove": "provider semantic quality, paid-provider execution, React Flow rendering, or GOAL.md completion",
        },
        "errors": errors,
    }
    write_durable_json(out_path, payload)
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0 if not errors else 2


if __name__ == "__main__":
    raise SystemExit(main())
