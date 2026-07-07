import hashlib
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
    dag_receipt = run_dir / "dag-receipt.json"
    contract = Path(
        json.loads(dag_receipt.read_text(encoding="utf-8"))["contract_path"]
    )
    assert receipt["source_artifacts"] == [
        {
            "label": "dag_receipt",
            "path": str(dag_receipt.resolve()),
            "sha256": f"sha256:{_sha256(dag_receipt)}",
            "bytes": dag_receipt.stat().st_size,
        },
        {
            "label": "dag_contract",
            "path": str(contract.resolve()),
            "sha256": f"sha256:{_sha256(contract)}",
            "bytes": contract.stat().st_size,
        },
    ]
    assert report_path.exists()
    receipt_path = Path(str(receipt["receipt_path"]))
    assert receipt_path.exists()
    on_disk_receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    assert on_disk_receipt == receipt
    assert "receipt_sha256" not in receipt
    assert receipt["receipt_sha256_excludes_self"] is True
    preimage = dict(receipt)
    preimage.pop("receipt_sha256_excludes_self")
    preimage.pop("unsigned_receipt_preimage_sha256")
    preimage_text = json.dumps(preimage, indent=2, sort_keys=True) + "\n"
    assert receipt["unsigned_receipt_preimage_sha256"] == (
        f"sha256:{hashlib.sha256(preimage_text.encode('utf-8')).hexdigest()}"
    )
    html = report_path.read_text(encoding="utf-8")
    assert "Tau Run Report" in html
    assert 'id="goal"' in html
    assert 'id="policy"' in html
    assert 'id="data-boundary"' in html
    assert 'id="memory-intent"' in html
    assert 'id="evidence-case"' in html
    assert 'id="dag-steps"' in html
    assert 'id="coding-evidence"' in html
    assert "tau.test_run_receipt.v1" in html
    assert "tau.course_correction.v1" in html
    assert "patch_stale" in html
    assert "retry_node" in html
    assert "tau.github_read_receipt.v1" in html
    assert "issue://grahama1970/tau/67" in html
    assert "mutation_allowed" in html
    assert "tau.debug_session_receipt.v1" in html
    assert "debugpy" in html
    assert "python -m pytest tests/test_example.py" in html
    assert "log_artifact_count" in html
    assert "tau.commit_plan_receipt.v1" in html
    assert "changed_file_count" in html
    assert "high_risk_path_count" in html
    assert "policy_profile_sha256" in html
    assert "data_boundary_sha256" in html
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
    receipts_dir = run_dir / "receipts"
    receipts_dir.mkdir()
    _write_json(
        receipts_dir / "test-run-receipt.json",
        {
            "schema": "tau.test_run_receipt.v1",
            "ok": True,
            "status": "PASS",
            "mocked": False,
            "live": True,
            "provider_live": False,
            "goal_hash": "sha256:report-test",
            "policy_profile_sha256": "sha256:policy",
            "data_boundary_sha256": "sha256:boundary",
        },
    )
    _write_json(
        receipts_dir / "course-correction-receipt.json",
        {
            "schema": "tau.course_correction.v1",
            "ok": False,
            "status": "REQUIRED",
            "mocked": False,
            "live": True,
            "provider_live": False,
            "goal_hash": "sha256:report-test",
            "trigger": "patch_stale",
            "node_id": "coder",
            "agent": "coder",
            "attempt": 2,
            "required_next_action": "retry_node",
        },
    )
    _write_json(
        receipts_dir / "github-read-receipt.json",
        {
            "schema": "tau.github_read_receipt.v1",
            "ok": True,
            "status": "PASS",
            "mocked": False,
            "live": False,
            "provider_live": False,
            "goal_hash": "sha256:report-test",
            "uri": "issue://grahama1970/tau/67",
            "parsed": {
                "kind": "issue",
                "owner": "grahama1970",
                "repo": "tau",
                "number": 67,
            },
            "read_only": True,
            "mutation_allowed": False,
        },
    )
    _write_json(
        receipts_dir / "debug-session-receipt.json",
        {
            "schema": "tau.debug_session_receipt.v1",
            "ok": True,
            "status": "PASS",
            "mocked": False,
            "live": True,
            "provider_live": False,
            "goal_hash": "sha256:report-test",
            "adapter": "debugpy",
            "target": "python -m pytest tests/test_example.py",
            "adapter_available": True,
            "log_artifacts": [
                {"label": "stdout", "path": "debug.stdout.txt"},
                {"label": "stderr", "path": "debug.stderr.txt"},
            ],
            "variable_redaction_count": 1,
        },
    )
    _write_json(
        receipts_dir / "commit-plan-receipt.json",
        {
            "schema": "tau.commit_plan_receipt.v1",
            "ok": True,
            "status": "PASS",
            "mocked": False,
            "live": True,
            "provider_live": False,
            "goal_hash": "sha256:report-test",
            "dry_run": True,
            "apply_requested": False,
            "apply_eligible": False,
            "changed_file_count": 3,
            "group_count": 2,
            "evidence_receipt_count": 1,
            "approval_required": True,
            "high_risk_paths": [{"path": "pyproject.toml"}],
        },
    )
    return run_dir


def _write_json(path: Path, payload: dict) -> None:
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()
