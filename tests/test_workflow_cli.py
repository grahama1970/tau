import hashlib
import json
import subprocess
from pathlib import Path

from typer.testing import CliRunner

from tau_coding.cli import app


def test_workflows_list_and_describe() -> None:
    runner = CliRunner()

    listed = runner.invoke(app, ["workflows", "list", "--json"])
    described = runner.invoke(app, ["workflows", "describe", "repository-readiness", "--json"])

    assert listed.exit_code == 0, listed.output
    assert [workflow["workflow_id"] for workflow in json.loads(listed.stdout)["workflows"]] == [
        "repository-readiness",
        "tau-operator-reference",
        "repository-evidence-map",
        "approved-release-bundle",
        "durable-repository-qualification",
    ]
    assert [workflow["rung"] for workflow in json.loads(listed.stdout)["workflows"]] == [
        1,
        2,
        3,
        4,
        5,
    ]
    assert described.exit_code == 0, described.output
    assert json.loads(described.stdout)["topology"] == "LINEAR"


def test_workflows_operator_reference_description_and_run_help() -> None:
    runner = CliRunner()
    described = runner.invoke(app, ["workflows", "describe", "tau-operator-reference", "--json"])
    help_probe = subprocess.run(
        ["tau", "workflows", "run", "--help"],
        check=False,
        capture_output=True,
        text=True,
    )

    assert described.exit_code == 0, described.output
    assert json.loads(described.stdout)["topology"] == "MULTI_STEP_SEQUENTIAL"
    assert help_probe.returncode == 0, help_probe.stderr
    assert "--required-workflow" in help_probe.stdout
    assert "--require-tests" in help_probe.stdout


def test_workflows_describes_evidence_map() -> None:
    result = CliRunner().invoke(app, ["workflows", "describe", "repository-evidence-map", "--json"])

    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout)["topology"] == "FAN_OUT_FAN_IN"


def test_workflows_approve_and_resume_release_bundle(tmp_path: Path) -> None:
    repo = _git_repo(tmp_path / "repo")
    run_dir = tmp_path / "run"
    publish_path = tmp_path / "published"
    runner = CliRunner()
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
            str(run_dir),
        ],
    )
    blocked_approval = runner.invoke(app, ["workflows", "approve", str(run_dir)])
    approval_path = _write_approval_packet(
        run_dir=run_dir,
        transaction_node_id="publish-approved-release",
        path=tmp_path / "human-approval.json",
    )
    approved = runner.invoke(
        app,
        [
            "workflows",
            "approve",
            str(run_dir),
            "--approval-packet",
            str(approval_path),
        ],
    )
    resumed = runner.invoke(app, ["workflows", "resume", str(run_dir)])

    assert first.exit_code == 1
    assert json.loads(first.stdout)["status"] == "BLOCKED"
    assert blocked_approval.exit_code == 1
    assert json.loads(blocked_approval.stdout)["errors"] == ["approval_packet_required"]
    assert approved.exit_code == 0, approved.output
    assert json.loads(approved.stdout)["status"] == "PASS"
    assert resumed.exit_code == 0, resumed.output
    assert json.loads(resumed.stdout)["result"]["status"] == "APPROVED"
    assert (publish_path / "approved-release-bundle.json").is_file()


def test_workflows_repair_approve_and_resume_durable_qualification(
    tmp_path: Path,
) -> None:
    repo = _git_repo(tmp_path / "repo")
    run_dir = tmp_path / "run"
    publish_path = tmp_path / "published"
    runner = CliRunner()
    first = runner.invoke(
        app,
        [
            "workflows",
            "run",
            "durable-repository-qualification",
            "--repo",
            str(repo),
            "--goal",
            "Qualify this repository durably.",
            "--publish-path",
            str(publish_path),
            "--run-dir",
            str(run_dir),
            "--inject-test-branch-failure",
            "--step-delay-seconds",
            "0.01",
        ],
    )
    blocked_repair = runner.invoke(
        app,
        ["workflows", "repair", str(run_dir), "--node", "qualify-tests"],
    )
    repair_approval_path = _write_repair_approval_packet(
        run_dir=run_dir,
        node_id="qualify-tests",
        path=tmp_path / "human-repair-approval.json",
    )
    repaired = runner.invoke(
        app,
        [
            "workflows",
            "repair",
            str(run_dir),
            "--node",
            "qualify-tests",
            "--approval-packet",
            str(repair_approval_path),
        ],
    )
    resumed = runner.invoke(app, ["workflows", "resume", str(run_dir)])
    blocked_approval = runner.invoke(app, ["workflows", "approve", str(run_dir)])
    approval_path = _write_approval_packet(
        run_dir=run_dir,
        transaction_node_id="publish-qualification",
        path=tmp_path / "human-qualification-approval.json",
    )
    approved = runner.invoke(
        app,
        [
            "workflows",
            "approve",
            str(run_dir),
            "--approval-packet",
            str(approval_path),
        ],
    )
    final = runner.invoke(app, ["workflows", "resume", str(run_dir)])

    assert first.exit_code == 1
    assert blocked_repair.exit_code == 1
    assert json.loads(blocked_repair.stdout)["errors"] == ["approval_packet_required"]
    assert repaired.exit_code == 0, repaired.output
    assert resumed.exit_code == 1
    assert json.loads(resumed.stdout)["status"] == "BLOCKED"
    assert blocked_approval.exit_code == 1
    assert json.loads(blocked_approval.stdout)["errors"] == ["approval_packet_required"]
    assert approved.exit_code == 0, approved.output
    assert final.exit_code == 0, final.output
    assert json.loads(final.stdout)["result"]["status"] == "QUALIFIED"
    assert (publish_path / "publication-ledger.json").is_file()


def test_workflows_run_executes_packaged_definition(tmp_path: Path) -> None:
    repo = _git_repo(tmp_path / "repo")
    run_dir = tmp_path / "run"
    runner = CliRunner()

    result = runner.invoke(
        app,
        [
            "workflows",
            "run",
            "repository-readiness",
            "--repo",
            str(repo),
            "--goal",
            "Determine whether this checkout is ready for focused work.",
            "--require-clean",
            "--run-dir",
            str(run_dir),
        ],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["workflow_id"] == "repository-readiness"
    assert payload["result"]["status"] == "READY"


def test_workflows_run_dispatches_operator_reference(tmp_path: Path) -> None:
    repo = Path(__file__).resolve().parents[1]
    run_dir = tmp_path / "operator-reference"
    runner = CliRunner()

    result = runner.invoke(
        app,
        [
            "workflows",
            "run",
            "tau-operator-reference",
            "--repo",
            str(repo),
            "--run-dir",
            str(run_dir),
        ],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["workflow_id"] == "tau-operator-reference"
    assert payload["result"]["status"] == "ACCEPTED"


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
    _write_signed_packet(
        path,
        action="generic_dag_transaction_continue",
        target=target,
        reason="approve exact deterministic continuation",
        evidence=[str(gate_path)],
    )
    return path


def _write_repair_approval_packet(*, run_dir: Path, node_id: str, path: Path) -> Path:
    request = json.loads(
        (run_dir / "input" / "durable-qualification-request.json").read_text(encoding="utf-8")
    )
    target = {
        "id": f"durable-repository-qualification:{node_id}:{request['goal']['goal_hash']}",
        "workflow_id": "durable-repository-qualification",
        "node_id": node_id,
        "goal_hash": str(request["goal"]["goal_hash"]),
    }
    _write_signed_packet(
        path,
        action="workflow_repair",
        target=target,
        reason="approve exact targeted repair",
        evidence=[str(run_dir / "receipts" / f"{node_id}.json")],
    )
    return path


def _write_signed_packet(
    path: Path,
    *,
    action: str,
    target: object,
    reason: str,
    evidence: list[str],
) -> None:
    payload = {
        "schema": "tau.human_approval_packet.v1",
        "approved": True,
        "actor": {"id": "human:test", "auth_method": "local-signature"},
        "action": action,
        "target": target,
        "reason": reason,
        "evidence": evidence,
        "nonce": hashlib.sha256(json.dumps(target, sort_keys=True).encode("utf-8")).hexdigest(),
    }
    payload["signature"] = _local_signature(payload)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _local_signature(payload: dict[str, object]) -> str:
    canonical = dict(payload)
    canonical.pop("signature", None)
    digest = hashlib.sha256(
        json.dumps(canonical, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return f"local-signature-sha256:{digest}"
