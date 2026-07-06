import hashlib
import json
from pathlib import Path

from typer.testing import CliRunner

from tau_coding.cli import app
from tau_coding.github_apply_policy import write_github_apply_policy_receipt
from tau_coding.github_read_schemes import GITHUB_READ_RECEIPT_SCHEMA, write_github_read_receipt


def test_github_read_issue_scheme_is_read_only(tmp_path: Path) -> None:
    receipt = write_github_read_receipt(
        uri="issue://grahama1970/tau/67",
        output_path=tmp_path / "github-read-receipt.json",
    )

    assert receipt["schema"] == GITHUB_READ_RECEIPT_SCHEMA
    assert receipt["status"] == "PASS"
    assert receipt["read_only"] is True
    assert receipt["mutation_allowed"] is False
    assert receipt["parsed"] == {
        "identifier": "67",
        "kind": "issue",
        "name": "tau",
        "owner": "grahama1970",
        "repo": "grahama1970/tau",
    }
    assert receipt["suggested_gh_command"][:4] == ["gh", "issue", "view", "67"]


def test_github_read_pr_diff_scheme_is_read_only(tmp_path: Path) -> None:
    receipt = write_github_read_receipt(
        uri="diff://grahama1970/tau/pull/123",
        output_path=tmp_path / "github-read-receipt.json",
    )

    assert receipt["status"] == "PASS"
    assert receipt["read_only"] is True
    assert receipt["parsed"]["kind"] == "diff"
    assert receipt["suggested_gh_command"] == [
        "gh",
        "pr",
        "diff",
        "123",
        "--repo",
        "grahama1970/tau",
    ]


def test_github_read_commit_scheme_is_read_only(tmp_path: Path) -> None:
    receipt = write_github_read_receipt(
        uri="commit://grahama1970/tau/abc123",
        output_path=tmp_path / "github-read-receipt.json",
    )

    assert receipt["status"] == "PASS"
    assert receipt["read_only"] is True
    assert receipt["parsed"]["kind"] == "commit"
    assert "Any GitHub mutation is allowed." in receipt["proof_scope"]["does_not_prove"]


def test_github_read_blocks_unsupported_uri(tmp_path: Path) -> None:
    receipt = write_github_read_receipt(
        uri="comment://grahama1970/tau/67",
        output_path=tmp_path / "github-read-receipt.json",
    )

    assert receipt["status"] == "BLOCKED"
    assert "unsupported_github_read_uri" in receipt["alert_codes"]
    assert receipt["mutation_allowed"] is False


def test_github_read_zero_trust_blocks_missing_policy_boundary(tmp_path: Path) -> None:
    receipt = write_github_read_receipt(
        uri="issue://grahama1970/tau/67",
        output_path=tmp_path / "github-read-receipt.json",
        zero_trust=True,
    )

    assert receipt["status"] == "BLOCKED"
    assert "missing_policy_profile" in receipt["alert_codes"]
    assert "missing_data_boundary" in receipt["alert_codes"]
    assert receipt["mutation_allowed"] is False


def test_github_read_zero_trust_accepts_policy_boundary(tmp_path: Path) -> None:
    receipt = write_github_read_receipt(
        uri="issue://grahama1970/tau/67",
        output_path=tmp_path / "github-read-receipt.json",
        zero_trust=True,
        policy_profile={"schema": "tau.policy_profile.v1", "profile_id": "test"},
        data_boundary={"schema": "tau.data_boundary.v1", "classification": "public"},
    )

    assert receipt["status"] == "PASS"
    assert receipt["zero_trust"] is True
    assert receipt["policy_profile"]["profile_id"] == "test"
    assert receipt["data_boundary"]["classification"] == "public"


def test_github_read_execute_runs_read_only_command_and_records_logs(tmp_path: Path) -> None:
    gh_bin = _write_fake_gh(tmp_path)
    out = tmp_path / "github-read-receipt.json"

    receipt = write_github_read_receipt(
        uri="issue://grahama1970/tau/67",
        output_path=out,
        execute=True,
        gh_bin=str(gh_bin),
    )

    assert receipt["status"] == "PASS"
    assert receipt["live"] is True
    assert receipt["mutation_allowed"] is False
    assert receipt["execution"]["command_executed"] is True
    assert receipt["execution"]["command"][1:4] == ["issue", "view", "67"]
    assert receipt["execution"]["exit_code"] == 0
    stdout_path = Path(receipt["execution"]["stdout_path"])
    stderr_path = Path(receipt["execution"]["stderr_path"])
    assert json.loads(stdout_path.read_text(encoding="utf-8"))["fake_gh"] is True
    assert stderr_path.read_text(encoding="utf-8") == ""


def test_github_read_execute_skips_invalid_uri(tmp_path: Path) -> None:
    gh_bin = _write_fake_gh(tmp_path)
    marker = tmp_path / "fake-gh-called.json"

    receipt = write_github_read_receipt(
        uri="comment://grahama1970/tau/67",
        output_path=tmp_path / "github-read-receipt.json",
        execute=True,
        gh_bin=str(gh_bin),
    )

    assert receipt["status"] == "BLOCKED"
    assert receipt["execution"]["execute_requested"] is True
    assert receipt["execution"]["command_executed"] is False
    assert "unsupported_github_read_uri" in receipt["alert_codes"]
    assert not marker.exists()


def test_cli_github_read_writes_receipt(tmp_path: Path) -> None:
    out = tmp_path / "github-read-receipt.json"

    result = CliRunner().invoke(
        app,
        [
            "github-read",
            "--uri",
            "pr://grahama1970/tau/12",
            "--out",
            str(out),
        ],
    )

    payload = json.loads(result.output)
    assert result.exit_code == 0
    assert payload == json.loads(out.read_text(encoding="utf-8"))
    assert payload["schema"] == GITHUB_READ_RECEIPT_SCHEMA
    assert payload["parsed"]["kind"] == "pr"


def test_cli_github_read_execute_writes_receipt_and_logs(tmp_path: Path) -> None:
    gh_bin = _write_fake_gh(tmp_path)
    out = tmp_path / "github-read-receipt.json"

    result = CliRunner().invoke(
        app,
        [
            "github-read",
            "--uri",
            "diff://grahama1970/tau/pull/123",
            "--out",
            str(out),
            "--execute",
            "--gh-bin",
            str(gh_bin),
            "--timeout-s",
            "5",
        ],
    )

    payload = json.loads(result.output)
    assert result.exit_code == 0
    assert payload == json.loads(out.read_text(encoding="utf-8"))
    assert payload["live"] is True
    assert payload["execution"]["command"][1:4] == ["pr", "diff", "123"]
    assert Path(payload["execution"]["stdout_path"]).exists()


def test_cli_github_read_zero_trust_missing_boundary_exits_blocked(
    tmp_path: Path,
) -> None:
    out = tmp_path / "github-read-receipt.json"

    result = CliRunner().invoke(
        app,
        [
            "github-read",
            "--uri",
            "pr://grahama1970/tau/12",
            "--out",
            str(out),
            "--zero-trust",
        ],
    )

    payload = json.loads(result.output)
    assert result.exit_code == 1
    assert payload == json.loads(out.read_text(encoding="utf-8"))
    assert payload["status"] == "BLOCKED"
    assert "missing_policy_profile" in payload["alert_codes"]
    assert "missing_data_boundary" in payload["alert_codes"]


def test_github_mutation_requires_apply_policy_receipt(tmp_path: Path) -> None:
    paths = _write_policy_fixture(tmp_path)

    receipt = write_github_apply_policy_receipt(
        projection_path=paths["projection"],
        policy_path=paths["policy"],
        receipt_path=paths["receipt"],
    )

    assert receipt["status"] == "BLOCKED"
    assert "approval receipt is required by policy" in receipt["errors"]
    assert "redaction receipt is required by policy" in receipt["errors"]


def test_github_public_comment_requires_redaction_receipt(tmp_path: Path) -> None:
    paths = _write_policy_fixture(tmp_path)

    receipt = write_github_apply_policy_receipt(
        projection_path=paths["projection"],
        policy_path=paths["policy"],
        receipt_path=paths["receipt"],
        approval_receipt_path=paths["approval"],
        preflight_ready=True,
    )

    assert receipt["status"] == "BLOCKED"
    assert "redaction receipt is required by policy" in receipt["errors"]


def _write_policy_fixture(tmp_path: Path) -> dict[str, Path]:
    projection_path = tmp_path / "projection.json"
    redacted_projection_path = tmp_path / "projection.redacted.json"
    policy_path = tmp_path / "github-apply-policy.json"
    redaction_path = tmp_path / "github-redaction-receipt.json"
    approval_path = tmp_path / "approval-gate-receipt.json"
    receipt_path = tmp_path / "github-apply-policy-receipt.json"
    projection = {
        "schema": "tau.agent_handoff_projection_receipt.v1",
        "ok": True,
        "target": {"repo": "grahama1970/tau", "target": "issue#67"},
        "comment": {"body": "## Tau Agent Handoff\n"},
        "labels": {"add": ["agent-work"], "remove": []},
        "errors": [],
    }
    redacted_projection_path.write_text(
        json.dumps(projection, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    redacted_hash = _sha256(redacted_projection_path)
    policy = {
        "schema": "tau.github_apply_policy.v1",
        "allowed_repos": ["grahama1970/tau"],
        "allowed_actions": ["comment", "label"],
        "denied_actions": ["close", "merge", "push", "release"],
        "requires_approval_packet": True,
        "requires_preflight": True,
        "requires_redaction": True,
    }
    redaction = {
        "schema": "tau.github_projection_redaction_receipt.v1",
        "ok": True,
        "status": "PASS",
        "redacted_projection_path": str(redacted_projection_path),
        "redacted_projection_sha256": f"sha256:{redacted_hash}",
    }
    approval = {
        "schema": "tau.approval_gate_receipt.v1",
        "ok": True,
        "status": "PASS",
        "approved": True,
        "requested_action": "github_apply",
        "packet_summary": {"target_id": "grahama1970/tau:issue#67"},
    }
    projection_path.write_text(
        json.dumps(projection, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    policy_path.write_text(json.dumps(policy, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    redaction_path.write_text(
        json.dumps(redaction, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    approval_path.write_text(
        json.dumps(approval, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return {
        "approval": approval_path,
        "policy": policy_path,
        "projection": projection_path,
        "receipt": receipt_path,
        "redaction": redaction_path,
    }


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_fake_gh(tmp_path: Path) -> Path:
    gh_bin = tmp_path / "fake-gh"
    marker = tmp_path / "fake-gh-called.json"
    gh_bin.write_text(
        "\n".join(
            [
                "#!/usr/bin/env python3",
                "import json",
                "import pathlib",
                "import sys",
                f"marker = pathlib.Path({str(marker)!r})",
                "payload = {'fake_gh': True, 'args': sys.argv[1:]}",
                "marker.write_text(json.dumps(payload, sort_keys=True) + '\\n')",
                "print(json.dumps(payload, sort_keys=True))",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    gh_bin.chmod(0o755)
    return gh_bin
