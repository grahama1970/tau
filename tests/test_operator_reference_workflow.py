import json
from pathlib import Path

from tau_coding.workflows.runner import run_tau_operator_reference_workflow


def test_operator_reference_live_run_recomputes_and_publishes(tmp_path: Path) -> None:
    repo = Path(__file__).resolve().parents[1]
    run_dir = tmp_path / "operator-reference"

    receipt = run_tau_operator_reference_workflow(
        repo_path=repo,
        required_workflow="tau-operator-reference",
        run_dir=run_dir,
        open_viewer=False,
        browser_open=False,
        viewer_hold_seconds=None,
    )

    assert receipt["ok"] is True
    assert receipt["mocked"] is False
    assert receipt["live"] is True
    assert receipt["provider_live"] is False
    result = json.loads(
        (run_dir / "results" / "tau-operator-reference.json").read_text(encoding="utf-8")
    )
    markdown = (run_dir / "results" / "tau-operator-reference.md").read_text(
        encoding="utf-8"
    )
    assert result["schema"] == "tau.operator_reference.v1"
    assert result["status"] == "ACCEPTED"
    assert [source["path"] for source in result["source_evidence"]["sources"]] == [
        "pyproject.toml",
        "README.md",
        "docs/getting-started.md",
        "docs/live-dag-viewer.md",
        "docs/generic-dag-runner.md",
    ]
    public_probes = [
        item["public_argv"] for item in result["cli_evidence"]["results"]
    ]
    assert public_probes == [
        ["tau", "workflows", "list", "--json"],
        ["tau", "workflows", "run", "--help"],
        ["tau", "dag-view-capabilities", "--json"],
    ]
    assert all(item["exit_code"] == 0 for item in result["cli_evidence"]["results"])
    assert "# Tau Operator Reference" in markdown
    assert (run_dir / "intermediate" / "tau-operator-reference.draft.json").is_file()
    assert (run_dir / "intermediate" / "tau-operator-reference.draft.md").is_file()

    goal_hash = receipt["goal"]["goal_hash"]
    receipt_paths = sorted((run_dir / "receipts").glob("*.json"))
    assert len(receipt_paths) == 4
    node_receipts = [json.loads(path.read_text(encoding="utf-8")) for path in receipt_paths]
    assert all(item["goal_hash"] == goal_hash for item in node_receipts)
    assert all("accepted_output" in item for item in node_receipts)
    validation = next(
        item for item in node_receipts if item["node_id"] == "validate-operator-reference"
    )
    assert validation["accepted_output"]["independently_recomputed"] is True


def test_operator_reference_missing_required_workflow_blocks_without_results(
    tmp_path: Path,
) -> None:
    repo = Path(__file__).resolve().parents[1]
    run_dir = tmp_path / "operator-reference-negative"

    receipt = run_tau_operator_reference_workflow(
        repo_path=repo,
        required_workflow="deliberately-absent",
        run_dir=run_dir,
        open_viewer=False,
        browser_open=False,
        viewer_hold_seconds=None,
    )

    assert receipt["ok"] is False
    assert receipt["status"] == "BLOCKED"
    validation = json.loads(
        (run_dir / "receipts" / "validate-operator-reference.json").read_text(
            encoding="utf-8"
        )
    )
    assert validation["status"] == "BLOCKED"
    assert validation["errors"][0] == "required_workflow_missing"
    assert "accepted_output" in validation
    assert validation["accepted_output"] is None
    assert not (run_dir / "results" / "tau-operator-reference.json").exists()
    assert not (run_dir / "results" / "tau-operator-reference.md").exists()
