from __future__ import annotations

from pathlib import Path

from tau_coding.workflows.acceptance import (
    ACCEPTANCE_RECEIPT_SCHEMA,
    EXPECTED_WORKFLOW_IDS,
    verify_provider_live_acceptance_payload,
)


def test_verify_provider_live_acceptance_payload_accepts_complete_receipt() -> None:
    errors = verify_provider_live_acceptance_payload(_receipt())

    assert errors == []


def test_verify_provider_live_acceptance_payload_rejects_provider_false() -> None:
    receipt = _receipt()
    receipt["provider_live"] = False
    receipt["provider"]["terminal_evidence"]["provider_live"] = False  # type: ignore[index]

    errors = verify_provider_live_acceptance_payload(receipt)

    assert "provider_live_invalid" in errors
    assert "provider_provider_live_flag_invalid" in errors


def test_verify_acceptance_payload_rejects_missing_successful_provider_check() -> None:
    receipt = _receipt()
    receipt["provider"]["terminal_evidence"]["checks"] = [  # type: ignore[index]
        {"ok": True, "status_code": 401}
    ]

    errors = verify_provider_live_acceptance_payload(receipt)

    assert "provider_successful_check_missing" in errors


def test_verify_provider_live_acceptance_payload_rejects_missing_rung() -> None:
    receipt = _receipt()
    receipt["workflow_ids"] = list(EXPECTED_WORKFLOW_IDS[:-1])
    receipt["rungs"] = receipt["rungs"][:-1]

    errors = verify_provider_live_acceptance_payload(receipt)

    assert "workflow_ids_not_exact" in errors
    assert "rungs_missing_or_wrong_count" in errors


def test_verify_provider_live_acceptance_payload_rejects_stale_repo_commit(
    tmp_path: Path,
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    (repo / "README.md").write_text("# repo\n", encoding="utf-8")
    _git(repo, "add", ".")
    _git(
        repo,
        "-c",
        "user.name=Tau",
        "-c",
        "user.email=tau@example.invalid",
        "commit",
        "-qm",
        "fixture",
    )
    receipt = _receipt()
    receipt["source"]["commit"] = "0" * 40  # type: ignore[index]

    errors = verify_provider_live_acceptance_payload(receipt, repo=repo)

    assert "source_commit_not_current" in errors


def _receipt() -> dict[str, object]:
    return {
        "schema": ACCEPTANCE_RECEIPT_SCHEMA,
        "status": "PASS",
        "ok": True,
        "mocked": False,
        "live": True,
        "provider_live": True,
        "source": {
            "repo": "/repo",
            "commit": "a" * 40,
            "branch": "main",
            "clean": True,
            "status_porcelain": [],
        },
        "wheel": {
            "path": "/tmp/tau-0.1.0-py3-none-any.whl",
            "sha256": "sha256:" + "b" * 64,
        },
        "provider": {
            "terminal_evidence": {
                "status": "PASS",
                "ok": True,
                "mocked": False,
                "live": True,
                "provider_live": True,
                "checks": [{"ok": True, "status_code": 200}],
            }
        },
        "workflow_ids": list(EXPECTED_WORKFLOW_IDS),
        "rungs": [
            {
                "workflow_id": workflow_id,
                "terminal": True,
                "accepted_by_harness": True,
                "mocked": False,
                "live": True,
                "installed_entrypoint": True,
                "workflow_receipt_sha256": "sha256:" + "c" * 64,
            }
            for workflow_id in EXPECTED_WORKFLOW_IDS
        ],
    }


def _git(cwd: Path, *args: str) -> str:
    import subprocess

    return subprocess.run(
        ["git", *args],
        cwd=cwd,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
