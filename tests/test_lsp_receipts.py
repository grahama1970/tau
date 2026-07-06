import json
from pathlib import Path

from typer.testing import CliRunner

from tau_coding.cli import app
from tau_coding.lsp_receipts import (
    LSP_DIAGNOSTICS_RECEIPT_SCHEMA,
    LSP_RENAME_RECEIPT_SCHEMA,
    LSP_SYMBOL_RECEIPT_SCHEMA,
    write_lsp_diagnostics_receipt,
    write_lsp_rename_plan_receipt,
)


def test_lsp_diagnostics_receipt_records_counts(tmp_path: Path) -> None:
    (tmp_path / "example.py").write_text("def ok():\n    return 1\n", encoding="utf-8")

    payload = write_lsp_diagnostics_receipt(
        workspace=tmp_path,
        output_path=tmp_path / "diagnostics.json",
    )

    assert payload["schema"] == LSP_DIAGNOSTICS_RECEIPT_SCHEMA
    assert payload["ok"] is True
    assert payload["file_count"] == 1
    assert payload["severity_counts"]["error"] == 0
    assert payload["diagnostics_increased"] == "NOT_EVALUATED"


def test_lsp_diagnostics_blocks_when_server_unavailable_if_required(tmp_path: Path) -> None:
    payload = write_lsp_diagnostics_receipt(
        workspace=tmp_path / "missing",
        output_path=tmp_path / "diagnostics.json",
        required=True,
    )

    assert payload["status"] == "BLOCKED"
    assert "lsp_server_unavailable" in payload["alert_codes"]


def test_lsp_rename_plan_records_references_without_applying_by_default(tmp_path: Path) -> None:
    source = tmp_path / "example.py"
    source.write_text("def target():\n    return target()\n", encoding="utf-8")

    payload = write_lsp_rename_plan_receipt(
        workspace=tmp_path,
        symbol="target",
        new_name="renamed",
        output_path=tmp_path / "rename.json",
    )

    assert payload["schema"] == LSP_RENAME_RECEIPT_SCHEMA
    assert payload["applied"] is False
    assert payload["reference_count"] == 2
    assert source.read_text(encoding="utf-8") == "def target():\n    return target()\n"


def test_lsp_receipt_does_not_claim_semantic_correctness(tmp_path: Path) -> None:
    (tmp_path / "example.py").write_text("value = 1\n", encoding="utf-8")
    payload = write_lsp_diagnostics_receipt(
        workspace=tmp_path,
        output_path=tmp_path / "diagnostics.json",
    )

    assert "Semantic correctness of the code." in payload["proof_scope"]["does_not_prove"]
    assert "Runtime behavior is correct." in payload["proof_scope"]["does_not_prove"]


def test_cli_lsp_symbols_writes_receipt(tmp_path: Path) -> None:
    source = tmp_path / "example.py"
    source.write_text("def lookup_symbol():\n    return lookup_symbol()\n", encoding="utf-8")
    out = tmp_path / "symbols.json"

    result = CliRunner().invoke(
        app,
        [
            "lsp-symbols",
            "--workspace",
            str(tmp_path),
            "--query",
            "lookup_symbol",
            "--out",
            str(out),
        ],
    )

    payload = json.loads(result.output)
    assert result.exit_code == 0
    assert payload == json.loads(out.read_text(encoding="utf-8"))
    assert payload["schema"] == LSP_SYMBOL_RECEIPT_SCHEMA
    assert payload["reference_count"] == 2
