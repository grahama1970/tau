#!/usr/bin/env python3
"""#215 load campaign: loop the three absence-family tests under parallel
load until each fires once (or reps exhaust), then harvest classification
evidence from every produced run store and receipt tree."""

from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path

TESTS = [
    "tests/test_repository_evidence_map_workflow.py::test_evidence_map_missing_required_tests_blocks_join_without_result",
    "tests/test_workflow_cli.py::test_workflows_repair_approve_and_resume_durable_qualification",
    "tests/test_tui_proof.py::test_textual_tui_memory_stage_proof_writes_receipt_and_screenshot",
]
LOAD = ["tests/test_generic_dag.py", "tests/test_dag_runtime_run_store.py",
        "tests/test_workflow_cli.py", "tests/test_tui_app.py"]


def main() -> int:
    reps = int(sys.argv[1]) if len(sys.argv) > 1 else 40
    out = Path(sys.argv[2] if len(sys.argv) > 2 else "215-campaign").resolve()
    out.mkdir(parents=True, exist_ok=True)
    firings: dict[str, list[dict]] = {t: [] for t in TESTS}
    for rep in range(reps):
        if all(firings[t] for t in TESTS):
            break
        procs = [subprocess.Popen(
            [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", spec],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
            env={"PATH": "/usr/bin:/bin", "HOME": str(out / "home"),
                 "TMPDIR": str(out / f"tmp-{rep}-{i}")},
        ) for i, spec in enumerate([*TESTS, *LOAD])]
        for d in [out / f"tmp-{rep}-{i}" for i in range(len(TESTS) + len(LOAD))]:
            d.mkdir(parents=True, exist_ok=True)
        results = [p.communicate()[0] for p in procs]
        for spec, textout, proc in zip([*TESTS, *LOAD], results, procs):
            if spec in firings and proc.returncode != 0:
                firings[spec].append({"rep": rep, "tail": textout[-1500:]})
                (out / f"firing-{len(firings[spec])}-{Path(spec.split('::')[0]).stem}.log").write_text(textout)
        print(f"rep {rep}: " + " ".join(
            f"{Path(t.split('::')[0]).stem}={len(firings[t])}" for t in TESTS), flush=True)
    receipt = {
        "schema": "tau.absence_family_campaign.v1",
        "mocked": False, "live": True,
        "reps_run": rep + 1,
        "firings": {Path(k.split("::")[0]).stem: len(v) for k, v in firings.items()},
    }
    (out / "campaign-receipt.json").write_text(json.dumps(receipt, indent=2))
    print(json.dumps(receipt, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
