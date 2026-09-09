from __future__ import annotations

from pathlib import Path

import pytest

from tau_coding.dag_runtime.triage_error_bridge import classify_tau_failure


def test_classify_tau_failure_mints_code_when_triage_runner_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runner = tmp_path / "run.sh"
    runner.write_text("#!/usr/bin/env bash\necho classifier exploded >&2\nexit 7\n")
    runner.chmod(0o755)
    monkeypatch.setenv("TAU_TRIAGE_ERROR_RUN_SH", str(runner))

    result = classify_tau_failure("internal tau failure", layer="tau")

    assert result["ambiguous"] is True
    assert result["code"].startswith("tau_triage_classification_failed_unclassified_")
    assert result["layer"] == "tau"
    assert "classifier exploded" in result["cause"]


def test_classify_tau_failure_uses_native_fallback_without_runner(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("TAU_TRIAGE_ERROR_RUN_SH", raising=False)
    monkeypatch.delenv("TAU_SKILLS_ROOT", raising=False)
    monkeypatch.delenv("TAU_AGENT_SKILLS_ROOT", raising=False)

    result = classify_tau_failure("node missing required evidence", layer="dag-runtime")

    assert result == {
        "code": "tau_project_dag_missing_required_evidence",
        "layer": "dag-runtime",
        "cause": "DAG node failed because required evidence was missing.",
        "next_command": "rerun the same semantic node after attaching required evidence",
        "ambiguous": False,
        "classifier_kind": "NATIVE_FALLBACK",
        "classifier_version": "tau.dag_runtime.triage_error_bridge.v1",
    }


def test_classify_tau_failure_uses_configured_skills_root(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runner = tmp_path / "skills" / "triage-error" / "run.sh"
    runner.parent.mkdir(parents=True)
    runner.write_text(
        "#!/usr/bin/env python3\n"
        "import json\n"
        "print(json.dumps({\"code\": \"external_code\", \"layer\": \"tau\", \"cause\": \"ok\"}))\n",
        encoding="utf-8",
    )
    runner.chmod(0o755)
    monkeypatch.delenv("TAU_TRIAGE_ERROR_RUN_SH", raising=False)
    monkeypatch.setenv("TAU_SKILLS_ROOT", str(tmp_path / "skills"))

    result = classify_tau_failure("anything", layer="tau")

    assert result["code"] == "external_code"
    assert result["classifier_kind"] == "EXTERNAL_CLASSIFIER"
    assert result["classifier_path"] == str(runner)


def test_classify_tau_failure_does_not_execute_old_home_relative_runner(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    old_runner = (
        tmp_path
        / "workspace"
        / "experiments"
        / "agent-skills"
        / "skills"
        / "triage-error"
        / "run.sh"
    )
    marker = tmp_path / "executed.txt"
    old_runner.parent.mkdir(parents=True)
    old_runner.write_text(f"#!/usr/bin/env bash\necho ran > {marker}\n", encoding="utf-8")
    old_runner.chmod(0o755)
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("TAU_TRIAGE_ERROR_RUN_SH", raising=False)
    monkeypatch.delenv("TAU_SKILLS_ROOT", raising=False)
    monkeypatch.delenv("TAU_AGENT_SKILLS_ROOT", raising=False)

    result = classify_tau_failure("unknown failure", layer="tau")

    assert result["classifier_kind"] == "UNAVAILABLE"
    assert result["code"].startswith("tau_triage_unavailable_unclassified_")
    assert not marker.exists()
