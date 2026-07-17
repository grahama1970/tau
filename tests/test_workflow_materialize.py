import json
import subprocess
from pathlib import Path

import pytest

from tau_coding.dag_runtime.model import canonical_sha256
from tau_coding.workflows.catalog import get_workflow
from tau_coding.workflows.materialize import (
    materialize_approved_release_bundle,
    materialize_durable_repository_qualification,
    materialize_repository_evidence_map,
    materialize_repository_readiness,
    materialize_tau_operator_reference,
)


def test_durable_qualification_materializer_writes_repairable_mixed_dag(
    tmp_path: Path,
) -> None:
    repo = _git_repo(tmp_path / "repo")
    materialized = materialize_durable_repository_qualification(
        definition=get_workflow("durable-repository-qualification"),
        repo_path=repo,
        human_goal="Qualify this repository durably.",
        publish_path=tmp_path / "published",
        run_dir=tmp_path / "run",
        inject_test_branch_failure=True,
    )
    request = json.loads(materialized.request_path.read_text(encoding="utf-8"))
    dag = json.loads(materialized.source_dag_path.read_text(encoding="utf-8"))

    assert request["goal"]["goal_hash"] == canonical_sha256(
        {key: value for key, value in request["goal"].items() if key != "goal_hash"}
    )
    assert dag["max_concurrency"] == 3
    assert dag["workflow"]["topology"] == "DURABLE_MIXED_REPAIR_APPROVAL"
    assert [node["node_id"] for node in dag["nodes"]] == [
        "capture-repository",
        "qualify-documentation",
        "qualify-tests",
        "qualify-package",
        "reconcile-qualification",
        "publish-qualification",
        "finalize-qualification",
    ]
    assert dag["nodes"][5]["transaction"]["continuation"]["approval"]["action"] == (
        "generic_dag_transaction_continue"
    )


def test_release_materializer_writes_mixed_retry_approval_dag(tmp_path: Path) -> None:
    repo = _git_repo(tmp_path / "repo")
    materialized = materialize_approved_release_bundle(
        definition=get_workflow("approved-release-bundle"),
        repo_path=repo,
        human_goal="Publish an approved release bundle.",
        publish_path=tmp_path / "published",
        run_dir=tmp_path / "run",
    )
    dag = json.loads(materialized.source_dag_path.read_text(encoding="utf-8"))

    assert dag["max_concurrency"] == 3
    assert dag["workflow"]["topology"] == "MIXED_RETRY_APPROVAL"
    assert [node["node_id"] for node in dag["nodes"]] == [
        "prepare-release",
        "draft-release-notes",
        "build-release-manifest",
        "verify-release-policy",
        "assemble-release-bundle",
        "publish-approved-release",
        "finalize-approved-release",
    ]
    assert dag["nodes"][1]["max_attempts"] == 2
    assert dag["nodes"][1]["transaction"]["acceptance"] == {
        "require_output_change_after_revise": True
    }
    approval = dag["nodes"][5]["transaction"]["continuation"]["approval"]
    assert approval["action"] == "generic_dag_transaction_continue"
    assert not (tmp_path / "run" / "results").exists()


def test_evidence_map_materializer_writes_locked_fan_out_fan_in_dag(
    tmp_path: Path,
) -> None:
    repo = _git_repo(tmp_path / "repo")
    materialized = materialize_repository_evidence_map(
        definition=get_workflow("repository-evidence-map"),
        repo_path=repo,
        human_goal="Map this repository.",
        require_tests=True,
        run_dir=tmp_path / "run",
    )

    request = json.loads(materialized.request_path.read_text(encoding="utf-8"))
    dag = json.loads(materialized.source_dag_path.read_text(encoding="utf-8"))
    assert request["goal"]["goal_hash"] == canonical_sha256(
        {key: value for key, value in request["goal"].items() if key != "goal_hash"}
    )
    assert dag["max_concurrency"] == 3
    assert [node["node_id"] for node in dag["nodes"]] == [
        "inventory-repository",
        "analyze-documentation",
        "analyze-tests",
        "analyze-package",
        "publish-evidence-map",
    ]
    assert dag["nodes"][-1]["accepted_context_from"] == [
        "inventory-repository",
        "analyze-documentation",
        "analyze-tests",
        "analyze-package",
    ]


def test_materializer_writes_full_goal_and_three_node_dag(tmp_path: Path) -> None:
    repo = _git_repo(tmp_path / "repo")
    run_dir = tmp_path / "run"

    materialized = materialize_repository_readiness(
        definition=get_workflow("repository-readiness"),
        repo_path=repo,
        human_goal="Determine whether this checkout is ready for focused work.",
        require_clean=True,
        run_dir=run_dir,
    )

    request = json.loads(materialized.request_path.read_text(encoding="utf-8"))
    dag = json.loads(materialized.source_dag_path.read_text(encoding="utf-8"))
    goal = dag["goal"]
    expected_hash = canonical_sha256(
        {key: value for key, value in goal.items() if key != "goal_hash"}
    )
    assert goal["goal_hash"] == expected_hash == dag["goal_hash"]
    assert request["goal"] == goal
    assert dag["workflow"]["workflow_id"] == "repository-readiness"
    assert [node["node_id"] for node in dag["nodes"]] == [
        "inspect-repository",
        "validate-readiness",
        "publish-readiness",
    ]
    assert dag["max_concurrency"] == 1
    assert all(str(run_dir.resolve()) in node["receipt_path"] for node in dag["nodes"])


def test_materializer_rejects_existing_runtime_database(tmp_path: Path) -> None:
    repo = _git_repo(tmp_path / "repo")
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    (run_dir / "dag-run.sqlite3").touch()

    with pytest.raises(RuntimeError, match="workflow run already exists"):
        materialize_repository_readiness(
            definition=get_workflow("repository-readiness"),
            repo_path=repo,
            human_goal="Inspect this checkout.",
            require_clean=False,
            run_dir=run_dir,
        )


def test_operator_materializer_writes_locked_sequential_dag(tmp_path: Path) -> None:
    repo = Path(__file__).resolve().parents[1]
    run_dir = tmp_path / "operator-run"

    materialized = materialize_tau_operator_reference(
        definition=get_workflow("tau-operator-reference"),
        repo_path=repo,
        required_workflow="tau-operator-reference",
        run_dir=run_dir,
    )

    request = json.loads(materialized.request_path.read_text(encoding="utf-8"))
    dag = json.loads(materialized.source_dag_path.read_text(encoding="utf-8"))
    assert request["source_paths"] == [
        "pyproject.toml",
        "README.md",
        "docs/getting-started.md",
        "docs/live-dag-viewer.md",
        "docs/generic-dag-runner.md",
    ]
    assert request["goal"]["goal_id"] == "tau-canonical-workflow-slice-02"
    assert request["goal"]["goal_version"] == 1
    assert request["goal"]["goal_hash"] == canonical_sha256(
        {key: value for key, value in request["goal"].items() if key != "goal_hash"}
    )
    assert dag["goal"] == request["goal"]
    assert dag["workflow"]["topology"] == "MULTI_STEP_SEQUENTIAL"
    assert dag["max_concurrency"] == 1
    assert [node["node_id"] for node in dag["nodes"]] == [
        "collect-operator-sources",
        "capture-operator-cli",
        "compose-operator-reference",
        "validate-operator-reference",
    ]
    assert all(node["max_attempts"] == 1 for node in dag["nodes"])
    assert all(
        not ({"routes", "joins", "retry", "side_effects"} & set(node))
        for node in dag["nodes"]
    )
    assert not (run_dir / "results").exists()


def _git_repo(path: Path) -> Path:
    path.mkdir()
    subprocess.run(["git", "init", "-q", str(path)], check=True)
    (path / "README.md").write_text("# fixture\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(path), "add", "README.md"], check=True)
    subprocess.run(
        [
            "git",
            "-C",
            str(path),
            "-c",
            "user.name=Tau Test",
            "-c",
            "user.email=tau@example.invalid",
            "commit",
            "-qm",
            "fixture",
        ],
        check=True,
    )
    return path
