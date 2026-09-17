#!/usr/bin/env python3
"""Agentic eval proof for tau#363: dev-cycle controller properties.

Live-executes the committed workflowScript artifact:
  1. node --check on the canonical script (parse gate).
  2. The real node:test harness over the committed script (positive control:
     every scenario passes).
  3. A fault-injected run: the harness is executed against a deliberately
     corrupted scenario copy and MUST FAIL (negative control proving the
     assertions can actually catch a broken property).

Writes a machine-readable proof with claim_semantics metadata; prints it so the
fixture's stdout_contains oracle can match.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SCRIPT = REPO / "workflows" / "dev-cycle.workflow.js"
TEST_DIR = REPO / "workflows" / "test"


def _run(cmd: list[str], *, cwd: Path, timeout: int = 120) -> dict:
    result = subprocess.run(
        cmd, cwd=cwd, text=True, capture_output=True, timeout=timeout, check=False
    )
    return {
        "command": cmd,
        "cwd": str(cwd),
        "exit_code": result.returncode,
        "stdout": result.stdout[-8000:],
        "stderr": result.stderr[-8000:],
    }


def _tap_counts(stdout: str) -> dict:
    counts = {}
    for line in stdout.splitlines():
        parts = line.split()
        if len(parts) == 3 and parts[0] == "#" and parts[1] in {"tests", "pass", "fail"}:
            counts[parts[1]] = int(parts[2])
    return counts


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", required=True)
    parser.add_argument("--only", choices=["all", "positive", "fault"], default="all")
    args = parser.parse_args()
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)

    script_sha = hashlib.sha256(SCRIPT.read_bytes()).hexdigest()

    ran_positive = args.only in {"all", "positive"}
    ran_fault = args.only in {"all", "fault"}
    node_check_ok = None if not ran_positive else False
    positive_ok = None if not ran_positive else False
    fault_ok = None if not ran_fault else False
    positive_counts = {}
    fault_detail = "skipped" if not ran_fault else ""

    if ran_positive:
        # Gate 1: the committed script must parse standalone.
        check = _run(["node", "--check", str(SCRIPT.relative_to(REPO))], cwd=REPO)
        node_check_ok = check["exit_code"] == 0

        # Gate 2 (positive control): the real harness over the real script, live.
        positive = _run(["node", "--test", "workflows/test/"], cwd=REPO)
        positive_counts = _tap_counts(positive["stdout"])
        positive_ok = (
            positive["exit_code"] == 0
            and positive_counts.get("fail") == 0
            and positive_counts.get("tests", 0) >= 10
        )

    if ran_fault:
        fault_ok, fault_detail = _fault_injected_run(script_sha)

    checks = {}
    if ran_positive:
        checks["node_check"] = node_check_ok
        checks["positive_suite"] = {"ok": positive_ok, "tap_counts": positive_counts}
    if ran_fault:
        checks["fault_injected"] = {
            "ok": fault_ok,
            "detail": fault_detail,
            "corrupted_scenario": "workflows/test/scenarios/h_audit_veto.json",
        }
    ran = [v for v in (node_check_ok, positive_ok, fault_ok) if v is not None]
    ok = bool(ran) and all(ran)
    proof = {
        "schema": "tau.dev_cycle_controller_eval.v1",
        "ok": ok,
        "live": True,
        "mocked": False,
        "claim_semantics": "protocol",
        "script_sha256": script_sha,
        "checks": checks,
        "final_status": "PASS" if ok else "FAIL",
    }
    out.write_text(json.dumps(proof, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(proof, indent=2, sort_keys=True))
    return 0 if ok else 1


def _fault_injected_run(script_sha: str) -> tuple[bool, str]:
    """Corrupt one scenario's expected terminal state in a temp copy and require
    the harness to FAIL (negative control proving the assertions bind)."""
    with tempfile.TemporaryDirectory(prefix="tau-devcycle-eval-") as tmp:
        tmp_root = Path(tmp)
        tmp_workflows = tmp_root / "workflows"
        shutil.copytree(TEST_DIR, tmp_workflows / "test")
        shutil.copy2(SCRIPT, tmp_workflows / "dev-cycle.workflow.js")
        copied_sha = hashlib.sha256(
            (tmp_workflows / "dev-cycle.workflow.js").read_bytes()
        ).hexdigest()
        scenario = tmp_workflows / "test" / "scenarios" / "h_audit_veto.json"
        payload = json.loads(scenario.read_text(encoding="utf-8"))
        # The controller vetoes to audit_vetoed (stranded commit); expecting
        # ready_to_share here makes the corrupted copy FAIL if (and only if)
        # the harness assertions really bind the controller property.
        payload["cases"][0]["expect"]["status"] = "ready_to_share"
        scenario.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
        fault = _run(["node", "--test", "workflows/test/"], cwd=tmp_root)
        fault_failed = fault["exit_code"] != 0
        fault_named = (
            "h_audit_veto.json" in fault["stdout"] or "h_audit_veto.json" in fault["stderr"]
        )
        ok = bool(fault_failed and fault_named and copied_sha == script_sha)
        return (
            ok,
            f"failed={fault_failed} named={fault_named} bytes_identical={copied_sha == script_sha}",
        )


if __name__ == "__main__":
    sys.exit(main())
