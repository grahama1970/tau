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
