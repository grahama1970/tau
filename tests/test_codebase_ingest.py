from pathlib import Path

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
    assert receipt["status"] == "QUEUED"
    assert receipt["changed_files"] == ["pkg/__init__.py", "pkg/mod.py"]
    assert receipt["interactive_blocking"] is False
    assert receipt["resumable"] is True
    assert receipt["memory_writes_performed_by_tau"] is False
    assert receipt["command"][:4] == ["/skills/ingest-code/run.sh", "rescan", "-c", str(repo)]
    assert "--treesitter" in receipt["command"]
    assert "--code-index" in receipt["command"]
    assert (tmp_path / "ingest-state.json").exists()


def test_codebase_ingest_skips_unchanged_files_on_next_pass(tmp_path: Path) -> None:
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

    assert receipt["status"] == "SKIPPED"
    assert receipt["changed_files"] == []
    assert receipt["started"] is False


def test_codebase_ingest_detects_incremental_edit(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    state_path = tmp_path / "ingest-state.json"
    write_codebase_ingest_receipt(
        repo_path=repo,
        receipt_path=tmp_path / "first.json",
        state_path=state_path,
        ingest_runner="/skills/ingest-code/run.sh",
    )
    (repo / "pkg" / "mod.py").write_text("def renamed(value: int) -> int:\n    return value\n")

    receipt = write_codebase_ingest_receipt(
        repo_path=repo,
        receipt_path=tmp_path / "second.json",
        state_path=state_path,
        ingest_runner="/skills/ingest-code/run.sh",
    )

    assert receipt["status"] == "QUEUED"
    assert receipt["changed_files"] == ["pkg/mod.py"]
    assert "--since" in receipt["command"]


def _repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    (repo / "pkg").mkdir(parents=True)
    (repo / "pkg" / "__init__.py").write_text("", encoding="utf-8")
    (repo / "pkg" / "mod.py").write_text(
        "class Worker:\n    pass\n\ndef run(value: int) -> int:\n    return value\n",
        encoding="utf-8",
    )
    return repo
