"""Live conformance receipt for Tau's canonical scheduler surface."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

CANONICAL_SCHEDULER_CONFORMANCE_SCHEMA = "tau.canonical_scheduler_conformance.v1"
REQUIRED_SURFACES = (
    "command_node",
    "validator_node",
    "transaction_node",
    "skill_node",
    "human_boundary",
    "join",
    "retry",
    "durable_resume",
    "targeted_repair",
    "map_node",
    "child_dag",
    "conditional_route",
)


def write_canonical_scheduler_conformance(
    output: Path,
    *,
    allow_live_filesystem: bool,
    repo_root: Path | None = None,
) -> dict[str, Any]:
    """Run available live scheduler lanes and write a conformance receipt.

    This receipt is intentionally fail-closed. It does not infer unsupported
    scheduler surfaces from prose or tests; every required surface needs a live
    command result or it remains an explicit blocker.
    """

    if not allow_live_filesystem:
        raise RuntimeError("--allow-live-filesystem is required")
    root = (repo_root or Path.cwd()).expanduser().resolve()
    resolved_output = output.expanduser().resolve()
    proof_dir = resolved_output.parent
    run_root = proof_dir / "runs"
    proof_dir.mkdir(parents=True, exist_ok=True)
    run_root.mkdir(parents=True, exist_ok=True)

    runs: dict[str, dict[str, Any]] = {}
    runs["canonical_01"] = _run_json_command(
        [
            sys.executable,
            os.fspath(root / "examples/canonical-dags/01-simple-linear/workflow.py"),
            "run",
            "--run-root",
            os.fspath(run_root / "canonical-01"),
            "--step-delay-seconds",
            "0",
            "--no-open",
            "--serve-after-seconds",
            "0",
        ],
        cwd=root,
        out_dir=proof_dir / "command-logs",
        name="canonical-01",
        expect_exit_codes={0},
    )
    for dag_number in (2, 3, 4):
        command = [
            sys.executable,
            os.fspath(root / "examples/canonical-dags/run.py"),
            "--dag",
            str(dag_number),
            "--run-root",
            os.fspath(run_root / f"canonical-{dag_number:02d}"),
            "--step-delay-seconds",
            "0",
            "--serve-after-seconds",
            "0",
        ]
        if dag_number == 4:
            command.append("--approve")
        runs[f"canonical_{dag_number:02d}"] = _run_json_command(
            command,
            cwd=root,
            out_dir=proof_dir / "command-logs",
            name=f"canonical-{dag_number:02d}",
            expect_exit_codes={0},
        )
    dag5_base = [
        sys.executable,
        os.fspath(root / "examples/canonical-dags/run.py"),
        "--dag",
        "5",
        "--run-root",
        os.fspath(run_root / "canonical-05"),
        "--step-delay-seconds",
        "0",
        "--serve-after-seconds",
        "0",
        "--approve",
    ]
    runs["canonical_05_initial_block"] = _run_json_command(
        dag5_base,
        cwd=root,
        out_dir=proof_dir / "command-logs",
        name="canonical-05-initial-block",
        expect_exit_codes={2},
    )
    runs["canonical_05_resume_repair"] = _run_json_command(
        [*dag5_base, "--repair", "--resume"],
        cwd=root,
        out_dir=proof_dir / "command-logs",
        name="canonical-05-resume-repair",
        expect_exit_codes={0},
    )
    runs["transaction_viewer_smoke"] = _run_json_command(
        [
            sys.executable,
            os.fspath(root / "scripts/run-dag-viewer-live-smoke.py"),
            "--out",
            os.fspath(proof_dir / "transaction-viewer-smoke.json"),
            "--run-root",
            os.fspath(run_root / "transaction-viewer-smoke"),
            "--step-delay-seconds",
            "0.25",
            "--serve-after-seconds",
            "0",
        ],
        cwd=root,
        out_dir=proof_dir / "command-logs",
        name="transaction-viewer-smoke",
        expect_exit_codes={0},
    )
    project_route_join = _write_project_route_join_fixture(run_root / "project-route-join")
    runs["project_route_join"] = _run_json_command(
        [
            "uv",
            "run",
            "tau",
            "dag-run",
            os.fspath(project_route_join["contract"]),
            "--receipt-dir",
            os.fspath(project_route_join["receipt_dir"]),
            "--agents-root",
            os.fspath(project_route_join["agents_root"]),
            "--scheduler",
            "bounded-ready-queue",
        ],
        cwd=root,
        out_dir=proof_dir / "command-logs",
        name="project-route-join",
        expect_exit_codes={0},
    )
    skill_node = _write_skill_node_fixture(run_root / "skill-node")
    runs["skill_node"] = _run_json_command(
        [
            "uv",
            "run",
            "tau",
            "dag-run",
            os.fspath(skill_node["contract"]),
            "--no-resume",
        ],
        cwd=root,
        out_dir=proof_dir / "command-logs",
        name="skill-node",
        expect_exit_codes={0},
    )
    map_child_dag = _write_map_child_dag_fixture(
        run_root / "map-child-dag", repo_root=root
    )
    runs["map_child_dag"] = _run_json_command(
        [
            "uv",
            "run",
            "tau",
            "dag-run",
            os.fspath(map_child_dag["contract"]),
            "--no-resume",
        ],
        cwd=root,
        out_dir=proof_dir / "command-logs",
        name="map-child-dag",
        expect_exit_codes={0},
    )

    surfaces = _surface_evidence(runs)
    missing_surfaces = [name for name in REQUIRED_SURFACES if surfaces.get(name) is not True]
    status = "PASS" if not missing_surfaces else "BLOCKED"
    payload = {
        "schema": CANONICAL_SCHEDULER_CONFORMANCE_SCHEMA,
        "status": status,
        "mocked": False,
        "live": True,
        "provider_live": False,
        "repo_root": str(root),
        "output": str(resolved_output),
        "timestamp": datetime.now(UTC).isoformat(),
        "required_surfaces": list(REQUIRED_SURFACES),
        "surfaces": surfaces,
        "missing_surfaces": missing_surfaces,
        "runs": runs,
        "proof_boundary": {
            "proves": [
                (
                    "Tau executed live local canonical DAG examples through the durable "
                    "generic scheduler."
                ),
                (
                    "Tau observed transaction validator/reviewer admission through the "
                    "live DAG viewer smoke."
                ),
                "Tau executed a native generic DAG skill node through a Tau-owned provider.",
                (
                    "Tau executed a canonical map workload that launched item-specific "
                    "child DAGs through tau dag-run and joined the exact child cardinality."
                ),
                "Tau reports unsupported or unexercised scheduler surfaces as blockers.",
            ],
            "does_not_prove": [
                "Provider or model semantic quality.",
                "Security sandbox enforcement.",
                "Resource lease enforcement.",
                "A first-class dynamic map-node schema beyond this canonical conformance workload.",
            ],
        },
    }
    resolved_output.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return payload


def _surface_evidence(runs: dict[str, dict[str, Any]]) -> dict[str, bool]:
    c01 = _json_payload(runs, "canonical_01")
    c03 = _json_payload(runs, "canonical_03")
    c04 = _json_payload(runs, "canonical_04")
    c05_initial = _json_payload(runs, "canonical_05_initial_block")
    c05_resume = _json_payload(runs, "canonical_05_resume_repair")
    tx = _json_payload(runs, "transaction_viewer_smoke")
    route_join = _json_payload(runs, "project_route_join")
    skill = _json_payload(runs, "skill_node")
    map_child = _artifact_json_from_node(
        _json_payload(runs, "map_child_dag"),
        schema="tau.map_child_dag_conformance.v1",
    )
    tx_checks = tx.get("checks") if isinstance(tx, dict) else {}
    route_receipts = route_join.get("route_decision_receipts", [])
    join_receipts = route_join.get("join_decision_receipts", [])
    contribution_receipts = route_join.get("terminal_contribution_receipts", [])
    skill_nodes = skill.get("nodes", []) if isinstance(skill.get("nodes"), list) else []
    tau_skill_nodes = [
        node
        for node in skill_nodes
        if isinstance(node, dict)
        and node.get("status") == "PASS"
        and node.get("skill_provider") == "tau"
        and node.get("capability") == "runtime_handshake"
        and node.get("skill_live") is True
    ]
    map_contract = map_child.get("map_node", {}) if isinstance(map_child, dict) else {}
    child_contract = map_child.get("child_dag", {}) if isinstance(map_child, dict) else {}
    return {
        "command_node": _pass(c01) and _pass(c03),
        "validator_node": _pass(c01) and _pass(tx),
        "transaction_node": _pass(tx) and bool(tx_checks.get("accepted_only_after_receipt")),
        "skill_node": _pass(skill) and bool(tau_skill_nodes),
        "human_boundary": _pass(c04) and _pass(c05_resume),
        "join": (
            _pass(c03)
            and int(c03.get("max_observed_concurrency") or 0) >= 2
            and _pass(route_join)
            and bool(join_receipts)
            and bool(contribution_receipts)
        ),
        "retry": _pass(c04) and int(c04.get("completed_node_count") or 0) >= 5,
        "durable_resume": _pass(c05_resume) and int(c05_resume.get("resumed_node_count") or 0) >= 4,
        "targeted_repair": (
            _blocked(c05_initial)
            and _pass(c05_resume)
            and int(c05_resume.get("resumed_node_count") or 0) >= 4
        ),
        "map_node": (
            _pass(map_child)
            and int(map_contract.get("item_count") or 0) >= 3
            and map_contract.get("exact_join_cardinality") is True
            and len(map_contract.get("deterministic_child_ids", [])) >= 3
        ),
        "child_dag": (
            _pass(map_child)
            and int(child_contract.get("child_count") or 0) >= 3
            and child_contract.get("all_children_passed") is True
            and all(
                isinstance(item, dict) and item.get("status") == "PASS"
                for item in child_contract.get("child_receipts", [])
            )
        ),
        "conditional_route": (
            _pass(route_join)
            and bool(route_receipts)
            and set(route_join.get("selected_agents", [])) >= {"router", "accept", "revise"}
        ),
    }


def _artifact_json_from_node(payload: dict[str, Any], *, schema: str) -> dict[str, Any]:
    nodes = payload.get("nodes")
    if not isinstance(nodes, list):
        return {}
    for node in nodes:
        if not isinstance(node, dict):
            continue
        artifacts = node.get("artifacts")
        if not isinstance(artifacts, list):
            continue
        for artifact in artifacts:
            if not isinstance(artifact, dict) or artifact.get("schema") != schema:
                continue
            path = artifact.get("path")
            if not isinstance(path, str) or not path:
                continue
            try:
                loaded = json.loads(Path(path).read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if isinstance(loaded, dict) and loaded.get("schema") == schema:
                return loaded
    return {}


def _write_skill_node_fixture(root: Path) -> dict[str, Path]:
    root.mkdir(parents=True, exist_ok=True)
    contract_path = root / "skill-node.json"
    work_order_path = root / "skill-work-order.json"
    receipt_path = root / "skill-node-receipt.json"
    _write_json(
        work_order_path,
        {
            "schema": "tau.skill_work_order.v1",
            "goal_hash": "sha256:canonical-scheduler-conformance",
            "task": "Run Tau runtime handshake through a native generic DAG skill node.",
        },
    )
    _write_json(
        contract_path,
        {
            "schema": "tau.generic_dag_spec.v1",
            "run_id": "canonical-skill-node",
            "run_dir": str(root / "run"),
            "goal_hash": "sha256:canonical-scheduler-conformance",
            "nodes": [
                {
                    "node_id": "tau-runtime-handshake-skill",
                    "receipt_path": str(receipt_path),
                    "work_order_path": str(work_order_path),
                    "skill": {
                        "schema": "tau.skill_dag_node.v1",
                        "capability": "runtime_handshake",
                        "provider": "tau",
                        "output_dir": str(root / "skill-output"),
                        "configuration": {"timeout_seconds": 120},
                    },
                }
            ],
        },
    )
    return {"contract": contract_path}


def _write_map_child_dag_fixture(root: Path, *, repo_root: Path) -> dict[str, Path]:
    root.mkdir(parents=True, exist_ok=True)
    runner_path = root / "map_child_runner.py"
    contract_path = root / "map-child-parent.json"
    receipt_path = root / "map-node-receipt.json"
    runner_path.write_text(_map_child_runner_source(), encoding="utf-8")
    _write_json(
        contract_path,
        {
            "schema": "tau.generic_dag_spec.v1",
            "run_id": "canonical-map-child-dag",
            "run_dir": str(root / "parent-run"),
            "goal_hash": "sha256:canonical-scheduler-conformance",
            "nodes": [
                {
                    "node_id": "map-items",
                    "receipt_path": str(receipt_path),
                    "timeout_seconds": 120,
                    "command": [
                        sys.executable,
                        str(runner_path),
                        "--repo-root",
                        str(repo_root),
                        "--root",
                        str(root / "map-run"),
                        "--receipt",
                        str(receipt_path),
                        "--goal-hash",
                        "sha256:canonical-scheduler-conformance",
                    ],
                }
            ],
        },
    )
    return {"contract": contract_path}


def _map_child_runner_source() -> str:
    return r'''from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

from tau_coding.dag_runtime.admission import write_durable_json

GENERIC_DAG_SPEC_SCHEMA = "tau.generic_dag_spec.v1"
GENERIC_DAG_NODE_RECEIPT_SCHEMA = "tau.generic_dag_node_receipt.v1"
MAP_CHILD_SCHEMA = "tau.map_child_dag_conformance.v1"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo-root", required=True)
    parser.add_argument("--root", required=True)
    parser.add_argument("--receipt", required=True)
    parser.add_argument("--goal-hash", required=True)
    args = parser.parse_args()

    repo_root = Path(args.repo_root).expanduser().resolve()
    root = Path(args.root).expanduser().resolve()
    receipt_path = Path(args.receipt).expanduser().resolve()
    root.mkdir(parents=True, exist_ok=True)
    items = [
        {"item_id": "alpha", "value": 1},
        {"item_id": "bravo", "value": 2},
        {"item_id": "charlie", "value": 3},
    ]
    child_receipts: list[dict[str, Any]] = []
    child_commands: list[list[str]] = []
    errors: list[str] = []
    env = dict(os.environ)
    src_path = str(repo_root / "src")
    env["PYTHONPATH"] = (
        src_path
        if not env.get("PYTHONPATH")
        else f"{src_path}{os.pathsep}{env['PYTHONPATH']}"
    )

    for item in items:
        child_id = f"child-{item['item_id']}"
        child_root = root / "children" / item["item_id"]
        child_root.mkdir(parents=True, exist_ok=True)
        child_receipt_path = child_root / "receipt.json"
        child_spec_path = child_root / "dag.json"
        child_code = (
            "import json\n"
            "from pathlib import Path\n"
            f"receipt_path = Path({json.dumps(str(child_receipt_path))})\n"
            "payload = json.loads("
            f"{json.dumps(json.dumps(_child_receipt_payload(child_id, item, args.goal_hash)))}"
            ")\n"
            "receipt_path.parent.mkdir(parents=True, exist_ok=True)\n"
            "receipt_path.write_text(\n"
            "    json.dumps(payload, indent=2, sort_keys=True) + '\\n',\n"
            "    encoding='utf-8',\n"
            ")\n"
            "print(json.dumps(payload, sort_keys=True))\n"
        )
        write_json(
            child_spec_path,
            {
                "schema": GENERIC_DAG_SPEC_SCHEMA,
                "run_id": child_id,
                "run_dir": str(child_root / "run"),
                "goal_hash": args.goal_hash,
                "nodes": [
                    {
                        "node_id": child_id,
                        "receipt_path": str(child_receipt_path),
                        "timeout_seconds": 30,
                        "command": [sys.executable, "-c", child_code],
                    }
                ],
            },
        )
        command = ["uv", "run", "tau", "dag-run", str(child_spec_path), "--no-resume"]
        child_commands.append(command)
        completed = subprocess.run(
            command,
            cwd=str(repo_root),
            check=False,
            capture_output=True,
            text=True,
            timeout=60,
            env=env,
        )
        stdout_path = child_root / "dag-run.stdout"
        stderr_path = child_root / "dag-run.stderr"
        stdout_path.write_text(completed.stdout, encoding="utf-8")
        stderr_path.write_text(completed.stderr, encoding="utf-8")
        dag_receipt = parse_json_stdout(completed.stdout)
        node_receipt = read_json(child_receipt_path)
        child_status = dag_receipt.get("status")
        if (
            completed.returncode != 0
            or child_status != "PASS"
            or node_receipt.get("status") != "PASS"
        ):
            errors.append(f"{child_id}:child_dag_failed:{completed.returncode}:{child_status}")
        child_receipts.append(
            {
                "item_id": item["item_id"],
                "child_id": child_id,
                "status": child_status,
                "exit_code": completed.returncode,
                "spec_path": str(child_spec_path),
                "receipt_path": str(child_receipt_path),
                "receipt_sha256": (
                    file_sha256(child_receipt_path) if child_receipt_path.is_file() else None
                ),
                "stdout_path": str(stdout_path),
                "stderr_path": str(stderr_path),
            }
        )

    deterministic_child_ids = [f"child-{item['item_id']}" for item in items]
    exact_join_cardinality = len(child_receipts) == len(items) and {
        item["child_id"] for item in child_receipts
    } == set(deterministic_child_ids)
    all_children_passed = all(item.get("status") == "PASS" for item in child_receipts)
    if not exact_join_cardinality:
        errors.append("map_exact_join_cardinality_failed")
    if not all_children_passed:
        errors.append("child_dag_pass_contract_failed")
    status = "PASS" if not errors else "BLOCKED"
    aggregate_path = root / "map-child-dag-conformance.json"
    aggregate = {
        "schema": MAP_CHILD_SCHEMA,
        "status": status,
        "mocked": False,
        "live": True,
        "provider_live": False,
        "map_node": {
            "item_count": len(items),
            "deterministic_child_ids": deterministic_child_ids,
            "exact_join_cardinality": exact_join_cardinality,
        },
        "child_dag": {
            "child_count": len(child_receipts),
            "all_children_passed": all_children_passed,
            "child_receipts": child_receipts,
        },
        "commands_run": child_commands,
        "errors": errors,
    }
    write_json(aggregate_path, aggregate)
    receipt = {
        "schema": GENERIC_DAG_NODE_RECEIPT_SCHEMA,
        "node_id": "map-items",
        "status": status,
        "verdict": "PASS" if status == "PASS" else "BLOCKED",
        "mocked": False,
        "live": True,
        "provider_live": False,
        "goal_hash": args.goal_hash,
        "artifacts": [artifact(aggregate_path, MAP_CHILD_SCHEMA)],
        "commands_run": child_commands,
        "errors": errors,
        "policy_exceptions": [],
        "handoff_summary": (
            f"Mapped {len(items)} items into {len(child_receipts)} child DAGs "
            f"with exact cardinality {exact_join_cardinality}"
        ),
    }
    write_json(receipt_path, receipt)
    print(json.dumps(aggregate, indent=2, sort_keys=True))
    return 0 if status == "PASS" else 2


def _child_receipt_payload(child_id: str, item: dict[str, Any], goal_hash: str) -> dict[str, Any]:
    return {
        "schema": GENERIC_DAG_NODE_RECEIPT_SCHEMA,
        "node_id": child_id,
        "status": "PASS",
        "verdict": "PASS",
        "mocked": False,
        "live": True,
        "provider_live": False,
        "goal_hash": goal_hash,
        "accepted_output": {
            "item_id": item["item_id"],
            "value": item["value"],
            "result": item["value"] * 2,
        },
        "artifacts": [],
        "commands_run": [],
        "errors": [],
        "policy_exceptions": [],
        "handoff_summary": f"Processed map item {item['item_id']}",
    }


def parse_json_stdout(stdout: str) -> dict[str, Any]:
    text = stdout.strip()
    start = text.find("{")
    end = text.rfind("}")
    if start < 0 or end < start:
        return {}
    try:
        payload = json.loads(text[start : end + 1])
    except json.JSONDecodeError:
        return {}
    return payload if isinstance(payload, dict) else {}


def read_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def write_json(path: Path, payload: dict[str, Any]) -> None:
    """Durable JSON write (#209): delegates to the admission primitive.

    The former bare write_text body could tear under kill; every authoritative
    artifact now goes through temp/fsync/rename/dir-fsync.
    """

    write_durable_json(path, payload)


def artifact(path: Path, schema: str) -> dict[str, Any]:
    return {
        "path": str(path),
        "schema": schema,
        "sha256": file_sha256(path),
        "bytes": path.stat().st_size,
    }


def file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


if __name__ == "__main__":
    raise SystemExit(main())
'''


def _write_project_route_join_fixture(root: Path) -> dict[str, Path]:
    """Materialize a live project DAG fixture for typed route and join receipts."""

    root.mkdir(parents=True, exist_ok=True)
    agents_root = root / "agents"
    agents_root.mkdir(parents=True, exist_ok=True)
    spec_root = root / "specs"
    receipt_dir = root / "run"
    contract_path = root / "project-route-join.json"
    _write_response_spec(
        spec_root,
        "router",
        _handoff("router", "accept", _creator_evidence(), result_fields={"route": "BOTH"}),
        cwd=root,
    )
    _write_response_spec(
        spec_root,
        "accept",
        _handoff("accept", "join", _creator_evidence()),
        cwd=root,
    )
    _write_response_spec(
        spec_root,
        "revise",
        _handoff("revise", "join", _creator_evidence()),
        cwd=root,
    )

    def condition(value: str) -> dict[str, object]:
        return {
            "schema": "tau.route_condition.v1",
            "op": "eq",
            "field": "route",
            "value": value,
        }

    _write_json(
        contract_path,
        {
            "schema": "tau.dag_contract.v1",
            "dag_id": "canonical-project-route-join",
            "goal": {
                "goal_id": "canonical-scheduler-conformance",
                "goal_version": 1,
                "goal_hash": "sha256:canonical-scheduler-conformance",
            },
            "target": {
                "repo": "grahama1970/tau",
                "target": "canonical-scheduler-conformance",
            },
            "entry_node": "start",
            "terminal_nodes": ["human"],
            "limits": {
                "resume": True,
                "default_timeout_seconds": 30,
                "max_total_attempts": 6,
                "max_concurrency": 2,
            },
            "nodes": [
                {
                    "id": "start",
                    "agent": "goal-guardian",
                    "executor": "scheduler",
                    "max_attempts": 1,
                    "required_evidence": [],
                },
                {
                    "id": "router",
                    "agent": "router",
                    "executor": "local",
                    "max_attempts": 1,
                    "command_spec": str(spec_root / "router" / "tau-dispatch-command.json"),
                    "required_evidence": ["creator_artifact"],
                    "route": {"mode": "fanout"},
                },
                {
                    "id": "accept",
                    "agent": "accept",
                    "executor": "local",
                    "max_attempts": 1,
                    "command_spec": str(spec_root / "accept" / "tau-dispatch-command.json"),
                    "required_evidence": ["creator_artifact"],
                },
                {
                    "id": "revise",
                    "agent": "revise",
                    "executor": "local",
                    "max_attempts": 1,
                    "command_spec": str(spec_root / "revise" / "tau-dispatch-command.json"),
                    "required_evidence": ["creator_artifact"],
                },
                {
                    "id": "join",
                    "agent": "join",
                    "executor": "local",
                    "max_attempts": 1,
                    "required_evidence": [],
                    "join": {
                        "schema": "tau.dag_join_policy.v1",
                        "policy": "minimum_success_count",
                        "required_successes": 2,
                        "timeout_seconds": 30,
                    },
                },
            ],
            "edges": [
                {"from": "start", "to": "router"},
                {"from": "router", "to": "accept", "condition": condition("BOTH")},
                {"from": "router", "to": "revise", "condition": condition("BOTH")},
                {"from": "accept", "to": "join"},
                {"from": "revise", "to": "join"},
                {"from": "join", "to": "human"},
            ],
            "required_evidence": ["creator_artifact"],
            "fail_closed_on": ["malformed_handoff"],
        },
    )
    return {
        "contract": contract_path,
        "agents_root": agents_root,
        "receipt_dir": receipt_dir,
    }


def _write_response_spec(
    spec_root: Path,
    agent: str,
    response: dict[str, object],
    *,
    cwd: Path,
) -> None:
    spec_path = spec_root / agent / "tau-dispatch-command.json"
    spec_path.parent.mkdir(parents=True, exist_ok=True)
    code = f"import json; print({json.dumps(json.dumps(response))})"
    _write_json(
        spec_path,
        {
            "command": [sys.executable, "-c", code],
            "cwd": str(cwd),
            "timeout_s": 5,
        },
    )


def _handoff(
    previous_subagent: str,
    next_agent: str,
    evidence: list[object],
    *,
    result_fields: dict[str, object] | None = None,
) -> dict[str, object]:
    result: dict[str, object] = {
        "status": "PASS",
        "summary": f"{previous_subagent} completed.",
        "evidence": evidence,
    }
    if result_fields:
        result.update(result_fields)
    return {
        "schema": "tau.agent_handoff.v1",
        "github": {
            "repo": "grahama1970/tau",
            "target": "canonical-scheduler-conformance",
        },
        "goal": {
            "goal_id": "canonical-scheduler-conformance",
            "goal_version": 1,
            "goal_hash": "sha256:canonical-scheduler-conformance",
        },
        "previous_subagent": previous_subagent,
        "context": {
            "summary": f"{previous_subagent} node response.",
            "artifacts": [],
        },
        "result": result,
        "rationale": "The DAG contract controls the next route.",
        "next_agent": {
            "name": next_agent,
            "executor": "human" if next_agent == "human" else "local",
            "reason": "Continue along the DAG route.",
        },
        "required_evidence": ["creator_artifact"],
        "stop_condition": "Stop at human.",
    }


def _creator_evidence() -> list[object]:
    return [
        {
            "kind": "creator_artifact",
            "summary": "Canonical scheduler conformance fixture artifact.",
        }
    ]


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _pass(payload: Any) -> bool:
    return isinstance(payload, dict) and payload.get("status") == "PASS"


def _blocked(payload: Any) -> bool:
    return isinstance(payload, dict) and payload.get("status") == "BLOCKED"


def _json_payload(runs: dict[str, dict[str, Any]], key: str) -> dict[str, Any]:
    payload = runs.get(key, {}).get("json")
    return payload if isinstance(payload, dict) else {}


def _run_json_command(
    command: list[str],
    *,
    cwd: Path,
    out_dir: Path,
    name: str,
    expect_exit_codes: set[int],
) -> dict[str, Any]:
    out_dir.mkdir(parents=True, exist_ok=True)
    stdout_path = out_dir / f"{name}.stdout"
    stderr_path = out_dir / f"{name}.stderr"
    completed = subprocess.run(
        command,
        cwd=cwd,
        check=False,
        capture_output=True,
        text=True,
        timeout=120,
    )
    stdout_path.write_text(completed.stdout, encoding="utf-8")
    stderr_path.write_text(completed.stderr, encoding="utf-8")
    parsed, parse_error = _parse_json_stdout(completed.stdout)
    return {
        "command": command,
        "exit_code": completed.returncode,
        "expected_exit_codes": sorted(expect_exit_codes),
        "exit_code_expected": completed.returncode in expect_exit_codes,
        "stdout_path": str(stdout_path),
        "stdout_sha256": _sha256(stdout_path),
        "stderr_path": str(stderr_path),
        "stderr_sha256": _sha256(stderr_path),
        "json": parsed,
        "json_parse_error": parse_error,
    }


def _parse_json_stdout(stdout: str) -> tuple[dict[str, Any] | None, str | None]:
    text = stdout.strip()
    start = text.find("{")
    end = text.rfind("}")
    if start < 0 or end < start:
        return None, "json_object_not_found"
    try:
        payload = json.loads(text[start : end + 1])
    except json.JSONDecodeError as exc:
        return None, str(exc)
    if not isinstance(payload, dict):
        return None, "json_stdout_not_object"
    return payload, None


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return f"sha256:{digest.hexdigest()}"
