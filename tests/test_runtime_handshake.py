from pathlib import Path

import pytest

from tau_coding.runtime_handshake import (
    RUNTIME_HANDSHAKE_SCHEMA,
    _command_capability,
    _wrapper_resolution_source,
    _wrapper_tau_root,
    write_runtime_handshake,
)


def test_runtime_handshake_passes_with_explicit_wrapper_and_tau_root(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo_root = Path(__file__).resolve().parents[1]
    wrapper_path = tmp_path / "agent-skills" / "skills" / "tau" / "run.sh"
    wrapper_path.parent.mkdir(parents=True)
    wrapper_path.write_text("#!/bin/sh\nexec tau \"$@\"\n", encoding="utf-8")
    monkeypatch.setenv("TAU_SKILL_WRAPPER", str(wrapper_path))
    monkeypatch.setenv("TAU_ROOT", str(repo_root))

    receipt = write_runtime_handshake(tmp_path / "runtime-handshake.json")

    assert receipt["schema"] == RUNTIME_HANDSHAKE_SCHEMA
    assert receipt["status"] == "PASS"
    assert receipt["mocked"] is False
    assert receipt["live"] is True
    assert receipt["operator_wrapper"]["path"] == str(wrapper_path.resolve())
    assert receipt["operator_wrapper"]["exists"] is True
    assert receipt["operator_wrapper"]["resolved_tau_root"] == str(repo_root.resolve())
    assert receipt["operator_wrapper"]["resolution_source"] == "env:TAU_ROOT"
    assert receipt["checks"] == {
        "tau_version_present": True,
        "capabilities_present": True,
        "wrapper_path_exists": True,
        "wrapper_resolves_same_checkout": True,
        "unsupported_planned_lanes_marked_unavailable": True,
        "no_direct_scillm_project_agent_shortcut": True,
    }
    assert receipt["failed_checks"] == []
    assert receipt["planned_unsupported_lanes"]["proof_index_build"] == {
        "command": "proof-index build",
        "available": False,
        "status": "UNAVAILABLE",
        "reason": "planned lane is not present in this checkout and is not claimed",
    }
    assert all(
        capability["status"] == "AVAILABLE"
        for capability in receipt["capabilities"].values()
    )


def test_runtime_handshake_blocks_missing_operator_wrapper(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo_root = Path(__file__).resolve().parents[1]
    missing_wrapper = tmp_path / "missing-run.sh"
    monkeypatch.setenv("TAU_SKILL_WRAPPER", str(missing_wrapper))
    monkeypatch.setenv("TAU_ROOT", str(repo_root))

    receipt = write_runtime_handshake(tmp_path / "runtime-handshake.json")

    assert receipt["status"] == "BLOCKED"
    assert receipt["operator_wrapper"]["exists"] is False
    assert receipt["checks"]["wrapper_path_exists"] is False
    assert receipt["failed_checks"] == ["wrapper_path_exists"]


def test_runtime_handshake_blocks_wrapper_bound_to_different_checkout(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    wrapper_path = tmp_path / "run.sh"
    wrapper_path.write_text("#!/bin/sh\n", encoding="utf-8")
    other_checkout = tmp_path / "other-tau"
    other_checkout.mkdir()
    monkeypatch.setenv("TAU_SKILL_WRAPPER", str(wrapper_path))
    monkeypatch.setenv("TAU_ROOT", str(other_checkout))

    receipt = write_runtime_handshake(tmp_path / "runtime-handshake.json")

    assert receipt["status"] == "BLOCKED"
    assert receipt["operator_wrapper"]["resolved_tau_root"] == str(
        other_checkout.resolve()
    )
    assert receipt["checks"]["wrapper_resolves_same_checkout"] is False
    assert receipt["failed_checks"] == ["wrapper_resolves_same_checkout"]


def test_runtime_handshake_discovers_special_cli_capabilities() -> None:
    cli_text = '''
@workflows_app.command("list")
def list_workflows() -> None:
    pass

if command == "dag-run":
    run_generic_dag()
'''

    workflows = _command_capability(
        name="workflows_list",
        command="workflows",
        cli_text=cli_text,
    )
    dag_run = _command_capability(
        name="dag_run",
        command="dag-run",
        cli_text=cli_text,
    )
    missing = _command_capability(
        name="doctor",
        command="doctor",
        cli_text=cli_text,
    )

    assert workflows == {
        "command": "workflows",
        "available": True,
        "status": "AVAILABLE",
    }
    assert dag_run == {
        "command": "dag-run",
        "available": True,
        "status": "AVAILABLE",
    }
    assert missing == {
        "command": "doctor",
        "available": False,
        "status": "UNAVAILABLE",
    }


def test_runtime_handshake_uses_current_checkout_when_tau_root_is_unset(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo_root = Path(__file__).resolve().parents[1]
    monkeypatch.delenv("TAU_ROOT", raising=False)
    monkeypatch.chdir(repo_root)

    assert _wrapper_tau_root() == repo_root.resolve()
    assert _wrapper_resolution_source() == "cwd"
