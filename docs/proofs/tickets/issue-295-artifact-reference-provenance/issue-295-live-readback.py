#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import json
import shutil
import sqlite3
import sys
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT / "src"))

from tau_coding.dag_runtime.artifact_reference import (  # noqa: E402
    ArtifactDereferenceResult,
    ArtifactReferenceError,
    dereference_artifact_reference,
)
from tau_coding.dag_runtime.compiler import compile_generic_dag_plan  # noqa: E402
from tau_coding.dag_runtime.model import DagPlan, DagPlanNode, canonical_sha256  # noqa: E402
from tau_coding.dag_runtime.run_store import (  # noqa: E402
    SqliteDagRunReader,
    SqliteDagRunStore,
)
from tau_coding.dag_runtime.scheduler import DagNodeAttempt, run_dag_plan  # noqa: E402

PROOF_DIR = Path(__file__).resolve().parent
RUN_ROOT = PROOF_DIR / "live-readback-run"
OUT = PROOF_DIR / "live-readback.json"


def main() -> int:
    if RUN_ROOT.exists():
        shutil.rmtree(RUN_ROOT)
    RUN_ROOT.mkdir(parents=True)
    proof: dict[str, Any] = {
        "schema": "tau.issue_295.live_readback.v1",
        "mocked": False,
        "live": True,
        "checks": {},
    }

    plan, database, reference, dereference_receipt, manifest = _valid_reference_run(
        RUN_ROOT / "valid"
    )
    binding = plan.context_bindings[0]
    proof["checks"]["valid_dereference"] = {
        "reference_sha256": reference["reference_sha256"],
        "admitted_artifact_id": reference["admitted_artifact_id"],
        "dereference_receipt_schema": dereference_receipt["schema"],
        "dereference_receipt_sha256": dereference_receipt["receipt_sha256"],
        "manifest_records_dereference_receipt": manifest["bindings"][0][
            "dereference_receipt"
        ]["reference_sha256"]
        == reference["reference_sha256"],
    }

    mutation_results: dict[str, str] = {}
    for name, mutate, expected in _mutation_cases(reference):
        mutated = mutate(reference)
        with SqliteDagRunReader(database) as reader:
            try:
                dereference_artifact_reference(
                    mutated,
                    run_store=reader,
                    expected_run_id=str(reference["producer"]["run_id"]),
                    expected_producer_node_id="producer",
                    expected_producer_attempt_id=str(reference["producer"]["attempt_id"]),
                    expected_consumer_node_id="consumer",
                    binding=binding,
                    run_store_root=database.parent,
                )
            except ArtifactReferenceError as exc:
                observed = exc.code
            else:
                raise AssertionError(f"{name} unexpectedly dereferenced")
        if observed != expected:
            raise AssertionError(f"{name}: expected {expected}, observed {observed}")
        mutation_results[name] = observed
    proof["checks"]["mutation_matrix"] = mutation_results

    wrong_context: dict[str, str] = {}
    for name, kwargs, expected in (
        ("wrong_run", {"expected_run_id": "wrong-run"}, "ARTIFACT_REFERENCE_ADMISSION_MISSING"),
        (
            "wrong_attempt",
            {"expected_producer_attempt_id": "wrong-attempt"},
            "ARTIFACT_REFERENCE_PRODUCER_MISMATCH",
        ),
        (
            "wrong_consumer",
            {"expected_consumer_node_id": "wrong-consumer"},
            "ARTIFACT_REFERENCE_CONSUMER_MISMATCH",
        ),
    ):
        params = {
            "expected_run_id": str(reference["producer"]["run_id"]),
            "expected_producer_node_id": "producer",
            "expected_producer_attempt_id": str(reference["producer"]["attempt_id"]),
            "expected_consumer_node_id": "consumer",
            **kwargs,
        }
        with SqliteDagRunReader(database) as reader:
            try:
                dereference_artifact_reference(
                    reference,
                    run_store=reader,
                    expected_run_id=params["expected_run_id"],
                    expected_producer_node_id=params["expected_producer_node_id"],
                    expected_producer_attempt_id=params["expected_producer_attempt_id"],
                    expected_consumer_node_id=params["expected_consumer_node_id"],
                    binding=binding,
                    run_store_root=database.parent,
                )
            except ArtifactReferenceError as exc:
                observed = exc.code
            else:
                raise AssertionError(f"{name} unexpectedly dereferenced")
        if observed != expected:
            raise AssertionError(f"{name}: expected {expected}, observed {observed}")
        wrong_context[name] = observed
    proof["checks"]["wrong_context_reuse"] = wrong_context

    schema_block = _embedded_schema_blocks_before_consumer(RUN_ROOT / "schema-block")
    proof["checks"]["embedded_schema_block"] = schema_block

    with sqlite3.connect(database) as connection:
        try:
            connection.execute("DELETE FROM receipt_admissions")
        except sqlite3.DatabaseError as exc:
            delete_error = str(exc)
        else:
            raise AssertionError("receipt admission delete unexpectedly succeeded")
    proof["checks"]["admission_delete_denied"] = delete_error
    proof["checks"]["admission_ambiguity"] = "eliminated_by_exact_admitted_artifact_id_lookup"

    _assert_proof(proof)
    OUT.write_text(json.dumps(proof, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(proof, sort_keys=True))
    return 0


def _valid_reference_run(
    run_dir: Path,
) -> tuple[DagPlan, Path, dict[str, Any], dict[str, Any], dict[str, Any]]:
    plan = _plan(run_dir)
    database = run_dir / "dag-run.sqlite3"
    captured_reference: list[dict[str, Any]] = []
    captured_receipt: list[dict[str, Any]] = []
    captured_manifest: list[dict[str, Any]] = []

    def execute(
        node: DagPlanNode,
        accepted_inputs: tuple[dict[str, Any], ...],
        attempt: DagNodeAttempt,
    ) -> dict[str, Any]:
        if node.node_id == "consumer":
            with SqliteDagRunReader(database) as reader:
                result = dereference_artifact_reference(
                    accepted_inputs[0],
                    run_store=reader,
                    expected_run_id=attempt.run_id,
                    expected_producer_node_id="producer",
                    expected_producer_attempt_id=str(accepted_inputs[0]["producer"]["attempt_id"]),
                    expected_consumer_node_id=node.node_id,
                    binding=plan.context_bindings[0],
                    run_store_root=database.parent,
                    selector={"kind": "json_key", "key": "section"},
                    return_receipt=True,
                )
            if not isinstance(result, ArtifactDereferenceResult):
                raise AssertionError("dereference result did not include receipt")
            captured_reference.append(dict(accepted_inputs[0]))
            captured_receipt.append(result.receipt)
            if attempt.input_manifest_path is None:
                raise AssertionError("missing input manifest path")
            captured_manifest.append(
                json.loads(Path(attempt.input_manifest_path).read_text(encoding="utf-8"))
            )
            return {"node_id": node.node_id, "status": "PASS", "verdict": "PASS"}
        receipt_path = run_dir / "receipts" / "producer.json"
        digest, size = _write_json_receipt(
            receipt_path,
            {"schema": "large.report.v1", "section": {"kept": True}, "hidden": "sealed"},
        )
        return _producer_result(node.node_id, receipt_path, digest, size, "large.report.v1")

    with SqliteDagRunStore(database) as store:
        result = run_dag_plan(
            plan,
            execute_node=execute,
            run_store=store,
            lease_owner="issue-295-live",
        )
        integrity = store.integrity_check()
    if result.status != "PASS" or not integrity["ok"]:
        raise AssertionError(f"valid run failed: {result.status}:{result.verdict}:{integrity}")
    return plan, database, captured_reference[0], captured_receipt[0], captured_manifest[0]


def _embedded_schema_blocks_before_consumer(run_dir: Path) -> dict[str, Any]:
    plan = _plan(run_dir)
    calls: list[str] = []

    def execute(
        node: DagPlanNode,
        accepted_inputs: tuple[dict[str, Any], ...],
        attempt: DagNodeAttempt,
    ) -> dict[str, Any]:
        del accepted_inputs, attempt
        calls.append(node.node_id)
        receipt_path = run_dir / "receipts" / "producer.json"
        digest, size = _write_json_receipt(receipt_path, {"schema": "other.report.v1"})
        return _producer_result(node.node_id, receipt_path, digest, size, "large.report.v1")

    with SqliteDagRunStore(run_dir / "dag-run.sqlite3") as store:
        result = run_dag_plan(
            plan,
            execute_node=execute,
            run_store=store,
            lease_owner="issue-295-schema-block",
        )
    return {"status": result.status, "verdict": result.verdict, "calls": calls}


def _mutation_cases(
    reference: dict[str, Any],
) -> tuple[tuple[str, Callable[[dict[str, Any]], dict[str, Any]], str], ...]:
    return (
        (
            "reference_sha256",
            lambda item: {**item, "reference_sha256": "sha256:" + "0" * 64},
            "ARTIFACT_REFERENCE_ENVELOPE_HASH_MISMATCH",
        ),
        (
            "admitted_artifact_id",
            lambda item: _rehash_reference(item, admitted_artifact_id="missing"),
            "ARTIFACT_REFERENCE_ADMISSION_MISSING",
        ),
        (
            "size_bytes",
            lambda item: _rehash_reference(item, size_bytes=item["size_bytes"] + 1),
            "ARTIFACT_REFERENCE_SIZE_MISMATCH",
        ),
        (
            "uri",
            lambda item: _rehash_reference(
                item,
                uri=Path(str(item["path"])).with_name("other.json").as_uri(),
            ),
            "ARTIFACT_REFERENCE_URI_MISMATCH",
        ),
        (
            "producer",
            lambda item: _rehash_reference(
                item,
                producer={**dict(item["producer"]), "node_id": "other-producer"},
            ),
            "ARTIFACT_REFERENCE_PRODUCER_MISMATCH",
        ),
        (
            "consumer",
            lambda item: _rehash_reference(item, consumer={"node_id": "other-consumer"}),
            "ARTIFACT_REFERENCE_CONSUMER_MISMATCH",
        ),
        (
            "receipt_kind",
            lambda item: _rehash_reference(item, receipt_kind="other_receipt"),
            "ARTIFACT_REFERENCE_RECEIPT_KIND_MISMATCH",
        ),
        (
            "policy_sha256",
            lambda item: _rehash_reference(item, policy_sha256="sha256:" + "1" * 64),
            "ARTIFACT_REFERENCE_POLICY_MISMATCH",
        ),
        (
            "data_boundary_sha256",
            lambda item: _rehash_reference(item, data_boundary_sha256="sha256:" + "2" * 64),
            "ARTIFACT_REFERENCE_DATA_BOUNDARY_MISMATCH",
        ),
        (
            "artifact_schema",
            lambda item: _rehash_reference(item, artifact_schema="other.report.v1"),
            "ARTIFACT_REFERENCE_EMBEDDED_SCHEMA_MISMATCH",
        ),
        (
            "selector",
            lambda item: _rehash_reference(
                item,
                selector={**dict(item["selector"]), "projection": "undeclared"},
            ),
            "ARTIFACT_REFERENCE_SELECTOR_POLICY_MISMATCH",
        ),
    )


def _assert_proof(proof: dict[str, Any]) -> None:
    assert proof["mocked"] is False
    assert proof["live"] is True
    assert proof["checks"]["valid_dereference"]["dereference_receipt_schema"] == (
        "tau.artifact_dereference_receipt.v1"
    )
    assert proof["checks"]["valid_dereference"]["manifest_records_dereference_receipt"] is True
    assert len(proof["checks"]["mutation_matrix"]) == 11
    assert proof["checks"]["embedded_schema_block"] == {
        "status": "BLOCKED",
        "verdict": "NODE_INPUT_REFERENCE_EMBEDDED_SCHEMA_MISMATCH",
        "calls": ["producer"],
    }
    assert "receipt_admissions is append-only" in proof["checks"]["admission_delete_denied"]


def _producer_result(
    node_id: str,
    receipt_path: Path,
    digest: str,
    size: int,
    schema: str,
) -> dict[str, Any]:
    return {
        "node_id": node_id,
        "status": "PASS",
        "verdict": "PASS",
        "receipt_path": str(receipt_path),
        "accepted_output": {
            "receipts": [
                {
                    "schema": schema,
                    "path": str(receipt_path),
                    "sha256": digest,
                    "size_bytes": size,
                    "receipt_kind": "node_receipt",
                }
            ]
        },
    }


def _plan(run_dir: Path) -> DagPlan:
    run_dir.mkdir(parents=True, exist_ok=True)
    plan = compile_generic_dag_plan(
        {
            "schema": "tau.generic_dag_spec.v1",
            "run_id": "issue-295-live-readback",
            "run_dir": str(run_dir),
            "nodes": [
                _node(run_dir, "producer"),
                _node(run_dir, "consumer", depends_on=["producer"]),
            ],
        },
        source_path=run_dir / "dag.json",
    )
    binding = plan.context_bindings[0]
    return replace(
        plan,
        context_bindings=(
            replace(
                binding,
                selector_kind="receipt_by_schema",
                accepted_source_schemas=("large.report.v1",),
                materialization_mode="by_reference",
                on_invalid="block",
                max_reference_bytes=4096,
            ),
        ),
    ).with_computed_hash()


def _node(
    run_dir: Path,
    node_id: str,
    *,
    depends_on: list[str] | None = None,
) -> dict[str, object]:
    return {
        "node_id": node_id,
        "role": node_id,
        "command": ["true"],
        "depends_on": depends_on or [],
        "accepted_context_from": depends_on or [],
        "receipt_path": str(run_dir / "receipts" / f"{node_id}.json"),
        "timeout_seconds": 1,
        "max_attempts": 1,
    }


def _write_json_receipt(path: Path, payload: dict[str, Any]) -> tuple[str, int]:
    path.parent.mkdir(parents=True, exist_ok=True)
    encoded = (json.dumps(payload, sort_keys=True) + "\n").encode("utf-8")
    path.write_bytes(encoded)
    return f"sha256:{hashlib.sha256(encoded).hexdigest()}", len(encoded)


def _rehash_reference(reference: dict[str, Any], **updates: Any) -> dict[str, Any]:
    mutated = {**reference, **updates}
    without_hash = {key: value for key, value in mutated.items() if key != "reference_sha256"}
    return {**without_hash, "reference_sha256": canonical_sha256(without_hash)}


if __name__ == "__main__":
    raise SystemExit(main())
