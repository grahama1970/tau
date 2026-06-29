import json
import os
from pathlib import Path

from tau_coding.cli import project_agent_self_fix_poll_command
from tau_coding.self_fix_ticket_repair import extract_repair_request


def test_self_fix_poll_writes_idle_receipt_for_empty_queue(
    tmp_path: Path,
    monkeypatch,
) -> None:
    _install_fake_gh(tmp_path, monkeypatch, [])

    ok = project_agent_self_fix_poll_command(
        repo="grahama1970/tau",
        receipt_dir=tmp_path / "receipts",
        agents_root=tmp_path / "agents",
        command_spec_root=None,
        active_goal_hash=None,
        memory_base_url="http://127.0.0.1:8601",
        max_steps=1,
        required_labels=("agent-work", "agent:coder"),
        issue_limit=30,
        dispatch=False,
    )
    receipt = json.loads((tmp_path / "receipts" / "self-fix-poll-receipt.json").read_text())

    assert ok is True
    assert receipt["schema"] == "tau.self_fix_poll_receipt.v1"
    assert receipt["status"] == "IDLE"
    assert receipt["mocked"] is False
    assert receipt["live"] is True
    assert receipt["open_issue_count"] == 0
    assert receipt["eligible_issue_count"] == 0
    assert receipt["selected_issue"] is None
    assert receipt["artifacts"]["open_issues"].endswith("open-issues.json")


def test_self_fix_poll_selects_first_eligible_issue_without_dispatch(
    tmp_path: Path,
    monkeypatch,
) -> None:
    _install_fake_gh(
        tmp_path,
        monkeypatch,
        [
            {
                "number": 41,
                "title": "Not routed",
                "state": "OPEN",
                "url": "https://github.com/grahama1970/tau/issues/41",
                "labels": [{"name": "needs-human"}],
            },
            {
                "number": 42,
                "title": "Routed repair",
                "state": "OPEN",
                "url": "https://github.com/grahama1970/tau/issues/42",
                "labels": [{"name": "agent-work"}, {"name": "agent:coder"}],
            },
        ],
    )

    ok = project_agent_self_fix_poll_command(
        repo="grahama1970/tau",
        receipt_dir=tmp_path / "receipts",
        agents_root=tmp_path / "agents",
        command_spec_root=None,
        active_goal_hash=None,
        memory_base_url="http://127.0.0.1:8601",
        max_steps=1,
        required_labels=("agent-work", "agent:coder"),
        issue_limit=30,
        dispatch=False,
    )
    receipt = json.loads((tmp_path / "receipts" / "self-fix-poll-receipt.json").read_text())

    assert ok is True
    assert receipt["status"] == "READY"
    assert receipt["open_issue_count"] == 2
    assert receipt["eligible_issue_count"] == 1
    assert receipt["selected_issue"]["number"] == 42
    assert receipt["selected_issue"]["eligibility"]["matched_labels"] == [
        "agent-work",
        "agent:coder",
    ]


def test_extract_repair_request_from_issue_body() -> None:
    body = """
## Required repair

```json
{
  "schema": "tau.self_fix_repair_request.v1",
  "request": "Change the probe value.",
  "target_file": "tests/fixtures/self_fix_ticket_probe.py",
  "find_text": "STATUS = 'bug'",
  "replace_text": "STATUS = 'fixed'",
  "verification_commands": ["python -m py_compile tests/fixtures/self_fix_ticket_probe.py"]
}
```
"""

    request = extract_repair_request(body)

    assert request is not None
    assert request["target_file"] == "tests/fixtures/self_fix_ticket_probe.py"
    assert request["verification_commands"] == [
        "python -m py_compile tests/fixtures/self_fix_ticket_probe.py"
    ]


def _install_fake_gh(tmp_path: Path, monkeypatch, issues: list[dict[str, object]]) -> None:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    fake_gh = bin_dir / "gh"
    fake_gh.write_text(
        "\n".join(
            [
                "#!/usr/bin/env python",
                "import json",
                f"print(json.dumps({issues!r}))",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    fake_gh.chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}")
