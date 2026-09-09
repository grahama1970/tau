from __future__ import annotations

import json
from pathlib import Path

import pytest

from tau_coding.dag_runtime.triage_error_bridge import (
    TRIAGE_CLASSIFICATION_SCHEMA,
    TRIAGE_CONTRACT_INVALID_CODE,
    TRIAGE_REPAIR_ARGS_SCHEMA,
    admit_triage_classification,
    classify_tau_failure,
)


def _valid_payload(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "schema": TRIAGE_CLASSIFICATION_SCHEMA,
        "code": "tau_project_dag_missing_required_evidence",
        "layer": "dag-runtime",
        "cause": "DAG node failed because required evidence was missing.",
        "repair_family": "result_contract_invalid",
        "disposition": "KNOWN_REPAIR",
        "repair_handler_id": "scheduler.correction_handler",
        "repair_args_schema": TRIAGE_REPAIR_ARGS_SCHEMA,
        "repair_args": {
            "strategy": "same_semantic_node_rerun",
            "repair_family": "result_contract_invalid",
            "classification_code": "tau_project_dag_missing_required_evidence",
        },
        "requires_human": False,
        "diagnostics": {"source": "fixture"},
    }
    payload.update(overrides)
    return payload


def test_classify_tau_failure_mints_closed_contract_when_triage_runner_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runner = tmp_path / "run.sh"
    runner.write_text("#!/usr/bin/env bash\necho classifier exploded >&2\nexit 7\n")
    runner.chmod(0o755)
    monkeypatch.setenv("TAU_TRIAGE_ERROR_RUN_SH", str(runner))

    result = classify_tau_failure("internal tau failure", layer="tau")

    assert result["schema"] == TRIAGE_CLASSIFICATION_SCHEMA
    assert result["disposition"] == "AMBIGUOUS"
    assert result["requires_human"] is True
    assert result["code"].startswith("tau_triage_classification_failed_unclassified_")
    assert result["layer"] == "tau"
    assert "classifier exploded" in result["cause"]
    assert "next_command" not in result


def test_classify_tau_failure_uses_native_fallback_without_runner(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("TAU_TRIAGE_ERROR_RUN_SH", raising=False)
    monkeypatch.delenv("TAU_SKILLS_ROOT", raising=False)
    monkeypatch.delenv("TAU_AGENT_SKILLS_ROOT", raising=False)

    result = classify_tau_failure("node missing required evidence", layer="dag-runtime")

    assert result["schema"] == TRIAGE_CLASSIFICATION_SCHEMA
    assert result["code"] == "tau_project_dag_missing_required_evidence"
    assert result["disposition"] == "KNOWN_REPAIR"
    assert result["repair_handler_id"] == "scheduler.correction_handler"
    assert result["repair_args_schema"] == TRIAGE_REPAIR_ARGS_SCHEMA
    assert result["repair_args_sha256"].startswith("sha256:")
    assert result["requires_human"] is False
    assert result["diagnostics"]["classifier_kind"] == "NATIVE_FALLBACK"
    assert "next_command" not in result


def test_classify_tau_failure_accepts_canonical_external_contract(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runner = tmp_path / "skills" / "triage-error" / "run.sh"
    runner.parent.mkdir(parents=True)
    payload = _valid_payload(code="external_code", layer="tau")
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    runner.write_text(
        "#!/usr/bin/env python3\n"
        "print(" + repr(canonical) + ")\n",
        encoding="utf-8",
    )
    runner.chmod(0o755)
    monkeypatch.delenv("TAU_TRIAGE_ERROR_RUN_SH", raising=False)
    monkeypatch.setenv("TAU_SKILLS_ROOT", str(tmp_path / "skills"))

    result = classify_tau_failure("anything", layer="tau")

    assert result["code"] == "external_code"
    assert result["diagnostics"]["classifier_kind"] == "EXTERNAL_CLASSIFIER"
    assert result["diagnostics"]["classifier_path"] == str(runner)


def test_classify_tau_failure_rejects_legacy_classifier_payload(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runner = tmp_path / "run.sh"
    runner.write_text(
        "#!/usr/bin/env python3\n"
        "import json\n"
        "print(json.dumps({'code':'external_code','layer':'tau','cause':'ok','next_command':'rm -rf /'}))\n",
        encoding="utf-8",
    )
    runner.chmod(0o755)
    monkeypatch.setenv("TAU_TRIAGE_ERROR_RUN_SH", str(runner))

    result = classify_tau_failure("anything", layer="tau")

    assert result["code"] == TRIAGE_CONTRACT_INVALID_CODE
    assert result["disposition"] == "CONTRACT_INVALID"
    assert result["requires_human"] is True
    assert result["diagnostics"]["classifier_kind"] == "CONTRACT_INVALID"
    assert "next_command" not in result


@pytest.mark.parametrize(
    "overrides",
    [
        {"code": "bad code"},
        {"code": "bad/slash"},
        {"code": "unicodé"},
        {"code": "x" * 129},
        {"layer": "unknown"},
        {"repair_handler_id": "shell.exec"},
        {"repair_args": {"strategy": "same_semantic_node_rerun", "repair_family": "result_contract_invalid", "classification_code": "ok_code", "extra": "nope"}},
        {"repair_args": {"strategy": "same_semantic_node_rerun", "repair_family": "result_contract_invalid", "classification_code": "ok_code", "payload": "echo ok && rm -rf /"}},
        {"disposition": "KNOWN_REPAIR", "requires_human": True},
        {"disposition": "AMBIGUOUS", "repair_handler_id": "scheduler.correction_handler"},
    ],
)
def test_admit_triage_classification_invalid_payloads_fail_closed(overrides: dict[str, object]) -> None:
    result = admit_triage_classification(_valid_payload(**overrides), layer="scheduler")

    assert result["code"] == TRIAGE_CONTRACT_INVALID_CODE
    assert result["layer"] == "scheduler"
    assert result["disposition"] == "CONTRACT_INVALID"
    assert result["requires_human"] is True
    assert "repair_handler_id" not in result
    assert "next_command" not in result


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

    assert result["diagnostics"]["classifier_kind"] == "UNAVAILABLE"
    assert result["code"].startswith("tau_triage_unavailable_unclassified_")
    assert not marker.exists()
