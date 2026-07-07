import json
from pathlib import Path

from typer.testing import CliRunner

from tau_coding.cli import app
from tau_coding.skill_capability_registry import (
    SKILL_CAPABILITY_REGISTRY_SCHEMA,
    SKILL_CAPABILITY_REGISTRY_VALIDATION_RECEIPT_SCHEMA,
    validate_skill_capability_registry,
    write_skill_capability_registry_validation_receipt,
)


def test_registry_accepts_known_skills(tmp_path: Path) -> None:
    skills_root = _skills_root(tmp_path, "debugger", "code-runner")
    registry = _registry(
        {
            "debug_runtime_state": {
                "skill": "debugger",
                "native_artifact_schema": "debugger.proof.v1",
                "tau_receipt_schema": "tau.debug_session_receipt.v1",
                "required_for_triggers": ["debugger_evidence_required"],
            },
            "bounded_code_fix": {
                "skill": "code-runner",
                "native_artifact_schema": "code_runner.result.v1",
                "tau_receipt_schema": "tau.code_patch_receipt.v1",
                "required_for_triggers": ["retry_node"],
            },
        }
    )

    assert validate_skill_capability_registry(registry, skills_root=skills_root) == []


def test_registry_blocks_missing_skill_name(tmp_path: Path) -> None:
    registry = _registry(
        {
            "debug_runtime_state": {
                "skill": "missing-debugger",
                "tau_receipt_schema": "tau.debug_session_receipt.v1",
            }
        }
    )

    errors = validate_skill_capability_registry(registry, skills_root=_skills_root(tmp_path))

    assert any(
        "skill does not exist under skills_root: missing-debugger" in error
        for error in errors
    )


def test_registry_blocks_missing_tau_receipt_schema(tmp_path: Path) -> None:
    skills_root = _skills_root(tmp_path, "debugger")
    registry = _registry(
        {
            "debug_runtime_state": {
                "skill": "debugger",
                "native_artifact_schema": "debugger.proof.v1",
            }
        }
    )

    errors = validate_skill_capability_registry(registry, skills_root=skills_root)

    assert (
        "capabilities.debug_runtime_state.tau_receipt_schema must be a non-empty string"
        in errors
    )


def test_registry_blocks_unknown_required_trigger(tmp_path: Path) -> None:
    skills_root = _skills_root(tmp_path, "debugger")
    registry = _registry(
        {
            "debug_runtime_state": {
                "skill": "debugger",
                "tau_receipt_schema": "tau.debug_session_receipt.v1",
                "required_for_triggers": ["invent_swarm_retry"],
            }
        }
    )

    errors = validate_skill_capability_registry(registry, skills_root=skills_root)

    assert (
        "capabilities.debug_runtime_state.required_for_triggers[] unknown trigger: "
        "invent_swarm_retry"
    ) in errors


def test_registry_validation_receipt_has_non_claims(tmp_path: Path) -> None:
    skills_root = _skills_root(tmp_path, "debugger")
    registry_path = tmp_path / "registry.json"
    registry_path.write_text(
        json.dumps(
            _registry(
                {
                    "debug_runtime_state": {
                        "skill": "debugger",
                        "tau_receipt_schema": "tau.debug_session_receipt.v1",
                    }
                }
            )
        ),
        encoding="utf-8",
    )
    receipt_path = tmp_path / "receipt.json"

    receipt = write_skill_capability_registry_validation_receipt(
        registry_path,
        receipt_path,
        skills_root=skills_root,
    )

    assert receipt["schema"] == SKILL_CAPABILITY_REGISTRY_VALIDATION_RECEIPT_SCHEMA
    assert receipt["status"] == "PASS"
    assert receipt["ok"] is True
    assert receipt["capability_count"] == 1
    assert receipt["skill_names"] == ["debugger"]
    assert "Any skill was executed." in receipt["proof_scope"]["does_not_prove"]
    assert json.loads(receipt_path.read_text(encoding="utf-8")) == receipt


def test_cli_skill_capability_registry_validate_blocks_bad_registry(tmp_path: Path) -> None:
    registry_path = tmp_path / "registry.json"
    registry_path.write_text(
        json.dumps(
            _registry(
                {
                    "debug_runtime_state": {
                        "skill": "debugger",
                        "tau_receipt_schema": "tau.debug_session_receipt.v1",
                        "required_for_triggers": ["unknown_trigger"],
                    }
                }
            )
        ),
        encoding="utf-8",
    )
    receipt_path = tmp_path / "receipt.json"

    result = CliRunner().invoke(
        app,
        [
            "skill-capability-registry-validate",
            "--registry",
            str(registry_path),
            "--out",
            str(receipt_path),
            "--skills-root",
            str(_skills_root(tmp_path, "debugger")),
        ],
    )

    payload = json.loads(result.output)
    assert result.exit_code == 1
    assert payload["schema"] == SKILL_CAPABILITY_REGISTRY_VALIDATION_RECEIPT_SCHEMA
    assert payload["status"] == "BLOCKED"
    assert any("unknown_trigger" in error for error in payload["errors"])


def _registry(capabilities: dict) -> dict:
    return {
        "schema": SKILL_CAPABILITY_REGISTRY_SCHEMA,
        "capabilities": capabilities,
    }


def _skills_root(tmp_path: Path, *names: str) -> Path:
    root = tmp_path / "skills"
    root.mkdir(exist_ok=True)
    for name in names:
        skill_dir = root / name
        skill_dir.mkdir()
        (skill_dir / "SKILL.md").write_text(f"---\nname: {name}\n---\n", encoding="utf-8")
    return root
