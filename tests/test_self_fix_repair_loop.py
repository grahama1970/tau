import subprocess
from pathlib import Path

from tau_coding.self_fix_repair_loop import write_coder_reviewer_repair_loop


def test_coder_reviewer_loop_changes_target_when_checks_pass(
    tmp_path: Path,
    monkeypatch,
) -> None:
    repo = _init_repo(tmp_path)
    target = repo / "target.py"
    target.write_text("ROUTES = {'reviewer'}\n", encoding="utf-8")
    subprocess.run(["git", "add", "target.py"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-m", "seed"], cwd=repo, check=True)

    monkeypatch.setattr(
        "tau_coding.self_fix_repair_loop._memory_preflight",
        lambda **kwargs: {
            "ok": True,
            "mocked": False,
            "live": True,
            "artifacts": {},
            "intent_call": {"ok": True},
            "recall_call": {"ok": True},
        },
    )
    monkeypatch.setattr(
        "tau_coding.self_fix_repair_loop._resolve_api_key",
        lambda explicit: {"api_key": "test-key", "source": "test"},
    )
    monkeypatch.setattr(
        "tau_coding.self_fix_repair_loop._call_scillm",
        lambda **kwargs: {
            "schema": "tau.self_fix_scillm_call_receipt.v1",
            "role": kwargs["role"],
            "status": "PASS",
            "mocked": False,
            "live": True,
            "http_status": 200,
            "content_excerpt": "ok",
        },
    )

    receipt = write_coder_reviewer_repair_loop(
        repo_root=repo,
        out_dir=tmp_path / "proof",
        request="Add battle-scorekeeper route.",
        target_file=Path("target.py"),
        find_text="ROUTES = {'reviewer'}",
        replace_text="ROUTES = {'reviewer', 'battle-scorekeeper'}",
        verification_commands=["python -m py_compile target.py"],
    )

    assert receipt["ok"] is True
    assert receipt["status"] == "PASS"
    assert "battle-scorekeeper" in target.read_text(encoding="utf-8")
    assert (tmp_path / "proof" / "cycle-001" / "coder" / "tau-subagent-receipt.json").exists()
    assert (tmp_path / "proof" / "cycle-001" / "reviewer" / "tau-subagent-receipt.json").exists()


def test_coder_reviewer_loop_restores_target_when_checks_fail(
    tmp_path: Path,
    monkeypatch,
) -> None:
    repo = _init_repo(tmp_path)
    target = repo / "target.py"
    target.write_text("VALUE = 'bad'\n", encoding="utf-8")
    subprocess.run(["git", "add", "target.py"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-m", "seed"], cwd=repo, check=True)

    monkeypatch.setattr(
        "tau_coding.self_fix_repair_loop._memory_preflight",
        lambda **kwargs: {
            "ok": True,
            "mocked": False,
            "live": True,
            "artifacts": {},
            "intent_call": {"ok": True},
            "recall_call": {"ok": True},
        },
    )
    monkeypatch.setattr(
        "tau_coding.self_fix_repair_loop._resolve_api_key",
        lambda explicit: {"api_key": "test-key", "source": "test"},
    )
    monkeypatch.setattr(
        "tau_coding.self_fix_repair_loop._call_scillm",
        lambda **kwargs: {
            "schema": "tau.self_fix_scillm_call_receipt.v1",
            "role": kwargs["role"],
            "status": "PASS",
            "mocked": False,
            "live": True,
            "http_status": 200,
            "content_excerpt": "ok",
        },
    )

    receipt = write_coder_reviewer_repair_loop(
        repo_root=repo,
        out_dir=tmp_path / "proof",
        request="Apply a change that fails checks.",
        target_file=Path("target.py"),
        find_text="VALUE = 'bad'",
        replace_text="VALUE = 'still bad'",
        verification_commands=["python -c 'raise SystemExit(7)'"],
        max_review_cycles=1,
    )

    assert receipt["ok"] is False
    assert receipt["status"] == "BLOCKED"
    assert receipt["rollback"]["attempted"] is True
    assert receipt["rollback"]["restored"] is True
    assert target.read_text(encoding="utf-8") == "VALUE = 'bad'\n"


def _init_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init"], cwd=repo, check=True, stdout=subprocess.DEVNULL)
    subprocess.run(
        ["git", "config", "user.email", "tau-test@example.invalid"],
        cwd=repo,
        check=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Tau Test"],
        cwd=repo,
        check=True,
    )
    return repo
