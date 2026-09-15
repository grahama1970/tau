#!/usr/bin/env python3
"""tau#362: prove the developer_share_evidence gates read back at a clean tree.

Builds a clean clone of the repository (HEAD), overlays the candidate
developer-share evidence producer and the two retained repair-boundary proofs
that are not yet tracked at HEAD, commits them (clean ``git status --porcelain``),
then runs the real ``tau project-status build`` and ``tau developer-share
status --json`` CLI paths in that clone and asserts the three evidence gates
read back PASS. A second clean clone mutates one retained proof and must fail
closed, proving the gates do not synthesize green from mutated evidence.

Proof boundary: the provider-wheels, browser, and repair runs behind the
retained artifacts are NOT re-executed here; this proves the gate readback
binding of the retained proofs at a clean tree, live and non-mocked.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

SCHEMA = "tau.developer_share_evidence_clean_checkout_proof.v1"

# Candidate files overlaid onto the clean clone: the producer module plus the
# two retained repair-boundary proofs that HEAD does not track yet. The other
# pinned retained artifacts are already tracked at HEAD.
CANDIDATE_OVERLAY_FILES = (
    "src/tau_coding/project_status.py",
    "local/agentic-evals/tau-triage-contract/proof.json",
    "local/agentic-evals/tau-scheduler-boundary-registry/proof.json",
)

EVIDENCE_CLASSES = (
    "clean_checkout_installed_wheel_launch",
    "viewer_browser",
    "repair_self_heal",
)

GATE_IDS = {
    "clean_checkout_installed_wheel_launch": (
        "clean_checkout_installed_wheel_launch_proof_present"
    ),
    "viewer_browser": "viewer_browser_proof_present",
    "repair_self_heal": "repair_self_heal_proof_present",
}


def _run(cmd: list[str], *, cwd: Path, timeout: int = 600) -> dict[str, Any]:
    result = subprocess.run(
        cmd, cwd=cwd, text=True, capture_output=True, timeout=timeout, check=False
    )
    return {
        "command": cmd,
        "cwd": str(cwd),
        "returncode": result.returncode,
        "stdout": result.stdout,
        "stderr": result.stderr,
    }


def _abbreviated(call: dict[str, Any]) -> dict[str, Any]:
    """Proof-record view of a command call with truncated streams."""

    return {
        "command": call["command"],
        "cwd": call["cwd"],
        "returncode": call["returncode"],
        "stdout": call["stdout"][-2000:],
        "stderr": call["stderr"][-2000:],
    }


def _write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _make_clean_clone(repo: Path, work: Path, name: str) -> Path:
    clone = work / name
    cloned = _run(["git", "clone", "--quiet", str(repo), str(clone)], cwd=work.parent)
    assert cloned["returncode"] == 0, cloned
    for relative in CANDIDATE_OVERLAY_FILES:
        source = repo / relative
        assert source.is_file(), f"missing candidate overlay source: {relative}"
        target = clone / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(source.read_bytes())
    _run(["git", "add", "-A"], cwd=clone)
    committed = _run(
        [
            "git",
            "-c",
            "user.email=t@t",
            "-c",
            "user.name=t",
            "commit",
            "-q",
            "-m",
            "tau#362 candidate: developer_share_evidence producer + retained proofs",
        ],
        cwd=clone,
    )
    assert committed["returncode"] == 0, committed
    porcelain = _run(["git", "status", "--porcelain"], cwd=clone)
    assert porcelain["stdout"].strip() == "", porcelain
    return clone


def _read_gates(clone: Path, work: Path, name: str) -> dict[str, Any]:
    status_path = work / f"{name}-status.json"
    share_path = work / f"{name}-developer-share.json"
    build = _run(
        [
            "uv",
            "run",
            "--quiet",
            "tau",
            "project-status",
            "build",
            "--repo",
            ".",
            "--out",
            str(status_path),
        ],
        cwd=clone,
    )
    if build["returncode"] != 0:
        return {
            "ok": False,
            "errors": [f"build_failed:{build['stderr'][-500:]}"],
            "commands": [_abbreviated(build)],
        }
    share = _run(
        [
            "uv",
            "run",
            "--quiet",
            "tau",
            "developer-share",
            "status",
            "--repo",
            ".",
            "--status",
            str(status_path),
            "--json",
        ],
        cwd=clone,
    )
    if share["returncode"] != 0:
        return {
            "ok": False,
            "errors": [f"status_failed:{share['stderr'][-500:]}"],
            "commands": [_abbreviated(build), _abbreviated(share)],
        }
    _write_json(share_path, json.loads(share["stdout"]))
    status = json.loads(status_path.read_text(encoding="utf-8"))
    result = json.loads(share["stdout"])
    gates = {gate["id"]: gate["state"] for gate in result["gates"]}
    evidence = status.get("developer_share_evidence", {})
    checks = {}
    errors = []
    for field, gate_id in GATE_IDS.items():
        gate_state = gates.get(gate_id)
        class_value = evidence.get(field)
        checks[gate_id] = {
            "gate_state": gate_state,
            "evidence_value": class_value,
            "detail": (evidence.get("detail", {}).get(field, [])),
        }
        if gate_state != "PASS" or class_value is not True:
            errors.append(f"{gate_id}_not_pass:{gate_state}:{class_value}")
    return {
        "ok": not errors,
        "errors": errors,
        "checks": checks,
        "commands": [_abbreviated(call) for call in [build, share]],
        "share_json_path": str(share_path),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", default=".", help="repository root (default: cwd)")
    parser.add_argument("--work", required=True, help="work directory for clones and artifacts")
    parser.add_argument("--out", required=True, help="proof.json output path")
    args = parser.parse_args()

    repo = Path(args.repo).resolve()
    work = Path(args.work).resolve()
    work.mkdir(parents=True, exist_ok=True)

    errors: list[str] = []
    commands: list[dict[str, Any]] = []
    with tempfile.TemporaryDirectory(prefix="tau362-clean-", dir=str(work)) as tmp:
        base = Path(tmp)

        # Positive: clean clone with the candidate patch; all three gates PASS.
        positive = _make_clean_clone(repo, base, "clean-positive")
        positive_result = _read_gates(positive, base, "positive")
        commands.extend(positive_result.pop("commands", []))
        errors.extend(positive_result.get("errors", []))

        # Negative: mutate one retained repair proof; the gate must fail closed.
        negative = _make_clean_clone(repo, base, "clean-negative")
        mutated = negative / "local/agentic-evals/tau-same-node-rerun-proof.json"
        mutated.write_text(mutated.read_text(encoding="utf-8") + "\n", encoding="utf-8")
        _run(
            [
                "git",
                "-c",
                "user.email=t@t",
                "-c",
                "user.name=t",
                "commit",
                "-q",
                "-am",
                "mutate retained proof",
            ],
            cwd=negative,
        )
        negative_result = _read_gates(negative, base, "negative")
        commands.extend(negative_result.pop("commands", []))
        repair_negative = negative_result.get("checks", {}).get(
            "repair_self_heal_proof_present", {}
        )
        if repair_negative.get("gate_state") != "FAIL":
            errors.append(
                f"mutated_proof_gate_not_fail:{repair_negative.get('gate_state')}"
            )
        if repair_negative.get("evidence_value") is True:
            errors.append("mutated_proof_evidence_green")

    checks = {
        "clean_clone_positive_gates_pass": positive_result.get("checks", {}),
        "clean_clone_positive_porcelain_clean": True,
        "mutated_retained_proof_fails_closed": negative_result.get("checks", {}).get(
            "repair_self_heal_proof_present", {}
        ),
    }
    proof: dict[str, Any] = {
        "schema": SCHEMA,
        "status": "PASS" if not errors else "FAIL",
        "ok": not errors,
        "live": True,
        "mocked": False,
        "provider_live": False,
        "errors": errors,
        "checks": checks,
        "commands": commands,
        "repo": str(repo),
        "proof_boundary": {
            "proves": (
                "developer_share_evidence producer reads the pinned retained proofs back "
                "through the real project-status build and developer-share status CLI at a "
                "clean tree, and fails closed on mutated retained evidence."
            ),
            "does_not_prove": (
                "Re-execution of the wheel/browser/repair proof runs behind the retained "
                "artifacts, provider quality, or human acceptance."
            ),
        },
    }
    _write_json(Path(args.out), proof)
    print(json.dumps({"status": proof["status"], "ok": proof["ok"], "errors": errors}))
    return 0 if proof["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
