import json
import subprocess
from pathlib import Path

from tau_coding.self_fix_repair_loop import _run_verification_commands
from tau_coding.self_fix_ticket_repair import extract_repair_request, run_ticket_repair


def test_ticket_repair_rolls_back_target_when_commit_fails(
    tmp_path: Path,
    monkeypatch,
) -> None:
    repo = _init_repo(tmp_path)
    target = repo / "target.py"
    target.write_text("VALUE = 'bug'\n", encoding="utf-8")
    subprocess.run(["git", "add", "target.py"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-m", "seed"], cwd=repo, check=True)
    checkpoint = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=repo,
        check=True,
        text=True,
        capture_output=True,
    ).stdout.strip()
    issue_payload = {
        "number": 77,
        "title": "Probe rollback",
        "url": "https://github.com/grahama1970/tau/issues/77",
        "authorAssociation": "OWNER",
        "body": """
```json
{
  "schema": "tau.self_fix_repair_request.v1",
  "request": "Repair target value.",
  "target_file": "target.py",
  "find_text": "VALUE = 'bug'",
  "replace_text": "VALUE = 'fixed'",
  "verification_commands": ["python -m py_compile target.py"]
}
```
""",
    }

    def fake_loop(**kwargs):
        (repo / "target.py").write_text("VALUE = 'fixed'\n", encoding="utf-8")
        return {
            "ok": True,
            "checkpoint": {"head": checkpoint},
            "cycles": [
                {
                    "coder": {"scillm_call": str(tmp_path / "coder.json")},
                    "reviewer": {"scillm_call": str(tmp_path / "reviewer.json")},
                }
            ],
        }

    monkeypatch.setattr(
        "tau_coding.self_fix_ticket_repair.write_coder_reviewer_repair_loop", fake_loop
    )
    monkeypatch.setattr(
        "tau_coding.self_fix_ticket_repair._commit_and_push_repair",
        lambda *args, **kwargs: {
            "ok": False,
            "commands": [
                {
                    "ok": False,
                    "command": ["git", "commit", "-m", "fail"],
                    "exit_code": 1,
                    "stdout": "",
                    "stderr": "synthetic commit failure",
                }
            ],
        },
    )

    receipt = run_ticket_repair(
        repo="grahama1970/tau",
        issue_payload=issue_payload,
        repo_root=repo,
        receipt_dir=tmp_path / "receipt",
        memory_base_url="http://127.0.0.1:8601",
        scillm_base_url="http://127.0.0.1:4001",
        model="gpt-5.5",
        active_goal_hash=None,
        apply_github=False,
    )

    assert receipt["ok"] is False
    assert receipt["error"] == "commit_or_push_failed"
    assert receipt["rollback"]["attempted"] is True
    assert receipt["rollback"]["restored"] is True
    assert target.read_text(encoding="utf-8") == "VALUE = 'bug'\n"
    assert _tracked_status(repo) == ""
    written = json.loads((tmp_path / "receipt" / "ticket-repair-receipt.json").read_text())
    assert written["rollback"]["restored"] is True


def test_ticket_repair_rejects_non_allowlisted_verification_command() -> None:
    body = """
```json
{
  "schema": "tau.self_fix_repair_request.v1",
  "request": "Repair target value.",
  "target_file": "target.py",
  "find_text": "VALUE = 'bug'",
  "replace_text": "VALUE = 'fixed'",
  "verification_commands": ["python -m py_compile target.py; touch owned"]
}
```
"""

    assert extract_repair_request(body) is None


def test_ticket_repair_rejects_untrusted_author_before_loop(tmp_path: Path, monkeypatch) -> None:
    repo = _init_repo(tmp_path)
    target = repo / "target.py"
    target.write_text("VALUE = 'bug'\n", encoding="utf-8")
    subprocess.run(["git", "add", "target.py"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-m", "seed"], cwd=repo, check=True)

    def fake_loop(**kwargs):
        raise AssertionError("repair loop must not run for an untrusted issue author")

    monkeypatch.setattr(
        "tau_coding.self_fix_ticket_repair.write_coder_reviewer_repair_loop", fake_loop
    )

    receipt = run_ticket_repair(
        repo="grahama1970/tau",
        issue_payload=_issue_payload(author_association="NONE"),
        repo_root=repo,
        receipt_dir=tmp_path / "receipt",
        memory_base_url="http://127.0.0.1:8601",
        scillm_base_url="http://127.0.0.1:4001",
        model="gpt-5.5",
        active_goal_hash=None,
        apply_github=False,
    )

    assert receipt["ok"] is False
    assert receipt["error"] == "untrusted_issue_author"
    assert target.read_text(encoding="utf-8") == "VALUE = 'bug'\n"


def test_ticket_repair_rejects_issue_edited_after_routing_label(
    tmp_path: Path, monkeypatch
) -> None:
    repo = _init_repo(tmp_path)
    target = repo / "target.py"
    target.write_text("VALUE = 'bug'\n", encoding="utf-8")
    subprocess.run(["git", "add", "target.py"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-m", "seed"], cwd=repo, check=True)

    def fake_loop(**kwargs):
        raise AssertionError("repair loop must not run after post-routing body edit")

    monkeypatch.setattr(
        "tau_coding.self_fix_ticket_repair.write_coder_reviewer_repair_loop", fake_loop
    )

    payload = _issue_payload(author_association="OWNER")
    payload["bodyEditedAfterRoutingLabel"] = True
    receipt = run_ticket_repair(
        repo="grahama1970/tau",
        issue_payload=payload,
        repo_root=repo,
        receipt_dir=tmp_path / "receipt",
        memory_base_url="http://127.0.0.1:8601",
        scillm_base_url="http://127.0.0.1:4001",
        model="gpt-5.5",
        active_goal_hash=None,
        apply_github=False,
    )

    assert receipt["ok"] is False
    assert receipt["error"] == "issue_body_edited_after_routing_label"
    assert target.read_text(encoding="utf-8") == "VALUE = 'bug'\n"


def test_issue_derived_verification_commands_use_argv_not_shell(
    tmp_path: Path, monkeypatch
) -> None:
    calls = []

    class Completed:
        returncode = 0
        stdout = "ok"
        stderr = ""

    def fake_run(command, **kwargs):
        calls.append((command, kwargs))
        assert kwargs.get("shell") is not True
        return Completed()

    monkeypatch.setattr("tau_coding.self_fix_repair_loop.subprocess.run", fake_run)

    results = _run_verification_commands(
        tmp_path,
        [["python", "-m", "py_compile", "target.py"]],
        out_dir=tmp_path / "verification",
    )

    assert results[0]["command"] == ["python", "-m", "py_compile", "target.py"]
    assert calls[0][0] == ["python", "-m", "py_compile", "target.py"]


def test_ticket_repair_blocks_github_close_when_redaction_receipt_fails(
    tmp_path: Path, monkeypatch
) -> None:
    repo = _init_repo(tmp_path)
    target = repo / "target.py"
    target.write_text("VALUE = 'bug'\n", encoding="utf-8")
    subprocess.run(["git", "add", "target.py"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-m", "seed"], cwd=repo, check=True)
    checkpoint = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=repo,
        check=True,
        text=True,
        capture_output=True,
    ).stdout.strip()
    helper_calls = []

    def fake_loop(**kwargs):
        (repo / "target.py").write_text("VALUE = 'fixed'\n", encoding="utf-8")
        return {
            "ok": True,
            "checkpoint": {"head": checkpoint},
            "cycles": [
                {
                    "coder": {"scillm_call": str(tmp_path / "coder.json")},
                    "reviewer": {"scillm_call": str(tmp_path / "reviewer.json")},
                }
            ],
        }

    def fake_commit(*args, **kwargs):
        return {"ok": True, "commands": [], "commit": "abc123"}

    def fake_redaction(*args, **kwargs):
        return {
            "projection_path": str(tmp_path / "projection.json"),
            "redacted_projection_path": str(tmp_path / "projection.redacted.json"),
            "receipt_path": str(tmp_path / "redaction.json"),
            "redacted_proof_path": str(tmp_path / "proof.redacted.md"),
            "receipt": {"ok": False, "status": "BLOCKED"},
        }

    def fake_ticket_helper(args):
        helper_calls.append(args)
        return {"ok": True, "command": ["ticket", *args], "exit_code": 0}

    monkeypatch.setattr(
        "tau_coding.self_fix_ticket_repair.write_coder_reviewer_repair_loop",
        fake_loop,
    )
    monkeypatch.setattr(
        "tau_coding.self_fix_ticket_repair._commit_and_push_repair",
        fake_commit,
    )
    monkeypatch.setattr(
        "tau_coding.self_fix_ticket_repair._write_self_fix_github_redaction",
        fake_redaction,
    )
    monkeypatch.setattr(
        "tau_coding.self_fix_ticket_repair._run_ticket_helper",
        fake_ticket_helper,
    )

    receipt = run_ticket_repair(
        repo="grahama1970/tau",
        issue_payload=_issue_payload(author_association="OWNER"),
        repo_root=repo,
        receipt_dir=tmp_path / "receipt",
        memory_base_url="http://127.0.0.1:8601",
        scillm_base_url="http://127.0.0.1:4001",
        model="gpt-5.5",
        active_goal_hash=None,
        apply_github=True,
    )

    assert receipt["ok"] is False
    assert receipt["error"] == "github_redaction_failed"
    assert [call[0] for call in helper_calls] == ["lease"]


def _init_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init"], cwd=repo, check=True, stdout=subprocess.DEVNULL)
    subprocess.run(
        ["git", "config", "user.email", "tau-test@example.invalid"], cwd=repo, check=True
    )
    subprocess.run(["git", "config", "user.name", "Tau Test"], cwd=repo, check=True)
    return repo


def _tracked_status(repo: Path) -> str:
    return subprocess.run(
        ["git", "status", "--porcelain", "--untracked-files=no"],
        cwd=repo,
        check=True,
        text=True,
        capture_output=True,
    ).stdout.strip()


def _issue_payload(*, author_association: str) -> dict[str, object]:
    return {
        "number": 77,
        "title": "Probe repair",
        "url": "https://github.com/grahama1970/tau/issues/77",
        "authorAssociation": author_association,
        "body": """
```json
{
  "schema": "tau.self_fix_repair_request.v1",
  "request": "Repair target value.",
  "target_file": "target.py",
  "find_text": "VALUE = 'bug'",
  "replace_text": "VALUE = 'fixed'",
  "verification_commands": ["python -m py_compile target.py"]
}
```
""",
    }
