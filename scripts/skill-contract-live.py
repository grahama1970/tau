#!/usr/bin/env python3
"""Live #222 read-only proof: bind the contract against the REAL review-code
skill on disk, prove correct binding, then prove hash drift fails closed when
SKILL.md is modified. Reads the actual agent-skills checkout."""
import hashlib
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from tau_coding.skill_execution_contract import (
    SKILL_EXECUTION_CONTRACT_SCHEMA,
    SkillContractError,
    bind_contract,
)


def sha(p): return "sha256:" + hashlib.sha256(p.read_bytes()).hexdigest()

skills_root = Path("/home/graham/workspace/experiments/agent-skills/skills")
skill = skills_root / "review-code"
contract = {
    "schema": SKILL_EXECUTION_CONTRACT_SCHEMA,
    "capability_id": "code-review-readonly",
    "skill_name": "review-code",
    "entrypoint_path": "run.sh",
    "skill_md_sha256": sha(skill / "SKILL.md"),
    "entrypoint_sha256": sha(skill / "run.sh"),
    "native_output_schema": "tau.review_code_native.v1",
    "tau_receipt_schema": "tau.skill_execution_receipt.v1",
    "effect_declaration": {"class": "read_only"},
    "proof_boundary": "read-only bind against the real review-code SKILL.md",
}
bound = bind_contract(contract, skills_root=skills_root)
bind_ok = bound.skill_name == "review-code" and bound.effect_class == "read_only"

# Now flip one declared digest to simulate a tampered SKILL.md -> must fail closed.
tampered = dict(contract, skill_md_sha256="sha256:" + "0" * 64)
drift_code = None
try:
    bind_contract(tampered, skills_root=skills_root)
except SkillContractError as e:
    drift_code = e.code

ok = bind_ok and drift_code == "skill_md_hash_drift"
receipt = {
    "schema": "tau.skill_contract_live_receipt.v1",
    "mocked": False, "live": True, "ok": ok,
    "real_skill": str(skill),
    "bound_ok": bind_ok,
    "bound_skill_md_sha256": bound.skill_md_sha256[:24],
    "hash_drift_fail_closed_code": drift_code,
}
out = Path(sys.argv[1] if len(sys.argv) > 1 else "222-live-run")
out.mkdir(parents=True, exist_ok=True)
(out / "skill-contract-receipt.json").write_text(json.dumps(receipt, indent=2))
print(json.dumps(receipt, indent=2))
sys.exit(0 if ok else 1)
