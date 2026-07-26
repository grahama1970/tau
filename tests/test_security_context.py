import json
from pathlib import Path

from tau_coding.receipt_signing import sign_receipt
from tau_coding.security_context import resolve_security_context


def _base_contract(tmp_path: Path, *, data_boundary: object) -> dict[str, object]:
    return {
        "schema": "tau.dag_contract.v1",
        "dag_id": "secure-context-test",
        "goal": {
            "goal_id": "secure-context-goal",
            "goal_version": 1,
            "goal_hash": "sha256:secure-context",
        },
        "policy_profile": str(_write_policy(tmp_path)),
        "data_boundary": data_boundary,
        "command_policy": str(_write_command_policy(tmp_path)),
    }


def test_security_context_embedded_and_relative_itar_boundary_match(tmp_path: Path) -> None:
    boundary = _itar_boundary()
    boundary_path = tmp_path / "data-boundary.json"
    boundary_path.write_text(json.dumps(boundary), encoding="utf-8")
    contract_path = tmp_path / "dag.json"
    contract_path.write_text("{}", encoding="utf-8")

    embedded = resolve_security_context(
        dag_contract=_base_contract(tmp_path, data_boundary=boundary),
        contract_path=contract_path,
        receipt_dir=tmp_path / "embedded-run",
        requested_mode="secure",
    )
    relative = resolve_security_context(
        dag_contract=_base_contract(tmp_path, data_boundary="data-boundary.json"),
        contract_path=contract_path,
        receipt_dir=tmp_path / "relative-run",
        requested_mode="secure",
    )

    assert embedded.context["data_boundary"]["controlled_boundary"] is True
    assert relative.context["data_boundary"]["controlled_boundary"] is True
    assert embedded.context["required_gates"] == relative.context["required_gates"]
    assert embedded.context["data_boundary"]["source_kind"] == "embedded"
    assert relative.context["data_boundary"]["source_kind"] == "relative_path"


def test_security_context_blocks_controlled_development_mode(tmp_path: Path) -> None:
    boundary_path = tmp_path / "data-boundary.json"
    boundary_path.write_text(json.dumps(_itar_boundary()), encoding="utf-8")
    contract_path = tmp_path / "dag.json"
    contract_path.write_text("{}", encoding="utf-8")

    result = resolve_security_context(
        dag_contract=_base_contract(tmp_path, data_boundary="data-boundary.json"),
        contract_path=contract_path,
        receipt_dir=tmp_path / "run",
        requested_mode="development",
    )

    assert result.receipt["status"] == "BLOCKED"
    assert "controlled_boundary_requires_secure_mode" in result.receipt["alert_codes"]


def test_development_generated_environment_manifest_records_inherited_secret_names(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "anthropic-test-value")
    monkeypatch.setenv("GH_TOKEN", "github-test-value")
    contract_path = tmp_path / "dag.json"
    contract_path.write_text("{}", encoding="utf-8")

    result = resolve_security_context(
        dag_contract=_base_contract(tmp_path, data_boundary=_public_boundary()),
        contract_path=contract_path,
        receipt_dir=tmp_path / "run",
        requested_mode="development",
    )

    assert result.receipt["status"] == "PASS"
    assert result.environment_manifest_path is not None
    manifest = json.loads(result.environment_manifest_path.read_text(encoding="utf-8"))
    serialized = json.dumps(manifest, sort_keys=True)
    assert manifest["network_policy"] == "unrestricted"
    assert manifest["host_environment_inherited"] is True
    assert manifest["environment_attestation"] == "non_attesting_host_environment_inherited"
    assert "ANTHROPIC_API_KEY" in manifest["secrets_visible"]
    assert "GH_TOKEN" in manifest["secrets_visible"]
    assert "ANTHROPIC_API_KEY" in manifest["environment_variables_visible"]
    assert "GH_TOKEN" in manifest["environment_variables_visible"]
    assert "anthropic-test-value" not in serialized
    assert "github-test-value" not in serialized


def test_signed_receipt_with_generated_development_manifest_cannot_claim_no_secret_exposure(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("SCILLM_API_KEY", "scillm-test-value")
    contract_path = tmp_path / "dag.json"
    contract_path.write_text("{}", encoding="utf-8")
    result = resolve_security_context(
        dag_contract=_base_contract(tmp_path, data_boundary=_public_boundary()),
        contract_path=contract_path,
        receipt_dir=tmp_path / "run",
        requested_mode="development",
    )
    assert result.environment_manifest_path is not None
    receipt_path = tmp_path / "receipt.json"
    receipt_path.write_text(
        json.dumps({"schema": "tau.test_receipt.v1", "ok": True, "status": "PASS"}),
        encoding="utf-8",
    )
    key_path = tmp_path / "signing.key"
    key_path.write_text("local-test-key", encoding="utf-8")

    signed = sign_receipt(
        receipt_path=receipt_path,
        key_path=key_path,
        output_path=tmp_path / "signed.json",
        environment_manifest_path=result.environment_manifest_path,
    )

    manifest = json.loads(result.environment_manifest_path.read_text(encoding="utf-8"))
    assert signed["status"] == "PASS"
    assert manifest["host_environment_inherited"] is True
    assert "SCILLM_API_KEY" in manifest["secrets_visible"]
    assert manifest["secrets_visible"]
    assert signed["signed_payload"]["environment_manifest"]["sha256"].startswith("sha256:")


def test_security_context_secure_mode_requires_actor_access_and_generates_environment(
    tmp_path: Path,
) -> None:
    boundary_path = tmp_path / "data-boundary.json"
    boundary_path.write_text(json.dumps(_itar_boundary()), encoding="utf-8")
    contract_path = tmp_path / "dag.json"
    contract_path.write_text("{}", encoding="utf-8")

    result = resolve_security_context(
        dag_contract=_base_contract(tmp_path, data_boundary="data-boundary.json"),
        contract_path=contract_path,
        receipt_dir=tmp_path / "run",
        requested_mode="secure",
    )

    assert "missing_actor_access_manifest" in result.receipt["alert_codes"]
    assert (tmp_path / "run" / "environment-manifest.json").exists()
    assert result.context["environment"]["generated"] is True
    assert result.context["environment"]["sha256"].startswith("sha256:")


def _write_policy(tmp_path: Path) -> Path:
    path = tmp_path / "policy-profile.json"
    path.write_text(
        json.dumps(
            {
                "schema": "tau.policy_profile.v1",
                "profile_id": "secure-context-test",
                "default_decision": "deny",
                "requires_data_boundary": True,
                "network": {"default": "deny"},
                "providers": {"cloud_llm": "deny", "local_model": "allow_with_review"},
                "research": {
                    "external_search": "deny",
                    "manual_sanitized_receipt": "allow_with_review",
                },
                "memory": {"read": "allow_with_review", "write": "approval_required"},
                "github": {"public_mutation": "deny", "dry_run_projection": "allow_with_review"},
                "filesystem": {"write_allowlist": [], "read_denylist": []},
            }
        ),
        encoding="utf-8",
    )
    return path


def _write_command_policy(tmp_path: Path) -> Path:
    path = tmp_path / "command-policy.json"
    path.write_text(
        json.dumps(
            {
                "schema": "tau.command_spec_policy.v1",
                "allowed_command_roots": ["python3"],
                "denied_commands": [],
                "allowed_cwd_roots": [str(tmp_path)],
            }
        ),
        encoding="utf-8",
    )
    return path


def _itar_boundary() -> dict[str, object]:
    return {
        "schema": "tau.data_boundary.v1",
        "classification": "ITAR",
        "export_controlled": True,
        "itar": True,
        "technical_data": True,
        "foreign_person_access": "prohibited",
        "external_provider_allowed": False,
        "external_research_allowed": False,
        "public_repo_allowed": False,
        "notes": ["synthetic fixture only"],
    }


def _public_boundary() -> dict[str, object]:
    return {
        "schema": "tau.data_boundary.v1",
        "classification": "public",
        "export_controlled": False,
        "itar": False,
        "technical_data": False,
        "foreign_person_access": "allowed",
        "external_provider_allowed": True,
        "external_research_allowed": False,
        "public_repo_allowed": False,
        "notes": ["synthetic fixture only"],
    }
