#!/usr/bin/env python3
"""Proof for Tau#353 developer doc status and installed-wheel command readback."""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

DOC_PATHS = [
    "README.md",
    "docs/run-report.md",
    "docs/replacement-harness-hardening.md",
    "docs/zero-trust-product-plan.md",
    "docs/threat-model.md",
    "docs/context-compaction.md",
]


def _loads(text: str) -> dict[str, Any]:
    try:
        data = json.loads(text)
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}


def _run(cmd: list[str], *, cwd: Path, timeout: int = 120) -> dict[str, Any]:
    completed = subprocess.run(
        cmd,
        cwd=cwd,
        text=True,
        capture_output=True,
        timeout=timeout,
        check=False,
    )
    return {
        "command": cmd,
        "cwd": str(cwd),
        "exit_code": completed.returncode,
        "stdout": completed.stdout,
        "stderr": completed.stderr,
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
        else Path(tempfile.mkdtemp(prefix="tau-353-doc-status-")).resolve()
    )
    if work.exists():
        shutil.rmtree(work)
    work.mkdir(parents=True)

    inventory = work / "doc-status-inventory.json"
    docs = _run(
        [sys.executable, "scripts/check-doc-status.py", "--out", str(inventory), *DOC_PATHS],
        cwd=repo,
    )
    inv = json.loads(inventory.read_text(encoding="utf-8")) if inventory.exists() else {}

    bad = work / "bad.md"
    bad.write_text("# Bad\n\nThis future supported feature is ready.\n", encoding="utf-8")
    bad_fixture = _run([sys.executable, "scripts/check-doc-status.py", str(bad)], cwd=repo)
    good = work / "good.md"
    good.write_text(
        "# Good\n<!-- tau-doc-status: ROADMAP -->\n\nThis future feature is planned.\n",
        encoding="utf-8",
    )
    good_fixture = _run([sys.executable, "scripts/check-doc-status.py", str(good)], cwd=repo)

    dist = work / "dist"
    venv = work / "venv"
    build = _run(["uv", "build", "--wheel", "--out-dir", str(dist)], cwd=repo, timeout=180)
    wheels = sorted(dist.glob("tau-*.whl"))
    venv_create = _run([sys.executable, "-m", "venv", str(venv)], cwd=repo)
    python = venv / "bin" / "python"
    pip = {"exit_code": 1, "stderr": "no wheel", "stdout": ""}
    commands: dict[str, dict[str, Any]] = {}
    if wheels:
        pip = _run([str(python), "-m", "pip", "install", str(wheels[-1])], cwd=work, timeout=180)
        tau = venv / "bin" / "tau"
        env_prefix = f"HOME={work / 'home'} PYTHONPATH= "
        commands["tau_help"] = _run(["bash", "-lc", env_prefix + f"{tau} --help"], cwd=work)
        commands["doctor_json"] = _run(
            ["bash", "-lc", env_prefix + f"{tau} doctor --json"], cwd=work
        )
        commands["project_status_verify"] = _run(
            [
                "bash",
                "-lc",
                env_prefix
                + f"{tau} project-status verify --status {repo / 'docs/status/CURRENT_STATE.json'} "
                + f"--github-snapshot {repo / 'docs/status/github-snapshot.json'}",
            ],
            cwd=work,
        )

    classifications = {row.get("classification") for row in inv.get("claims", [])}
    security_rows = [
        row for row in inv.get("claims", []) if row.get("classification") == "SECURITY_NONCLAIM"
    ]
    errors: list[str] = []
    if docs["exit_code"] != 0 or inv.get("unclassified_count") != 0:
        errors.append("unclassified_doc_claims")
    if bad_fixture["exit_code"] == 0:
        errors.append("unmarked_future_fixture_allowed")
    if good_fixture["exit_code"] != 0:
        errors.append("marked_roadmap_fixture_rejected")
    if not security_rows:
        errors.append("security_nonclaims_missing")
    if "ROADMAP" not in classifications or "KNOWN_LIMITATION" not in classifications:
        errors.append("required_classifications_missing")
    if build["exit_code"] != 0 or venv_create["exit_code"] != 0 or pip["exit_code"] != 0:
        errors.append("wheel_install_failed")
    if commands.get("tau_help", {}).get("exit_code") != 0:
        errors.append("tau_help_failed")
    doctor_payload = _loads(commands.get("doctor_json", {}).get("stdout", ""))
    if doctor_payload.get("schema") != "tau.doctor.v1" or doctor_payload.get("ok") is not False:
        errors.append("doctor_json_not_truthful_readback")
    status_payload = _loads(commands.get("project_status_verify", {}).get("stdout", ""))
    if status_payload.get("action") != "verify" or status_payload.get("status") != "FAIL":
        errors.append("project_status_verify_not_truthful_readback")

    proof = {
        "schema": "tau.doc_status_proof.v1",
        "issue": 353,
        "status": "PASS" if not errors else "BLOCKED",
        "ok": not errors,
        "mocked": False,
        "live": True,
        "provider_live": False,
        "repo": str(repo),
        "work": str(work),
        "audited_paths": DOC_PATHS,
        "inventory": inv,
        "inventory_path": str(inventory),
        "security_nonclaim_count": len(security_rows),
        "classifications_present": sorted(c for c in classifications if c),
        "wheel": str(wheels[-1]) if wheels else None,
        "command_readback": commands,
        "fixture_checks": {"unmarked_future": bad_fixture, "marked_roadmap": good_fixture},
        "commands": {"docs": docs, "build": build, "venv_create": venv_create, "pip_install": pip},
        "errors": errors,
        "proof_boundary": {
            "proves": "Primary docs classify aspirational phrases and installed Tau exposes "
            "the documented normal-path command surfaces.",
            "does_not_prove": "Runtime semantic correctness, provider quality, or "
            "GOAL.md completion.",
        },
    }
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(proof, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(proof, indent=2, sort_keys=True))
    return 0 if not errors else 1


if __name__ == "__main__":
    raise SystemExit(main())
