import json
import sys
from dataclasses import replace
from pathlib import Path

from tau_coding import generic_dag
from tau_coding.dag_runtime.run_store import SqliteDagRunReader
from tau_coding.generic_artifact_transaction import (
    canonical_command_sha256,
    parse_transaction_spec,
    revalidate_accepted_manifest,
)
from tau_coding.generic_dag import run_generic_dag


def test_transaction_diagnostics_are_namespaced_by_scheduler_attempt(
    tmp_path: Path, monkeypatch
) -> None:
    worker = _write_worker(tmp_path)
    spec_path = _write_transaction_spec(tmp_path, worker=worker)
    compile_plan = generic_dag.compile_generic_dag_plan

    def compile_with_scheduler_retry(*args, **kwargs):  # type: ignore[no-untyped-def]
        plan = compile_plan(*args, **kwargs)
        return replace(
            plan,
            nodes=tuple(replace(node, max_attempts=2) for node in plan.nodes),
        ).with_computed_hash()

    def run_transaction(*args, **kwargs):  # type: ignore[no-untyped-def]
        runtime_identity = kwargs["runtime_identity"]
        progress_sink = kwargs["progress_sink"]
        scheduler_attempt = runtime_identity["attempt"]
        evidence = {"candidate_manifest_sha256": f"sha256:scheduler-{scheduler_attempt}"}
        progress_sink("stage", 1, "producer_completed", evidence)
        progress_sink("stage", 1, "producer_completed", evidence)
        return {
            "node_id": "stage",
            "status": "BLOCKED" if scheduler_attempt == 1 else "PASS",
            "verdict": "RETRY" if scheduler_attempt == 1 else "PASS",
        }

    monkeypatch.setattr(generic_dag, "_run_transaction_node", run_transaction)
    monkeypatch.setattr(generic_dag, "compile_generic_dag_plan", compile_with_scheduler_retry)

    receipt = run_generic_dag(spec_path=spec_path)

    assert receipt["status"] == "PASS"
    with SqliteDagRunReader(tmp_path / "run" / "dag-run.sqlite3") as reader:
        events = tuple(
            event.to_mapping()
            for event in reader.load_events("run-transaction")
            if event.event_type == "dag_diagnostic_event_appended"
        )
    assert [event["event_key"] for event in events] == [
        "transaction:stage:1:1:producer_completed",
        "transaction:stage:2:1:producer_completed",
    ]
    assert [event["payload"]["scheduler_attempt"] for event in events] == [1, 2]
    assert [event["payload"]["evidence"]["candidate_manifest_sha256"] for event in events] == [
        "sha256:scheduler-1",
        "sha256:scheduler-2",
    ]


def test_transaction_revises_then_projects_only_accepted_artifact(tmp_path: Path) -> None:
    worker = _write_worker(tmp_path)
    spec_path = _write_transaction_spec(tmp_path, worker=worker, include_downstream=True)

    receipt = run_generic_dag(spec_path=spec_path)

    assert receipt["status"] == "PASS"
    transaction = receipt["nodes"][0]
    assert transaction["transaction_state"] == "ACCEPTED"
    assert transaction["attempt_count"] == 2
    assert [item["review_verdict"] for item in transaction["attempts"]] == ["REVISE", "PASS"]
    accepted = json.loads(Path(transaction["accepted_manifest_path"]).read_text())
    assert accepted["accepted_attempt"] == 2
    assert accepted["artifacts"][0]["path"].endswith("candidate-2.bin")
    downstream = json.loads((tmp_path / "downstream-context.json").read_text())
    serialized = json.dumps(downstream)
    assert "candidate-2.bin" in serialized
    assert "candidate-1.bin" not in serialized
    second_context_path = (
        tmp_path / "run" / "transactions" / "stage" / "attempt-002" / "attempt-context.json"
    )
    second_context = json.loads(second_context_path.read_text())
    assert second_context["revision"]["source_attempt"] == 1
    assert second_context["revision"]["findings"][0]["revision_instruction"] == "revise bytes"
    leases = [item["runtime_endpoint_lease"] for item in transaction["command_results"]]
    assert all(item["run_id"] == "run-transaction" for item in leases)
    assert all(item["node_id"] == "stage" for item in leases)
    assert all(item["dag_id"] != "generic-local-command" for item in leases)
    assert len({item["attempt_id"] for item in leases}) == len(leases)


def test_transaction_resume_blocks_modified_accepted_artifact_without_rerun(
    tmp_path: Path,
) -> None:
    worker = _write_worker(tmp_path)
    spec_path = _write_transaction_spec(tmp_path, worker=worker)
    first = run_generic_dag(spec_path=spec_path)
    accepted_path = Path(first["nodes"][0]["artifacts"][0]["path"])
    counter_before = (tmp_path / "producer-count.txt").read_text()
    accepted_path.write_bytes(b"tampered")

    second = run_generic_dag(spec_path=spec_path, resume=True)

    assert second["status"] == "BLOCKED"
    assert second["verdict"] == "STALE_ACCEPTED_STATE"
    assert "artifact_hash_mismatch:primary" in second["nodes"][0]["errors"]
    assert (tmp_path / "producer-count.txt").read_text() == counter_before


def test_transaction_continuation_waits_for_exact_approval_binding(tmp_path: Path) -> None:
    worker = _write_worker(tmp_path)
    continuation_marker = tmp_path / "continued.txt"
    continuation = [sys.executable, str(worker), "continue", str(continuation_marker)]
    approval_path = tmp_path / "approval.json"
    spec_path = _write_transaction_spec(
        tmp_path,
        worker=worker,
        continuation=continuation,
        approval_path=approval_path,
    )

    first = run_generic_dag(spec_path=spec_path)

    assert first["status"] == "BLOCKED"
    assert first["verdict"] == "APPROVAL_REQUIRED"
    assert not continuation_marker.exists()
    node = first["nodes"][0]
    target = {
        "id": "generic-dag-transaction:run-transaction:tx-stage",
        "run_id": "run-transaction",
        "node_id": "stage",
        "transaction_id": "tx-stage",
        "accepted_manifest_sha256": node["accepted_manifest_sha256"],
        "continuation_command_sha256": canonical_command_sha256(continuation),
    }
    approval_path.write_text(
        json.dumps(
            {
                "schema": "tau.human_approval_packet.v1",
                "approved": True,
                "actor": {"id": "human:test", "auth_method": "manual"},
                "action": "generic_dag_transaction_continue",
                "target": target,
                "reason": "approve exact deterministic continuation",
                "evidence": [node["accepted_manifest_path"]],
                "nonce": "test-nonce",
                "signature": "declared-test-signature",
            }
        )
        + "\n"
    )
    counter_before = (tmp_path / "producer-count.txt").read_text()

    second = run_generic_dag(spec_path=spec_path, resume=True)

    assert second["status"] == "PASS"
    assert second["nodes"][0]["transaction_state"] == "CONTINUED"
    assert continuation_marker.read_text() == "continued"
    assert (tmp_path / "producer-count.txt").read_text() == counter_before


def test_transaction_blocks_non_provider_live_producer_when_required(tmp_path: Path) -> None:
    worker = _write_worker(tmp_path)
    spec_path = _write_transaction_spec(
        tmp_path,
        worker=worker,
        acceptance={"require_provider_live_producer": True},
    )

    receipt = run_generic_dag(spec_path=spec_path)

    assert receipt["status"] == "BLOCKED"
    assert receipt["verdict"] == "ACCEPTANCE_POLICY_BLOCKED"
    assert receipt["nodes"][0]["errors"] == ["producer_provider_live_required"]


def test_transaction_blocks_unchanged_output_after_revise(tmp_path: Path) -> None:
    worker = _write_worker(tmp_path)
    spec_path = _write_transaction_spec(
        tmp_path,
        worker=worker,
        acceptance={"require_output_change_after_revise": True},
        same_output_on_retry=True,
    )

    receipt = run_generic_dag(spec_path=spec_path)

    assert receipt["status"] == "BLOCKED"
    assert receipt["verdict"] == "ACCEPTANCE_POLICY_BLOCKED"
    assert receipt["nodes"][0]["errors"] == ["unchanged_output_after_revise"]


def test_transaction_accepts_hash_bound_nested_provider_execution(tmp_path: Path) -> None:
    worker = _write_worker(tmp_path)
    spec_path = _write_transaction_spec(
        tmp_path,
        worker=worker,
        acceptance={"require_provider_live_producer": True},
        producer_provider_live=True,
    )

    receipt = run_generic_dag(spec_path=spec_path)

    assert receipt["status"] == "PASS"
    node = receipt["nodes"][0]
    assert node["producer_provider_live"] is True
    assert node["attempts"][-1]["producer_provider"] == "fixture-provider"


def test_transaction_can_exclude_execution_dependency_from_accepted_context(
    tmp_path: Path,
) -> None:
    worker = _write_worker(tmp_path)
    spec_path = _write_transaction_spec(tmp_path, worker=worker, include_downstream=True)
    spec = json.loads(spec_path.read_text())
    spec["nodes"][1]["accepted_context_from"] = []
    spec_path.write_text(json.dumps(spec, indent=2) + "\n")

    receipt = run_generic_dag(spec_path=spec_path)

    assert receipt["status"] == "PASS"
    downstream = json.loads((tmp_path / "downstream-context.json").read_text())
    assert downstream["accepted_inputs"] == []


def test_transaction_validator_blocks_before_reviewer(tmp_path: Path) -> None:
    worker = _write_worker(tmp_path)
    spec_path = _write_transaction_spec(
        tmp_path,
        worker=worker,
        validator=True,
        validator_pass=False,
    )

    receipt = run_generic_dag(spec_path=spec_path)

    assert receipt["status"] == "BLOCKED"
    assert receipt["verdict"] == "VALIDATOR_BLOCKED"
    assert receipt["nodes"][0]["attempts"] == []


def test_transaction_validator_passes_before_reviewer(tmp_path: Path) -> None:
    worker = _write_worker(tmp_path)
    spec_path = _write_transaction_spec(tmp_path, worker=worker, validator=True)

    receipt = run_generic_dag(spec_path=spec_path)

    assert receipt["status"] == "PASS"
    assert all(item["validation_receipt_path"] for item in receipt["nodes"][0]["attempts"])


def test_mixed_dag_runs_transaction_validator_and_command_on_shared_scheduler(
    tmp_path: Path,
) -> None:
    worker = _write_worker(tmp_path)
    spec_path = _write_transaction_spec(
        tmp_path,
        worker=worker,
        include_downstream=True,
        validator=True,
    )

    receipt = run_generic_dag(spec_path=spec_path, resume=False)

    assert receipt["status"] == "PASS"
    assert receipt["scheduler"] == "dag_plan_ready_queue"
    assert receipt["mocked"] is False
    assert receipt["live"] is True
    assert receipt["provider_live"] is False
    assert receipt["completed_node_count"] == 2
    assert [node["node_id"] for node in receipt["nodes"]] == ["stage", "downstream"]
    transaction, downstream = receipt["nodes"]
    assert transaction["transaction_state"] == "ACCEPTED"
    assert transaction["live"] is True
    assert transaction["provider_live"] is False
    assert all(
        Path(attempt["validation_receipt_path"]).is_file()
        for attempt in transaction["attempts"]
    )
    assert downstream["status"] == "PASS"
    assert downstream["live"] is True
    assert downstream["provider_live"] is False
    assert all(
        command_result["returncode"] == 0
        for node in receipt["nodes"]
        for command_result in node["command_results"]
    )
    downstream_context = json.loads((tmp_path / "downstream-context.json").read_text())
    assert downstream_context["accepted_inputs"][0]["source_node_id"] == "stage"
    assert (
        downstream_context["accepted_inputs"][0]["accepted_manifest_sha256"]
        == transaction["accepted_manifest_sha256"]
    )


def test_accepted_manifest_rejects_changed_upstream_context(tmp_path: Path) -> None:
    worker = _write_worker(tmp_path)
    spec_path = _write_transaction_spec(tmp_path, worker=worker)
    receipt = run_generic_dag(spec_path=spec_path)
    raw = json.loads(spec_path.read_text())["nodes"][0]
    transaction = parse_transaction_spec(
        raw["transaction"], base_dir=spec_path.parent, node_id="stage"
    )

    _, errors = revalidate_accepted_manifest(
        path=Path(receipt["nodes"][0]["accepted_manifest_path"]),
        expected_sha256=receipt["nodes"][0]["accepted_manifest_sha256"],
        spec=transaction,
        node_id="stage",
        work_order_sha256=receipt["nodes"][0]["work_order_sha256"],
        accepted_inputs=[
            {
                "source_node_id": "changed-anchor",
                "accepted_manifest_sha256": "0" * 64,
                "artifacts": [],
            }
        ],
    )

    assert "stale_accepted_context" in errors


def test_fourteen_transaction_selective_context_stress(tmp_path: Path) -> None:
    states = [
        "idle",
        "walk",
        "run",
        "research",
        "payload",
        "mutate",
        "handoff",
        "spawn",
        "hit",
        "blocked",
        "killed",
        "fastest-crash",
        "victory",
        "promoted",
    ]
    worker = _write_worker(tmp_path)
    nodes: list[dict[str, object]] = []
    for index, state in enumerate(states):
        work_order = tmp_path / f"{state}-work-order.json"
        work_order.write_text(json.dumps({"task": state, "validator_pass": True}) + "\n")
        dependencies = [] if index == 0 else [states[index - 1]]
        if index > 1:
            dependencies.append("idle")
        nodes.append(
            {
                "node_id": state,
                "role": "artifact-transaction",
                "command": [
                    sys.executable,
                    str(worker),
                    "produce",
                    str(tmp_path / "artifacts" / state),
                    str(tmp_path / f"{state}-receipt.json"),
                    str(work_order),
                    str(tmp_path / f"{state}-producer-count.txt"),
                ],
                "depends_on": dependencies,
                "accepted_context_from": [] if index == 0 else ["idle"],
                "receipt_path": str(tmp_path / f"{state}-receipt.json"),
                "work_order_path": str(work_order),
                "max_attempts": 2,
                "transaction": {
                    "schema": "tau.generic_artifact_transaction.v1",
                    "transaction_id": f"tx-{state}",
                    "artifact_root": str(tmp_path / "artifacts" / state),
                    "producer_id": f"producer-{state}",
                    "validator": {
                        "validator_id": "deterministic-validator",
                        "command": [sys.executable, str(worker), "validate", str(work_order)],
                    },
                    "reviewer": {
                        "reviewer_id": f"reviewer-{state}",
                        "command": [sys.executable, str(worker), "review"],
                    },
                    "acceptance": {"require_output_change_after_revise": True},
                },
            }
        )
    spec_path = tmp_path / "fourteen-state-dag.json"
    spec_path.write_text(
        json.dumps(
            {
                "schema": "tau.generic_dag_spec.v1",
                "run_id": "fourteen-transaction-stress",
                "run_dir": str(tmp_path / "run"),
                "nodes": nodes,
            },
            indent=2,
        )
        + "\n"
    )

    receipt = run_generic_dag(spec_path=spec_path)
    (tmp_path / "stress-receipt.json").write_text(json.dumps(receipt, indent=2) + "\n")

    assert receipt["status"] == "PASS"
    assert receipt["completed_node_count"] == 14
    assert all(node["attempt_count"] == 2 for node in receipt["nodes"])
    for state in states[1:]:
        context = json.loads(
            (
                tmp_path / "run" / "transactions" / state / "attempt-001" / "attempt-context.json"
            ).read_text()
        )
        assert [item["source_node_id"] for item in context["accepted_inputs"]] == ["idle"]


def _write_transaction_spec(
    root: Path,
    *,
    worker: Path,
    include_downstream: bool = False,
    continuation: list[str] | None = None,
    approval_path: Path | None = None,
    acceptance: dict[str, bool] | None = None,
    same_output_on_retry: bool = False,
    producer_provider_live: bool = False,
    validator: bool = False,
    validator_pass: bool = True,
) -> Path:
    run_dir = root / "run"
    artifacts = root / "artifacts"
    receipt = root / "stage-receipt.json"
    work_order = root / "work-order.json"
    work_order.write_text(
        json.dumps(
            {
                "task": "produce deterministic candidate",
                "same_output_on_retry": same_output_on_retry,
                "producer_provider_live": producer_provider_live,
                "validator_pass": validator_pass,
            }
        )
        + "\n"
    )
    transaction: dict[str, object] = {
        "schema": "tau.generic_artifact_transaction.v1",
        "transaction_id": "tx-stage",
        "artifact_root": str(artifacts),
        "producer_id": "producer",
        "reviewer": {
            "reviewer_id": "reviewer",
            "command": [sys.executable, str(worker), "review"],
        },
    }
    if acceptance is not None:
        transaction["acceptance"] = acceptance
    if validator:
        transaction["validator"] = {
            "validator_id": "deterministic-validator",
            "command": [sys.executable, str(worker), "validate", str(work_order)],
        }
    if continuation is not None:
        transaction["continuation"] = {
            "command": continuation,
            "approval": {
                "action": "generic_dag_transaction_continue",
                "packet_path": str(approval_path),
            },
        }
    nodes: list[dict[str, object]] = [
        {
            "node_id": "stage",
            "role": "producer",
            "command": [
                sys.executable,
                str(worker),
                "produce",
                str(artifacts),
                str(receipt),
                str(work_order),
                str(root / "producer-count.txt"),
            ],
            "depends_on": [],
            "receipt_path": str(receipt),
            "work_order_path": str(work_order),
            "max_attempts": 2,
            "transaction": transaction,
        }
    ]
    if include_downstream:
        downstream_receipt = root / "downstream-receipt.json"
        nodes.append(
            {
                "node_id": "downstream",
                "command": [
                    sys.executable,
                    str(worker),
                    "downstream",
                    str(root / "downstream-context.json"),
                    str(downstream_receipt),
                ],
                "depends_on": ["stage"],
                "receipt_path": str(downstream_receipt),
            }
        )
    spec = {
        "schema": "tau.generic_dag_spec.v1",
        "run_id": "run-transaction",
        "run_dir": str(run_dir),
        "nodes": nodes,
    }
    path = root / "dag.json"
    path.write_text(json.dumps(spec, indent=2) + "\n")
    return path


def _write_worker(root: Path) -> Path:
    path = root / "transaction_worker.py"
    path.write_text(_WORKER_SOURCE)
    return path


_WORKER_SOURCE = r"""
import hashlib
import json
import os
import sys
from pathlib import Path

mode = sys.argv[1]
if mode == "produce":
    artifact_root, receipt_path, work_order_path, counter_path = map(Path, sys.argv[2:])
    context_path = Path(os.environ["TAU_GENERIC_DAG_CONTEXT"])
    context = json.loads(context_path.read_text())
    assert (
        hashlib.sha256(context_path.read_bytes()).hexdigest()
        == os.environ["TAU_GENERIC_DAG_CONTEXT_SHA256"]
    )
    attempt = context["attempt"]
    if attempt == 2:
        assert context["revision"]["source_attempt"] == 1
    artifact_root.mkdir(parents=True, exist_ok=True)
    artifact = artifact_root / f"candidate-{attempt}.bin"
    work_order = json.loads(work_order_path.read_text())
    output_attempt = 1 if work_order.get("same_output_on_retry") else attempt
    artifact.write_bytes(f"candidate-{output_attempt}".encode())
    manifest_path = Path(context["output_contract"]["candidate_manifest_path"])
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(json.dumps({
        "schema": "tau.media_artifact_manifest.v1",
        "transaction_id": context["transaction_id"],
        "node_id": context["node_id"],
        "attempt": attempt,
        "producer_id": context["producer_id"],
        "work_order_sha256": context["work_order"]["sha256"],
        "attempt_context_sha256": os.environ["TAU_GENERIC_DAG_CONTEXT_SHA256"],
        "artifacts": [{
            "artifact_id": "primary",
            "kind": "binary",
            "media_type": "application/octet-stream",
            "path": str(artifact),
            "sha256": hashlib.sha256(artifact.read_bytes()).hexdigest(),
            "bytes": artifact.stat().st_size,
        }],
    }))
    receipt_path.parent.mkdir(parents=True, exist_ok=True)
    provider_execution = ({
        "provider_live": True,
        "provider": "fixture-provider",
        "model": "fixture-model",
        "receipt_path": str(artifact),
        "receipt_sha256": hashlib.sha256(artifact.read_bytes()).hexdigest(),
    } if work_order.get("producer_provider_live") else None)
    receipt_path.write_text(json.dumps({
        "schema": "tau.generic_dag_node_receipt.v1",
        "node_id": context["node_id"],
        "status": "PASS",
        "verdict": "PASS",
        "mocked": False,
        "live": True,
        "provider_live": False,
        "artifacts": [],
        "commands_run": ["deterministic producer"],
        "errors": [],
        "policy_exceptions": [],
        "handoff_summary": "candidate produced",
        "work_order_sha256": hashlib.sha256(work_order_path.read_bytes()).hexdigest(),
        "provider_execution": provider_execution,
    }))
    count = int(counter_path.read_text()) if counter_path.exists() else 0
    counter_path.write_text(str(count + 1))
elif mode == "review":
    context_path = Path(os.environ["TAU_GENERIC_DAG_REVIEW_CONTEXT"])
    context = json.loads(context_path.read_text())
    assert (
        hashlib.sha256(context_path.read_bytes()).hexdigest()
        == os.environ["TAU_GENERIC_DAG_REVIEW_CONTEXT_SHA256"]
    )
    attempt = context["attempt"]
    output = Path(context["output_contract"]["review_feedback_path"])
    verdict = "REVISE" if attempt == 1 else "PASS"
    findings = ([{
        "finding_id": "f1",
        "code": "REVISE_BYTES",
        "severity": "ERROR",
        "message": "first candidate needs revision",
        "artifact_ids": ["primary"],
        "revision_instruction": "revise bytes",
    }] if verdict == "REVISE" else [])
    output.write_text(json.dumps({
        "schema": "tau.generic_artifact_review.v1",
        "transaction_id": context["transaction_id"],
        "node_id": context["node_id"],
        "attempt": attempt,
        "producer_id": context["producer_id"],
        "reviewer_id": context["reviewer_id"],
        "review_context_sha256": os.environ["TAU_GENERIC_DAG_REVIEW_CONTEXT_SHA256"],
        "candidate_manifest_sha256": context["candidate_manifest_sha256"],
        "verdict": verdict,
        "mocked": False,
        "live": True,
        "provider_live": False,
        "summary": "revise first" if verdict == "REVISE" else "accepted",
        "findings": findings,
    }))
elif mode == "validate":
    context_path = Path(os.environ["TAU_GENERIC_DAG_VALIDATION_CONTEXT"])
    context = json.loads(context_path.read_text())
    work_order = json.loads(Path(sys.argv[2]).read_text())
    output = Path(context["output_contract"]["validation_receipt_path"])
    output.write_text(json.dumps({
        "schema": "tau.generic_artifact_validation.v1",
        "status": "PASS" if work_order.get("validator_pass") else "BLOCKED",
        "node_id": context["node_id"],
        "transaction_id": context["transaction_id"],
        "attempt": context["attempt"],
        "validator_id": context["validator_id"],
        "validation_context_sha256": os.environ[
            "TAU_GENERIC_DAG_VALIDATION_CONTEXT_SHA256"
        ],
        "candidate_manifest_sha256": context["candidate_manifest_sha256"],
    }))
    if not work_order.get("validator_pass"):
        raise SystemExit(1)
elif mode == "downstream":
    output, receipt = map(Path, sys.argv[2:])
    context = Path(os.environ["TAU_GENERIC_DAG_CONTEXT"])
    output.write_text(context.read_text())
    payload = json.loads(context.read_text())
    receipt.write_text(json.dumps({
        "schema": "tau.generic_dag_node_receipt.v1",
        "node_id": payload["node_id"],
        "status": "PASS", "verdict": "PASS", "artifacts": [],
        "mocked": False, "live": True, "provider_live": False,
        "commands_run": ["read accepted context"], "errors": [],
        "policy_exceptions": [], "handoff_summary": "accepted input consumed",
    }))
elif mode == "continue":
    Path(sys.argv[2]).write_text("continued")
"""
