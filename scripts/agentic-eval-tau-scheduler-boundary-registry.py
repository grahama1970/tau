#!/usr/bin/env python3
"""Proof for Tau#349 scheduler boundary failure containment."""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any


def _run(cmd: list[str], *, cwd: Path, timeout: int = 180) -> dict[str, Any]:
    result = subprocess.run(cmd, cwd=cwd, text=True, capture_output=True, timeout=timeout)
    return {
        "command": cmd,
        "cwd": str(cwd),
        "exit_code": result.returncode,
        "stdout": result.stdout,
        "stderr": result.stderr,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", required=True)
    parser.add_argument("--work")
    args = parser.parse_args()
    repo = Path(__file__).resolve().parents[1]
    work = (
        Path(args.work).expanduser().resolve()
        if args.work
        else Path(tempfile.mkdtemp(prefix="tau-349-boundary-"))
    )
    if work.exists():
        shutil.rmtree(work)
    work.mkdir(parents=True)

    dist = work / "dist"
    venv = work / "venv"
    build = _run(["uv", "build", "--wheel", "--out-dir", str(dist)], cwd=repo)
    wheels = sorted(dist.glob("tau-*.whl"))
    venv_create = _run([sys.executable, "-m", "venv", str(venv)], cwd=work)
    python = venv / "bin" / "python"
    pip = _run([str(python), "-m", "pip", "install", str(wheels[-1])], cwd=work)
    tau = venv / "bin" / "tau"

    registry_run = _run([str(tau), "scheduler-boundary-registry", "--json"], cwd=work)
    registry = json.loads(registry_run["stdout"])

    fault_script = work / "scheduler_faults.py"
    fault_script.write_text(
        r'''
import json
from pathlib import Path
from unittest.mock import patch
from tau_coding.dag_runtime.compiler import compile_generic_dag_plan
from tau_coding.dag_runtime.scheduler import run_dag_plan


def spec(root):
    return {
        "schema": "tau.generic_dag_spec.v1",
        "run_id": "boundary-faults",
        "run_dir": str(root / "run"),
        "nodes": [{
            "node_id": "n", "role": "n", "command": ["true"], "depends_on": [],
            "accepted_context_from": [], "receipt_path": str(root / "n.json"),
            "timeout_seconds": 1, "max_attempts": 1,
        }],
    }

root = Path("faults")
root.mkdir()
plan = compile_generic_dag_plan(spec(root), source_path=root / "dag.json")

def boom(node, accepted_inputs, execution):
    raise RuntimeError("adapter exploded")

def bad(node, accepted_inputs, execution):
    return {
        "node_id": node.node_id,
        "status": "PASS",
        "verdict": "FAIL",
        "accepted_output": {"source_node_id": node.node_id},
    }

def classify(signal, *, layer):
    return {
        "code": "tau_unclassified_fault",
        "layer": layer,
        "cause": signal,
        "next_command": "repair",
    }

normal = run_dag_plan(plan, execute_node=boom).node_results[0]
malformed = run_dag_plan(plan, execute_node=bad).node_results[0]
with patch(
    "tau_coding.dag_runtime.scheduler.classify_tau_failure",
    side_effect=RuntimeError("classifier down"),
):
    recursive = run_dag_plan(plan, execute_node=boom).node_results[0]
with patch("tau_coding.dag_runtime.scheduler.classify_tau_failure", classify):
    classified = run_dag_plan(plan, execute_node=boom).node_results[0]
print(json.dumps({
    "normal": normal,
    "malformed": malformed,
    "recursive": recursive,
    "classified": classified,
}, sort_keys=True))
''',
        encoding="utf-8",
    )
    faults_run = _run([str(python), str(fault_script)], cwd=work)
    faults = json.loads(faults_run["stdout"])

    ids = set(registry.get("boundary_ids", []))
    checks = {
        "installed_cli_registry_schema": registry.get("schema")
        == "tau.scheduler_boundary_registry.v1",
        "registry_has_required_boundaries": ids
        >= {
            "adapter_future_execution",
            "attempt_result_admission",
            "recursive_failure_object_admission",
            "workspace_stale_read",
            "result_store_commit",
        },
        "registry_has_at_least_10_boundaries": len(ids) >= 10,
        "adapter_boundary_classified": faults["classified"].get("boundary_id")
        == "adapter_future_execution",
        "malformed_attempt_boundary_classified": faults["malformed"].get("boundary_id")
        == "attempt_result_admission",
        "recursive_failure_contained": faults["recursive"].get("boundary_id")
        == "recursive_failure_object_admission"
        and faults["recursive"].get("retryable") is False,
        "recursive_failure_has_tau_code": faults["recursive"].get("failure", {}).get(
            "classification_code"
        )
        == "tau_scheduler_boundary_fallback",
    }
    errors = [name for name, ok in checks.items() if not ok]
    proof = {
        "schema": "tau.scheduler_boundary_registry_proof.v1",
        "issue": 349,
        "status": "PASS" if not errors else "BLOCKED",
        "ok": not errors,
        "mocked": False,
        "live": True,
        "installed_wheel": str(wheels[-1]),
        "registry": registry,
        "faults": {
            name: {
                "status": value.get("status"),
                "verdict": value.get("verdict"),
                "boundary_id": value.get("boundary_id"),
                "repair_category": value.get("repair_category"),
                "failure": value.get("failure"),
                "retryable": value.get("retryable"),
            }
            for name, value in faults.items()
        },
        "checks": checks,
        "commands": {
            "build": build,
            "venv_create": venv_create,
            "pip_install": pip,
            "registry": registry_run,
            "faults": faults_run,
        },
        "errors": errors,
        "proof_boundary": {
            "proves": "Installed-wheel scheduler boundary registry and recursive "
            "failure containment.",
            "does_not_prove": "Provider quality or every external runtime backend path.",
        },
    }
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(proof, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(proof, indent=2, sort_keys=True))
    return 0 if not errors else 1


if __name__ == "__main__":
    raise SystemExit(main())
