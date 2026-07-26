import hashlib
import json
import subprocess
from pathlib import Path

from typer.testing import CliRunner

from tau_coding.cli import app
from tau_coding.pending_decisions import collect_pending_decisions, pending_decision_report


def test_pending_decision_inbox_tracks_approval_boundary_and_clears(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("TAU_RUN_REGISTRY", str(tmp_path / "registry" / "runs.json"))
    repo = _git_repo(tmp_path / "repo")
    runner = CliRunner()
    approval_run = tmp_path / "approval-run"
    publish_path = tmp_path / "published"

    first = runner.invoke(
        app,
        [
            "workflows",
            "run",
            "approved-release-bundle",
            "--repo",
            str(repo),
            "--goal",
            "Publish an approved release bundle.",
            "--publish-path",
            str(publish_path),
            "--run-dir",
            str(approval_run),
        ],
    )

    assert first.exit_code == 1
    assert json.loads(first.stdout)["status"] == "BLOCKED"
    decisions = collect_pending_decisions()
    assert len(decisions) == 1
    decision = decisions[0]
    assert decision.workflow_id == "approved-release-bundle"
    assert decision.node_id == "publish-approved-release"
    assert decision.requested_action == "generic_dag_transaction_continue"
    assert "Provide human approval" in decision.required_action
    assert decision.command == (
        f"tau workflows approve {approval_run.resolve()} --approval-packet <approval.json>"
    )
    assert "approved-release-bundle/publish-approved-release" in pending_decision_report(decisions)

    blocked_run = tmp_path / "blocked-run"
    (repo / "dirty.txt").write_text("dirty\n", encoding="utf-8")
    non_approval = runner.invoke(
        app,
        [
            "workflows",
            "run",
            "repository-readiness",
            "--repo",
            str(repo),
            "--goal",
            "Require a clean repository.",
            "--require-clean",
            "--run-dir",
            str(blocked_run),
        ],
    )
    assert non_approval.exit_code == 1
    assert all(item.run_dir != blocked_run.resolve() for item in collect_pending_decisions())

    approval_packet = _write_approval_packet(
        run_dir=approval_run,
        transaction_node_id="publish-approved-release",
        path=tmp_path / "human-approval.json",
    )
    approved = runner.invoke(
        app,
        [
            "workflows",
            "approve",
            str(approval_run),
            "--approval-packet",
            str(approval_packet),
        ],
    )

    assert approved.exit_code == 0, approved.output
    assert collect_pending_decisions() == ()


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


def _write_approval_packet(*, run_dir: Path, transaction_node_id: str, path: Path) -> Path:
    gate_path = run_dir / "transactions" / transaction_node_id / "approval-gate-receipt.json"
    target = json.loads(gate_path.read_text(encoding="utf-8"))["expected_target"]
    payload = {
        "schema": "tau.human_approval_packet.v1",
        "approved": True,
        "actor": {"id": "human:test", "auth_method": "local-signature"},
        "action": "generic_dag_transaction_continue",
        "target": target,
        "reason": "approve exact deterministic continuation",
        "evidence": [str(gate_path)],
        "nonce": hashlib.sha256(json.dumps(target, sort_keys=True).encode("utf-8")).hexdigest(),
    }
    payload["signature"] = _local_signature(payload)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path


def _local_signature(payload: dict[str, object]) -> str:
    canonical = dict(payload)
    canonical.pop("signature", None)
    digest = hashlib.sha256(
        json.dumps(canonical, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return f"local-signature-sha256:{digest}"
