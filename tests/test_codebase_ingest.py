from pathlib import Path
from typing import Any

from tau_coding.codebase_ingest import (
    CODEBASE_INGEST_RECEIPT_SCHEMA,
    write_codebase_ingest_receipt,
)


def test_codebase_ingest_first_pass_records_files_and_command(tmp_path: Path) -> None:
    repo = _repo(tmp_path)

    receipt = write_codebase_ingest_receipt(
        repo_path=repo,
        receipt_path=tmp_path / "ingest-receipt.json",
        state_path=tmp_path / "ingest-state.json",
        ingest_runner="/skills/ingest-code/run.sh",
    )

    assert receipt["schema"] == CODEBASE_INGEST_RECEIPT_SCHEMA
    assert receipt["status"] == "READY_FOR_SCAN"
    assert receipt["changed_files"] == ["pkg/__init__.py", "pkg/mod.py"]
    assert receipt["interactive_blocking"] is False
    assert receipt["resumable"] is True
    assert receipt["memory_writes_performed_by_tau"] is False
    assert receipt["command"][:3] == ["/skills/ingest-code/run.sh", "scan", str(repo)]
    assert "--treesitter" in receipt["command"]
    assert "--code-index" not in receipt["command"]
    assert receipt["command"][receipt["command"].index("--projection-mode") + 1] == "emit"
    assert receipt["process"] is None
    assert receipt["worker"] is None
    assert receipt["change_marker_advanced"] is False
    assert not (tmp_path / "ingest-state.json").exists()


def test_codebase_ingest_skips_unchanged_files_on_next_pass(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    state_path = tmp_path / "ingest-state.json"
    _write_state(state_path, repo)

    receipt = write_codebase_ingest_receipt(
        repo_path=repo,
        receipt_path=tmp_path / "second.json",
        state_path=state_path,
        ingest_runner="/skills/ingest-code/run.sh",
    )

    assert receipt["status"] == "SKIPPED_UNCHANGED"
    assert receipt["changed_files"] == []
    assert receipt["started"] is False
    assert receipt["worker"] is None
    assert receipt["accepted_effect_count"] == 0


def test_codebase_ingest_does_not_advance_marker_from_queued_work(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    state_path = tmp_path / "ingest-state.json"
    write_codebase_ingest_receipt(
        repo_path=repo,
        receipt_path=tmp_path / "first.json",
        state_path=state_path,
        ingest_runner="/skills/ingest-code/run.sh",
    )

    receipt = write_codebase_ingest_receipt(
        repo_path=repo,
        receipt_path=tmp_path / "second.json",
        state_path=state_path,
        ingest_runner="/skills/ingest-code/run.sh",
    )

    assert receipt["status"] == "READY_FOR_SCAN"
    assert receipt["changed_files"] == ["pkg/__init__.py", "pkg/mod.py"]
    assert not state_path.exists()


def test_codebase_ingest_ignores_generated_ingest_artifacts_after_admission(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    repo = _repo(tmp_path)
    state_path = tmp_path / "state.json"

    def run(command: list[str], **_: Any) -> Any:
        if command[0] == "git":
            return _Completed(stdout="unknown\n")
        _write_ingest_marker(repo)
        (repo / ".batch_state.json").write_text("{}", encoding="utf-8")
        return _Completed()

    monkeypatch.setattr("tau_coding.codebase_ingest.subprocess.run", run)
    first = write_codebase_ingest_receipt(
        repo_path=repo,
        receipt_path=tmp_path / "first.json",
        state_path=state_path,
        ingest_runner="/skills/ingest-code/run.sh",
        start=True,
        run_id="run-1",
        goal_hash="sha256:" + "1" * 64,
    )
    second = write_codebase_ingest_receipt(
        repo_path=repo,
        receipt_path=tmp_path / "second.json",
        state_path=state_path,
        ingest_runner="/skills/ingest-code/run.sh",
    )

    assert first["status"] == "SCAN_ADMITTED"
    assert second["status"] == "SKIPPED_UNCHANGED"
    assert second["changed_files"] == []


def test_codebase_ingest_detects_incremental_edit(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    state_path = tmp_path / "ingest-state.json"
    _write_state(state_path, repo)
    (repo / "pkg" / "mod.py").write_text("def renamed(value: int) -> int:\n    return value\n")

    receipt = write_codebase_ingest_receipt(
        repo_path=repo,
        receipt_path=tmp_path / "second.json",
        state_path=state_path,
        ingest_runner="/skills/ingest-code/run.sh",
    )

    assert receipt["status"] == "READY_FOR_SCAN"
    assert receipt["changed_files"] == ["pkg/mod.py"]
    assert "--since" not in receipt["command"]


def test_codebase_ingest_start_uses_owned_worker_and_admits_emit_artifacts(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    repo = _repo(tmp_path)

    def run(command: list[str], **_: Any) -> Any:
        if command[0] == "git":
            return _Completed(stdout="unknown\n")
        assert command[:3] == ["/skills/ingest-code/run.sh", "scan", str(repo)]
        _write_ingest_marker(repo)
        return _Completed()

    monkeypatch.setattr("tau_coding.codebase_ingest.subprocess.run", run)
    receipt = write_codebase_ingest_receipt(
        repo_path=repo,
        receipt_path=tmp_path / "receipt.json",
        state_path=tmp_path / "state.json",
        ingest_runner="/skills/ingest-code/run.sh",
        start=True,
        run_id="run-1",
        goal_hash="sha256:" + "1" * 64,
    )

    assert receipt["status"] == "SCAN_ADMITTED"
    assert receipt["worker"]["detached_child"] is False
    assert receipt["worker"]["runtime_endpoint_lease"]["schema"] == "tau.runtime_endpoint_lease.v1"
    assert receipt["admission"]["ok"] is True
    assert receipt["admission"]["projection_mode"] == "emit"
    assert receipt["admission"]["projection_applied"] is False
    assert receipt["projection"]["state"] == "request_emitted_unapplied"
    assert receipt["accepted_effect_count"] == 0
    assert receipt["change_marker_advanced"] is True
    assert (tmp_path / "state.json").exists()


def test_codebase_ingest_authorized_projection_records_one_accepted_effect(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    repo = _repo(tmp_path)

    def run(command: list[str], **_: Any) -> Any:
        if command[0] == "git":
            return _Completed(stdout="unknown\n")
        _write_ingest_marker(repo)
        return _Completed()

    def apply_projection(admission: dict[str, Any], **_: Any) -> dict[str, str]:
        admission["projection_state"] = {
            "schema": "tau.codebase_ingest_projection_state.v1",
            "state": "accepted_effect_applied",
            "policy_authorized": True,
            "accepted_effect_count": 1,
            "generation_id": "cg_fixture",
            "readback": {"generation": {"generation_id": "cg_fixture"}},
        }
        return {"terminal_status": "PROJECTION_ACCEPTED"}

    monkeypatch.setattr("tau_coding.codebase_ingest.subprocess.run", run)
    monkeypatch.setattr("tau_coding.codebase_ingest._apply_projection_request", apply_projection)
    receipt = write_codebase_ingest_receipt(
        repo_path=repo,
        receipt_path=tmp_path / "receipt.json",
        state_path=tmp_path / "state.json",
        ingest_runner="/skills/ingest-code/run.sh",
        start=True,
        run_id="run-1",
        goal_hash="sha256:" + "1" * 64,
        projection_authorized=True,
    )

    assert receipt["status"] == "PROJECTION_ACCEPTED"
    assert receipt["projection"]["policy_authorized"] is True
    assert receipt["projection"]["generation_id"] == "cg_fixture"
    assert receipt["accepted_effect_count"] == 1
    assert receipt["change_marker_advanced"] is True


def test_codebase_ingest_memory_outage_keeps_admitted_scan_degraded(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    repo = _repo(tmp_path)

    def run(command: list[str], **_: Any) -> Any:
        if command[0] == "git":
            return _Completed(stdout="unknown\n")
        _write_ingest_marker(repo)
        return _Completed()

    def apply_projection(admission: dict[str, Any], **_: Any) -> dict[str, str]:
        admission["projection_state"] = {
            "schema": "tau.codebase_ingest_projection_state.v1",
            "state": "degraded_unapplied",
            "policy_authorized": True,
            "accepted_effect_count": 0,
            "generation_id": None,
            "readback": None,
            "errors": ["memory unavailable"],
        }
        return {"terminal_status": "SCAN_ADMITTED_PROJECTION_DEGRADED"}

    monkeypatch.setattr("tau_coding.codebase_ingest.subprocess.run", run)
    monkeypatch.setattr("tau_coding.codebase_ingest._apply_projection_request", apply_projection)
    receipt = write_codebase_ingest_receipt(
        repo_path=repo,
        receipt_path=tmp_path / "receipt.json",
        state_path=tmp_path / "state.json",
        ingest_runner="/skills/ingest-code/run.sh",
        start=True,
        run_id="run-1",
        goal_hash="sha256:" + "1" * 64,
        projection_authorized=True,
    )

    assert receipt["status"] == "SCAN_ADMITTED_PROJECTION_DEGRADED"
    assert receipt["admission"]["ok"] is True
    assert receipt["projection"]["state"] == "degraded_unapplied"
    assert receipt["accepted_effect_count"] == 0
    assert receipt["change_marker_advanced"] is True


def test_codebase_ingest_blocks_before_projection_on_bad_emit_artifact(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    repo = _repo(tmp_path)

    def run(command: list[str], **_: Any) -> Any:
        if command[0] == "git":
            return _Completed(stdout="unknown\n")
        assert "--projection-mode" in command
        _write_ingest_marker(repo, projection_mode="apply")
        return _Completed()

    monkeypatch.setattr("tau_coding.codebase_ingest.subprocess.run", run)
    receipt = write_codebase_ingest_receipt(
        repo_path=repo,
        receipt_path=tmp_path / "receipt.json",
        state_path=tmp_path / "state.json",
        ingest_runner="/skills/ingest-code/run.sh",
        start=True,
        run_id="run-1",
        goal_hash="sha256:" + "1" * 64,
    )

    assert receipt["status"] == "BLOCKED"
    assert receipt["admission"]["first_failed_gate"] == "projection_mode"
    assert receipt["projection"]["state"] == "blocked_before_projection"
    assert receipt["accepted_effect_count"] == 0
    assert not (tmp_path / "state.json").exists()


def _repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    (repo / "pkg").mkdir(parents=True)
    (repo / "pkg" / "__init__.py").write_text("", encoding="utf-8")
    (repo / "pkg" / "mod.py").write_text(
        "class Worker:\n    pass\n\ndef run(value: int) -> int:\n    return value\n",
        encoding="utf-8",
    )
    return repo


class _Completed:
    def __init__(self, *, stdout: str = "ok", stderr: str = "", returncode: int = 0) -> None:
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


def _write_state(path: Path, repo: Path) -> None:
    from tau_coding.codebase_ingest import _sha256

    payload = {
        "schema": "tau.codebase_ingest_state.v2",
        "repo_path": str(repo),
        "commit": "unknown",
        "files": {
            "pkg/__init__.py": _sha256(repo / "pkg" / "__init__.py"),
            "pkg/mod.py": _sha256(repo / "pkg" / "mod.py"),
        },
    }
    path.write_text(__import__("json").dumps(payload), encoding="utf-8")


def _write_ingest_marker(repo: Path, *, projection_mode: str = "emit") -> None:
    import json

    graph_dir = repo / "artifacts" / "ingest-code" / "code-graph"
    graph_dir.mkdir(parents=True)
    manifest = graph_dir / "manifest.json"
    checksums = graph_dir / "checksums.json"
    coverage = graph_dir / "coverage.json"
    request = graph_dir / "code_projection_request.json"
    environment = repo / "artifacts" / "ingest-code" / "environment_manifest.json"
    manifest.write_text("{}", encoding="utf-8")
    checksums.write_text("{}", encoding="utf-8")
    coverage.write_text("{}", encoding="utf-8")
    environment.parent.mkdir(parents=True, exist_ok=True)
    environment.write_text("{}", encoding="utf-8")
    request.write_text(
        json.dumps(
            {
                "schema": "ingest-code.code_projection_request.v1",
                "environment_manifest_digest": "sha256:" + "2" * 64,
            }
        ),
        encoding="utf-8",
    )
    marker = {
        "code_index": {
            "projection_mode": projection_mode,
            "projection_applied": False,
        },
        "local_artifacts": {
            "code_graph": {
                "complete": True,
                "manifest": str(manifest),
                "checksums": str(checksums),
                "coverage": str(coverage),
            },
            "code_projection_request": {
                "status": "emitted_not_applied",
                "path": str(request),
                "sha256": "sha256:" + "3" * 64,
                "submitted_bundle_digest": "sha256:" + "4" * 64,
                "checksums_digest": "sha256:" + "5" * 64,
                "idempotency_key": "projection-key",
            },
            "environment_manifest": {
                "admissible": True,
                "path": str(environment),
                "environment_manifest_digest": "sha256:" + "2" * 64,
            },
        },
    }
    (repo / ".ingest-code.json").write_text(json.dumps(marker), encoding="utf-8")
