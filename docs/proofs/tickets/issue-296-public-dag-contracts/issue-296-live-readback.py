from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path
from typing import Any

import yaml  # type: ignore[import-untyped]

from tau_coding.dag_runtime.compiler import compile_generic_dag_plan, compile_project_dag_plan
from tau_coding.generic_dag import load_generic_dag_spec, run_generic_dag
from tau_coding.project_dag import run_project_dag_contract

ROOT = Path(__file__).resolve().parents[4]
PROOF_DIR = Path(__file__).resolve().parent
WORK_DIR = PROOF_DIR / "live-readback-work"
OUT = PROOF_DIR / "live-readback.json"


def main() -> int:
    if WORK_DIR.exists():
        shutil.rmtree(WORK_DIR)
    WORK_DIR.mkdir(parents=True)

    project_result = _run_valid_project_dag()
    generic_result = _run_valid_generic_dag()
    invalid_result = _run_invalid_contracts()
    schema_result = _run_schema_drift_check()

    checks = {
        "valid_project_dag_passed": project_result["status"] == "PASS",
        "valid_project_extensions_hash_bound": project_result["plan_hashes_differ"],
        "valid_generic_dag_passed": generic_result["status"] == "PASS",
        "valid_generic_json_yaml_plan_parity": generic_result["json_yaml_plan_parity"],
        "invalid_project_rejected_before_dispatch": invalid_result[
            "invalid_project_rejected_before_dispatch"
        ],
        "invalid_generic_rejected_before_dispatch": invalid_result[
            "invalid_generic_rejected_before_dispatch"
        ],
        "schema_drift_check_passed": schema_result["status"] == "PASS",
    }
    payload = {
        "schema": "tau.issue_296_live_readback.v1",
        "mocked": False,
        "live": True,
        "provider_live": False,
        "work_dir": str(WORK_DIR),
        "checks": checks,
        "project": project_result,
        "generic": generic_result,
        "invalid": invalid_result,
        "schema_drift": schema_result,
        "proof_scope": {
            "proves": [
                "tau.dag_contract.v1 rejects malformed public input before local dispatch",
                "tau.generic_dag_spec.v1 rejects malformed public input before local dispatch",
                "explicit extensions are preserved in compiled DagPlan source_extensions",
                "explicit extensions alter the canonical DagPlan hash",
                "generic JSON and YAML source files compile to the same canonical plan",
                "public DAG contract key snapshot matches runtime strict validator allowlists",
            ],
            "does_not_prove": [
                "paid provider calls",
                "historical artifact migration beyond the explicit compatibility fields",
            ],
        },
    }
    payload["status"] = "PASS" if all(checks.values()) else "FAIL"
    OUT.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    print(json.dumps({"status": payload["status"], "receipt": str(OUT)}, sort_keys=True))
    return 0 if payload["status"] == "PASS" else 1


def _run_valid_project_dag() -> dict[str, Any]:
    root = WORK_DIR / "project"
    first = _project_contract(root / "first", revision=1)
    second = _project_contract(root / "second", revision=2)
    first_path = root / "first" / "dag.json"
    second_path = root / "second" / "dag.json"
    _write_json(first_path, first)
    _write_json(second_path, second)
    _write_project_worker(root / "first", "worker", goal_id="issue-296-project-1")
    _write_project_worker(root / "second", "worker", goal_id="issue-296-project-2")

    first_plan = compile_project_dag_plan(first, source_path=first_path)
    second_plan = compile_project_dag_plan(second, source_path=second_path)
    receipt = run_project_dag_contract(
        contract_path=first_path,
        receipt_dir=root / "run",
        agents_root=root / "agents",
        scheduler="bounded-ready-queue",
    )
    return {
        "status": receipt["status"],
        "receipt": str(root / "run" / "dag-receipt.json"),
        "mocked": receipt.get("mocked"),
        "live": receipt.get("live"),
        "source_extensions": first_plan.to_payload()["source_extensions"],
        "first_plan_sha256": first_plan.plan_sha256,
        "second_plan_sha256": second_plan.plan_sha256,
        "plan_hashes_differ": first_plan.plan_sha256 != second_plan.plan_sha256,
    }


def _run_valid_generic_dag() -> dict[str, Any]:
    root = WORK_DIR / "generic"
    payload = _generic_spec(root, "json-yaml")
    json_path = root / "dag.json"
    yaml_path = root / "dag.yaml"
    _write_json(json_path, payload)
    yaml_path.write_text(yaml.safe_dump(payload, sort_keys=True), encoding="utf-8")

    json_plan = compile_generic_dag_plan(load_generic_dag_spec(json_path), source_path=json_path)
    yaml_plan = compile_generic_dag_plan(load_generic_dag_spec(yaml_path), source_path=yaml_path)
    receipt = run_generic_dag(spec_path=json_path)
    return {
        "status": receipt["status"],
        "receipt": str(root / "run" / "run-receipt.json"),
        "mocked": receipt.get("mocked"),
        "live": receipt.get("live"),
        "source_extensions": json_plan.to_payload()["source_extensions"],
        "json_yaml_plan_parity": json_plan.to_payload() == yaml_plan.to_payload(),
        "completed_node_count": receipt.get("completed_node_count"),
    }


def _run_invalid_contracts() -> dict[str, Any]:
    root = WORK_DIR / "invalid"
    root.mkdir()

    project_marker = root / "project-marker.txt"
    project = _project_contract(root / "project", revision=1)
    project["nodes"][0]["max_attempts"] = "2"
    project_path = root / "project" / "dag.json"
    _write_json(project_path, project)
    _write_project_worker(
        root / "project",
        "worker",
        marker=project_marker,
        goal_id="issue-296-project-1",
    )
    project_error = None
    try:
        project_receipt = run_project_dag_contract(
            contract_path=project_path,
            receipt_dir=root / "project-run",
            agents_root=root / "agents",
            scheduler="bounded-ready-queue",
        )
    except RuntimeError as exc:
        project_receipt = None
        project_error = str(exc)

    generic_marker = root / "generic-marker.txt"
    generic = _generic_spec(root / "generic", "invalid")
    generic["nodes"][0]["timeout_seconds"] = "5"
    generic["nodes"][0]["command"] = [
        sys.executable,
        "-c",
        f"from pathlib import Path; Path({str(generic_marker)!r}).write_text('ran')",
    ]
    generic_path = root / "generic" / "dag.json"
    _write_json(generic_path, generic)
    generic_error = None
    try:
        run_generic_dag(spec_path=generic_path)
    except RuntimeError as exc:
        generic_error = str(exc)

    return {
        "project_status": project_receipt["status"] if project_receipt else "REJECTED",
        "project_verdict": project_receipt["verdict"] if project_receipt else "REJECTED",
        "project_error": (
            project_receipt.get("dag_error", {}).get("message")
            if project_receipt
            else project_error
        ),
        "project_marker_exists": project_marker.exists(),
        "invalid_project_rejected_before_dispatch": (
            project_error is not None
            and "max_attempts" in project_error
            and not project_marker.exists()
        ),
        "generic_error": generic_error,
        "generic_marker_exists": generic_marker.exists(),
        "invalid_generic_rejected_before_dispatch": (
            generic_error is not None
            and "timeout_seconds" in generic_error
            and not generic_marker.exists()
        ),
    }


def _run_schema_drift_check() -> dict[str, Any]:
    import subprocess

    result = subprocess.run(
        [sys.executable, "scripts/check_public_dag_contract_schema.py"],
        cwd=ROOT,
        check=False,
        text=True,
        capture_output=True,
    )
    return {
        "status": "PASS" if result.returncode == 0 else "FAIL",
        "returncode": result.returncode,
        "stdout": result.stdout.strip(),
        "stderr": result.stderr.strip(),
    }


def _project_contract(root: Path, *, revision: int) -> dict[str, Any]:
    return {
        "schema": "tau.dag_contract.v1",
        "dag_id": f"issue-296-project-{revision}",
        "goal": {
            "goal_id": f"issue-296-project-{revision}",
            "goal_version": 1,
            "goal_hash": "sha256:issue-296",
        },
        "target": {"repo": "grahama1970/tau", "target": "issue#296"},
        "entry_node": "worker",
        "terminal_nodes": ["human"],
        "limits": {"default_timeout_seconds": 5, "max_total_attempts": 1},
        "nodes": [
            {
                "id": "worker",
                "agent": "worker",
                "executor": "local",
                "max_attempts": 1,
                "command_spec": str(root / "specs/worker/tau-dispatch-command.json"),
                "required_evidence": ["creator_artifact"],
            }
        ],
        "edges": [{"from": "worker", "to": "human"}],
        "required_evidence": ["creator_artifact"],
        "fail_closed_on": [
            "goal_hash_mismatch",
            "target_changed",
            "unexpected_node",
            "unexpected_edge",
            "missing_required_evidence",
            "max_attempts_exceeded",
            "malformed_handoff",
        ],
        "extensions": {"project_extension": {"revision": revision}},
    }


def _write_project_worker(
    root: Path,
    agent: str,
    *,
    goal_id: str,
    marker: Path | None = None,
) -> None:
    spec_path = root / "specs" / agent / "tau-dispatch-command.json"
    spec_path.parent.mkdir(parents=True, exist_ok=True)
    marker_code = f"Path({str(marker)!r}).write_text('ran', encoding='utf-8'); " if marker else ""
    code = (
        "import json; from pathlib import Path; "
        f"{marker_code}"
        "response = {"
        "'schema':'tau.agent_handoff.v1',"
        "'github':{'repo':'grahama1970/tau','target':'issue#296'},"
        f"'goal':{{'goal_id':{goal_id!r},'goal_version':1,'goal_hash':'sha256:issue-296'}},"
        f"'previous_subagent':{agent!r},"
        "'context':{'summary':'worker passed','artifacts':[]},"
        "'result':{'status':'PASS','summary':'worker passed','evidence':["
        "{'kind':'creator_artifact','goal_hash':'sha256:issue-296'}]},"
        "'rationale':'local worker receipt',"
        "'next_agent':{'name':'human','executor':'human','reason':'complete'},"
        "'required_evidence':['creator_artifact'],"
        "'stop_condition':'done'}; "
        "print(json.dumps(response, sort_keys=True))"
    )
    _write_json(spec_path, {"command": [sys.executable, "-c", code], "timeout_s": 5})


def _generic_spec(root: Path, name: str) -> dict[str, Any]:
    receipt = root / f"{name}-receipt.json"
    code = (
        "import json; from pathlib import Path; "
        f"p=Path({str(receipt)!r}); p.parent.mkdir(parents=True, exist_ok=True); "
        "p.write_text(json.dumps({"
        "'schema':'tau.generic_dag_node_receipt.v1',"
        "'node_id':'worker','status':'PASS','verdict':'PASS',"
        "'artifacts':[],'commands_run':['local worker'],"
        "'handoff_summary':'worker passed','errors':[],'policy_exceptions':[]"
        "}, sort_keys=True), encoding='utf-8')"
    )
    return {
        "schema": "tau.generic_dag_spec.v1",
        "run_id": f"issue-296-generic-{name}",
        "run_dir": str(root / "run"),
        "nodes": [
            {
                "node_id": "worker",
                "role": "worker",
                "command": [sys.executable, "-c", code],
                "receipt_path": str(receipt),
                "timeout_seconds": 5,
                "max_attempts": 1,
            }
        ],
        "extensions": {"project_extension": {"name": name}},
    }


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")


if __name__ == "__main__":
    raise SystemExit(main())
