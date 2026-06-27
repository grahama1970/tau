from __future__ import annotations

import importlib
import json
from pathlib import Path

import pytest

from tau_coding import cli


def test_scillm_key_preparation_uses_env_without_mutating_source_contract(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    contract_path = tmp_path / "contract.json"
    source_contract = {
        "schema": "loop2.repair_node_contract.v1",
        "node_id": "env-key-test",
        "objective": "repair",
        "repo": str(tmp_path / "repo"),
        "allowed_globs": ["src/target.py"],
        "checks": ["python3 -m pytest tests -q"],
        "backend": "scillm",
        "scillm": {
            "base_url": "http://127.0.0.1:4001",
            "api_key": "redacted-placeholder",
        },
    }
    contract_path.write_text(json.dumps(source_contract))
    monkeypatch.setenv("SCILLM_API_KEY", "active-proxy-key")
    (tmp_path / "temp").mkdir()

    prepared_path, report = cli._prepare_delegated_scillm_contract_for_runner(
        contract_path,
        source_contract,
        temp_dir=tmp_path / "temp",
    )

    assert report["auth_source"] == "env:SCILLM_API_KEY"
    assert json.loads(contract_path.read_text())["scillm"]["api_key"] == "redacted-placeholder"
    assert json.loads(prepared_path.read_text())["scillm"]["api_key"] == "active-proxy-key"


def test_materialization_preflight_rejects_tmp_repo() -> None:
    errors = cli._scillm_materialization_preflight_errors(
        {
            "backend": "scillm",
            "repo": "/tmp/tau-unsafe-repo",
            "allowed_globs": ["src/target.py"],
            "checks": ["python3 -m pytest tests -q"],
        }
    )

    assert any("/tmp" in error for error in errors)


def test_delegated_result_artifact_errors_name_missing_files(tmp_path: Path) -> None:
    final_receipt = tmp_path / "run" / "final-receipt.json"
    final_receipt.parent.mkdir(parents=True)
    final_receipt.write_text("{}")

    errors = cli._delegated_loop2_result_artifact_errors(
        {
            "final_receipt": str(final_receipt),
            "transport_dag_evidence": "",
            "events": "",
            "checks": [{"stdout_path": "", "stderr_path": ""}],
        }
    )

    joined = "\n".join(errors)
    assert "node_result.transport_dag_evidence" in joined
    assert "run_dir.contract.json" in joined
    assert "node_result.checks[1].stdout_path" in joined


def test_loop2_runner_discovery_requires_run_sh(tmp_path: Path) -> None:
    src = tmp_path / "loop2-src"
    src.mkdir()

    assert cli._loop2_runner_from_src(src) is None

    run_sh = src.parent / "run.sh"
    run_sh.write_text("#!/usr/bin/env bash\n")
    assert cli._loop2_runner_from_src(src) == run_sh


def test_native_validator_detects_missing_required_artifacts(tmp_path: Path) -> None:
    validator = importlib.import_module("tau_coding.loop_validation")

    run_dir = tmp_path / "run"
    run_dir.mkdir()
    (run_dir / "final-receipt.json").write_text(json.dumps({"schema": "loop2.final_receipt.v1"}))

    result = validator.validate_native_loop2_run_with_contracts(run_dir)

    assert result.ok is False
    assert any("missing" in error.lower() for error in result.errors)
