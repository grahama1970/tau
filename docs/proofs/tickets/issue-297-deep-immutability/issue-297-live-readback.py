from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

from tau_coding.dag_runtime.compiler import compile_project_dag_plan
from tau_coding.dag_runtime.model import canonical_sha256
from tau_coding.dag_runtime.transition import DagNodeCompletion
from tau_coding.generic_dag import (
    GENERIC_DAG_NODE_RECEIPT_SCHEMA,
    GENERIC_DAG_SPEC_SCHEMA,
    run_generic_dag,
    validate_generic_dag_spec,
)
from tau_coding.project_dag import DAG_CONTRACT_SCHEMA, validate_dag_contract

ROOT = Path(__file__).resolve().parents[4]
PROOF_DIR = Path(__file__).resolve().parent
WORK_DIR = PROOF_DIR / "live-readback-work"
READBACK = PROOF_DIR / "live-readback.json"


def main() -> int:
    if WORK_DIR.exists():
        shutil.rmtree(WORK_DIR)
    WORK_DIR.mkdir(parents=True)

    project_path = WORK_DIR / "project.dag.json"
    project_payload = _project_payload(WORK_DIR)
    project_path.write_text(json.dumps(project_payload, sort_keys=True), encoding="utf-8")
    contract = validate_dag_contract(project_payload)
    project_payload["goal"]["goal_id"] = "mutated-after-validation"
    project_payload["nodes"][0]["context"]["labels"].append("mutated")

    project_contract_immutable = (
        contract.goal["goal_id"] == "goal-immutable"
        and contract.nodes["coder"].context["labels"] == ["initial"]
    )
    project_mutation_blocked = _mutation_raises(
        lambda: contract.nodes["coder"].context["labels"].append("blocked")
    )

    plan = compile_project_dag_plan(contract.payload.copy(), source_path=project_path)
    plan_payload = plan.to_payload()
    plan_payload["nodes"][0]["static_context"]["node"]["labels"].append("mutated")
    plan_payload_isolated = plan.to_payload()["nodes"][0]["static_context"]["node"]["labels"] == [
        "initial"
    ]
    plan_hash_round_trip = (
        canonical_sha256(
            {key: value for key, value in plan.to_payload().items() if key != "plan_sha256"}
        )
        == plan.plan_sha256
    )

    generic_path = WORK_DIR / "generic.dag.json"
    generic_receipt = WORK_DIR / "receipts" / "one.json"
    generic_spec = {
        "schema": GENERIC_DAG_SPEC_SCHEMA,
        "run_id": "issue-297-live-readback",
        "run_dir": str(WORK_DIR / "generic-run"),
        "nodes": [
            {
                "node_id": "one",
                "role": "producer",
                "command": [
                    sys.executable,
                    "-c",
                    _receipt_writer_code(generic_receipt),
                ],
                "receipt_path": str(generic_receipt),
                "timeout_seconds": 20,
                "max_attempts": 1,
            }
        ],
    }
    generic_path.write_text(json.dumps(generic_spec, sort_keys=True), encoding="utf-8")
    nodes = validate_generic_dag_spec(generic_spec, source_path=generic_path)
    generic_spec["nodes"][0]["command"].append("mutated")
    generic_command_immutable = nodes["one"].command == (
        sys.executable,
        "-c",
        _receipt_writer_code(generic_receipt),
    )
    generic_run = run_generic_dag(spec_path=generic_path, resume=False)

    raw_result = {"accepted_output": {"items": ["kept"]}}
    completion = DagNodeCompletion(
        node_id="one",
        attempt=1,
        status="PASS",
        verdict="PASS",
        retryable=False,
        raw_result=raw_result,
    )
    raw_result["accepted_output"]["items"].append("mutated")
    completion_immutable = completion.raw_result["accepted_output"]["items"] == ["kept"]
    completion_mutation_blocked = _mutation_raises(
        lambda: completion.raw_result["accepted_output"]["items"].append("blocked")
    )

    checks = {
        "project_contract_immutable_after_source_mutation": project_contract_immutable,
        "project_contract_nested_mutation_blocked": project_mutation_blocked,
        "project_plan_to_payload_isolated": plan_payload_isolated,
        "project_plan_hash_round_trip": plan_hash_round_trip,
        "generic_command_tuple_immutable_after_source_mutation": generic_command_immutable,
        "generic_live_local_dag_passed": generic_run.get("status") == "PASS",
        "transition_completion_raw_result_immutable": completion_immutable,
        "transition_completion_nested_mutation_blocked": completion_mutation_blocked,
    }
    ok = all(checks.values())
    readback = {
        "schema": "tau.issue_297_deep_immutability_readback.v1",
        "ok": ok,
        "status": "PASS" if ok else "FAIL",
        "mocked": False,
        "live": True,
        "provider_live": False,
        "checks": checks,
        "artifacts": {
            "project_spec": str(project_path),
            "generic_spec": str(generic_path),
            "generic_run_receipt": str(WORK_DIR / "generic-run" / "dag-receipt.json"),
            "generic_node_receipt": str(generic_receipt),
        },
        "does_not_prove": ["paid provider behavior", "every possible future DAG field"],
    }
    READBACK.write_text(json.dumps(readback, indent=2, sort_keys=True), encoding="utf-8")
    print(f"{readback['status']} wrote {READBACK}")
    if not ok:
        print(json.dumps(checks, indent=2, sort_keys=True))
        return 1
    return 0


def _mutation_raises(action: object) -> bool:
    try:
        action()  # type: ignore[operator]
    except TypeError:
        return True
    return False


def _receipt_writer_code(receipt_path: Path) -> str:
    payload = {
        "schema": GENERIC_DAG_NODE_RECEIPT_SCHEMA,
        "node_id": "one",
        "status": "PASS",
        "verdict": "PASS",
        "mocked": False,
        "live": True,
        "provider_live": False,
        "artifacts": [],
        "commands_run": ["python receipt writer"],
        "handoff_summary": "one finished",
        "errors": [],
        "policy_exceptions": [],
    }
    return (
        "import json; "
        "from pathlib import Path; "
        f"path = Path({str(receipt_path)!r}); "
        "path.parent.mkdir(parents=True, exist_ok=True); "
        f"path.write_text(json.dumps({payload!r}, sort_keys=True), encoding='utf-8')"
    )


def _project_payload(work_dir: Path) -> dict[str, object]:
    command_spec_dir = work_dir / "specs" / "coder"
    command_spec_dir.mkdir(parents=True)
    (command_spec_dir / "tau-dispatch-command.json").write_text(
        json.dumps({"schema": "tau.agent_handoff_command.v1", "command": ["true"]}),
        encoding="utf-8",
    )
    return {
        "schema": DAG_CONTRACT_SCHEMA,
        "dag_id": "issue-297-project",
        "goal": {
            "goal_id": "goal-immutable",
            "goal_version": 1,
            "goal_hash": "sha256:goal",
            "summary": "keep validated contracts immutable",
            "completion_criteria": ["source mutation cannot alter contract"],
        },
        "target": {
            "repo": "grahama1970/tau",
            "target": "issue-297",
            "allowed_paths": ["src/tau_coding"],
        },
        "entry_node": "coder",
        "terminal_nodes": ["done"],
        "limits": {"max_total_attempts": 1, "default_timeout_seconds": 60},
        "context": {"run": {"labels": ["contract"]}},
        "nodes": [
            {
                "id": "coder",
                "agent": "coder",
                "executor": "local",
                "max_attempts": 1,
                "command_spec": str(command_spec_dir),
                "required_evidence": ["creator_artifact"],
                "context": {"labels": ["initial"]},
            }
        ],
        "edges": [{"from": "coder", "to": "done"}],
        "required_evidence": [],
        "fail_closed_on": [],
    }


if __name__ == "__main__":
    raise SystemExit(main())
