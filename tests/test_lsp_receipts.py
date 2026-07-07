import hashlib
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
    write_lsp_symbol_receipt,
)


def test_lsp_diagnostics_receipt_records_counts(tmp_path: Path) -> None:
    source = tmp_path / "example.py"
    source.write_text("def ok():\n    return 1\n", encoding="utf-8")

    payload = write_lsp_diagnostics_receipt(
        workspace=tmp_path,
        output_path=tmp_path / "diagnostics.json",
    )

    assert payload["schema"] == LSP_DIAGNOSTICS_RECEIPT_SCHEMA
    assert payload["ok"] is True
    assert payload["file_count"] == 1
    assert payload["severity_counts"]["error"] == 0
    assert payload["diagnostics_increased"] == "NOT_EVALUATED"
    assert payload["inspected_artifacts"] == [
        {
            "path": str(source.resolve()),
            "exists": True,
            "sha256": f"sha256:{_sha256(source)}",
            "bytes": source.stat().st_size,
        }
    ]


def test_lsp_diagnostics_receipt_records_baseline_delta(tmp_path: Path) -> None:
    source = tmp_path / "example.py"
    source.write_text("def ok():\n    return 1\n", encoding="utf-8")
    baseline = write_lsp_diagnostics_receipt(
        workspace=tmp_path,
        output_path=tmp_path / "baseline-diagnostics.json",
    )
    source.write_text("def broken(:\n    return 1\n", encoding="utf-8")

    payload = write_lsp_diagnostics_receipt(
        workspace=tmp_path,
        output_path=tmp_path / "after-diagnostics.json",
        baseline_receipt_path=Path(baseline["receipt_path"]),
    )

    assert payload["baseline_severity_counts"] == baseline["severity_counts"]
    baseline_path = Path(baseline["receipt_path"])
    assert payload["baseline_receipt_artifact"] == {
        "path": str(baseline_path.resolve()),
        "exists": True,
        "sha256": f"sha256:{_sha256(baseline_path)}",
        "bytes": baseline_path.stat().st_size,
    }
    assert payload["diagnostic_delta"]["error"] > 0
    assert payload["diagnostics_increased"] is True


def test_lsp_diagnostics_receipt_records_goal_hash(tmp_path: Path) -> None:
    (tmp_path / "example.py").write_text("def ok():\n    return 1\n", encoding="utf-8")

    payload = write_lsp_diagnostics_receipt(
        workspace=tmp_path,
        output_path=tmp_path / "diagnostics.json",
        goal_hash="sha256:goal",
    )

    assert payload["status"] == "PASS"
    assert payload["goal_hash"] == "sha256:goal"


def test_lsp_diagnostics_blocks_non_pass_baseline_receipt(tmp_path: Path) -> None:
    source = tmp_path / "example.py"
    source.write_text("def ok():\n    return 1\n", encoding="utf-8")
    baseline = tmp_path / "blocked-baseline.json"
    baseline.write_text(
        json.dumps(
            {
                "schema": LSP_DIAGNOSTICS_RECEIPT_SCHEMA,
                "ok": False,
                "status": "BLOCKED",
                "severity_counts": {"error": 0, "warning": 0, "information": 0, "hint": 0},
            }
        )
        + "\n",
        encoding="utf-8",
    )

    payload = write_lsp_diagnostics_receipt(
        workspace=tmp_path,
        output_path=tmp_path / "after-diagnostics.json",
        baseline_receipt_path=baseline,
    )

    assert payload["status"] == "BLOCKED"
    assert "baseline_receipt_not_pass" in payload["alert_codes"]
    assert payload["baseline_severity_counts"] is None
    assert payload["diagnostic_delta"] is None
    assert payload["diagnostics_increased"] == "NOT_EVALUATED"


def test_lsp_diagnostics_blocks_baseline_goal_hash_mismatch(tmp_path: Path) -> None:
    source = tmp_path / "example.py"
    source.write_text("def ok():\n    return 1\n", encoding="utf-8")
    baseline = write_lsp_diagnostics_receipt(
        workspace=tmp_path,
        output_path=tmp_path / "baseline-diagnostics.json",
        goal_hash="sha256:other",
    )

    payload = write_lsp_diagnostics_receipt(
        workspace=tmp_path,
        output_path=tmp_path / "after-diagnostics.json",
        goal_hash="sha256:goal",
        baseline_receipt_path=Path(baseline["receipt_path"]),
    )

    assert payload["status"] == "BLOCKED"
    assert "baseline_receipt_goal_hash_mismatch" in payload["alert_codes"]
    assert payload["baseline_severity_counts"] is None
    assert payload["diagnostic_delta"] is None


def test_lsp_diagnostics_blocks_when_server_unavailable_if_required(tmp_path: Path) -> None:
    payload = write_lsp_diagnostics_receipt(
        workspace=tmp_path / "missing",
        output_path=tmp_path / "diagnostics.json",
        required=True,
    )

    assert payload["status"] == "BLOCKED"
    assert "lsp_server_unavailable" in payload["alert_codes"]


def test_lsp_diagnostics_zero_trust_blocks_missing_policy_boundary(tmp_path: Path) -> None:
    (tmp_path / "example.py").write_text("value = 1\n", encoding="utf-8")

    payload = write_lsp_diagnostics_receipt(
        workspace=tmp_path,
        output_path=tmp_path / "diagnostics.json",
        zero_trust=True,
    )

    assert payload["status"] == "BLOCKED"
    assert "missing_goal_hash" in payload["alert_codes"]
    assert "missing_policy_profile" in payload["alert_codes"]
    assert "missing_data_boundary" in payload["alert_codes"]


def test_lsp_diagnostics_zero_trust_accepts_policy_boundary(tmp_path: Path) -> None:
    (tmp_path / "example.py").write_text("value = 1\n", encoding="utf-8")

    payload = write_lsp_diagnostics_receipt(
        workspace=tmp_path,
        output_path=tmp_path / "diagnostics.json",
        goal_hash="sha256:goal",
        zero_trust=True,
        policy_profile={"schema": "tau.policy_profile.v1", "profile_id": "test"},
        data_boundary={"schema": "tau.data_boundary.v1", "classification": "public"},
    )

    assert payload["status"] == "PASS"
    assert payload["goal_hash"] == "sha256:goal"
    assert payload["zero_trust"] is True
    assert payload["policy_profile"]["profile_id"] == "test"
    assert payload["data_boundary"]["classification"] == "public"


def test_lsp_rename_plan_records_references_without_applying_by_default(tmp_path: Path) -> None:
    source = tmp_path / "example.py"
    source.write_text("def target():\n    return target()\n", encoding="utf-8")

    payload = write_lsp_rename_plan_receipt(
        workspace=tmp_path,
        symbol="target",
        new_name="renamed",
        output_path=tmp_path / "rename.json",
        goal_hash="sha256:goal",
    )

    assert payload["schema"] == LSP_RENAME_RECEIPT_SCHEMA
    assert payload["goal_hash"] == "sha256:goal"
    assert payload["applied"] is False
    assert payload["reference_count"] == 2
    symbol_receipt = tmp_path / "rename.symbols.tmp.json"
    assert payload["symbol_receipt_artifact"] == {
        "path": str(symbol_receipt.resolve()),
        "exists": True,
        "sha256": f"sha256:{_sha256(symbol_receipt)}",
        "bytes": symbol_receipt.stat().st_size,
    }
    assert payload["inspected_artifacts"] == [
        {
            "path": str(source.resolve()),
            "exists": True,
            "sha256": f"sha256:{_sha256(source)}",
            "bytes": source.stat().st_size,
        }
    ]
    assert source.read_text(encoding="utf-8") == "def target():\n    return target()\n"


def test_lsp_rename_plan_blocks_missing_symbol(tmp_path: Path) -> None:
    source = tmp_path / "example.py"
    source.write_text("def target():\n    return target()\n", encoding="utf-8")

    payload = write_lsp_rename_plan_receipt(
        workspace=tmp_path,
        symbol="missing_target",
        new_name="renamed",
        output_path=tmp_path / "rename.json",
    )

    assert payload["status"] == "BLOCKED"
    assert payload["reference_count"] == 0
    assert "symbol_not_found" in payload["alert_codes"]
    assert source.read_text(encoding="utf-8") == "def target():\n    return target()\n"


def test_lsp_rename_plan_blocks_invalid_new_name(tmp_path: Path) -> None:
    source = tmp_path / "example.py"
    source.write_text("def target():\n    return target()\n", encoding="utf-8")

    payload = write_lsp_rename_plan_receipt(
        workspace=tmp_path,
        symbol="target",
        new_name="not-valid-name",
        output_path=tmp_path / "rename.json",
    )

    assert payload["status"] == "BLOCKED"
    assert "invalid_new_name" in payload["alert_codes"]
    assert source.read_text(encoding="utf-8") == "def target():\n    return target()\n"


def test_lsp_rename_plan_blocks_noop_same_name(tmp_path: Path) -> None:
    source = tmp_path / "example.py"
    source.write_text("def target():\n    return target()\n", encoding="utf-8")

    payload = write_lsp_rename_plan_receipt(
        workspace=tmp_path,
        symbol="target",
        new_name="target",
        output_path=tmp_path / "rename.json",
    )

    assert payload["status"] == "BLOCKED"
    assert "rename_noop" in payload["alert_codes"]
    assert source.read_text(encoding="utf-8") == "def target():\n    return target()\n"


def test_lsp_symbols_zero_trust_blocks_missing_policy_boundary(tmp_path: Path) -> None:
    (tmp_path / "example.py").write_text("def target():\n    return target()\n", encoding="utf-8")

    payload = write_lsp_symbol_receipt(
        workspace=tmp_path,
        query="target",
        output_path=tmp_path / "symbols.json",
        zero_trust=True,
    )

    assert payload["status"] == "BLOCKED"
    assert "missing_goal_hash" in payload["alert_codes"]
    assert "missing_policy_profile" in payload["alert_codes"]
    assert "missing_data_boundary" in payload["alert_codes"]


def test_lsp_rename_plan_zero_trust_blocks_missing_policy_boundary(tmp_path: Path) -> None:
    (tmp_path / "example.py").write_text("def target():\n    return target()\n", encoding="utf-8")

    payload = write_lsp_rename_plan_receipt(
        workspace=tmp_path,
        symbol="target",
        new_name="renamed",
        output_path=tmp_path / "rename.json",
        zero_trust=True,
    )

    assert payload["status"] == "BLOCKED"
    assert "missing_goal_hash" in payload["alert_codes"]
    assert "missing_policy_profile" in payload["alert_codes"]
    assert "missing_data_boundary" in payload["alert_codes"]


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
            "--goal-hash",
            "sha256:goal",
        ],
    )

    payload = json.loads(result.output)
    assert result.exit_code == 0
    assert payload == json.loads(out.read_text(encoding="utf-8"))
    assert payload["schema"] == LSP_SYMBOL_RECEIPT_SCHEMA
    assert payload["goal_hash"] == "sha256:goal"
    assert payload["reference_count"] == 2
    assert payload["inspected_artifacts"] == [
        {
            "path": str(source.resolve()),
            "exists": True,
            "sha256": f"sha256:{_sha256(source)}",
            "bytes": source.stat().st_size,
        }
    ]


def test_cli_lsp_symbols_zero_trust_missing_boundary_exits_blocked(
    tmp_path: Path,
) -> None:
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
            "--zero-trust",
        ],
    )

    payload = json.loads(result.output)
    assert result.exit_code == 1
    assert payload == json.loads(out.read_text(encoding="utf-8"))
    assert payload["status"] == "BLOCKED"
    assert "missing_goal_hash" in payload["alert_codes"]
    assert "missing_policy_profile" in payload["alert_codes"]
    assert "missing_data_boundary" in payload["alert_codes"]


def test_cli_lsp_rename_plan_zero_trust_missing_boundary_exits_blocked(
    tmp_path: Path,
) -> None:
    source = tmp_path / "example.py"
    source.write_text("def lookup_symbol():\n    return lookup_symbol()\n", encoding="utf-8")
    out = tmp_path / "rename.json"

    result = CliRunner().invoke(
        app,
        [
            "lsp-rename-plan",
            "--workspace",
            str(tmp_path),
            "--symbol",
            "lookup_symbol",
            "--new-name",
            "renamed_symbol",
            "--out",
            str(out),
            "--zero-trust",
        ],
    )

    payload = json.loads(result.output)
    assert result.exit_code == 1
    assert payload == json.loads(out.read_text(encoding="utf-8"))
    assert payload["status"] == "BLOCKED"
    assert "missing_goal_hash" in payload["alert_codes"]
    assert "missing_policy_profile" in payload["alert_codes"]
    assert "missing_data_boundary" in payload["alert_codes"]


def test_cli_lsp_diagnostics_zero_trust_missing_boundary_exits_blocked(
    tmp_path: Path,
) -> None:
    (tmp_path / "example.py").write_text("value = 1\n", encoding="utf-8")
    out = tmp_path / "diagnostics.json"

    result = CliRunner().invoke(
        app,
        [
            "lsp-diagnostics",
            "--workspace",
            str(tmp_path),
            "--out",
            str(out),
            "--zero-trust",
        ],
    )

    payload = json.loads(result.output)
    assert result.exit_code == 1
    assert payload == json.loads(out.read_text(encoding="utf-8"))
    assert payload["status"] == "BLOCKED"
    assert "missing_goal_hash" in payload["alert_codes"]
    assert "missing_policy_profile" in payload["alert_codes"]
    assert "missing_data_boundary" in payload["alert_codes"]


def test_cli_lsp_diagnostics_accepts_baseline_receipt(tmp_path: Path) -> None:
    source = tmp_path / "example.py"
    source.write_text("value = 1\n", encoding="utf-8")
    baseline = write_lsp_diagnostics_receipt(
        workspace=tmp_path,
        output_path=tmp_path / "baseline.json",
    )
    source.write_text("def broken(:\n    return 1\n", encoding="utf-8")
    out = tmp_path / "after.json"

    result = CliRunner().invoke(
        app,
        [
            "lsp-diagnostics",
            "--workspace",
            str(tmp_path),
            "--out",
            str(out),
            "--baseline-receipt",
            str(baseline["receipt_path"]),
        ],
    )

    payload = json.loads(result.output)
    assert result.exit_code == 0
    assert payload == json.loads(out.read_text(encoding="utf-8"))
    assert payload["diagnostics_increased"] is True
    assert payload["baseline_receipt_path"] == str(Path(baseline["receipt_path"]))


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()
