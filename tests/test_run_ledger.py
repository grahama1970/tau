"""tau.run_ledger.v1: tamper-evident build/verify + agentic-eval admission (#327)."""
from __future__ import annotations
import copy
from tau_coding import run_ledger as rl


def _sample_entries():
    return [
        {"schema": "tau.dag_live_event.v1", "node": "start", "status": "scheduled"},
        {"schema": "tau.dag_live_event.v1", "node": "coder", "status": "pass"},
        rl.admit_agentic_eval({
            "skill": "surf", "readiness": "READY", "live": True, "mocked": False, "trial_count": 2,
            "cases": [{"name": "c1", "type": "adversarial", "real_world": True,
                       "trials": [{"outcome": "PASS"}, {"outcome": "PASS"}]}],
        }),
    ]


def test_intact_ledger_verifies():
    led = rl.build_ledger(_sample_entries(), goal_hash="sha256:goalA", run_id="run-1", dag_id="dag-1")
    v = rl.verify_ledger(led)
    assert v["ok"] is True and v["first_bad_index"] is None
    assert led["entry_count"] == 3 and led["head_hash"].startswith("sha256:")


def test_tampered_entry_fails_and_names_index():
    led = rl.build_ledger(_sample_entries(), goal_hash="sha256:goalA", run_id="run-1")
    tampered = copy.deepcopy(led)
    tampered["entries"][1]["payload"]["status"] = "fail"  # silent edit of entry 1
    v = rl.verify_ledger(tampered)
    assert v["ok"] is False
    assert v["first_bad_index"] == 1
    assert v["reason"] == "entry_hash_mismatch"


def test_deleted_entry_breaks_sequence():
    led = rl.build_ledger(_sample_entries(), goal_hash="sha256:goalA", run_id="run-1")
    tampered = copy.deepcopy(led)
    del tampered["entries"][1]  # drop an entry
    v = rl.verify_ledger(tampered)
    assert v["ok"] is False and v["first_bad_index"] == 1


def test_cross_run_splice_rejected():
    # An entry chain from goalA must not verify under goalB (goal binding).
    led = rl.build_ledger(_sample_entries(), goal_hash="sha256:goalA", run_id="run-1")
    spliced = copy.deepcopy(led)
    spliced["goal_hash"] = "sha256:goalB"
    v = rl.verify_ledger(spliced)
    assert v["ok"] is False


def test_agentic_eval_admitted_with_boundary():
    e = rl.admit_agentic_eval({"skill": "ask", "readiness": "READY", "live": True, "mocked": False,
                               "trial_count": 3,
                               "cases": [{"name": "x", "trials": [{"outcome": "PASS"}]}]})
    assert e["schema"] == rl.AGENTIC_EVAL_RECEIPT_SCHEMA
    assert e["readiness"] == "READY" and e["live"] is True and e["mocked"] is False
    assert e["cases"][0]["passed"] is True
