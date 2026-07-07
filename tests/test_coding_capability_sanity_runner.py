import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType


def test_expected_artifact_requires_scillm_apply_result_artifact(tmp_path: Path) -> None:
    runner = _load_runner()
    expected = tmp_path / "expected.json"
    actual = tmp_path / "demo-receipt.json"
    expected.write_text(
        json.dumps(
            {
                "schema": "tau.scillm_worker_example_receipt.v1",
                "status": "PASS",
                "expected_apply_launch_result_artifact": True,
            }
        )
        + "\n",
        encoding="utf-8",
    )
    actual.write_text(
        json.dumps(
            {
                "schema": "tau.scillm_worker_example_receipt.v1",
                "status": "PASS",
            }
        )
        + "\n",
        encoding="utf-8",
    )

    payload = runner._check_expected_artifact(  # noqa: SLF001
        actual_path=actual,
        expected_path=expected,
    )

    assert payload["ok"] is False
    assert "apply_launch_response_result_path missing" in payload["errors"]
    assert "apply_launch_response_result_sha256 missing or invalid" in payload["errors"]
    assert (
        "apply_launch_response_result_artifact missing or not an object"
        in payload["errors"]
    )


def test_expected_artifact_accepts_scillm_apply_result_artifact(tmp_path: Path) -> None:
    runner = _load_runner()
    expected = tmp_path / "expected.json"
    actual = tmp_path / "demo-receipt.json"
    result_path = tmp_path / "scillm-result.json"
    result_sha = "sha256:abc123"
    expected.write_text(
        json.dumps(
            {
                "schema": "tau.scillm_worker_example_receipt.v1",
                "status": "PASS",
                "expected_apply_launch_result_artifact": True,
            }
        )
        + "\n",
        encoding="utf-8",
    )
    actual.write_text(
        json.dumps(
            {
                "schema": "tau.scillm_worker_example_receipt.v1",
                "status": "PASS",
                "apply_launch_response_result_path": str(result_path),
                "apply_launch_response_result_sha256": result_sha,
                "apply_launch_response_result_artifact": {
                    "path": str(result_path),
                    "exists": True,
                    "sha256": result_sha,
                },
            }
        )
        + "\n",
        encoding="utf-8",
    )

    payload = runner._check_expected_artifact(  # noqa: SLF001
        actual_path=actual,
        expected_path=expected,
    )

    assert payload["ok"] is True
    assert payload["errors"] == []


def _load_runner() -> ModuleType:
    path = Path(__file__).resolve().parents[1] / "scripts" / "run-coding-capability-sanity.py"
    spec = importlib.util.spec_from_file_location("run_coding_capability_sanity", path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module
