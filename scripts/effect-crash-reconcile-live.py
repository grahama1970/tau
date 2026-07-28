#!/usr/bin/env python3
"""Live #218 harness: a real child process declares an effect intent,
acquires ownership, performs a REAL external effect (writes a file outside
the run dir), and is SIGKILLed before mark_succeeded. After restart,
mark_uncertain_effects must surface the ambiguity, the run must not silently
accept, and a reconciliation pass (digest read-back of the external target)
must resolve it to reconciled. Exit 0 only if every read-back matches."""

from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(SRC))

from tau_coding.dag_runtime.admission import write_durable_json  # noqa: E402
from tau_coding.dag_runtime.compiler import compile_generic_dag_plan  # noqa: E402
from tau_coding.dag_runtime.effects import EffectLedger  # noqa: E402
from tau_coding.dag_runtime.run_store import DagRunLease, SqliteDagRunStore  # noqa: E402

CHILD = """
import os, signal, sys, time
sys.path.insert(0, {src!r})
from pathlib import Path
from tau_coding.dag_runtime.effects import EffectLedger
from tau_coding.dag_runtime.run_store import DagRunLease, SqliteDagRunStore
store = SqliteDagRunStore(Path({db!r}))
lease = DagRunLease({run_id!r}, {owner!r}, {epoch}, {expires})
fx = EffectLedger(store)
fx.declare(lease, effect_type="filesystem_publish", effect_scope={scope!r},
           effect_key="artifact-1", reconciliation="handler")
handle = fx.acquire(lease, effect_type="filesystem_publish", effect_scope={scope!r},
                    effect_key="artifact-1", owner_attempt_id="attempt-live",
                    ttl_seconds=0.2)
assert handle is not None
Path({target!r}).write_text("EXTERNAL EFFECT LANDED")   # the real external call
os.kill(os.getpid(), signal.SIGKILL)                     # dies before succeeded
"""


def main() -> int:
    base = Path(sys.argv[1] if len(sys.argv) > 1 else "effect-crash-run").resolve()
    base.mkdir(parents=True, exist_ok=True)
    external = base / "external-target.txt"
    db = base / "dag-run.sqlite3"
    plan = compile_generic_dag_plan(
        {"schema": "tau.generic_dag_spec.v1", "run_id": "run-fx-live",
         "run_dir": str(base / "run"),
         "nodes": [{"node_id": "n", "role": "n", "command": ["true"],
                    "depends_on": [], "accepted_context_from": [],
                    "receipt_path": str(base / "n.json"),
                    "timeout_seconds": 1, "max_attempts": 1}]},
        source_path=base / "dag.json",
    )
    store = SqliteDagRunStore(db)
    lease = store.acquire_run(plan=plan, run_id="run-fx-live", owner_id="fx", ttl_seconds=300)
    store.close()

    code = CHILD.format(src=str(SRC), db=str(db), run_id=lease.run_id,
                        owner=lease.owner_id, epoch=lease.epoch,
                        expires=lease.expires_at_ms, scope=str(base),
                        target=str(external))
    proc = subprocess.run([sys.executable, "-c", code], capture_output=True)
    killed = proc.returncode == -9
    time.sleep(0.3)  # let the effect lease expire

    store = SqliteDagRunStore(db)
    lease2 = DagRunLease(lease.run_id, lease.owner_id, lease.epoch, lease.expires_at_ms)
    fx = EffectLedger(store)
    moved = fx.mark_uncertain_effects(lease2)
    uncertain_ok = [m["effect_key"] for m in moved] == ["artifact-1"]

    # Reconciliation handler for filesystem_publish: read back the target.
    external_landed = external.exists() and external.read_text() == "EXTERNAL EFFECT LANDED"
    handle = fx.acquire(lease2, effect_type="filesystem_publish",
                        effect_scope=str(base), effect_key="artifact-1",
                        owner_attempt_id="attempt-reconcile")
    fx.mark_reconciled(lease2, handle)
    final_state = fx.list_effects()[0]["state"]
    store.close()

    ok = killed and uncertain_ok and external_landed and final_state == "reconciled"
    receipt = {
        "schema": "tau.effect_crash_reconcile_receipt.v1",
        "mocked": False, "live": True, "ok": ok,
        "child_killed": killed,
        "uncertain_after_crash": uncertain_ok,
        "external_effect_landed_before_crash": external_landed,
        "final_state": final_state,
    }
    write_durable_json(base / "effect-crash-receipt.json", receipt)
    print(json.dumps(receipt, indent=2))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
