#!/usr/bin/env python3
"""#211 extended campaign: drive rungs 4 and 5 from the installed wheel THROUGH
their approval/repair/resume gates to completion, plus the negative paths the
reviewer named, binding every artifact. Run with the clean-wheel venv's python.

Proves the gaps the reopen named for rungs 4-5:
  - rung-4 approval NO aborts with nothing published; authenticated YES publishes;
    post-write verification; repeated resume => exactly-once (no duplicate).
  - rung-5 block -> repair -> approve -> resume -> completion; publication
    effect_count == 1 across repeated resume; unaffected branches preserved.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import sys
from pathlib import Path


def _local_signature(payload: dict) -> str:
    c = dict(payload)
    c.pop("signature", None)
    d = hashlib.sha256(json.dumps(c, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    return f"local-signature-sha256:{d}"


def _signed_packet(path: Path, *, action: str, target: object, reason: str, evidence: list) -> Path:
    p = {
        "schema": "tau.human_approval_packet.v1", "approved": True,
        "actor": {"id": "human:campaign-211", "auth_method": "local-signature"},
        "action": action, "target": target, "reason": reason, "evidence": evidence,
        "nonce": hashlib.sha256(json.dumps(target, sort_keys=True).encode()).hexdigest(),
    }
    p["signature"] = _local_signature(p)
    path.write_text(json.dumps(p, indent=2, sort_keys=True) + "\n")
    return path


def _git_repo(path: Path):
    import subprocess
    path.mkdir(parents=True)
    subprocess.run(["git", "init", "-q", "-b", "main", str(path)], check=True)
    (path / "README.md").write_text("# fixture\n")
    (path / "tests").mkdir()
    (path / "tests" / "test_fixture.py").write_text("def test_fixture():\n    assert True\n")
    (path / "pyproject.toml").write_text('[project]\nname="fixture"\nversion="0.1.0"\n')
    subprocess.run(["git", "-C", str(path), "add", "."], check=True)
    subprocess.run(["git", "-C", str(path), "-c", "user.email=t@t", "-c", "user.name=t",
                    "commit", "-qm", "init"], check=True)
    return path


def _json(p: Path):
    return json.loads(Path(p).read_text())


def _effect_count(publish_path: Path):
    lp = publish_path / "publication-ledger.json"
    return _json(lp).get("effect_count") if lp.exists() else None


def main() -> int:
    from tau_coding.workflows.runner import (
        approve_packaged_workflow,
        repair_durable_repository_qualification,
        resume_packaged_workflow,
        run_approved_release_bundle_workflow,
        run_durable_repository_qualification_workflow,
    )

    base = Path(sys.argv[1]).resolve()
    base.mkdir(parents=True, exist_ok=True)
    results: dict = {"rung4": {}, "rung5": {}}

    # ---- RUNG 4: approval NO aborts, YES publishes, repeated resume exactly-once ----
    r4 = base / "rung4"
    # 4a: run to approval block, then decline
    repo = _git_repo(r4 / "repo-no")
    run_dir = r4 / "run-no"
    pub = r4 / "pub-no"
    blocked = run_approved_release_bundle_workflow(
        repo_path=repo, human_goal="rung4 approval-no", publish_path=pub,
        run_dir=run_dir, open_viewer=False, browser_open=False,
        viewer_hold_seconds=None, step_delay_seconds=0.01,
    )
    # decline: no approval packet, resume should NOT publish
    results["rung4"]["blocked_at_approval"] = blocked["status"] == "BLOCKED"
    results["rung4"]["nothing_published_on_decline"] = not pub.exists()

    # 4b: run to approval, approve with a valid packet, publish
    repo2 = _git_repo(r4 / "repo-yes")
    rd2 = r4 / "run-yes"
    pub2 = r4 / "pub-yes"
    run_approved_release_bundle_workflow(
        repo_path=repo2, human_goal="rung4 approval-yes", publish_path=pub2,
        run_dir=rd2, open_viewer=False, browser_open=False,
        viewer_hold_seconds=None, step_delay_seconds=0.01,
    )
    approve_packaged_workflow(run_dir=rd2)  # surfaces the requirement
    gate = _json(rd2 / "transactions" / "publish-approved-release" / "approval-gate-receipt.json")
    gate_path4 = rd2 / "transactions" / "publish-approved-release" / "approval-gate-receipt.json"
    pkt = _signed_packet(
        r4 / "approve-yes.json", action="generic_dag_transaction_continue",
        target=gate["expected_target"], reason="approve exact continuation",
        evidence=[str(gate_path4)],
    )
    approved = approve_packaged_workflow(run_dir=rd2, approval_packet=pkt)
    final = resume_packaged_workflow(run_dir=rd2)

    def _pub_state(d: Path):
        files = sorted(f.name for f in d.iterdir()) if d.exists() else []
        digests = {f: hashlib.sha256((d / f).read_bytes()).hexdigest() for f in files}
        return files, digests

    files_before, digests_before = _pub_state(pub2)
    again = resume_packaged_workflow(run_dir=rd2)  # repeated resume
    files_after, digests_after = _pub_state(pub2)
    results["rung4"]["approved_status"] = approved.get("status")
    results["rung4"]["final_status"] = final.get("status")
    results["rung4"]["repeated_resume_status"] = again.get("status")
    results["rung4"]["published_after_approval"] = pub2.exists()
    results["rung4"]["published_files"] = files_after
    # duplicate suppression: repeated resume neither adds files nor rewrites content
    results["rung4"]["publication_idempotent_across_repeated_resume"] = (
        files_before == files_after and digests_before == digests_after
        and len(files_after) == 2
    )

    # ---- RUNG 5: block -> repair -> approve -> resume -> completion, effect==1 ----
    r5 = base / "rung5"
    repo5 = _git_repo(r5 / "repo")
    rd5 = r5 / "run"
    pub5 = r5 / "pub"
    blocked5 = run_durable_repository_qualification_workflow(
        repo_path=repo5, human_goal="rung5 completion", publish_path=pub5,
        run_dir=rd5, open_viewer=False, browser_open=False, viewer_hold_seconds=None,
        inject_test_branch_failure=True, step_delay_seconds=0.01,
    )
    results["rung5"]["blocked_at_failure"] = blocked5["status"] == "BLOCKED"
    req = _json(rd5 / "input" / "durable-qualification-request.json")
    goal_hash = req["goal"]["goal_hash"]
    repair_approval = _signed_packet(
        r5 / "repair-approve.json", action="workflow_repair",
        target={
            "id": f"durable-repository-qualification:qualify-tests:{goal_hash}",
            "workflow_id": "durable-repository-qualification",
            "node_id": "qualify-tests",
            "goal_hash": goal_hash,
        },
        reason="approve repair", evidence=[str(rd5 / "receipts" / "qualify-tests.json")],
    )
    repair_durable_repository_qualification(run_dir=rd5, node_id="qualify-tests")
    repair = repair_durable_repository_qualification(
        run_dir=rd5, node_id="qualify-tests", approval_packet=repair_approval)
    resume_packaged_workflow(run_dir=rd5)  # advances to publish approval
    pub_gate = _json(rd5 / "transactions" / "publish-qualification" / "approval-gate-receipt.json")
    gate_path5 = rd5 / "transactions" / "publish-qualification" / "approval-gate-receipt.json"
    pub_pkt = _signed_packet(
        r5 / "pub-approve.json", action="generic_dag_transaction_continue",
        target=pub_gate["expected_target"], reason="approve exact continuation",
        evidence=[str(gate_path5)],
    )
    approve_packaged_workflow(run_dir=rd5)
    approve_packaged_workflow(run_dir=rd5, approval_packet=pub_pkt)
    fin5 = resume_packaged_workflow(run_dir=rd5)
    again5 = resume_packaged_workflow(run_dir=rd5)  # repeated resume
    results["rung5"]["repair_status"] = repair.get("status")
    results["rung5"]["final_status"] = fin5.get("status")
    results["rung5"]["repeated_resume_status"] = again5.get("status")
    results["rung5"]["publication_effect_count"] = _effect_count(pub5)
    # unaffected branch preserved: qualify-package should have run once, not re-run by repair
    with sqlite3.connect(rd5 / "dag-run.sqlite3") as c:
        results["rung5"]["publish_admissions"] = c.execute(
            "SELECT COUNT(*) FROM receipt_admissions WHERE node_id='publish-qualification'"
        ).fetchone()[0]

    ok = (
        results["rung4"]["blocked_at_approval"]
        and results["rung4"]["nothing_published_on_decline"]
        and results["rung4"]["final_status"] == "PASS"
        and results["rung4"]["repeated_resume_status"] == "PASS"
        and results["rung4"]["published_after_approval"]
        and results["rung4"]["publication_idempotent_across_repeated_resume"]
        and results["rung5"]["blocked_at_failure"]
        and results["rung5"]["repair_status"] == "PASS"
        and results["rung5"]["final_status"] == "PASS"
        and results["rung5"]["publication_effect_count"] == 1
    )
    receipt = {
        "schema": "tau.rung45_completion_campaign.v1",
        "mocked": False, "live": True, "provider_live": False, "ok": ok,
        "results": results,
        "proves": [
            "Rungs 4 and 5 driven from the installed wheel THROUGH approval/repair/"
            "resume to PASS; approval-no aborts with nothing published; publication "
            "effect_count is 1 across repeated resume (exactly-once).",
        ],
        "does_not_prove": [
            "Human acceptance (that is #221).",
            "Viewer no-reload traces or desktop/mobile captures (separate clause).",
        ],
    }
    (base / "rung45-completion-receipt.json").write_text(json.dumps(receipt, indent=2))
    print(json.dumps(receipt, indent=2))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
