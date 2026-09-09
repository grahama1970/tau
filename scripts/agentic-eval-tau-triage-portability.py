#!/usr/bin/env python3
"""Proof for Tau#345: triage classification has no machine-local path fallback."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any


def _run(
    cmd: list[str],
    *,
    cwd: Path,
    env: dict[str, str] | None = None,
    timeout: int = 120,
) -> dict[str, Any]:
    completed = subprocess.run(
        cmd,
        cwd=cwd,
        env=env,
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


def _json_call(python: Path, code: str, *, cwd: Path, env: dict[str, str]) -> dict[str, Any]:
    result = _run([str(python), "-c", code], cwd=cwd, env=env)
    payload: Any = None
    if result["stdout"].strip():
        payload = json.loads(result["stdout"])
    return {"result": result, "payload": payload}


def _import_call(expression: str) -> str:
    return (
        "import json; "
        "from tau_coding.dag_runtime.triage_error_bridge import classify_tau_failure; "
        f"print(json.dumps({expression}, sort_keys=True))"
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", required=True)
    parser.add_argument("--work")
    args = parser.parse_args()

    repo = Path(__file__).resolve().parents[1]
    work = (
        Path(args.work).expanduser().resolve()
        if args.work
        else Path(tempfile.mkdtemp(prefix="tau-345-triage-")).resolve()
    )
    work.mkdir(parents=True, exist_ok=True)
    out = Path(args.out)
    dist = work / "dist"
    venv = work / "venv"
    fake_home = work / "home with spaces"
    run_cwd = work / "not-source"
    for path in (dist, venv, fake_home, run_cwd):
        if path.exists():
            shutil.rmtree(path)
        path.mkdir(parents=True)

    build = _run(["uv", "build", "--wheel", "--out-dir", str(dist)], cwd=repo, timeout=180)
    wheels = sorted(dist.glob("tau-*.whl"))
    venv_create = _run([sys.executable, "-m", "venv", str(venv)], cwd=repo)
    python = venv / "bin" / "python"
    pip = {"exit_code": 1, "stderr": "no wheel", "stdout": ""}
    if wheels:
        pip = _run([str(python), "-m", "pip", "install", str(wheels[-1])], cwd=run_cwd)

    old_runner = (
        fake_home
        / "workspace"
        / "experiments"
        / "agent-skills"
        / "skills"
        / "triage-error"
        / "run.sh"
    )
    old_marker = fake_home / "old-runner-executed.txt"
    old_runner.parent.mkdir(parents=True)
    old_runner.write_text(f"#!/usr/bin/env bash\necho old > {old_marker}\n", encoding="utf-8")
    old_runner.chmod(0o755)

    scrub = {"PYTHONPATH", "TAU_TRIAGE_ERROR_RUN_SH", "TAU_SKILLS_ROOT", "TAU_AGENT_SKILLS_ROOT"}
    base_env = {key: value for key, value in os.environ.items() if key not in scrub}
    base_env["HOME"] = str(fake_home)

    native = _json_call(
        python,
        _import_call("classify_tau_failure('node missing required evidence', layer='dag-runtime')"),
        cwd=run_cwd,
        env=base_env,
    )
    unavailable = _json_call(
        python,
        _import_call("classify_tau_failure('unknown failure family', layer='dag-runtime')"),
        cwd=run_cwd,
        env={**base_env, "TAU_TRIAGE_ERROR_RUN_SH": str(work / "missing" / "run.sh")},
    )

    skills_root = work / "configured skills"
    external_runner = skills_root / "triage-error" / "run.sh"
    external_runner.parent.mkdir(parents=True)
    external_runner.write_text(
        "#!/usr/bin/env python3\n"
        "import json\n"
        "print(json.dumps({\n"
        "  \"code\": \"external_triage_code\",\n"
        "  \"layer\": \"dag-runtime\",\n"
        "  \"cause\": \"external ok\"\n"
        "}))\n",
        encoding="utf-8",
    )
    external_runner.chmod(0o755)
    external = _json_call(
        python,
        _import_call("classify_tau_failure('anything', layer='dag-runtime')"),
        cwd=run_cwd,
        env={**base_env, "TAU_SKILLS_ROOT": str(skills_root)},
    )

    source_pattern = (
        "workspace/experiments/agent-skills|Path\\.home\\(\\).*triage-error|/home/graham"
    )
    source_scan = _run(
        [
            "bash",
            "-lc",
            f"! rg -n {source_pattern!r} src/tau_coding/dag_runtime/triage_error_bridge.py",
        ],
        cwd=repo,
    )

    errors: list[str] = []
    if build["exit_code"] != 0 or not wheels:
        errors.append("wheel_build_failed")
    if venv_create["exit_code"] != 0 or pip["exit_code"] != 0:
        errors.append("wheel_install_failed")
    if (native["payload"] or {}).get("code") != "tau_project_dag_missing_required_evidence":
        errors.append("native_fallback_code_missing")
    if (native["payload"] or {}).get("classifier_kind") != "NATIVE_FALLBACK":
        errors.append("native_fallback_identity_missing")
    if (external["payload"] or {}).get("classifier_kind") != "EXTERNAL_CLASSIFIER":
        errors.append("external_classifier_identity_missing")
    if (external["payload"] or {}).get("classifier_path") != str(external_runner):
        errors.append("external_classifier_path_missing")
    if (unavailable["payload"] or {}).get("classifier_kind") != "UNAVAILABLE":
        errors.append("unavailable_identity_missing")
    if old_marker.exists():
        errors.append("old_home_relative_runner_executed")
    if source_scan["exit_code"] != 0:
        errors.append("runtime_source_scan_failed")

    proof = {
        "schema": "tau.triage_portability_proof.v1",
        "issue": 345,
        "status": "PASS" if not errors else "BLOCKED",
        "ok": not errors,
        "mocked": False,
        "live": True,
        "provider_live": False,
        "repo": str(repo),
        "work": str(work),
        "wheel": str(wheels[-1]) if wheels else None,
        "run_cwd": str(run_cwd),
        "throwaway_home": str(fake_home),
        "old_home_relative_runner": str(old_runner),
        "old_home_relative_runner_executed": old_marker.exists(),
        "native_fallback": native["payload"],
        "external_classifier": external["payload"],
        "unavailable_classifier": unavailable["payload"],
        "source_scan_exit_code": source_scan["exit_code"],
        "commands": {
            "build": build,
            "venv_create": venv_create,
            "pip_install": pip,
            "native": native["result"],
            "external": external["result"],
            "unavailable": unavailable["result"],
            "source_scan": source_scan,
        },
        "errors": errors,
        "proof_boundary": {
            "proves": "Installed Tau wheel classifies known internal DAG evidence failures "
            "without old home-relative agent-skills fallback, uses configured triage-error "
            "runner when TAU_SKILLS_ROOT is set, and reports unavailable classifier truthfully.",
            "does_not_prove": "Full triage-error catalog parity, provider quality, or external "
            "developer acceptance.",
        },
    }
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(proof, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(proof, indent=2, sort_keys=True))
    return 0 if not errors else 1


if __name__ == "__main__":
    raise SystemExit(main())
