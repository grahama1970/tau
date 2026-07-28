import json
from pathlib import Path

from typer.testing import CliRunner

from tau_coding.cli import app
from tau_coding.tui.proof import render_textual_tui_memory_stage_proof


def test_textual_tui_memory_stage_proof_writes_receipt_and_screenshot(tmp_path: Path) -> None:
    receipt = render_textual_tui_memory_stage_proof(
        output_dir=tmp_path,
        prompt="How does Tau handle a CWE-287 SPARTA evidence case?",
        run_id="loop2-test-run",
        route="COMPLIANCE",
        next_agent="reviewer",
    )

    assert receipt["schema"] == "tau.textual_tui_render_proof.v1"
    assert receipt["ok"] is True
    assert receipt["mocked"] is True
    assert receipt["live"] is False
    assert receipt["run_id"] == "loop2-test-run"
    assert (tmp_path / "proof.json").exists()
    assert (tmp_path / "tau-textual-tui-memory-stage.svg").exists()
    saved = json.loads((tmp_path / "proof.json").read_text(encoding="utf-8"))
    assert saved["visible_assertions"]["accessing_memory"] is True
    assert saved["visible_assertions"]["permission_receipt"] is True
    assert saved["visible_assertions"]["permission_not_mutated"] is True
    assert saved["visible_assertions"]["bash_execution_metadata"] is True
    assert saved["visible_assertions"]["hidden_reasoning_absent"] is True
    assert any("live Memory backend call" in item for item in saved["does_not_prove"])


def test_tui_proof_cli_writes_fixture_backed_textual_receipt(tmp_path: Path) -> None:
    result = CliRunner().invoke(
        app,
        [
            "tui-proof",
            "--out-dir",
            str(tmp_path),
            "--run-id",
            "loop2-cli-proof",
            "--next-agent",
            "reviewer",
        ],
    )

    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert payload["schema"] == "tau.textual_tui_render_proof.v1"
    assert payload["ok"] is True
    assert payload["mocked"] is True
    assert payload["live"] is False
    assert payload["run_id"] == "loop2-cli-proof"
    assert payload["visible_assertions"]["permission_receipt"] is True
    assert payload["visible_assertions"]["bash_execution_metadata"] is True
    assert (tmp_path / "proof.json").exists()
    assert (tmp_path / "tau-textual-tui-memory-stage.svg").exists()


def test_manifest_binds_screenshot_digest(tmp_path: Path) -> None:
    """#216: the manifest names the screenshot by content hash."""
    import hashlib
    import json as _json

    receipt = render_textual_tui_memory_stage_proof(
        output_dir=tmp_path, prompt="bind test", run_id="run-bind",
        route="memory_first", next_agent="coder",
    )
    svg = Path(str(receipt["screenshot_svg"]))
    assert receipt["screenshot_sha256"] == f"sha256:{hashlib.sha256(svg.read_bytes()).hexdigest()}"
    on_disk = _json.loads((tmp_path / "proof.json").read_text())
    assert on_disk["screenshot_sha256"] == receipt["screenshot_sha256"]
    assert on_disk["visible_assertions"]["screenshot_present"] is True
    assert receipt["ok"] is True
