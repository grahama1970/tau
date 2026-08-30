#!/usr/bin/env python3
"""Live proof for Tau orchestration episode projection into Memory."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
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
from tau_coding.dag_runtime.memory_projection import (  # noqa: E402
    MemoryProjectionOutbox,
    build_tau_orchestration_episode,
    governed_memory_store_sender,
    projection_key_for,
    validate_tau_orchestration_episode,
)
from tau_coding.dag_runtime.run_store import SqliteDagRunStore  # noqa: E402


def _sha(text: str) -> str:
    return "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()


def _node_receipt_code(receipt: Path, node_id: str, summary: str, marker: str) -> str:
    payload = {
        "schema": "tau.generic_dag_node_receipt.v1",
        "node_id": node_id,
        "status": "PASS",
        "verdict": "PASS",
        "mocked": False,
        "live": True,
        "provider_live": False,
        "goal_hash": "sha256:issue321-memory-episode",
        "accepted_output": {"summary": summary, "unique_marker": marker},
        "artifacts": [],
        "commands_run": [],
        "policy_exceptions": [],
        "handoff_summary": summary,
        "errors": [],
    }
    return (
        "import json\n"
        "from pathlib import Path\n"
        f"path=Path({json.dumps(str(receipt))})\n"
        "path.parent.mkdir(parents=True, exist_ok=True)\n"
        f"payload=json.loads({json.dumps(json.dumps(payload, sort_keys=True))})\n"
        "path.write_text(json.dumps(payload, indent=2, sort_keys=True)+'\\n', encoding='utf-8')\n"
        "print(json.dumps(payload, sort_keys=True))\n"
    )


def _post_json(url: str, payload: dict[str, Any], timeout: float = 10.0) -> dict[str, Any]:
    req = request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with request.urlopen(req, timeout=timeout) as response:  # nosec B310
        return json.loads(response.read().decode("utf-8"))


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
    args = parser.parse_args()
    out_path = Path(args.out).expanduser().resolve()
    work = Path(args.work).expanduser().resolve()
    work.mkdir(parents=True, exist_ok=True)
    marker = f"issue321_tau_orchestration_episode_{int(time.time() * 1000)}"

    errors: list[str] = []
    dag_path = work / "issue321.dag.json"
    run_dir = work / "run"
    spec = {
        "schema": "tau.generic_dag_spec.v1",
        "run_id": "issue321-memory-episode",
        "run_dir": str(run_dir),
        "goal_hash": "sha256:issue321-memory-episode",
        "max_concurrency": 2,
        "nodes": [
            {
                "node_id": "turn-one",
                "role": "turn",
                "receipt_path": str(work / "turn-one-receipt.json"),
                "timeout_seconds": 30,
                "command": [
                    sys.executable,
                    "-c",
                    _node_receipt_code(
                        work / "turn-one-receipt.json",
                        "turn-one",
                        f"{marker} turn one accepted",
                        marker,
                    ),
                ],
            },
            {
                "node_id": "tool-and-join",
                "role": "tool",
                "depends_on": ["turn-one"],
                "accepted_context_from": ["turn-one"],
                "receipt_path": str(work / "tool-and-join-receipt.json"),
                "timeout_seconds": 30,
                "command": [
                    sys.executable,
                    "-c",
                    _node_receipt_code(
                        work / "tool-and-join-receipt.json",
                        "tool-and-join",
                        f"{marker} tool effect and join accepted",
                        marker,
                    ),
                ],
            },
        ],
    }
    dag_path.write_text(json.dumps(spec, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    env = dict(os.environ)
    env["PYTHONPATH"] = str(SRC) if not env.get("PYTHONPATH") else f"{SRC}{os.pathsep}{env['PYTHONPATH']}"
    completed = subprocess.run(
        ["uv", "run", "tau", "dag-run", str(dag_path), "--no-resume"],
        cwd=REPO_ROOT,
        check=False,
        capture_output=True,
        text=True,
        timeout=120,
        env=env,
    )
    (work / "dag-run.stdout").write_text(completed.stdout, encoding="utf-8")
    (work / "dag-run.stderr").write_text(completed.stderr, encoding="utf-8")
    if completed.returncode != 0:
        errors.append(f"dag_run_failed:{completed.returncode}")
    run_receipt = json.loads(completed.stdout) if completed.stdout.strip().startswith("{") else {}
    if run_receipt.get("status") != "PASS":
        errors.append(f"dag_status:{run_receipt.get('status')}")

    store = SqliteDagRunStore(Path(run_receipt.get("run_store_path") or run_dir / "dag-run.sqlite3"))
    outbox = MemoryProjectionOutbox(store)
    run_record = store.load_run_record("issue321-memory-episode")
    events = store.load_events("issue321-memory-episode")
    head_event = events[-1]
    admissions = store.list_admissions("issue321-memory-episode")
    attempts = [attempt for attempt in store.list_attempts("issue321-memory-episode") if attempt.state == "SETTLED"]
    if len(attempts) != 2:
        errors.append(f"settled_attempt_count:{len(attempts)}")
    admission_by_attempt = {item["attempt_id"]: item for item in admissions}

    projection_lease = store.acquire_run(
        plan=store._load_plan_for_validation(run_record.run_id),
        run_id=run_record.run_id,
        owner_id="issue321-projector",
        ttl_seconds=120,
    )
    projected_docs = []
    for attempt in attempts:
        node_id = attempt.identity.node_id
        fact_kind = "tool_effect" if node_id == "tool-and-join" else "agent_turn"
        key = projection_key_for(run_record.run_id, node_id, attempt.identity.attempt_id, fact_kind)
        admission = admission_by_attempt.get(attempt.identity.attempt_id, {})
        receipt_path = str(admission.get("path") or "")
        receipt_hash = str(admission.get("sha256") or _sha(receipt_path or node_id))
        node_events = [event for event in events if event.get("attempt_id") == attempt.identity.attempt_id]
        event_refs = [f"dag_run_events/{event['seq']}" for event in node_events[-3:]] or [
            f"dag_run_events/{head_event['seq']}"
        ]
        doc = build_tau_orchestration_episode(
            projection_key=key,
            source_outbox_row=key,
            run_id=run_record.run_id,
            dag_id=run_record.plan_id,
            dag_plan_hash=run_record.plan_sha256,
            node_id=node_id,
            attempt_id=attempt.identity.attempt_id,
            attempt_number=attempt.identity.attempt,
            goal_hash="sha256:issue321-memory-episode",
            work_order_hash=attempt.identity.idempotency_key,
            journal_sequence=int(head_event["seq"]),
            journal_head_hash=_sha(json.dumps(head_event, sort_keys=True, default=str)),
            source_event_refs=event_refs,
            source_receipt_refs=[receipt_path or f"receipt_admissions/{attempt.identity.attempt_id}"],
            source_receipt_hashes=[receipt_hash],
            fact_kind=fact_kind,
            summary=f"{marker} {node_id} accepted {fact_kind}",
            outcome="PASS",
            project="tau",
            live=True,
            mocked=False,
            provider_live=False,
            route_key="bounded-ready-queue",
            joined_from=["turn-one"] if node_id == "tool-and-join" else None,
            child_lineage=[{"child_run_id": "issue316-proof-child", "relationship": "available"}],
        )
        with store._transaction():
            outbox.enqueue_within_transaction(
                projection_lease,
                node_id=node_id,
                attempt_id=attempt.identity.attempt_id,
                fact_kind=fact_kind,
                payload=doc,
            )
        projected_docs.append(doc)

    pre_relay_pending = outbox.pending()
    live_results = outbox.relay(governed_memory_store_sender(memory_url=args.memory_url), max_attempts=3)
    replay_results = outbox.relay(governed_memory_store_sender(memory_url=args.memory_url), max_attempts=3)
    if [result.state for result in live_results].count("projected") != len(projected_docs):
        errors.append("live_projection_not_all_projected")
    if replay_results:
        errors.append("relay_replayed_projected_rows")

    readback = {}
    try:
        readback = _memory_readback(args.memory_url, [doc["_key"] for doc in projected_docs], marker)
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
        **{**_episode_kwargs(projected_docs[0]), "projection_key": "mp-outage", "source_outbox_row": "mp-outage", "attempt_id": "attempt-outage", "fact_kind": "outage"}
    )
    with store._transaction():
        outage_key = outbox.enqueue_within_transaction(
            projection_lease,
            node_id="turn-one",
            attempt_id="attempt-outage",
            fact_kind="outage",
            payload=outage_doc,
        )
    outbox.relay(lambda payload: {"ok": False, "retryable": True, "error": "memory stopped"}, max_attempts=1)
    run_state_after_outage = store.load_run_record(run_record.run_id).status
    outage_state = outbox.state_of(outage_key)
    if outage_state != "degraded" or run_state_after_outage != "PASS":
        errors.append(f"outage_state:{outage_state}:{run_state_after_outage}")

    rejection_doc = build_tau_orchestration_episode(
        **{**_episode_kwargs(projected_docs[0]), "projection_key": "mp-reject", "source_outbox_row": "mp-reject", "attempt_id": "attempt-reject", "fact_kind": "schema_rejection"}
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
    if rejection_state != "permanently_rejected" or store.load_run_record(run_record.run_id).status != "PASS":
        errors.append(f"rejection_state:{rejection_state}")

    store.close()
    payload = {
        "schema": "tau.memory_episode_projection_proof.v1",
        "status": "PASS" if not errors else "FAIL",
        "mocked": False,
        "live": True,
        "provider_live": False,
        "marker": marker,
        "dag_exit_code": completed.returncode,
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
        "outage_state": outage_state,
        "run_state_after_outage": run_state_after_outage,
        "permanent_rejection_state": rejection_state,
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
