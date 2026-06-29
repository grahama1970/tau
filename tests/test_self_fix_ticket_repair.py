import json
import subprocess
from pathlib import Path

from tau_coding.self_fix_ticket_repair import run_ticket_repair


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

    monkeypatch.setattr("tau_coding.self_fix_ticket_repair.write_coder_reviewer_repair_loop", fake_loop)
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


def _init_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init"], cwd=repo, check=True, stdout=subprocess.DEVNULL)
    subprocess.run(["git", "config", "user.email", "tau-test@example.invalid"], cwd=repo, check=True)
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
