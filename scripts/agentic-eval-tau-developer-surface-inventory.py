#!/usr/bin/env python3
"""Proof for Tau#351 developer-visible stub/degraded inventory."""

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
        else Path(tempfile.mkdtemp(prefix="tau-351-surface-")).resolve()
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

    inventory_cmd = [str(tau), "developer-share", "stub-inventory", "--json", "--repo", str(repo)]
    inventory_run = _run(inventory_cmd, cwd=work)
    inventory = json.loads(inventory_run["stdout"])

    media_root = work / "media-smoke"
    media_run = _run(
        [str(tau), "media-explainer-smoke", "--run-root", str(media_root)],
        cwd=work,
    )
    media = json.loads(media_run["stdout"])

    memory_script = work / "memory_degraded.py"
    memory_script.write_text(
        """
import json
from pathlib import Path
from tau_coding.dag_runtime.compiler import compile_generic_dag_plan
from tau_coding.dag_runtime.memory_projection import MemoryProjectionOutbox
from tau_coding.dag_runtime.run_store import SqliteDagRunStore
root = Path('memory')
root.mkdir()
plan = compile_generic_dag_plan({
 'schema': 'tau.generic_dag_spec.v1', 'run_id': 'run-mp', 'run_dir': str(root / 'run'),
 'nodes': [{'node_id': 'n', 'role': 'n', 'command': ['true'], 'depends_on': [],
 'accepted_context_from': [], 'receipt_path': str(root / 'n.json'),
 'timeout_seconds': 1, 'max_attempts': 1}],
}, source_path=root / 'dag.json')
store = SqliteDagRunStore(root / 'dag-run.sqlite3')
lease = store.acquire_run(plan=plan, run_id='run-mp', owner_id='t', ttl_seconds=60)
outbox = MemoryProjectionOutbox(store)
with store._transaction():
    key = outbox.enqueue_within_transaction(
        lease, node_id='n', attempt_id='attempt-1', fact_kind='accepted_outcome',
        payload={'verdict': 'PASS', 'evidence': 'sha256:abc'},
    )
result = outbox.relay(
    lambda _payload: {'ok': False, 'retryable': True, 'error': 'down'},
    max_attempts=1,
)[0]
print(json.dumps({
    'key': key,
    'state': result.state,
    'store_admissions': store.list_admissions('run-mp'),
}))
""",
        encoding="utf-8",
    )
    memory_run = _run([str(python), str(memory_script)], cwd=work)
    memory = json.loads(memory_run["stdout"])

    seeded = work / "seeded"
    shutil.copytree(repo, seeded, ignore=shutil.ignore_patterns(".git", ".venv", "local"))
    target = seeded / "src" / "tau_coding" / "project_status.py"
    target.write_text(target.read_text(encoding="utf-8") + "\nSEED = 'placeholder_result'\n")
    seeded_script = work / "seeded_inventory.py"
    seeded_script.write_text(
        """
import json
from pathlib import Path
from tau_coding.developer_surface_inventory import build_developer_surface_inventory
print(json.dumps(build_developer_surface_inventory(Path('seeded'))))
""",
        encoding="utf-8",
    )
    seeded_run = _run([str(python), str(seeded_script)], cwd=work)
    seeded_inventory = json.loads(seeded_run["stdout"])

    checks = {
        "installed_inventory_schema": inventory.get("schema")
        == "tau.developer_surface_inventory.v1",
        "installed_inventory_clean": inventory.get("status") == "PASS"
        and inventory.get("unclassified_count") == 0
        and inventory.get("bug_stub_count") == 0,
        "classifications_present": set(inventory.get("classifications", []))
        >= {"EXPERIMENTAL_EXPLICIT", "IMPLEMENTED", "OPTIONAL_DEGRADED"},
        "media_explicit_mocked": media.get("mocked") is True
        and media.get("live") is False
        and media.get("memory_policy", {}).get("persistence_mode")
        == "placeholder_receipts_only",
        "memory_degraded_typed": memory.get("state") == "degraded"
        and memory.get("store_admissions") == [],
        "seeded_marker_detected": seeded_inventory.get("status") == "FAIL"
        and seeded_inventory.get("unclassified_count", 0) > 0,
    }
    errors = [name for name, ok in checks.items() if not ok]
    proof = {
        "schema": "tau.developer_surface_inventory_proof.v1",
        "issue": 351,
        "status": "PASS" if not errors else "BLOCKED",
        "ok": not errors,
        "mocked": False,
        "live": True,
        "provider_live": False,
        "installed_wheel": str(wheels[-1]),
        "inventory": inventory,
        "media_smoke": {
            "schema": media.get("schema"),
            "mocked": media.get("mocked"),
            "live": media.get("live"),
            "provider_live": media.get("provider_live"),
            "memory_policy": media.get("memory_policy"),
        },
        "memory_projection": memory,
        "seeded_inventory_status": {
            "status": seeded_inventory.get("status"),
            "unclassified_count": seeded_inventory.get("unclassified_count"),
            "sample": seeded_inventory.get("unclassified_markers", [])[:3],
        },
        "checks": checks,
        "commands": {
            "build": build,
            "venv_create": venv_create,
            "pip_install": pip,
            "inventory": inventory_run,
            "media_smoke": media_run,
            "memory_degraded": memory_run,
            "seeded_inventory": seeded_run,
        },
        "errors": errors,
        "proof_boundary": {
            "proves": "Installed-wheel developer surface inventory and explicit "
            "degraded/experimental receipts.",
            "does_not_prove": "Provider quality or implementation completeness outside "
            "audited marker surfaces.",
        },
    }
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(proof, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(proof, indent=2, sort_keys=True))
    return 0 if not errors else 1


if __name__ == "__main__":
    raise SystemExit(main())
