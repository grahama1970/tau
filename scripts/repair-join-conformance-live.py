#!/usr/bin/env python3
"""Live #219 conformance: drives the REAL durable-repository-qualification
workflow through block -> targeted repair -> approve -> resume, then reads
back from the produced artifacts that the publisher released exactly once and
the repaired node created a fresh admitted attempt. Reuses the proven test
helpers so this is the same real path CI exercises."""

from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src"
TESTS = Path(__file__).resolve().parents[1] / "tests"
sys.path.insert(0, str(SRC))
sys.path.insert(0, str(TESTS))

import test_durable_repository_qualification_workflow as H  # noqa: E402
from tau_coding.dag_runtime.admission import write_durable_json  # noqa: E402
from tau_coding.workflows.runner import (  # noqa: E402
    repair_durable_repository_qualification,
    resume_packaged_workflow,
    run_durable_repository_qualification_workflow,
)


def main() -> int:
    base = Path(sys.argv[1] if len(sys.argv) > 1 else "219-live-run").resolve()
    base.mkdir(parents=True, exist_ok=True)
    repo = H._git_repo(base / "repo")
    run_dir = base / "run"
    publish_path = base / "published"

    blocked = run_durable_repository_qualification_workflow(
        repo_path=repo, human_goal="219 live conformance", publish_path=publish_path,
        run_dir=run_dir, open_viewer=False, browser_open=False,
        viewer_hold_seconds=None, inject_test_branch_failure=True,
        step_delay_seconds=0.01,
    )
    publish_before_repair = publish_path.exists()

    approval = H._write_repair_approval_packet(
        run_dir=run_dir, node_id="qualify-tests",
        path=base / "repair-approval.json",
    )
    repair = repair_durable_repository_qualification(
        run_dir=run_dir, node_id="qualify-tests", approval_packet=approval,
    )
    resume_packaged_workflow(run_dir=run_dir)

    from tau_coding.workflows.runner import approve_packaged_workflow

    approve_packaged_workflow(run_dir=run_dir)  # first call surfaces requirement
    approval_path = H._write_approval_packet(
        run_dir=run_dir,
        transaction_node_id="publish-qualification",
        path=base / "pub-approval.json",
    )
    approve_packaged_workflow(run_dir=run_dir, approval_packet=approval_path)
    resume_packaged_workflow(run_dir=run_dir)
    resume_packaged_workflow(run_dir=run_dir)  # idempotent second resume

    ledger_path = publish_path / "publication-ledger.json"
    ledger = json.loads(ledger_path.read_text()) if ledger_path.exists() else {}

    with sqlite3.connect(run_dir / "dag-run.sqlite3") as conn:
        pub_attempts = conn.execute(
            "SELECT COUNT(*) FROM dag_node_attempts WHERE node_id='publish-qualification'"
        ).fetchone()[0]
        qt_attempts = conn.execute(
            "SELECT attempt_no FROM dag_node_attempts "
            "WHERE node_id='qualify-tests' ORDER BY attempt_no"
        ).fetchall()

    ok = (
        blocked["status"] == "BLOCKED"
        and not publish_before_repair
        and repair["status"] == "PASS"
        and ledger.get("effect_count") == 1  # single release across repair + resumes
        and len(qt_attempts) > 1  # repair re-ran qualify-tests across generations
    )
    receipt = {
        "schema": "tau.repair_join_conformance_receipt.v1",
        "mocked": False, "live": True, "ok": ok,
        "blocked_before_repair": blocked["status"] == "BLOCKED",
        "no_publish_before_repair": not publish_before_repair,
        "repair_pass": repair["status"] == "PASS",
        "publication_effect_count": ledger.get("effect_count"),
        "publisher_attempt_rows": pub_attempts,
        "qualify_tests_generation_count": len(qt_attempts),
    }
    write_durable_json(base / "repair-join-receipt.json", receipt)
    print(json.dumps(receipt, indent=2))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
