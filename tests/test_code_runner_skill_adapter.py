import hashlib
import json
from pathlib import Path

from tau_coding.code_runner_skill_adapter import (
    CODE_RUNNER_WORKER_RECEIPT_SCHEMA,
    write_code_runner_skill_adapter_receipt,
)


def test_code_runner_adapter_accepts_allowlist_patch(tmp_path: Path) -> None:
    result = _write_code_runner_result(tmp_path)

    receipt = write_code_runner_skill_adapter_receipt(
        result_path=result,
        output_path=tmp_path / "code-runner-receipt.json",
        repo_root=tmp_path,
        expected_goal_hash="sha256:goal",
    )

    assert receipt["schema"] == CODE_RUNNER_WORKER_RECEIPT_SCHEMA
    assert receipt["status"] == "PASS"
    assert receipt["ok"] is True
    assert receipt["code_patch_receipt_status"] == "PASS"
    assert (tmp_path / "code-patch-receipt.json").is_file()
    assert (tmp_path / "src" / "example.py").read_text(encoding="utf-8") == _BEFORE


def test_code_runner_adapter_blocks_patch_outside_allowlist(tmp_path: Path) -> None:
    result = _write_code_runner_result(tmp_path, target_file="src/example.py")
    payload = json.loads(result.read_text(encoding="utf-8"))
    payload["allowed_paths"] = ["docs/**"]
    result.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    receipt = write_code_runner_skill_adapter_receipt(
        result_path=result,
        output_path=tmp_path / "code-runner-receipt.json",
        repo_root=tmp_path,
    )

    assert receipt["status"] == "BLOCKED"
    assert "patch target_file is outside allowed_paths" in receipt["errors"]


def test_code_runner_adapter_blocks_missing_dod_artifact(tmp_path: Path) -> None:
    result = _write_code_runner_result(tmp_path)
    payload = json.loads(result.read_text(encoding="utf-8"))
    payload["dod_artifact"] = "missing-dod.json"
    result.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    receipt = write_code_runner_skill_adapter_receipt(
        result_path=result,
        output_path=tmp_path / "code-runner-receipt.json",
        repo_root=tmp_path,
    )

    assert receipt["status"] == "BLOCKED"
    assert any("dod_artifact is missing" in error for error in receipt["errors"])


def test_code_runner_adapter_emits_course_correction_on_failure(tmp_path: Path) -> None:
    result = _write_code_runner_result(tmp_path, status="BLOCKED")

    receipt = write_code_runner_skill_adapter_receipt(
        result_path=result,
        output_path=tmp_path / "code-runner-receipt.json",
        repo_root=tmp_path,
    )

    assert receipt["status"] == "BLOCKED"
    assert receipt["course_correction"]["required_next_action"] == "retry_node"
    assert "dod_artifact" in receipt["course_correction"]["required_evidence_before_retry"]


_BEFORE = "def answer():\n    return 41\n"
_AFTER = "def answer():\n    return 42\n"


def _write_code_runner_result(
    tmp_path: Path,
    *,
    status: str = "PASS",
    target_file: str = "src/example.py",
) -> Path:
    target = tmp_path / target_file
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(_BEFORE, encoding="utf-8")
    patch = {
        "schema": "tau.code_patch.v1",
        "goal_hash": "sha256:goal",
        "target_file": target_file,
        "allowed_paths": ["src/**"],
        "forbidden_paths": [],
        "base_file_sha256": f"sha256:{_sha256_text(_BEFORE)}",
        "expected_post_sha256": f"sha256:{_sha256_text(_AFTER)}",
        "anchors": [{"kind": "symbol", "value": "answer"}],
        "patch": json.dumps([{"op": "replace", "old": "return 41", "new": "return 42"}]),
    }
    (tmp_path / "patch.json").write_text(
        json.dumps(patch, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    (tmp_path / "dod.json").write_text('{"ok":true}\n', encoding="utf-8")
    (tmp_path / "test-log.txt").write_text("pytest passed\n", encoding="utf-8")
    result = {
        "schema": "code_runner.result.v1",
        "status": status,
        "goal_hash": "sha256:goal",
        "allowed_paths": ["src/**"],
        "patch_artifact": "patch.json",
        "dod_artifact": "dod.json",
        "test_log_artifact": "test-log.txt",
    }
    result_path = tmp_path / "code-runner-result.json"
    result_path.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return result_path


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()
