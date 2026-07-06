import json
from pathlib import Path

from typer.testing import CliRunner

from tau_coding.cli import app
from tau_coding.proof_index import build_proof_index


def _write_json(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def test_build_proof_index_writes_receipt_entries_and_hashes(tmp_path: Path) -> None:
    proofs = tmp_path / "proofs"
    receipt = proofs / "run-a" / "run-receipt.json"
    _write_json(
        receipt,
        {
            "schema": "tau.dag_receipt.v1",
            "ok": True,
            "status": "PASS",
            "mocked": False,
            "live": "mixed",
            "provider_live": False,
            "dag_id": "demo-dag",
            "goal": {"goal_hash": "sha256:goal"},
            "proof_scope": {
                "proves": ["local DAG receipt was inspected"],
                "does_not_prove": ["provider/model semantic quality"],
            },
        },
    )
    _write_json(proofs / "not-a-receipt.json", {"schema": "tau.random.v1", "status": "PASS"})

    out = tmp_path / "index.jsonl"
    build_receipt = build_proof_index(proofs, output_path=out)

    rows = [json.loads(line) for line in out.read_text(encoding="utf-8").splitlines()]
    assert build_receipt["schema"] == "tau.proof_index_build_receipt.v1"
    assert build_receipt["status"] == "PASS"
    assert build_receipt["indexed_receipt_count"] == 1
    assert build_receipt["output_sha256"].startswith("sha256:")
    assert rows == [
        {
            "schema": "tau.proof_index_entry.v1",
            "receipt_path": str(receipt.resolve()),
            "receipt_relative_path": "run-a/run-receipt.json",
            "receipt_sha256": rows[0]["receipt_sha256"],
            "receipt_schema": "tau.dag_receipt.v1",
            "status": "PASS",
            "ok": True,
            "mocked": False,
            "live": "mixed",
            "provider_live": False,
            "run_id": None,
            "dag_id": "demo-dag",
            "goal_hash": "sha256:goal",
            "proves": ["local DAG receipt was inspected"],
            "does_not_prove": ["provider/model semantic quality"],
        }
    ]
    assert rows[0]["receipt_sha256"].startswith("sha256:")
    assert out.with_suffix(".receipt.json").exists()


def test_build_proof_index_blocks_on_malformed_json(tmp_path: Path) -> None:
    proofs = tmp_path / "proofs"
    proofs.mkdir()
    (proofs / "bad-receipt.json").write_text("{not json", encoding="utf-8")
    out = tmp_path / "index.jsonl"

    build_receipt = build_proof_index(proofs, output_path=out)

    assert build_receipt["ok"] is False
    assert build_receipt["status"] == "BLOCKED"
    assert build_receipt["indexed_receipt_count"] == 0
    assert build_receipt["error_count"] == 1
    assert build_receipt["errors"][0]["code"] == "malformed_json"
    assert out.read_text(encoding="utf-8") == ""


def test_proof_index_cli_builds_jsonl(tmp_path: Path) -> None:
    proofs = tmp_path / "proofs"
    _write_json(
        proofs / "receipt.json",
        {
            "schema": "tau.monitor_receipt.v1",
            "ok": False,
            "status": "REVIEW",
            "mocked": False,
            "live": False,
            "provider_live": False,
            "claims": {
                "proves": ["monitor inspected trace"],
                "does_not_prove": ["hidden chain-of-thought correctness"],
            },
        },
    )
    out = tmp_path / "proof-index.jsonl"
    receipt = tmp_path / "proof-index-receipt.json"

    result = CliRunner().invoke(
        app,
        ["proof-index", "build", str(proofs), "--out", str(out), "--receipt", str(receipt)],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["status"] == "PASS"
    assert payload["indexed_receipt_count"] == 1
    assert payload["receipt_path"] == str(receipt.resolve())
    row = json.loads(out.read_text(encoding="utf-8"))
    assert row["receipt_schema"] == "tau.monitor_receipt.v1"
    assert row["status"] == "REVIEW"
    assert row["proves"] == ["monitor inspected trace"]
