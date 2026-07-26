import json
import sys
from pathlib import Path
from typing import Any

import pytest

from tau_coding.generic_dag import run_generic_dag


def test_generic_dag_logs_correlated_pre_receipt_exception(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("TAU_LOG_PATH", raising=False)
    monkeypatch.setenv("TAU_LOG_LEVEL", "DEBUG")
    run_dir = tmp_path / "run"
    receipt_path = tmp_path / "worker-receipt.json"
    spec_path = tmp_path / "dag.json"
    spec_path.write_text(
        json.dumps(
            {
                "schema": "tau.generic_dag_spec.v1",
                "run_id": "diagnostic-run",
                "run_dir": str(run_dir),
                "nodes": [
                    {
                        "node_id": "worker",
                        "receipt_path": str(receipt_path),
                        "command": [sys.executable, "-c", "raise SystemExit(0)"],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    def fail_before_worker_receipt(point: str, context: dict[str, Any]) -> None:
        if point == "after_attempt_dispatched":
            raise RuntimeError("pre receipt dispatch failure")

    with pytest.raises(RuntimeError, match="pre receipt dispatch failure"):
        run_generic_dag(
            spec_path=spec_path,
            resume=False,
            diagnostic_fault_injector=fail_before_worker_receipt,
        )

    log_path = run_dir / "tau-diagnostics.jsonl"
    records = [
        json.loads(line)["record"]
        for line in log_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    fault_records = [
        record for record in records if record["message"] == "dag_fault_injector_exception"
    ]

    assert not receipt_path.exists()
    assert fault_records
    fault = fault_records[0]
    assert fault["level"]["name"] == "ERROR"
    assert fault["extra"]["run_id"] == "diagnostic-run"
    assert fault["extra"]["node_id"] == "worker"
    assert fault["extra"]["attempt"] == 1
    assert fault["extra"]["fault_point"] == "after_attempt_dispatched"
    assert fault["exception"]["type"] == "RuntimeError"
