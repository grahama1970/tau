"""Governed skill execution contract tests (#222 core: bind + fail-closed)."""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from tau_coding.skill_execution_contract import (
    SKILL_EXECUTION_CONTRACT_SCHEMA,
    SkillContractError,
    bind_contract,
    map_native_to_tau_receipt,
    validate_contract,
)


def _sha256(p: Path) -> str:
    return "sha256:" + hashlib.sha256(p.read_bytes()).hexdigest()


def _skill(tmp_path: Path, name="read-fixture", body="entry\n"):
    root = tmp_path / "skills"
    d = root / name
    d.mkdir(parents=True)
    (d / "SKILL.md").write_text(f"# {name}\nread only\n")
    (d / "run.sh").write_text(body)
    return root, d


def _contract(root: Path, d: Path, name="read-fixture", cls="read_only"):
    return {
        "schema": SKILL_EXECUTION_CONTRACT_SCHEMA,
        "capability_id": "cap-1",
        "skill_name": name,
        "entrypoint_path": "run.sh",
        "skill_md_sha256": _sha256(d / "SKILL.md"),
        "entrypoint_sha256": _sha256(d / "run.sh"),
        "native_output_schema": "tau.review_code_native.v1",
        "tau_receipt_schema": "tau.skill_execution_receipt.v1",
        "effect_declaration": {"class": cls},
        "proof_boundary": "read-only local fixture",
    }


def test_bind_succeeds_on_matching_hashes(tmp_path: Path) -> None:
    root, d = _skill(tmp_path)
    bound = bind_contract(_contract(root, d), skills_root=root)
    assert bound.skill_name == "read-fixture"
    assert bound.effect_class == "read_only"


def test_skill_md_hash_drift_fails_before_dispatch(tmp_path: Path) -> None:
    root, d = _skill(tmp_path)
    contract = _contract(root, d)
    (d / "SKILL.md").write_text("# tampered after contract\n")
    with pytest.raises(SkillContractError) as e:
        bind_contract(contract, skills_root=root)
    assert e.value.code == "skill_md_hash_drift"


def test_entrypoint_hash_drift_fails(tmp_path: Path) -> None:
    root, d = _skill(tmp_path)
    contract = _contract(root, d)
    (d / "run.sh").write_text("malicious\n")
    with pytest.raises(SkillContractError) as e:
        bind_contract(contract, skills_root=root)
    assert e.value.code == "entrypoint_hash_drift"


def test_unknown_skill_reports_unsupported_adapter(tmp_path: Path) -> None:
    root, d = _skill(tmp_path)
    contract = _contract(root, d, name="does-not-exist")
    contract["skill_name"] = "does-not-exist"
    with pytest.raises(SkillContractError) as e:
        bind_contract(contract, skills_root=root)
    assert e.value.code == "UNSUPPORTED_ADAPTER"


def test_undeclared_effect_class_is_rejected(tmp_path: Path) -> None:
    root, d = _skill(tmp_path)
    contract = _contract(root, d, cls="whatever")
    assert "undeclared_or_invalid_effect_class" in validate_contract(contract)


def test_entrypoint_escaping_skill_dir_is_refused(tmp_path: Path) -> None:
    root, d = _skill(tmp_path)
    contract = _contract(root, d)
    contract["entrypoint_path"] = "../../etc/passwd"
    with pytest.raises(SkillContractError):
        bind_contract(contract, skills_root=root)


def test_native_status_is_not_trusted(tmp_path: Path) -> None:
    root, d = _skill(tmp_path)
    bound = bind_contract(_contract(root, d), skills_root=root)
    # Native artifact claims PASS but with the WRONG schema -> Tau rejects.
    with pytest.raises(SkillContractError) as e:
        map_native_to_tau_receipt(
            bound, {"schema": "wrong", "status": "PASS"},
            native_schema="tau.review_code_native.v1",
        )
    assert e.value.code == "native_schema_mismatch"
    # A schema-valid artifact maps to PASS regardless of any claimed status.
    receipt = map_native_to_tau_receipt(
        bound, {"schema": "tau.review_code_native.v1", "status": "FAIL"},
        native_schema="tau.review_code_native.v1",
    )
    assert receipt["verdict"] == "PASS"
    assert receipt["mocked"] is False
