import json
import os
import sys
import tarfile
from pathlib import Path

from typer.testing import CliRunner

from tau_coding.cli import app
from tau_coding.dag_runtime.retention import expire_dag_run_directories
from tau_coding.generic_dag import (
    GENERIC_DAG_NODE_RECEIPT_SCHEMA,
    GENERIC_DAG_SPEC_SCHEMA,
    inspect_generic_dag_run,
    resume_generic_dag_from_run,
    run_generic_dag,
)


def test_dag_retention_expires_oldest_run_and_retained_runs_replay(tmp_path: Path) -> None:
    runs_root = tmp_path / "runs"
    archive_dir = tmp_path / "archives"
    oldest = _run_fixture_dag(runs_root, "run-001-oldest")
    middle = _run_fixture_dag(runs_root, "run-002-middle")
    newest = _run_fixture_dag(runs_root, "run-003-newest")
    _set_run_timestamp(oldest, 1000)
    _set_run_timestamp(middle, 2000)
    _set_run_timestamp(newest, 3000)
    receipt_path = tmp_path / "retention-receipt.json"

    receipt = expire_dag_run_directories(
        root=runs_root,
        archive_dir=archive_dir,
        keep_count=2,
        receipt_path=receipt_path,
    )

    assert receipt["schema"] == "tau.dag_retention_receipt.v1"
    assert receipt["ok"] is True
    assert receipt["mocked"] is False
    assert receipt["live"] is True
    assert receipt["candidate_count"] == 3
    assert receipt["expired_count"] == 1
    assert receipt["retained_count"] == 2
    assert oldest.exists() is False
    assert middle.exists()
    assert newest.exists()
    expired = receipt["expired_runs"][0]
    assert expired["removed"] is True
    archive_path = Path(str(expired["archive_path"]))
    manifest_path = Path(str(expired["archive_manifest_path"]))
    assert archive_path.is_file()
    assert manifest_path.is_file()
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["schema"] == "tau.dag_retention_archive_manifest.v1"
    assert isinstance(manifest["archive_sha256"], str) and manifest["archive_sha256"]
    assert isinstance(manifest["journal_sha256"], str) and manifest["journal_sha256"]
    assert isinstance(manifest["receipt_sha256"], str) and manifest["receipt_sha256"]
    with tarfile.open(archive_path, "r:gz") as archive:
        names = set(archive.getnames())
    assert "run-001-oldest/dag-run.sqlite3" in names
    assert "run-001-oldest/run-receipt.json" in names
    assert json.loads(receipt_path.read_text(encoding="utf-8"))["expired_count"] == 1

    middle_inspect = inspect_generic_dag_run(middle)
    newest_inspect = inspect_generic_dag_run(newest)
    assert middle_inspect["status"] == "PASS"
    assert newest_inspect["status"] == "PASS"
    middle_resume = resume_generic_dag_from_run(middle)
    newest_resume = resume_generic_dag_from_run(newest)
    assert middle_resume["status"] == "PASS"
    assert newest_resume["status"] == "PASS"
    assert middle_resume["replayed_event_count"] > 0
    assert newest_resume["replayed_event_count"] > 0


def test_cli_dag_retention_expire_writes_receipt(tmp_path: Path) -> None:
    runs_root = tmp_path / "runs"
    archive_dir = tmp_path / "archives"
    first = _run_fixture_dag(runs_root, "run-001-first")
    second = _run_fixture_dag(runs_root, "run-002-second")
    _set_run_timestamp(first, 1000)
    _set_run_timestamp(second, 2000)
    receipt_path = tmp_path / "retention-cli-receipt.json"

    result = CliRunner().invoke(
        app,
        [
            "dag-retention-expire",
            "--root",
            str(runs_root),
            "--archive-dir",
            str(archive_dir),
            "--keep-count",
            "1",
            "--receipt",
            str(receipt_path),
        ],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["schema"] == "tau.dag_retention_receipt.v1"
    assert payload["expired_count"] == 1
    assert first.exists() is False
    assert second.exists()
    assert json.loads(receipt_path.read_text(encoding="utf-8"))["expired_count"] == 1


def _run_fixture_dag(runs_root: Path, run_name: str) -> Path:
    run_dir = runs_root / run_name
    spec_path = run_dir / "dag-spec.json"
    receipt_path = run_dir / "receipts" / "worker.json"
    spec = {
        "schema": GENERIC_DAG_SPEC_SCHEMA,
        "run_id": run_name,
        "run_dir": str(run_dir),
        "events_jsonl": str(run_dir / "events.jsonl"),
        "nodes": [
            {
                "node_id": "worker",
                "role": "worker",
                "depends_on": [],
                "receipt_path": str(receipt_path),
                "timeout_seconds": 20,
                "max_attempts": 1,
                "command": [
                    sys.executable,
                    "-c",
                    _receipt_writer_code(receipt_path),
                ],
            }
        ],
    }
    spec_path.parent.mkdir(parents=True, exist_ok=True)
    spec_path.write_text(json.dumps(spec, indent=2, sort_keys=True), encoding="utf-8")
    receipt = run_generic_dag(spec_path=spec_path, resume=False)
    assert receipt["status"] == "PASS"
    return run_dir


def _receipt_writer_code(receipt_path: Path) -> str:
    payload = {
        "schema": GENERIC_DAG_NODE_RECEIPT_SCHEMA,
        "node_id": "worker",
        "status": "PASS",
        "verdict": "PASS",
        "mocked": False,
        "live": False,
        "provider_live": False,
        "artifacts": [],
        "commands_run": ["python fixture receipt writer"],
        "handoff_summary": "worker passed",
        "errors": [],
        "policy_exceptions": [],
    }
    return (
        "import json; "
        "from pathlib import Path; "
        f"path = Path({str(receipt_path)!r}); "
        "path.parent.mkdir(parents=True, exist_ok=True); "
        f"path.write_text(json.dumps({payload!r}, sort_keys=True), encoding='utf-8')"
    )


def _set_run_timestamp(run_dir: Path, timestamp: int) -> None:
    for path in run_dir.rglob("*"):
        os.utime(path, (timestamp, timestamp))
    os.utime(run_dir, (timestamp, timestamp))
