import json
from pathlib import Path
from urllib.parse import quote

from tau_coding.server import route_request


def test_server_health_and_doctor_routes() -> None:
    status, payload = route_request(method="GET", target="/health")

    assert status == 200
    assert payload["schema"] == "tau.server_health.v1"
    assert payload["ok"] is True

    doctor_status, doctor_payload = route_request(
        method="POST",
        target="/doctor",
        doctor_handler=lambda: {
            "schema": "tau.doctor.v1",
            "ok": True,
            "status": "PASS",
        },
    )

    assert doctor_status == 200
    assert doctor_payload["schema"] == "tau.doctor.v1"


def test_server_zero_trust_preflight_route(tmp_path: Path) -> None:
    policy = tmp_path / "policy.json"
    boundary = tmp_path / "boundary.json"
    receipt = tmp_path / "zero-trust-receipt.json"
    _write_json(policy, _policy_profile())
    _write_json(boundary, _data_boundary())

    status, payload = route_request(
        method="POST",
        target="/zero-trust/preflight",
        body=_json_body(
            {
                "policy_profile": str(policy),
                "data_boundary": str(boundary),
                "receipt": str(receipt),
            }
        ),
    )

    assert status == 200
    assert payload["schema"] == "tau.zero_trust_preflight_receipt.v1"
    assert payload["ok"] is True
    assert receipt.exists()


def test_server_memory_evidence_preflight_route(tmp_path: Path) -> None:
    receipt_dir = tmp_path / "receipts"

    status, payload = route_request(
        method="POST",
        target="/memory-evidence/preflight",
        body=_json_body(
            {
                "policy_profile": {
                    "schema": "tau.policy_profile.v1",
                    "profile_id": "itar-zero-trust-local-only",
                    "default_decision": "deny",
                    "memory": {"intent_required": True},
                },
                "data_boundary": _data_boundary(),
                "memory_intent": {
                    "schema": "memory.intent.v1",
                    "memory_first": True,
                    "planner_only": True,
                    "route": "ANSWER",
                    "confidence": 0.9,
                },
                "receipt_dir": str(receipt_dir),
            }
        ),
    )

    assert status == 200
    assert payload["schema"] == "tau.server_route_receipt.v1"
    assert payload["ok"] is True
    assert (receipt_dir / "memory-intent-gate-receipt.json").exists()
    assert (receipt_dir / "evidence-case-gate-receipt.json").exists()


def test_server_run_status_receipts_and_package_routes(tmp_path: Path) -> None:
    run_dir = _write_run_dir(tmp_path)
    encoded = quote(str(run_dir), safe="")

    summary_status, summary = route_request(method="GET", target=f"/runs/{encoded}")
    status_status, status_payload = route_request(method="GET", target=f"/runs/{encoded}/status")
    receipts_status, receipts = route_request(method="GET", target=f"/runs/{encoded}/receipts")
    package_status, package = route_request(
        method="POST",
        target=f"/runs/{encoded}/compliance-package",
        body=_json_body({"out": str(tmp_path / "package")}),
    )

    assert summary_status == 200
    assert summary["route"] == "/runs/{id}"
    assert status_status == 200
    assert status_payload["schema"] == "tau.run_status.v1"
    assert receipts_status == 200
    assert receipts["receipt_count"] == 1
    assert package_status == 200
    assert package["schema"] == "tau.compliance_evidence_package.v1"
    assert (tmp_path / "package" / "package-manifest.json").exists()


def _write_run_dir(tmp_path: Path) -> Path:
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    contract = tmp_path / "dag-contract.json"
    _write_json(
        contract,
        {
            "schema": "tau.dag_contract.v1",
            "dag_id": "server-test",
            "goal": {"goal_id": "server-test", "goal_hash": "sha256:server-test"},
            "policy_profile": _policy_profile(),
            "data_boundary": _data_boundary(),
        },
    )
    _write_json(
        run_dir / "dag-receipt.json",
        {
            "schema": "tau.dag_receipt.v1",
            "ok": True,
            "status": "PASS",
            "contract_path": str(contract),
        },
    )
    return run_dir


def _policy_profile() -> dict:
    return {
        "schema": "tau.policy_profile.v1",
        "profile_id": "itar-zero-trust-local-only",
        "default_decision": "deny",
        "requires_data_boundary": True,
        "network": {"default": "deny", "allowed_domains": []},
        "providers": {"cloud_llm": "deny", "local_model": "allow_with_approval"},
        "research": {"external_search": "deny", "manual_sanitized_receipt": "allow_with_review"},
        "memory": {"read": "allow", "write": "approval_required"},
        "github": {"public_mutation": "deny", "dry_run_projection": "allow"},
        "filesystem": {"write_allowlist": [], "read_denylist": []},
    }


def _data_boundary() -> dict:
    return {
        "schema": "tau.data_boundary.v1",
        "classification": "public",
        "export_controlled": False,
        "itar": False,
        "technical_data": False,
        "foreign_person_access": "allowed",
        "external_provider_allowed": False,
        "external_research_allowed": False,
        "public_repo_allowed": False,
        "notes": [],
    }


def _json_body(payload: dict) -> bytes:
    return json.dumps(payload).encode("utf-8")


def _write_json(path: Path, payload: dict) -> None:
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
