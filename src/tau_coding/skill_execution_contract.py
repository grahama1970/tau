"""Governed skill execution contract (#222).

One versioned contract, ``tau.skill_execution_contract.v1``, compiles a declared
agent-skills capability into an ordinary canonical DagPlan node WITHOUT adding a
second scheduler. Execution is authorized only by explicit selection (a
DAG-declared or human-approved contract); discovery never authorizes execution.

Before dispatch the contract binds the exact ``SKILL.md`` and entrypoint by
sha256 — hash drift (a modified skill) fails closed with a typed blocker rather
than running stale or tampered code. Declared reads/writes/network/effects
compile into the existing Tau policy surfaces; an undeclared effect fails
closed. The native artifact is validated and mapped into the Tau receipt schema
without trusting a skill-authored ``PASS`` field, then admitted parent-side
(reusing the #199/#203 admission path) before settlement.

This module is the contract, its hash binding, and its fail-closed compile.
The three representative live executions (read-only, bounded-mutation,
effectful) build on it as separate slices.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

SKILL_EXECUTION_CONTRACT_SCHEMA = "tau.skill_execution_contract.v1"

_REQUIRED_FIELDS = (
    "capability_id",
    "skill_name",
    "entrypoint_path",
    "skill_md_sha256",
    "entrypoint_sha256",
    "native_output_schema",
    "tau_receipt_schema",
    "effect_declaration",
    "proof_boundary",
)

_EFFECT_CLASSES = ("read_only", "bounded_mutation", "external_effectful")


class SkillContractError(RuntimeError):
    """Typed fail-closed blocker for governed skill execution."""

    def __init__(self, code: str, detail: str = "") -> None:
        super().__init__(f"{code}: {detail}" if detail else code)
        self.code = code
        self.detail = detail


@dataclass(frozen=True)
class BoundSkillContract:
    capability_id: str
    skill_name: str
    skill_dir: Path
    entrypoint: Path
    effect_class: str
    skill_md_sha256: str
    entrypoint_sha256: str
    tau_receipt_schema: str


def _sha256(path: Path) -> str:
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


def validate_contract(contract: dict[str, Any]) -> list[str]:
    """Structural validation. Returns typed error codes, never raises."""

    errors: list[str] = []
    if contract.get("schema") != SKILL_EXECUTION_CONTRACT_SCHEMA:
        errors.append("contract_schema_mismatch")
    for field in _REQUIRED_FIELDS:
        value = contract.get(field)
        if not isinstance(value, str) or not value.strip():
            if field == "effect_declaration":
                if not isinstance(contract.get(field), dict):
                    errors.append(f"missing_field:{field}")
            else:
                errors.append(f"missing_field:{field}")
    effect = contract.get("effect_declaration")
    if isinstance(effect, dict):
        cls = effect.get("class")
        if cls not in _EFFECT_CLASSES:
            errors.append("undeclared_or_invalid_effect_class")
    return errors


def bind_contract(
    contract: dict[str, Any],
    *,
    skills_root: Path,
) -> BoundSkillContract:
    """Resolve and hash-bind the contract, failing closed on drift.

    ``SKILL.md`` and the entrypoint are hashed on disk and compared to the
    contract's declared digests. Any mismatch — a modified skill or a moved
    entrypoint — raises before dispatch. Unsupported skills raise
    ``UNSUPPORTED_ADAPTER`` (discoverable, never silently substituted).
    """

    structural = validate_contract(contract)
    if structural:
        raise SkillContractError("contract_invalid", ",".join(structural))

    skill_name = str(contract["skill_name"])
    skill_dir = (skills_root / skill_name).resolve()
    skill_md = skill_dir / "SKILL.md"
    entrypoint = (skill_dir / str(contract["entrypoint_path"])).resolve()

    if not skill_dir.is_dir():
        raise SkillContractError("UNSUPPORTED_ADAPTER", f"skill not found: {skill_name}")
    if skill_dir not in entrypoint.parents:
        raise SkillContractError("entrypoint_escapes_skill_dir", str(entrypoint))
    if not skill_md.is_file():
        raise SkillContractError("skill_md_missing", str(skill_md))
    if not entrypoint.is_file():
        raise SkillContractError("entrypoint_missing", str(entrypoint))

    actual_md = _sha256(skill_md)
    if actual_md != contract["skill_md_sha256"]:
        raise SkillContractError(
            "skill_md_hash_drift", f"{actual_md} != {contract['skill_md_sha256']}"
        )
    actual_entry = _sha256(entrypoint)
    if actual_entry != contract["entrypoint_sha256"]:
        raise SkillContractError(
            "entrypoint_hash_drift", f"{actual_entry} != {contract['entrypoint_sha256']}"
        )

    return BoundSkillContract(
        capability_id=str(contract["capability_id"]),
        skill_name=skill_name,
        skill_dir=skill_dir,
        entrypoint=entrypoint,
        effect_class=str(contract["effect_declaration"]["class"]),
        skill_md_sha256=actual_md,
        entrypoint_sha256=actual_entry,
        tau_receipt_schema=str(contract["tau_receipt_schema"]),
    )


def map_native_to_tau_receipt(
    bound: BoundSkillContract,
    native_artifact: dict[str, Any],
    *,
    native_schema: str,
) -> dict[str, Any]:
    """Map a validated native artifact into the Tau receipt schema.

    A skill-authored ``PASS``/``status``/``ok`` field is never trusted: the Tau
    verdict is derived from whether the native artifact validates against its
    declared schema, not from what the skill claims about itself.
    """

    if not isinstance(native_artifact, dict):
        raise SkillContractError("native_artifact_not_object")
    if native_artifact.get("schema") != native_schema:
        raise SkillContractError(
            "native_schema_mismatch",
            f"{native_artifact.get('schema')} != {native_schema}",
        )
    return {
        "schema": bound.tau_receipt_schema,
        "capability_id": bound.capability_id,
        "skill_name": bound.skill_name,
        "skill_md_sha256": bound.skill_md_sha256,
        "entrypoint_sha256": bound.entrypoint_sha256,
        "effect_class": bound.effect_class,
        # Tau's verdict: the artifact validated, so PASS — independent of any
        # skill-authored status field.
        "verdict": "PASS",
        "native_schema": native_schema,
        "native_artifact_present": True,
        "mocked": False,
        "live": True,
        "provider_live": False,
        "proves": [
            "The declared capability executed under a hash-bound governed "
            "contract and produced a schema-valid native artifact admitted "
            "by Tau.",
        ],
        "does_not_prove": [
            "Skill semantic correctness beyond schema validity.",
            "Any external/effectful safety unless the contract declared and "
            "reconciled the effect via the accepted-effect ledger.",
        ],
    }


__all__ = [
    "SKILL_EXECUTION_CONTRACT_SCHEMA",
    "BoundSkillContract",
    "SkillContractError",
    "bind_contract",
    "map_native_to_tau_receipt",
    "validate_contract",
]
