"""Non-mocked sanity checks for Tau's canonical DAG product ladder."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest


def test_canonical_simple_linear_dag_produces_goal_summary(tmp_path: Path) -> None:
    repo_root = Path(__file__).resolve().parents[1]
    workflow = repo_root / "examples" / "canonical-dags" / "01-simple-linear" / "workflow.py"
    run_root = tmp_path / "canonical-01"

    completed = subprocess.run(
        [
            sys.executable,
            str(workflow),
            "run",
            "--run-root",
            str(run_root),
            "--step-delay-seconds",
            "0",
        ],
        cwd=repo_root,
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )

    assert completed.returncode == 0, completed.stderr
    result = json.loads(completed.stdout)
    assert result["status"] == "PASS"
    assert result["mocked"] is False
    assert result["live"] is True
    assert result["provider_live"] is False
    assert result["node_count"] == 2
    assert result["completed_node_count"] == 2
    assert result["max_observed_concurrency"] == 1
    assert (run_root / "run" / "dag-run.sqlite3").is_file()

    spec = json.loads((run_root / "dag.json").read_text(encoding="utf-8"))
    assert spec["goal_hash"].startswith("sha256:")
    assert spec["workflow"]["workflow_id"] == "canonical-01-simple-linear"
    assert spec["workflow"]["result_node_id"] == "validate-goal"
    assert [node["node_id"] for node in spec["nodes"]] == [
        "extract-goal",
        "validate-goal",
    ]
    assert spec["nodes"][1]["depends_on"] == ["extract-goal"]

    summary = (run_root / "artifacts" / "tau-goal-summary.md").read_text(encoding="utf-8")
    assert "# Tau Goal Summary" in summary
    assert "1. Simple linear DAG" in summary
    assert "5. Durable mixed-topology DAG" in summary
    run_receipt = json.loads((run_root / "run" / "run-receipt.json").read_text(encoding="utf-8"))
    accepted = run_receipt["nodes"][-1]["accepted_output"]
    assert accepted["status"] == "ACCEPTED"
    assert accepted["artifacts"][0]["sha256"] == result["output_sha256"]


def test_canonical_simple_linear_dag_rejects_incomplete_goal(tmp_path: Path) -> None:
    repo_root = Path(__file__).resolve().parents[1]
    workflow = repo_root / "examples" / "canonical-dags" / "01-simple-linear" / "workflow.py"
    goal = tmp_path / "GOAL.md"
    goal.write_text("## Goal\nIncomplete goal\n## Required Product Outcome\n", encoding="utf-8")
    context = tmp_path / "context.json"
    context.write_text(json.dumps({"node_id": "extract-goal"}), encoding="utf-8")

    completed = subprocess.run(
        [
            sys.executable,
            str(workflow),
            "extract-goal",
            "--goal",
            str(goal),
            "--output",
            str(tmp_path / "digest.json"),
            "--receipt",
            str(tmp_path / "receipt.json"),
        ],
        cwd=repo_root,
        env={**os.environ, "TAU_GENERIC_DAG_CONTEXT": str(context)},
        check=False,
        capture_output=True,
        text=True,
        timeout=10,
    )

    assert completed.returncode != 0
    assert "goal_topology_labels_missing" in completed.stderr
    assert not (tmp_path / "receipt.json").exists()


@pytest.mark.parametrize(
    ("dag", "node_count", "concurrency", "extra"),
    [
        (2, 4, 1, []),
        (3, 5, 3, []),
        (4, 5, 2, ["--approve"]),
    ],
)
def test_canonical_dag_ladder_runs_real_topologies(
    tmp_path: Path,
    dag: int,
    node_count: int,
    concurrency: int,
    extra: list[str],
) -> None:
    repo_root = Path(__file__).resolve().parents[1]
    run_root = tmp_path / f"canonical-{dag:02d}"
    completed = _run_canonical(repo_root, dag=dag, run_root=run_root, extra=extra)

    assert completed.returncode == 0, completed.stderr
    result = json.loads(completed.stdout)
    assert result["status"] == "PASS"
    assert result["mocked"] is False
    assert result["live"] is True
    assert result["durable"] is True
    assert result["node_count"] == node_count
    assert result["completed_node_count"] == node_count
    assert result["max_observed_concurrency"] == concurrency

    spec = json.loads((run_root / "dag.json").read_text(encoding="utf-8"))
    assert spec["workflow"] == {
        "schema": "tau.workflow_metadata.v1",
        "workflow_id": result["dag_id"],
        "workflow_version": 1,
        "title": result["title"],
        "summary": f"Run Tau's {result['title'].lower()} canonical workflow.",
        "topology": {
            2: "MULTI_STEP_SEQUENTIAL",
            3: "FAN_OUT_FAN_IN",
            4: "MIXED_RETRY_APPROVAL",
        }[dag],
        "result_node_id": spec["nodes"][-1]["node_id"],
        "result_schema": "tau.canonical_dag_result.v1",
    }

    if dag == 4:
        receipt = json.loads((run_root / "run" / "run-receipt.json").read_text(encoding="utf-8"))
        review = next(node for node in receipt["nodes"] if node["node_id"] == "review")
        assert review["attempt_count"] == 2
        assert [attempt["verdict"] for attempt in review["scheduler_attempts"]] == [
            "REVISE",
            "PASS",
        ]
        assert (run_root / "authorizations" / "human-release.json").is_file()
        rollback = json.loads((run_root / "rollback" / "release.json").read_text(encoding="utf-8"))
        assert rollback["rollback_action"] == "delete target"

    final_node = json.loads(Path(result["output_artifact"]).read_text(encoding="utf-8"))
    assert final_node["goal_sha256"].startswith("sha256:")
    if dag >= 3:
        assert final_node["accepted_input_artifacts"]
    receipt = json.loads((run_root / "run" / "run-receipt.json").read_text(encoding="utf-8"))
    accepted = receipt["nodes"][-1]["accepted_output"]
    assert accepted["schema"] == "tau.canonical_dag_result.v1"
    assert accepted["status"] == "ACCEPTED"
    assert accepted["artifacts"][0]["sha256"].startswith("sha256:")


@pytest.mark.parametrize(("dag", "fail_node"), [(2, "analyze"), (3, "tests")])
def test_canonical_dags_fail_closed_at_requested_node(
    tmp_path: Path, dag: int, fail_node: str
) -> None:
    repo_root = Path(__file__).resolve().parents[1]
    completed = _run_canonical(
        repo_root,
        dag=dag,
        run_root=tmp_path / f"canonical-{dag:02d}-blocked",
        extra=["--fail-node", fail_node],
    )

    assert completed.returncode == 2
    result = json.loads(completed.stdout)
    assert result["status"] == "BLOCKED"
    assert result["verdict"] == "BLOCKED"


def test_canonical_human_gate_blocks_without_approval(tmp_path: Path) -> None:
    repo_root = Path(__file__).resolve().parents[1]
    completed = _run_canonical(
        repo_root,
        dag=4,
        run_root=tmp_path / "canonical-04-unapproved",
        extra=[],
    )

    assert completed.returncode == 2
    result = json.loads(completed.stdout)
    assert result["status"] == "BLOCKED"
    assert result["completed_node_count"] == 4
    receipt = json.loads(
        (tmp_path / "canonical-04-unapproved" / "run" / "run-receipt.json").read_text(
            encoding="utf-8"
        )
    )
    release = next(node for node in receipt["nodes"] if node["node_id"] == "release")
    assert release["verdict"] == "BLOCKED"
    assert "human_approval_required" in release["errors"][0]


def test_canonical_durable_dag_blocks_then_resumes_only_remaining_nodes(
    tmp_path: Path,
) -> None:
    repo_root = Path(__file__).resolve().parents[1]
    run_root = tmp_path / "canonical-05"

    blocked = _run_canonical(
        repo_root,
        dag=5,
        run_root=run_root,
        extra=["--approve"],
    )
    assert blocked.returncode == 2, blocked.stderr
    blocked_result = json.loads(blocked.stdout)
    assert blocked_result["status"] == "BLOCKED"
    assert blocked_result["verdict"] == "BLOCKED"
    assert blocked_result["completed_node_count"] == 4

    resumed = _run_canonical(
        repo_root,
        dag=5,
        run_root=run_root,
        extra=["--approve", "--repair", "--resume"],
    )
    assert resumed.returncode == 0, resumed.stderr
    result = json.loads(resumed.stdout)
    assert result["status"] == "PASS"
    assert result["completed_node_count"] == 6
    assert result["resumed_node_count"] == 4
    assert result["max_observed_concurrency"] == 3

    receipt = json.loads((run_root / "run" / "run-receipt.json").read_text(encoding="utf-8"))
    by_id = {node["node_id"]: node for node in receipt["nodes"]}
    resumed_ids = ("discover", "build", "test", "document")
    assert all(by_id[node_id]["resumed"] is True for node_id in resumed_ids)
    assert by_id["reconcile"]["resumed"] is False
    assert by_id["release"]["resumed"] is False
    assert by_id["release"]["accepted_output"]["status"] == "ACCEPTED"


def _run_canonical(
    repo_root: Path,
    *,
    dag: int,
    run_root: Path,
    extra: list[str],
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            sys.executable,
            str(repo_root / "examples" / "canonical-dags" / "run.py"),
            "--dag",
            str(dag),
            "--run-root",
            str(run_root),
            "--step-delay-seconds",
            "0",
            *extra,
        ],
        cwd=repo_root,
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )
