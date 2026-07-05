import json
from pathlib import Path

from tau_coding.run_report import RUN_REPORT_RECEIPT_SCHEMA, write_run_report


def test_run_report_renders_static_html_sections(tmp_path: Path) -> None:
    run_dir = _write_report_run(tmp_path)
    report_path = tmp_path / "report.html"

    receipt = write_run_report(run_dir=run_dir, out_path=report_path)

    assert receipt["schema"] == RUN_REPORT_RECEIPT_SCHEMA
    assert receipt["ok"] is True
    assert receipt["status"] == "PASS"
    assert receipt["mocked"] is False
    assert receipt["live"] is False
    assert report_path.exists()
    assert Path(str(receipt["receipt_path"])).exists()
    html = report_path.read_text(encoding="utf-8")
    assert "Tau Run Report" in html
    assert 'id="goal"' in html
    assert 'id="policy"' in html
    assert 'id="data-boundary"' in html
    assert 'id="memory-intent"' in html
    assert 'id="evidence-case"' in html
    assert 'id="dag-steps"' in html
    assert 'id="receipts"' in html
    assert 'id="decisions"' in html
    assert 'id="non-claims"' in html
    assert "ITAR compliance." in html


def test_run_report_blocks_existing_output_without_force(tmp_path: Path) -> None:
    run_dir = _write_report_run(tmp_path)
    report_path = tmp_path / "report.html"
    report_path.write_text("existing", encoding="utf-8")

    receipt = write_run_report(run_dir=run_dir, out_path=report_path)

    assert receipt["ok"] is False
    assert receipt["status"] == "BLOCKED"
    assert "out file already exists" in receipt["errors"][0]
    assert report_path.read_text(encoding="utf-8") == "existing"


def test_run_report_force_rewrites_existing_output(tmp_path: Path) -> None:
    run_dir = _write_report_run(tmp_path)
    report_path = tmp_path / "report.html"
    report_path.write_text("existing", encoding="utf-8")

    receipt = write_run_report(run_dir=run_dir, out_path=report_path, force=True)

    assert receipt["ok"] is True
    assert "Tau Run Report" in report_path.read_text(encoding="utf-8")


def _write_report_run(tmp_path: Path) -> Path:
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    contract_path = tmp_path / "dag-contract.json"
    contract = {
        "schema": "tau.dag_contract.v1",
        "dag_id": "report-test",
        "goal": {"goal_id": "report-test", "goal_hash": "sha256:report-test"},
        "entry_node": "coder",
        "terminal_nodes": ["human"],
        "policy_profile": {
            "schema": "tau.policy_profile.v1",
            "profile_id": "itar-zero-trust-local-only",
            "default_decision": "deny",
        },
        "data_boundary": {
            "schema": "tau.data_boundary.v1",
            "classification": "public",
            "export_controlled": False,
            "itar": False,
            "technical_data": False,
        },
        "memory_intent": {
            "schema": "memory.intent.v1",
            "memory_first": True,
            "planner_only": True,
            "route": "COMPLIANCE",
        },
        "evidence_case": {
            "schema": "memory.evidence_case.v1",
            "sha256": "sha256:abc",
        },
        "nodes": [{"id": "coder", "agent": "coder"}],
        "edges": [{"from": "coder", "to": "human"}],
    }
    _write_json(contract_path, contract)
    _write_json(
        run_dir / "dag-receipt.json",
        {
            "schema": "tau.dag_receipt.v1",
            "ok": True,
            "status": "PASS",
            "verdict": "PASS",
            "contract_path": str(contract_path),
            "alerts": [],
        },
    )
    return run_dir


def _write_json(path: Path, payload: dict) -> None:
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
