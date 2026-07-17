import hashlib
import importlib.util
import json
import os
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "run-immutable-goal-audit.py"


def _load_audit_module():
    spec = importlib.util.spec_from_file_location("tau_immutable_goal_audit", SCRIPT)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"could not load audit script: {SCRIPT}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_supplied_proofs_are_hash_bound_and_fail_closed(tmp_path: Path) -> None:
    audit = _load_audit_module()
    desktop = tmp_path / "desktop.png"
    mobile = tmp_path / "mobile.png"
    desktop.write_bytes(b"desktop-image")
    mobile.write_bytes(b"mobile-image")
    browser = {
        "schema": "tau.browser_proof.v1",
        "status": "PASS",
        "mocked": False,
        "live": True,
        "provider_live": False,
        "checks": {"visible": True, "read_only": True},
        "request_methods": ["GET"],
        "desktop_screenshot": str(desktop),
        "desktop_screenshot_sha256": ("sha256:" + hashlib.sha256(desktop.read_bytes()).hexdigest()),
        "mobile_screenshot": str(mobile),
        "mobile_screenshot_sha256": ("sha256:" + hashlib.sha256(mobile.read_bytes()).hexdigest()),
    }
    rerun = {
        "schema": "tau.no_rerun_proof.v1",
        "status": "PASS",
        "mocked": False,
        "live": True,
        "provider_live": False,
        "details": {"accepted_producer_reran": False},
    }
    wheel = {
        "schema": "tau.durable_qualification_wheel_proof.v1",
        "status": "PASS",
        "mocked": False,
        "live": True,
        "provider_live": False,
        "installed_workflow_ids": list(audit.WORKFLOW_IDS),
        "publication_effect_count": 1,
        "repeated_resume_status": "PASS",
    }
    paths = {
        "slice04_browser": tmp_path / "slice04-browser.json",
        "slice04_rerun": tmp_path / "slice04-rerun.json",
        "slice05_browser": tmp_path / "slice05-browser.json",
        "slice05_wheel": tmp_path / "slice05-wheel.json",
    }
    _write_json(paths["slice04_browser"], browser)
    _write_json(paths["slice04_rerun"], rerun)
    _write_json(paths["slice05_browser"], browser)
    _write_json(paths["slice05_wheel"], wheel)

    records = audit._validate_supplied_proofs(**paths)

    assert [item["label"] for item in records] == [
        "slice04_browser",
        "slice04_no_accepted_producer_rerun",
        "slice05_browser",
        "slice05_wheel",
    ]
    for record in records:
        path = Path(record["path"])
        expected = "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()
        assert record["sha256"] == expected
        assert record["bytes"] == path.stat().st_size
        assert record["status"] == "PASS"
        assert record["mocked"] is False
        assert record["live"] is True
        if record["label"].endswith("browser"):
            assert [item["kind"] for item in record["screenshots"]] == [
                "desktop",
                "mobile",
            ]

    browser["request_methods"] = ["GET", "POST"]
    _write_json(paths["slice05_browser"], browser)
    with pytest.raises(audit.AuditError, match="supplied_proof_browser_not_get_only"):
        audit._validate_supplied_proofs(**paths)


def test_result_and_criteria_projection_are_deterministic(tmp_path: Path) -> None:
    audit = _load_audit_module()
    run_dir = tmp_path / "run"
    results = run_dir / "results"
    results.mkdir(parents=True)
    json_path = results / "repository-readiness.json"
    markdown_path = results / "repository-readiness.md"
    _write_json(
        json_path,
        {
            "schema": "tau.repository_readiness.v1",
            "status": "READY",
            "summary": "Repository is ready.",
        },
    )
    markdown_path.write_text("# Repository Readiness\n", encoding="utf-8")

    evidence = audit._result_evidence(run_dir, "repository-readiness")
    workflow_records = [
        {
            "workflow_id": workflow_id,
            "topology": audit.TOPOLOGIES[workflow_id],
            "positive": {"status": "PASS", "result": evidence},
            "negative": {"status": "BLOCKED"},
            "viewer_evidence": [{"workflow_id": workflow_id, "http_status": 200}],
        }
        for workflow_id in audit.WORKFLOW_IDS
    ]
    proofs = [
        {"label": "slice04_browser"},
        {"label": "slice04_no_accepted_producer_rerun"},
        {"label": "slice05_browser"},
        {"label": "slice05_wheel"},
    ]

    first = audit._established_criteria(workflow_records=workflow_records, proofs=proofs)
    second = audit._established_criteria(workflow_records=workflow_records, proofs=proofs)

    assert first == second
    assert [item["status"] for item in first[:9]] == ["ESTABLISHED"] * 9
    assert first[9] == {
        "criterion": 10,
        "status": "MISSING",
        "evidence": ["Human acceptance is not recorded by this automated audit."],
    }
    assert evidence["json"]["schema"] == "tau.repository_readiness.v1"
    assert evidence["json"]["sha256"].startswith("sha256:")
    assert evidence["markdown"]["bytes"] > 0


def test_command_records_exclude_environment_values(tmp_path: Path) -> None:
    audit = _load_audit_module()
    records: list[dict[str, object]] = []
    secret = "audit-secret-value"
    env = dict(os.environ)
    env["SECRET_TOKEN"] = secret

    result = audit._run(
        [sys.executable, "-c", "print('ok')"],
        cwd=tmp_path,
        commands=records,
        env=env,
    )

    assert result.stdout.strip() == "ok"
    assert records == [
        {
            "argv": [sys.executable, "-c", "print('ok')"],
            "cwd": str(tmp_path),
            "returncode": 0,
        }
    ]
    assert secret not in json.dumps(records)


def test_blocked_audit_retains_only_safe_failure_and_commands() -> None:
    audit = _load_audit_module()
    commands = [{"argv": ["git", "rev-parse"], "cwd": "/tmp/repo", "returncode": 1}]

    payload = audit._blocked_audit("a" * 40, commands, audit.AuditError("wrong ref\nsecret"))

    assert payload["schema"] == audit.SCHEMA
    assert payload["status"] == "BLOCKED"
    assert payload["live"] is False
    assert payload["commands"] == commands
    assert payload["first_unmet_criterion"] == 1
    assert payload["failure"]["message"] == "wrong ref secret"


def _write_json(path: Path, payload: dict[str, object]) -> None:
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
