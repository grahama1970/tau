"""Live proof harness for Tau's sandboxed persistent Python workspace."""

from __future__ import annotations

import argparse
import copy
import json
import shutil
import subprocess
from pathlib import Path
from typing import Any

from tau_coding.dag_runtime.model import canonical_sha256
from tau_coding.runtime_backends.python_workspace import (
    DEFAULT_WORKSPACE_IMAGE,
    SandboxedPythonWorkspace,
    SandboxedPythonWorkspaceRequest,
    admit_python_execution_artifact,
    stale_snapshot_errors,
)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--image", default=DEFAULT_WORKSPACE_IMAGE)
    args = parser.parse_args()
    args.out.parent.mkdir(parents=True, exist_ok=True)

    image_available = _docker_image_available(args.image)
    request = _request(args.image)
    workspace = SandboxedPythonWorkspace(request, state_dir=args.out.parent / "workspace")
    workspace_receipt = workspace.start()
    if workspace_receipt["status"] != "READY":
        proof = {
            "schema": "tau.python_workspace_issue317_live_proof.v1",
            "status": "BLOCKED",
            "mocked": False,
            "live": True,
            "image_available": image_available,
            "workspace_receipt": workspace_receipt,
            "checks": {"workspace_ready": False},
            "errors": workspace_receipt["errors"],
        }
        args.out.write_text(json.dumps(proof, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        print(f"python workspace live proof: BLOCKED {args.out}")
        return 2

    receipts: dict[str, Any] = {}
    try:
        receipts["turn1"] = workspace.execute(
            "turn-1",
            "import math\n"
            "workspace_value = 40\n"
            "def helper(x):\n"
            "    return math.sqrt(x) + workspace_value\n"
            "print('turn1', helper(4))\n",
        )
        receipts["turn2"] = workspace.execute(
            "turn-2",
            "print('turn2', helper(9))\n"
            "tau_exports = {'turn': 2, 'value': workspace_value, 'helper_result': helper(9)}\n",
        )
        receipts["turn3"] = workspace.execute(
            "turn-3",
            "print('PASS')\n"
            "tau_exports = {'printed_pass': True, 'settlement_claim': 'candidate-only'}\n",
        )
        compacted_context = {
            "schema": "tau.provider_context_after_compaction.v1",
            "workspace_handle": workspace.handle,
            "inlined_python_namespace": None,
            "hidden_state_embedded": False,
        }
        receipts["after_compaction"] = workspace.execute(
            "turn-4-after-compaction",
            "print('turn4', helper(16))\ntau_exports = {'after_compaction': helper(16)}\n",
        )
        receipts["export"] = workspace.execute(
            "turn-5-export",
            "tau_exports = {'answer': 42, 'items': [1, 2, 3]}\nprint('structured export ready')\n",
        )
        pre_admission = {
            "accepted": False,
            "reason": receipts["export"]["tau_admission_status"],
        }
        admission = admit_python_execution_artifact(receipts["export"])
        snapshot = workspace.snapshot("issue-317")
        duplicate_same = workspace.execute(
            "turn-5-export",
            "tau_exports = {'answer': 42, 'items': [1, 2, 3]}\nprint('structured export ready')\n",
        )
        duplicate_conflict = workspace.execute("turn-5-export", "print('different effect')\n")
        sandbox_denial = workspace.execute(
            "sandbox-denial",
            _sandbox_denial_code(host_pid=__import__("os").getpid()),
        )
    finally:
        kill_receipt = workspace.stop()

    restored_workspace = SandboxedPythonWorkspace(request, state_dir=args.out.parent / "restored")
    restored_start = restored_workspace.start()
    try:
        restore_receipt = restored_workspace.restore_snapshot(snapshot)
        restored_readback = restored_workspace.execute(
            "restore-readback",
            "print('restored', workspace_value)\n"
            "tau_exports = {\n"
            "    'workspace_value': workspace_value,\n"
            "    'helper_restored': 'helper' in globals(),\n"
            "}\n",
        )
    finally:
        restored_stop = restored_workspace.stop()

    stale_controls = {}
    for field, value in {
        "package_manifest_sha256": canonical_sha256({"package": "mutated"}),
        "goal_hash": canonical_sha256({"goal": "mutated"}),
        "attempt_id": "attempt-mutated",
        "policy_sha256": canonical_sha256({"policy": "mutated"}),
        "data_boundary_sha256": canonical_sha256({"boundary": "mutated"}),
    }.items():
        stale = copy.deepcopy(snapshot)
        stale[field] = value
        stale_controls[field] = stale_snapshot_errors(
            stale, request, restored_start["package_manifest"]
        )

    checks = {
        "workspace_ready": workspace_receipt["status"] == "READY",
        "three_turn_persistence": (
            receipts["turn1"]["status"] == "OK"
            and receipts["turn2"]["status"] == "OK"
            and "turn2 43.0" in receipts["turn2"]["stdout"]
            and receipts["turn3"]["status"] == "OK"
        ),
        "compaction_handle_valid_without_hidden_state": (
            compacted_context["hidden_state_embedded"] is False
            and "workspace_value" not in json.dumps(compacted_context)
            and "turn4 44.0" in receipts["after_compaction"]["stdout"]
        ),
        "export_requires_artifact_admission": (
            pre_admission["accepted"] is False
            and admission["status"] == "PASS"
            and admission["accepted_output"]["exports"]["answer"] == 42
        ),
        "snapshot_restore_reports_non_restorable": (
            restore_receipt["status"] == "OK"
            and restored_readback["exports"]["workspace_value"] == 40
            and restored_readback["exports"]["helper_restored"] is False
            and "helper" in restore_receipt["non_restorable_values"]
        ),
        "sandbox_denials_recorded": all(sandbox_denial["exports"].values()),
        "stale_snapshot_rejection": all(
            any(error.startswith("stale_snapshot:") for error in errors)
            for errors in stale_controls.values()
        ),
        "pass_stdout_cannot_settle": (
            "PASS" in receipts["turn3"]["stdout"]
            and receipts["turn3"]["tau_admission_status"] == "not_admitted"
            and receipts["turn3"]["effects_admitted"] is False
        ),
        "duplicate_execution_id_guarded": (
            duplicate_same["idempotent_replay"] is True
            and duplicate_conflict["status"] == "BLOCKED"
            and "duplicate_execution_id_conflict" in duplicate_conflict["errors"]
        ),
        "ordinary_tau_no_startup_cost": (
            workspace_receipt["ordinary_tau_startup_cost"] == "none_until_workspace_requested"
        ),
        "backend_neutral_receipts": (
            workspace_receipt["schema"] == "tau.python_workspace_receipt.v1"
            and receipts["turn1"]["schema"] == "tau.python_execution_receipt.v1"
            and snapshot["schema"] == "tau.python_workspace_snapshot.v1"
        ),
    }
    proof = {
        "schema": "tau.python_workspace_issue317_live_proof.v1",
        "status": "PASS" if all(checks.values()) else "BLOCKED",
        "mocked": False,
        "live": True,
        "provider_live": False,
        "herdr_comparison": {
            "status": "NOT_RUN",
            "reason": "issue_315_herdr_hosted_workspace_not_available_in_this_checkout",
        },
        "image_available": image_available,
        "workspace_receipt": workspace_receipt,
        "compacted_context": compacted_context,
        "execution_receipts": receipts,
        "pre_admission": pre_admission,
        "admission_receipt": admission,
        "snapshot": snapshot,
        "kill_receipt": kill_receipt,
        "restored_start_receipt": restored_start,
        "restore_receipt": restore_receipt,
        "restored_readback": restored_readback,
        "restored_stop_receipt": restored_stop,
        "stale_snapshot_controls": stale_controls,
        "duplicate_same_receipt": duplicate_same,
        "duplicate_conflict_receipt": duplicate_conflict,
        "checks": checks,
        "proof_boundary": {
            "mocked": False,
            "live": True,
            "exercised": (
                "real Docker worker endpoint with network none and no provider env allowlist"
            ),
            "unverified": ["Herdr-hosted comparison for issue #315"],
        },
    }
    args.out.write_text(json.dumps(proof, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"python workspace live proof: {proof['status']} {args.out}")
    return 0 if proof["status"] == "PASS" else 2


def _request(image: str) -> SandboxedPythonWorkspaceRequest:
    return SandboxedPythonWorkspaceRequest(
        run_id="issue-317-live",
        node_id="python-workspace-node",
        attempt_id="attempt-1",
        attempt_number=1,
        goal_hash=canonical_sha256({"goal": "issue-317"}),
        plan_hash=canonical_sha256({"plan": "issue-317"}),
        work_order_sha256=canonical_sha256({"work_order": "issue-317"}),
        policy_sha256=canonical_sha256({"policy": "zero-trust"}),
        data_boundary_sha256=canonical_sha256({"data_boundary": "local-only"}),
        worktree_sha256=canonical_sha256({"worktree": str(Path.cwd())}),
        sandbox_attestation_sha256=canonical_sha256({"sandbox": "docker-network-none"}),
        image=image,
    )


def _sandbox_denial_code(*, host_pid: int) -> str:
    return (
        "import os, socket\n"
        "from pathlib import Path\n"
        "def visible(path):\n"
        "    try:\n"
        "        return Path(path).exists()\n"
        "    except PermissionError:\n"
        "        return False\n"
        "denials = {}\n"
        "denials['ssh_keys'] = (\n"
        "    not visible('/home/graham/.ssh/id_rsa')\n"
        "    and not visible('/root/.ssh/id_rsa')\n"
        ")\n"
        "auth_keys = ['OPENAI_API_KEY', 'ANTHROPIC_API_KEY', 'SCILLM_API_KEY', 'GITHUB_TOKEN']\n"
        "denials['provider_auth'] = not any(k in os.environ for k in auth_keys)\n"
        "denials['undeclared_host_path'] = not visible(\n"
        "    '/home/graham/workspace/experiments/tau/pyproject.toml'\n"
        ")\n"
        f"denials['host_process_state'] = not visible('/proc/{host_pid}/cmdline')\n"
        "try:\n"
        "    socket.create_connection(('1.1.1.1', 80), timeout=1)\n"
        "    denials['network'] = False\n"
        "except OSError:\n"
        "    denials['network'] = True\n"
        "tau_exports = denials\n"
        "print('denials', denials)\n"
    )


def _docker_image_available(image: str) -> bool:
    if shutil.which("docker") is None:
        return False
    result = subprocess.run(
        ["docker", "image", "inspect", image],
        check=False,
        capture_output=True,
        text=True,
        timeout=10,
    )
    return result.returncode == 0


if __name__ == "__main__":
    raise SystemExit(main())
