"""#222 effectful-class live proof: governed external effect via the #218
EffectLedger, with a CONFIRMED read-back (using the fixed create-evidence-case
get), duplicate suppression, and Tau admission.

Flow:
  declare(intent, before the external call)
    -> run create-evidence-case live (external effect: upsert to evidence_cases)
    -> read back via `get` (now works after the read-back fix) = success evidence
    -> acquire + mark_succeeded(evidence={target_identity, read_back}) -> mark_accepted
    -> re-run same claim -> identical _key (duplicate suppression, one logical effect)
    -> admit the case via evidence_case_skill_adapter (memory.evidence_case.v1)
"""

import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

from tau_coding.dag_runtime.compiler import compile_generic_dag_plan
from tau_coding.dag_runtime.effects import EffectLedger
from tau_coding.dag_runtime.run_store import SqliteDagRunStore
from tau_coding.evidence_case_skill_adapter import write_evidence_case_skill_adapter_receipt

CEC = Path(sys.argv[1])  # fixed create-evidence-case skill dir
OUT = Path(sys.argv[2])
OUT.mkdir(parents=True, exist_ok=True)
CLAIM = "NIST AC-7 Unsuccessful Logon Attempts enforces a limit on consecutive invalid attempts"


def run_cec(*args: str) -> dict:
    proc = subprocess.run(
        ["bash", str(CEC / "run.sh"), *args],
        capture_output=True, text=True, timeout=300, cwd=str(CEC),
    )
    # create/get --json emit JSON on stdout
    start = proc.stdout.find("{")
    return json.loads(proc.stdout[start:]) if start >= 0 else {"_stderr": proc.stderr[-300:]}


result = {"schema": "tau.governed_effectful_proof.v1", "mocked": False, "live": True,
          "provider_live": True, "steps": {}}

# ---- store / lease / effect ledger ----
plan = compile_generic_dag_plan(
    {"schema": "tau.generic_dag_spec.v1", "run_id": "run-eff", "run_dir": str(OUT / "run"),
     "nodes": [{"node_id": "n", "role": "n", "command": ["true"], "depends_on": [],
                "accepted_context_from": [], "receipt_path": str(OUT / "n.json"),
                "timeout_seconds": 1, "max_attempts": 1}]},
    source_path=OUT / "dag.json",
)
store = SqliteDagRunStore(OUT / "dag-run.sqlite3")
lease = store.acquire_run(plan=plan, run_id="run-eff", owner_id="t", ttl_seconds=120)
fx = EffectLedger(store)

# effect identity keyed on the deterministic claim input (survives re-runs)
effect_key = "claim-" + hashlib.sha256(CLAIM.encode()).hexdigest()[:16]
IDENT = {"effect_type": "evidence_case", "effect_scope": "evidence_cases", "effect_key": effect_key}

# ---- declare BEFORE the external call ----
fx.declare(lease, **IDENT, reconciliation="handler")
handle = fx.acquire(lease, **IDENT, owner_attempt_id="attempt-1")
result["steps"]["declared_before_call"] = handle is not None

# ---- external effect: run create live ----
os.environ["TAU_AGENT_SKILLS_ROOT"] = str(CEC.parents[1])
create1 = run_cec("create", CLAIM, "--json", "--quiet")
claim_id = (create1.get("claim") or {}).get("id")
result["steps"]["external_effect_persisted"] = create1.get("persisted") is True
result["steps"]["claim_id"] = claim_id

# ---- read back via get (success evidence) ----
readback = run_cec("get", claim_id, "--json")
rb_claim_id = (readback.get("claim") or {}).get("id")
rb_key = readback.get("_key")
result["steps"]["read_back_confirmed"] = rb_claim_id == claim_id and rb_key is not None
result["steps"]["read_back_key"] = rb_key

# ---- mark_succeeded with external read-back evidence, then accepted ----
if result["steps"]["read_back_confirmed"]:
    fx.mark_succeeded(lease, handle, evidence={
        "target_identity": f"evidence_cases/{rb_key}",
        "read_back": {"claim_id": rb_claim_id, "key": rb_key},
    })
    fx.mark_accepted(lease, handle)
    result["steps"]["effect_lifecycle"] = "intent->succeeded->accepted"

# ---- duplicate suppression: same claim -> same stored _key, one logical effect ----
create2 = run_cec("create", CLAIM, "--json", "--quiet")
readback2 = run_cec("get", (create2.get("claim") or {}).get("id"), "--json")
result["steps"]["duplicate_suppressed"] = readback2.get("_key") == rb_key
# declare is idempotent: still exactly one effect row
result["steps"]["effect_rows"] = len(fx.list_effects())

# ---- admit the case via the Tau adapter ----
case_path = OUT / "native-case.json"
case_path.write_text(json.dumps(readback), encoding="utf-8")
admission = write_evidence_case_skill_adapter_receipt(
    case_path=case_path, output_path=OUT / "admission.json", repo_root=OUT,
)
result["steps"]["tau_admission_status"] = admission.get("status")
result["steps"]["tau_admission_ok"] = admission.get("ok")

result["ok"] = (
    result["steps"].get("external_effect_persisted")
    and result["steps"].get("read_back_confirmed")
    and result["steps"].get("effect_lifecycle") == "intent->succeeded->accepted"
    and result["steps"].get("duplicate_suppressed")
    and result["steps"].get("effect_rows") == 1
)
(OUT / "effectful-proof-receipt.json").write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
print(json.dumps(result["steps"], indent=2))
print("OK:", result["ok"])
