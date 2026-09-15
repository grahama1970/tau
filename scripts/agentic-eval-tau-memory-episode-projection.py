#!/usr/bin/env python3
"""Live proof for Tau orchestration episode projection into Memory."""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
import time
from pathlib import Path
from typing import Any
from urllib import request

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC = REPO_ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from tau_coding.dag_runtime.admission import write_durable_json  # noqa: E402
from tau_coding.dag_runtime.compiler import compile_generic_dag_plan  # noqa: E402
from tau_coding.dag_runtime.memory_projection import (  # noqa: E402
    MemoryProjectionOutbox,
    build_tau_orchestration_episode,
    governed_memory_store_sender,
    validate_tau_orchestration_episode,
)
from tau_coding.dag_runtime.model import canonical_sha256  # noqa: E402
from tau_coding.dag_runtime.run_store import SqliteDagRunStore  # noqa: E402
from tau_coding.dag_runtime.scheduler import DagNodeAttempt, run_dag_plan  # noqa: E402


def _sha(text: str) -> str:
    return "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()


def _agent_event(
    node_id: str, attempt: int, seq: int, prev: str, event_type: str, summary: str
) -> dict[str, Any]:
    entry: dict[str, Any] = {
        "schema": "tau.agent_event.v1",
        "node_id": node_id,
        "attempt": attempt,
        "seq": seq,
        "prev_sha256": prev,
        "event_type": event_type,
        "payload": {"summary": summary},
    }
    entry["sha256"] = canonical_sha256(entry)
    return entry


def _post_json(url: str, payload: dict[str, Any], timeout: float = 10.0) -> dict[str, Any]:
    req = request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with request.urlopen(req, timeout=timeout) as response:  # nosec B310
        return json.loads(response.read().decode("utf-8"))


def _get_json(url: str, timeout: float = 5.0) -> dict[str, Any]:
    with request.urlopen(url, timeout=timeout) as response:  # nosec B310
        return json.loads(response.read().decode("utf-8"))


def _docker(
    args: list[str], *, timeout: float = 60.0, input_text: str | None = None
) -> dict[str, Any]:
    completed = subprocess.run(
        ["docker", *args],
        check=False,
        capture_output=True,
        text=True,
        timeout=timeout,
        input=input_text,
    )
    return {
        "command": ["docker", *args],
        "returncode": completed.returncode,
        "stdout": completed.stdout.strip(),
        "stderr": completed.stderr.strip(),
    }


def _wait_memory(memory_url: str, *, up: bool, timeout: float = 90.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            ok = bool(_get_json(memory_url.rstrip("/") + "/health", timeout=2.0).get("ok"))
        except Exception:  # noqa: BLE001
            ok = False
        if ok is up:
            return True
        time.sleep(1)
    return False


def _delete_memory_projection(memory_container: str, keys: list[str]) -> dict[str, Any]:
    script = f'''
import json
from graph_memory.arango_client import get_db
keys = json.loads({json.dumps(json.dumps(keys))})
db = get_db()
out = {{"requested_keys": keys, "deleted_documents": 0, "deleted_edges": 0}}
for key in keys:
    doc_id = "agent_conversations/" + key
    if db.has_collection("agent_conversation_edges"):
        removed = list(db.aql.execute(
            """FOR e IN agent_conversation_edges
               FILTER e._from == @doc_id OR e._to == @doc_id
               REMOVE e IN agent_conversation_edges RETURN OLD._key""",
            bind_vars={{"doc_id": doc_id}},
        ))
        out["deleted_edges"] += len(removed)
    if db.has_collection("agent_conversations"):
        coll = db.collection("agent_conversations")
        if coll.has(key):
            coll.delete({{"_key": key}})
            out["deleted_documents"] += 1
print(json.dumps(out, sort_keys=True))
'''
    result = _docker(
        [
            "exec",
            "-i",
            memory_container,
            "sh",
            "-lc",
            "cd /app && PYTHONPATH=/app/src /usr/local/bin/python -",
        ],
        timeout=60.0,
        input_text=script,
    )
    try:
        result["parsed"] = json.loads(str(result.get("stdout") or "{}"))
    except json.JSONDecodeError:
        result["parsed"] = {}
    return result


def _tau_state(store: SqliteDagRunStore, run_id: str) -> dict[str, Any]:
    return {
        "status": store.load_run_record(run_id).status,
        "event_count": len(store.load_events(run_id)),
        "admission_count": len(store.list_admissions(run_id)),
        "settled_attempt_ids": sorted(
            attempt.identity.attempt_id
            for attempt in store.list_attempts(run_id)
            if attempt.state == "SETTLED"
        ),
    }


def _memory_readback(memory_url: str, keys: list[str], marker: str) -> dict[str, Any]:
    by_keys = _post_json(
        memory_url.rstrip("/") + "/recall/by-keys",
        {
            "collection": "agent_conversations",
            "keys": keys,
            "key_field": "_key",
            "return_fields": [
                "_key",
                "schema",
                "run_id",
                "node_id",
                "attempt_id",
                "journal_sequence",
                "journal_head_hash",
                "source_outbox_row",
                "source_receipt_hashes",
                "summary",
                "tags",
            ],
        },
    )
    recall = _post_json(
        memory_url.rstrip("/") + "/recall",
        {
            "q": marker,
            "k": 5,
            "threshold": 0.0,
            "collections": ["agent_conversations"],
            "recall_profile": "tau_orchestration_recall",
            "brief": True,
        },
    )
    return {"by_keys": by_keys, "recall": recall}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", required=True)
    parser.add_argument("--work", required=True)
    parser.add_argument("--memory-url", default="http://127.0.0.1:8601")
    parser.add_argument("--memory-container", default="embry-memory")
    args = parser.parse_args()
    out_path = Path(args.out).expanduser().resolve()
    work = Path(args.work).expanduser().resolve()
    work.mkdir(parents=True, exist_ok=True)
    marker = f"issue321_tau_orchestration_episode_{int(time.time() * 1000)}"
    run_id = marker.replace("_", "-")

    errors: list[str] = []
    dag_path = work / "issue321.dag.json"
    run_dir = work / "run"
    spec = {
        "schema": "tau.generic_dag_spec.v1",
        "run_id": run_id,
        "run_dir": str(run_dir),
        "goal_hash": "sha256:issue321-memory-episode",
        "max_concurrency": 2,
        "nodes": [
            {
                "node_id": "turn-one",
                "role": "turn",
                "receipt_path": str(work / "turn-one-receipt.json"),
                "timeout_seconds": 30,
                "command": ["true"],
            },
            {
                "node_id": "tool-and-join",
                "role": "tool",
                "depends_on": ["turn-one"],
                "accepted_context_from": ["turn-one"],
                "receipt_path": str(work / "tool-and-join-receipt.json"),
                "timeout_seconds": 30,
                "command": ["true"],
            },
        ],
    }
    dag_path.write_text(json.dumps(spec, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    plan = compile_generic_dag_plan(spec, source_path=dag_path)
    store = SqliteDagRunStore(run_dir / "dag-run.sqlite3")

    def execute(node: object, _inputs: object, attempt: DagNodeAttempt) -> dict[str, Any]:
        node_id = getattr(node, "node_id")
        event_store = SqliteDagRunStore(store.path)
        try:
            lease = event_store.acquire_run(
                plan=plan,
                run_id=run_id,
                owner_id="issue321-runner",
                ttl_seconds=120,
            )
            event_types = (
                ("agent_turn_recorded", f"{marker} {node_id} first accepted turn"),
                ("tool_effect_recorded", f"{marker} {node_id} accepted tool effect"),
                ("agent_turn_recorded", f"{marker} {node_id} final accepted turn"),
            )
            prev = ""
            for seq, (event_type, summary) in enumerate(event_types, start=1):
                entry = _agent_event(str(node_id), attempt.attempt, seq, prev, event_type, summary)
                event_store.append_agent_event(
                    lease,
                    entry=entry,
                    binding={
                        "plan_sha256": plan.plan_sha256,
                        "goal_hash": plan.runtime_goal_hash,
                        "work_order_sha256": attempt.idempotency_key,
                        "attempt_id": attempt.attempt_id,
                        "transport_correlation": {},
                    },
                )
                prev = str(entry["sha256"])
        finally:
            event_store.close()
        summary = (
            f"{marker} tool effect and join accepted"
            if node_id == "tool-and-join"
            else f"{marker} turn one accepted"
        )
        receipt = {
            "schema": "tau.generic_dag_node_receipt.v1",
            "node_id": node_id,
            "status": "PASS",
            "verdict": "PASS",
            "mocked": False,
            "live": True,
            "provider_live": False,
            "goal_hash": "sha256:issue321-memory-episode",
            "accepted_output": {"summary": summary, "unique_marker": marker},
            "handoff_summary": summary,
            "errors": [],
        }
        receipt_path = work / f"{node_id}-receipt.json"
        write_durable_json(receipt_path, receipt)
        return {
            "node_id": node_id,
            "status": "PASS",
            "verdict": "PASS",
            "receipt_path": str(receipt_path),
            "accepted_output": receipt["accepted_output"],
            "live": True,
            "mocked": False,
            "provider_live": False,
        }

    outcome = run_dag_plan(
        plan,
        execute_node=execute,
        run_store=store,
        run_id=run_id,
        lease_owner="issue321-runner",
    )
    run_receipt = {
        "status": outcome.status,
        "run_store_path": str(run_dir / "dag-run.sqlite3"),
    }
    (work / "dag-run.stdout").write_text(json.dumps(run_receipt, indent=2), encoding="utf-8")
    (work / "dag-run.stderr").write_text("", encoding="utf-8")
    dag_exit_code = 0 if outcome.status == "PASS" else 2
    if dag_exit_code != 0:
        errors.append(f"dag_run_failed:{dag_exit_code}")
    if run_receipt.get("status") != "PASS":
        errors.append(f"dag_status:{run_receipt.get('status')}")

    outbox = MemoryProjectionOutbox(store)
    run_record = store.load_run_record(run_id)
    events = store.load_events(run_id)
    admissions = store.list_admissions(run_id)
    attempts = [attempt for attempt in store.list_attempts(run_id) if attempt.state == "SETTLED"]
    if len(attempts) != 2:
        errors.append(f"settled_attempt_count:{len(attempts)}")

    pre_relay_pending = outbox.pending()
    projected_docs = [
        json.loads(str(row["payload_json"]))
        for row in pre_relay_pending
        if json.loads(str(row["payload_json"])).get("schema")
        == "memory.tau_orchestration_episode.v1"
    ]
    projection_lease = store.acquire_run(
        plan=store._load_plan_for_validation(run_record.run_id),
        run_id=run_record.run_id,
        owner_id="issue321-projector",
        ttl_seconds=120,
    )
    sender = governed_memory_store_sender(memory_url=args.memory_url)
    live_results = outbox.relay(sender, max_attempts=3)
    replay_results = outbox.relay(sender, max_attempts=3)
    if [result.state for result in live_results].count("projected") != len(projected_docs):
        errors.append("live_projection_not_all_projected")
    if replay_results:
        errors.append("relay_replayed_projected_rows")

    readback = {}
    try:
        readback = _memory_readback(
            args.memory_url,
            [doc["_key"] for doc in projected_docs],
            marker,
        )
    except Exception as exc:  # noqa: BLE001
        errors.append(f"memory_readback_failed:{exc}")

    by_key_payload = readback.get("by_keys", {}) if isinstance(readback, dict) else {}
    readback_items = by_key_payload.get("items") or by_key_payload.get("documents") or []
    if len(readback_items) < len(projected_docs):
        errors.append(f"memory_by_key_readback_count:{len(readback_items)}")

    try:
        forbidden = dict(projected_docs[0])
        forbidden["raw_prompt"] = "secret prompt"
        validate_tau_orchestration_episode(forbidden)
        forbidden_rejected = False
    except Exception:
        forbidden_rejected = True
    try:
        mutated = dict(projected_docs[0])
        mutated["goal_hash"] = "sha256:mutated"
        validate_tau_orchestration_episode(mutated, existing=projected_docs[0])
        mutation_rejected = False
    except Exception:
        mutation_rejected = True
    if not forbidden_rejected:
        errors.append("forbidden_raw_prompt_not_rejected")
    if not mutation_rejected:
        errors.append("lineage_mutation_not_rejected")

    outage_doc = build_tau_orchestration_episode(
        **{
            **_episode_kwargs(projected_docs[0]),
            "projection_key": "mp-outage",
            "source_outbox_row": "mp-outage",
            "attempt_id": "attempt-outage",
            "fact_kind": "outage",
        }
    )
    with store._transaction():
        outage_key = outbox.enqueue_within_transaction(
            projection_lease,
            node_id="turn-one",
            attempt_id="attempt-outage",
            fact_kind="outage",
            payload=outage_doc,
        )
    outage_probe: dict[str, Any] = {
        "container": args.memory_container,
        "health_before_stop": {},
        "memory_unreachable_after_stop": False,
        "memory_healthy_after_start": False,
    }
    outage_results = []
    recovery_results = []
    recovery_replay_results = []
    recovery_readback = {}
    recovery_readback_items = []
    stop_result: dict[str, Any] = {"returncode": 1}
    try:
        outage_probe["health_before_stop"] = _get_json(args.memory_url.rstrip("/") + "/health")
    except Exception as exc:  # noqa: BLE001
        errors.append(f"memory_health_before_outage_failed:{exc}")
    try:
        stop_result = _docker(["stop", args.memory_container], timeout=60.0)
        outage_probe["stop"] = stop_result
        if stop_result["returncode"] != 0:
            errors.append(
                f"memory_stop_failed:{stop_result.get('stderr') or stop_result.get('stdout')}"
            )
        else:
            outage_probe["memory_unreachable_after_stop"] = _wait_memory(
                args.memory_url,
                up=False,
                timeout=30.0,
            )
            outage_results = outbox.relay(sender, max_attempts=1)
    finally:
        if stop_result.get("returncode") == 0:
            start_result = _docker(["start", args.memory_container], timeout=60.0)
            outage_probe["start"] = start_result
            outage_probe["memory_healthy_after_start"] = _wait_memory(
                args.memory_url,
                up=True,
                timeout=120.0,
            )
    run_state_after_outage = store.load_run_record(run_record.run_id).status
    outage_state = outbox.state_of(outage_key)
    if outage_state != "degraded" or run_state_after_outage != "PASS":
        errors.append(f"outage_state:{outage_state}:{run_state_after_outage}")
    if not outage_probe.get("memory_unreachable_after_stop"):
        errors.append("memory_outage_not_observed")
    if not outage_probe.get("memory_healthy_after_start"):
        errors.append("memory_recovery_not_observed")
    if outage_probe.get("memory_healthy_after_start"):
        recovery_results = outbox.relay(sender, max_attempts=3)
        recovery_replay_results = outbox.relay(sender, max_attempts=3)
        try:
            recovery_readback = _memory_readback(
                args.memory_url,
                [outage_doc["_key"]],
                marker,
            )
        except Exception as exc:  # noqa: BLE001
            errors.append(f"memory_recovery_readback_failed:{exc}")
        recovery_by_key = (
            recovery_readback.get("by_keys", {}) if isinstance(recovery_readback, dict) else {}
        )
        recovery_readback_items = (
            recovery_by_key.get("items") or recovery_by_key.get("documents") or []
        )
    outage_recovery_state = outbox.state_of(outage_key)
    if [result.state for result in recovery_results] != ["projected"]:
        errors.append(f"outage_recovery_states:{[result.state for result in recovery_results]}")
    if recovery_replay_results:
        errors.append("outage_recovery_replayed_projected_row")
    if len(recovery_readback_items) != 1:
        errors.append(f"outage_recovery_readback_count:{len(recovery_readback_items)}")

    rejection_doc = build_tau_orchestration_episode(
        **{
            **_episode_kwargs(projected_docs[0]),
            "projection_key": "mp-reject",
            "source_outbox_row": "mp-reject",
            "attempt_id": "attempt-reject",
            "fact_kind": "schema_rejection",
        }
    )
    with store._transaction():
        rejection_key = outbox.enqueue_within_transaction(
            projection_lease,
            node_id="turn-one",
            attempt_id="attempt-reject",
            fact_kind="schema_rejection",
            payload=rejection_doc,
        )
    outbox.relay(lambda payload: {"ok": False, "retryable": False, "error": "schema rejected"})
    rejection_state = outbox.state_of(rejection_key)
    if (
        rejection_state != "permanently_rejected"
        or store.load_run_record(run_record.run_id).status != "PASS"
    ):
        errors.append(f"rejection_state:{rejection_state}")

    delete_target_keys = [outage_doc["_key"]]
    tau_state_before_delete = _tau_state(store, run_record.run_id)
    memory_projection_delete = _delete_memory_projection(args.memory_container, delete_target_keys)
    delete_readback = {}
    delete_readback_items = []
    try:
        delete_readback = _memory_readback(args.memory_url, delete_target_keys, marker)
    except Exception as exc:  # noqa: BLE001
        errors.append(f"memory_delete_readback_failed:{exc}")
    delete_by_key = delete_readback.get("by_keys", {}) if isinstance(delete_readback, dict) else {}
    delete_readback_items = delete_by_key.get("items") or delete_by_key.get("documents") or []
    post_delete_relay_results = outbox.relay(sender, max_attempts=3)
    tau_state_after_delete = _tau_state(store, run_record.run_id)
    delete_settlement_unchanged = tau_state_before_delete == tau_state_after_delete
    memory_projection_delete.update(
        {
            "target_keys": delete_target_keys,
            "tau_state_before_delete": tau_state_before_delete,
            "tau_state_after_delete": tau_state_after_delete,
            "post_delete_memory_readback_count": len(delete_readback_items),
            "post_delete_relay_result_count": len(post_delete_relay_results),
            "settlement_unchanged": delete_settlement_unchanged,
            "method": "proof_only_delete_inside_memory_container_arango_client",
        }
    )
    deleted_documents = int(
        memory_projection_delete.get("parsed", {}).get("deleted_documents") or 0
    )
    if deleted_documents != len(delete_target_keys):
        errors.append(f"memory_projection_delete_count:{deleted_documents}")
    if len(delete_readback_items) != 0:
        errors.append(f"memory_projection_delete_readback_count:{len(delete_readback_items)}")
    if post_delete_relay_results:
        errors.append("post_delete_relay_replayed_projected_row")
    if not delete_settlement_unchanged:
        errors.append("delete_changed_tau_settlement")

    store.close()
    payload = {
        "schema": "tau.memory_episode_projection_proof.v1",
        "status": "PASS" if not errors else "FAIL",
        "mocked": False,
        "live": True,
        "provider_live": False,
        "marker": marker,
        "dag_exit_code": dag_exit_code,
        "dag_status": run_receipt.get("status"),
        "run_store_path": run_receipt.get("run_store_path"),
        "source_journal_event_count": len(events),
        "source_receipt_admission_count": len(admissions),
        "pre_relay_pending_count": len(pre_relay_pending),
        "projected_episode_count": len(projected_docs),
        "live_relay_states": [result.state for result in live_results],
        "replay_result_count": len(replay_results),
        "memory_readback": readback,
        "memory_readback_count": len(readback_items),
        "memory_outage_probe": outage_probe,
        "outage_relay_states": [result.state for result in outage_results],
        "outage_state": outage_state,
        "run_state_after_outage": run_state_after_outage,
        "outage_recovery_relay_states": [result.state for result in recovery_results],
        "outage_recovery_replay_result_count": len(recovery_replay_results),
        "outage_recovery_state": outage_recovery_state,
        "outage_recovery_readback_count": len(recovery_readback_items),
        "outage_recovery_readback": recovery_readback,
        "permanent_rejection_state": rejection_state,
        "memory_projection_delete": memory_projection_delete,
        "forbidden_raw_prompt_rejected": forbidden_rejected,
        "lineage_mutation_rejected": mutation_rejected,
        "projected_doc_keys": [doc["_key"] for doc in projected_docs],
        "errors": errors,
    }
    write_durable_json(out_path, payload)
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0 if not errors else 2


def _episode_kwargs(doc: dict[str, Any]) -> dict[str, Any]:
    return {
        "projection_key": str(doc["projection_idempotency_key"]),
        "source_outbox_row": str(doc["source_outbox_row"]),
        "run_id": str(doc["run_id"]),
        "dag_id": str(doc["dag_id"]),
        "dag_plan_hash": str(doc["dag_plan_hash"]),
        "node_id": str(doc["node_id"]),
        "attempt_id": str(doc["attempt_id"]),
        "attempt_number": int(doc["attempt_number"]),
        "goal_hash": str(doc["goal_hash"]),
        "work_order_hash": str(doc["work_order_hash"]),
        "journal_sequence": int(doc["journal_sequence"]),
        "journal_head_hash": str(doc["journal_head_hash"]),
        "source_event_refs": list(doc["source_event_refs"]),
        "source_receipt_refs": list(doc["source_receipt_refs"]),
        "source_receipt_hashes": list(doc["source_receipt_hashes"]),
        "fact_kind": str(doc["fact_kind"]),
        "summary": str(doc["summary"]),
        "outcome": str(doc["outcome"]),
        "project": str(doc["project"]),
        "live": bool(doc["live"]),
        "mocked": bool(doc["mocked"]),
        "provider_live": bool(doc["provider_live"]),
        "route_key": str(doc.get("route_key") or ""),
    }


if __name__ == "__main__":
    raise SystemExit(main())
