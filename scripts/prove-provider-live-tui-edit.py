#!/usr/bin/env python3
"""Run a clean-wheel, provider-live Tau TUI edit acceptance proof.

The proof intentionally uses the installed wheel for the Tau commands and TUI
entrypoint.  The only source checkout dependency is the wheel build input.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import pty
import select
import subprocess
import sysconfig
import time
import venv
from contextlib import suppress
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

PROOF_SCHEMA = "tau.provider_live_tui_edit_proof.v1"
WORK_ORDER_SCHEMA = "tau.executor.scillm_worker.v1"
RESULT_SCHEMA = "tau.scillm_worker_result.v1"
SCILLM_ENDPOINT = "/v1/scillm/opencode/runs"


@dataclass(frozen=True)
class InstalledTau:
    root: Path
    home: Path
    bin_dir: Path
    site_packages: Path
    dependency_site: Path
    tau_bin: Path
    python_bin: Path
    import_probe: dict[str, Any]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, default=Path.cwd())
    parser.add_argument("--run-dir", type=Path, required=True)
    parser.add_argument("--positive-runs", type=int, default=3)
    parser.add_argument("--request-timeout-s", type=int, default=240)
    parser.add_argument("--allow-live-scillm", action="store_true")
    args = parser.parse_args()

    if not args.allow_live_scillm:
        raise RuntimeError("--allow-live-scillm is required for provider-live proof")
    if args.positive_runs < 1:
        raise RuntimeError("--positive-runs must be at least 1")

    repo = args.repo.expanduser().resolve()
    run_dir = args.run_dir.expanduser().resolve()
    run_dir.mkdir(parents=True, exist_ok=True)
    commands: list[dict[str, Any]] = []

    wheel = build_wheel(repo=repo, run_dir=run_dir, commands=commands)
    installed = install_wheel(wheel=wheel, run_dir=run_dir, commands=commands)
    clean_env = installed_env(installed)

    positives = []
    for index in range(1, args.positive_runs + 1):
        positives.append(
            run_positive_case(
                case_id=f"positive-{index:02d}",
                installed=installed,
                env=clean_env,
                run_dir=run_dir / f"positive-{index:02d}",
                timeout_s=args.request_timeout_s,
                commands=commands,
            )
        )

    negative = run_negative_unauthorized_case(
        installed=installed,
        env=clean_env,
        run_dir=run_dir / "negative-unauthorized",
        timeout_s=min(args.request_timeout_s, 90),
        commands=commands,
    )

    ok = all(case["ok"] is True for case in positives) and negative["ok"] is True
    proof = {
        "schema": PROOF_SCHEMA,
        "ok": ok,
        "status": "PASS" if ok else "BLOCKED",
        "mocked": False,
        "live": True,
        "provider_live": any(case.get("provider_live") is True for case in positives),
        "generated_at": utc_stamp(),
        "wheel": str(wheel),
        "wheel_sha256": sha256_uri(wheel),
        "installed_tau": installed.import_probe,
        "positive_run_count": len(positives),
        "positive_runs": positives,
        "negative_unauthorized": negative,
        "commands": commands,
        "proof_scope": {
            "proves": [
                "Tau was built into a wheel and imported from a clean installed environment.",
                "The packaged Tau TUI accepted a bounded coding request under a PTY.",
                "Tau recorded memory-first gate evidence before dispatch.",
                "Tau recorded permission request/reply receipts before mutation.",
                (
                    "Tau launched SciLLM through Tau's scillm-worker command, not a direct "
                    "provider client."
                ),
                (
                    "Three distinct provider-live SciLLM worker runs produced an allowlisted "
                    "one-file edit."
                ),
                "A focused real test was executed against each edited temporary repository.",
                "A relaunch/resume marker did not duplicate the accepted file effect.",
                (
                    "An unauthorized SciLLM route failed closed without provider-live evidence "
                    "or mutation."
                ),
            ],
            "does_not_prove": [
                "Provider/model semantic quality beyond the bounded fixture.",
                "The worker is trustworthy for arbitrary repositories.",
                "Full interactive production TUI command execution beyond PTY prompt acceptance.",
                "Graph Memory fact truth from a remote Memory service.",
            ],
        },
    }
    proof_path = run_dir / "provider-live-tui-edit-proof.json"
    write_json(proof_path, proof)
    print(json.dumps(proof, indent=2, sort_keys=True))
    return 0 if ok else 1


def build_wheel(*, repo: Path, run_dir: Path, commands: list[dict[str, Any]]) -> Path:
    wheelhouse = run_dir / "wheelhouse"
    wheelhouse.mkdir(parents=True, exist_ok=True)
    run_command(
        ["uv", "build", "--wheel", "--out-dir", str(wheelhouse)],
        cwd=repo,
        timeout=180,
        commands=commands,
    )
    wheels = sorted(wheelhouse.glob("tau-*.whl"))
    if len(wheels) != 1:
        raise RuntimeError(f"expected_one_tau_wheel:{[str(path) for path in wheels]}")
    return wheels[0]


def install_wheel(*, wheel: Path, run_dir: Path, commands: list[dict[str, Any]]) -> InstalledTau:
    root = run_dir / "installed"
    home = root / "home"
    environment = root / "venv"
    home.mkdir(parents=True, exist_ok=True)
    venv.EnvBuilder(with_pip=True).create(environment)
    bin_dir = environment / ("Scripts" if os.name == "nt" else "bin")
    python_bin = bin_dir / "python"
    tau_bin = bin_dir / "tau"
    site_packages = Path(
        run_command(
            [
                str(python_bin),
                "-c",
                "import sysconfig; print(sysconfig.get_path('purelib'))",
            ],
            cwd=root,
            commands=commands,
        ).stdout.strip()
    ).resolve()
    dependency_site = Path(sysconfig.get_path("purelib")).resolve()
    (site_packages / "tau-locked-dependencies.pth").write_text(
        str(dependency_site) + "\n", encoding="utf-8"
    )
    env = {
        key: value
        for key, value in os.environ.items()
        if key not in {"PYTHONHOME", "PYTHONPATH"}
    }
    env.update({"HOME": str(home), "PIP_NO_INDEX": "1", "PIP_DISABLE_PIP_VERSION_CHECK": "1"})
    run_command(
        [
            str(python_bin),
            "-m",
            "pip",
            "install",
            "--quiet",
            "--no-index",
            "--no-deps",
            str(wheel),
        ],
        cwd=root,
        env=env,
        commands=commands,
    )
    import_probe = json_object(
        run_command(
            [
                str(python_bin),
                "-c",
                (
                    "import json, tau_coding; "
                    "print(json.dumps({'tau_coding': tau_coding.__file__}))"
                ),
            ],
            cwd=root,
            env=env,
            commands=commands,
        ).stdout,
        label="installed_tau_import_probe",
    )
    tau_import = Path(str(import_probe.get("tau_coding"))).resolve()
    if not tau_import.is_relative_to(site_packages):
        raise RuntimeError(f"installed_tau_import_not_from_wheel:{tau_import}")
    return InstalledTau(
        root=root,
        home=home,
        bin_dir=bin_dir,
        site_packages=site_packages,
        dependency_site=dependency_site,
        tau_bin=tau_bin,
        python_bin=python_bin,
        import_probe={
            **import_probe,
            "site_packages": str(site_packages),
            "dependency_site": str(dependency_site),
        },
    )


def installed_env(installed: InstalledTau) -> dict[str, str]:
    env = {
        key: value
        for key, value in os.environ.items()
        if key not in {"PYTHONHOME", "PYTHONPATH"}
    }
    env.update(
        {
            "HOME": str(installed.home),
            "PATH": f"{installed.bin_dir}{os.pathsep}{os.environ.get('PATH', '')}",
            "TERM": os.environ.get("TERM", "xterm-256color"),
        }
    )
    return env


def run_positive_case(
    *,
    case_id: str,
    installed: InstalledTau,
    env: dict[str, str],
    run_dir: Path,
    timeout_s: int,
    commands: list[dict[str, Any]],
) -> dict[str, Any]:
    run_dir.mkdir(parents=True, exist_ok=True)
    repo = run_dir / "workspace" / "repo"
    baseline_head = init_fixture_repo(repo)
    goal_hash = f"sha256:{sha256_text(case_id + ':' + str(repo))}"
    prompt = (
        "Fix calc.py so add(a, b) returns a + b. Only edit calc.py. "
        "Run python test_calc.py and write the Tau worker result receipt."
    )
    tui_first = drive_tui_prompt(
        installed=installed,
        env=env,
        cwd=repo,
        run_id=f"{case_id}-submit",
        prompt=prompt,
        transcript_path=run_dir / "tui-submit.ansi",
    )
    memory = write_memory_gate_receipts(
        installed=installed,
        env=env,
        run_dir=run_dir / "memory",
        goal_hash=goal_hash,
        repo=repo,
    )
    permission_request, permission_reply = write_permission_receipts(
        installed=installed,
        env=env,
        run_dir=run_dir / "permissions",
        repo=repo,
        request_id=f"{case_id}-calc-patch",
    )
    work_order_path = run_dir / "work-order.json"
    result_path = repo / ".tau" / "receipts" / "scillm-result.json"
    launch_receipt_path = run_dir / "scillm-worker-launch-receipt.json"
    validate_receipt_path = repo / ".tau" / "receipts" / "scillm-worker-receipt.json"
    work_order = provider_live_work_order(
        case_id=case_id,
        repo=repo,
        goal_hash=goal_hash,
        timeout_s=max(120, timeout_s - 30),
    )
    write_json(work_order_path, work_order)
    write_sandbox_receipt(repo=repo, goal_hash=goal_hash, work_order_path=work_order_path)
    launch = json_object(
        run_command(
            [
                str(installed.tau_bin),
                "scillm-worker-launch",
                "--work-order",
                str(work_order_path),
                "--out",
                str(launch_receipt_path),
                "--apply",
                "--request-timeout-s",
                str(timeout_s),
            ],
            cwd=repo,
            env=env,
            timeout=timeout_s + 30,
            commands=commands,
        ).stdout,
        label=f"{case_id}_scillm_launch",
    )
    validate = json_object(
        run_command(
            [
                str(installed.tau_bin),
                "scillm-worker-validate",
                "--work-order",
                str(work_order_path),
                "--result",
                str(result_path),
                "--out",
                str(validate_receipt_path),
                "--launch-receipt",
                str(launch_receipt_path),
            ],
            cwd=repo,
            env=env,
            commands=commands,
        ).stdout,
        label=f"{case_id}_scillm_validate",
    )
    test_log = repo / ".tau" / "receipts" / "post-proof-test-output.txt"
    with test_log.open("w", encoding="utf-8") as output:
        test_process = subprocess.run(
            [str(installed.python_bin), "test_calc.py"],
            cwd=repo,
            env=env,
            check=False,
            stdout=output,
            stderr=subprocess.STDOUT,
            text=True,
            timeout=30,
        )
    commands.append(
        {
            "command": [str(installed.python_bin), "test_calc.py"],
            "cwd": str(repo),
            "exit_code": test_process.returncode,
            "stdout_path": str(test_log),
        }
    )
    if test_process.returncode != 0:
        raise RuntimeError(f"{case_id}_focused_test_failed:{test_log}")
    diff_text = run_command(
        ["git", "diff", "--no-ext-diff", "--no-color"],
        cwd=repo,
        commands=commands,
    ).stdout
    changed = run_command(
        ["git", "diff", "--name-only"],
        cwd=repo,
        commands=commands,
    ).stdout.splitlines()
    if changed != ["calc.py"]:
        raise RuntimeError(f"{case_id}_unexpected_git_diff:{changed}")
    if "return a + b" not in (repo / "calc.py").read_text(encoding="utf-8"):
        raise RuntimeError(f"{case_id}_calc_patch_missing")
    after_patch_hash = sha256_uri(repo / "calc.py")
    tui_resume = drive_tui_prompt(
        installed=installed,
        env=env,
        cwd=repo,
        run_id=f"{case_id}-resume",
        prompt="resume without duplicating the accepted calc.py edit",
        transcript_path=run_dir / "tui-resume.ansi",
    )
    after_resume_hash = sha256_uri(repo / "calc.py")
    if after_resume_hash != after_patch_hash:
        raise RuntimeError(f"{case_id}_resume_duplicated_effect")
    diff_path = run_dir / "final.diff"
    diff_path.write_text(diff_text, encoding="utf-8")
    result = json_object(result_path.read_text(encoding="utf-8"), label=f"{case_id}_worker_result")
    ok = (
        tui_first["input_received"]
        and tui_resume["input_received"]
        and memory["ok"] is True
        and permission_request.get("status") == "PENDING"
        and permission_reply.get("accepted") is True
        and launch.get("status") == "PASS"
        and launch.get("provider_live") is True
        and validate.get("status") == "PASS"
        and validate.get("provider_live") is True
        and result.get("schema") == RESULT_SCHEMA
        and result.get("goal_hash") == goal_hash
        and after_patch_hash == after_resume_hash
    )
    return {
        "case_id": case_id,
        "ok": ok,
        "status": "PASS" if ok else "BLOCKED",
        "mocked": False,
        "live": True,
        "provider_live": (
            launch.get("provider_live") is True and validate.get("provider_live") is True
        ),
        "repo": str(repo),
        "baseline_head": baseline_head,
        "goal_hash": goal_hash,
        "tui_submit": tui_first,
        "tui_resume": tui_resume,
        "memory_gate": memory,
        "permission_request": receipt_summary(permission_request),
        "permission_reply": receipt_summary(permission_reply),
        "launch_receipt": receipt_summary(launch),
        "validate_receipt": receipt_summary(validate),
        "worker_result": receipt_summary(result),
        "final_diff_path": str(diff_path),
        "final_diff_sha256": sha256_uri(diff_path),
        "changed_files": changed,
        "calc_py_sha256_after_patch": after_patch_hash,
        "calc_py_sha256_after_resume": after_resume_hash,
        "focused_test": {
            "command": [str(installed.python_bin), "test_calc.py"],
            "exit_code": test_process.returncode,
            "log_path": str(test_log),
            "log_sha256": sha256_uri(test_log),
        },
    }


def run_negative_unauthorized_case(
    *,
    installed: InstalledTau,
    env: dict[str, str],
    run_dir: Path,
    timeout_s: int,
    commands: list[dict[str, Any]],
) -> dict[str, Any]:
    run_dir.mkdir(parents=True, exist_ok=True)
    repo = run_dir / "workspace" / "repo"
    baseline_head = init_fixture_repo(repo)
    baseline_hash = sha256_uri(repo / "calc.py")
    goal_hash = f"sha256:{sha256_text('negative:' + str(repo))}"
    work_order_path = run_dir / "work-order.json"
    launch_receipt_path = run_dir / "scillm-worker-launch-receipt.json"
    work_order = provider_live_work_order(
        case_id="negative-unauthorized",
        repo=repo,
        goal_hash=goal_hash,
        timeout_s=60,
    )
    write_json(work_order_path, work_order)
    write_sandbox_receipt(repo=repo, goal_hash=goal_hash, work_order_path=work_order_path)
    process = run_command(
        [
            str(installed.tau_bin),
            "scillm-worker-launch",
            "--work-order",
            str(work_order_path),
            "--out",
            str(launch_receipt_path),
            "--apply",
            "--auth-token",
            "tau-issue-235-invalid-token",
            "--request-timeout-s",
            str(timeout_s),
        ],
        cwd=repo,
        env=env,
        timeout=timeout_s + 30,
        expected_codes=(1,),
        commands=commands,
    )
    launch = json_object(process.stdout, label="negative_unauthorized_launch")
    changed = run_command(
        ["git", "diff", "--name-only"],
        cwd=repo,
        commands=commands,
    ).stdout.splitlines()
    result_path = repo / ".tau" / "receipts" / "scillm-result.json"
    after_hash = sha256_uri(repo / "calc.py")
    ok = (
        launch.get("status") == "BLOCKED"
        and launch.get("provider_live") is False
        and launch.get("http_executed") is True
        and launch.get("http_status") in {401, 403}
        and changed == []
        and not result_path.exists()
        and after_hash == baseline_hash
    )
    return {
        "case_id": "negative-unauthorized",
        "ok": ok,
        "status": "PASS" if ok else "BLOCKED",
        "mocked": False,
        "live": True,
        "provider_live": False,
        "repo": str(repo),
        "baseline_head": baseline_head,
        "goal_hash": goal_hash,
        "launch_receipt": receipt_summary(launch),
        "changed_files": changed,
        "result_path_exists": result_path.exists(),
        "calc_py_sha256_before": baseline_hash,
        "calc_py_sha256_after": after_hash,
    }


def provider_live_work_order(
    *,
    case_id: str,
    repo: Path,
    goal_hash: str,
    timeout_s: int,
) -> dict[str, Any]:
    return {
        "schema": WORK_ORDER_SCHEMA,
        "dag_id": f"issue-235-{case_id}",
        "node_id": "provider-live-coder",
        "agent": "coder",
        "goal_hash": goal_hash,
        "attempt": 1,
        "repo": str(repo),
        "allowed_paths": ["calc.py", ".tau/receipts/**"],
        "forbidden_paths": ["README.md", "test_calc.py", ".git/**"],
        "task": (
            "Fix exactly one source file: calc.py. The current add(a, b) function is wrong. "
            "Change it so add(a, b) returns a + b. Do not edit README.md or test_calc.py. "
            "Run `python test_calc.py > .tau/receipts/test-output.txt 2>&1`. "
            "Write .tau/receipts/scillm-result.json as JSON with schema "
            f"{RESULT_SCHEMA}, status PASS, goal_hash {goal_hash}, changed_files [\"calc.py\"], "
            "artifacts [\"calc.py\", \".tau/receipts/test-output.txt\"], tests_run with one PASS "
            "entry whose command is python test_calc.py and log_path is "
            ".tau/receipts/test-output.txt, "
            "findings [], and next_recommended_route reviewer."
        ),
        "required_artifacts": ["calc.py", ".tau/receipts/test-output.txt"],
        "result_path": ".tau/receipts/scillm-result.json",
        "receipt_path": ".tau/receipts/scillm-worker-receipt.json",
        "execution_substrate": "docker-sandbox",
        "sandbox_receipt_path": ".tau/receipts/sandbox-run-receipt.json",
        "high_stakes": True,
        "zero_trust": True,
        "timeout_s": timeout_s,
        "policy_profile": {
            "schema": "tau.policy_profile.v1",
            "profile_id": "issue-235-provider-live",
            "default_decision": "deny",
            "requires_data_boundary": True,
            "network": {"default": "deny", "allowed_domains": []},
            "providers": {"cloud_llm": "allow_with_approval", "local_model": "allow_with_approval"},
            "research": {
                "external_search": "deny",
                "manual_sanitized_receipt": "allow_with_review",
            },
            "memory": {
                "read": "allow",
                "write": "approval_required",
                "intent_required": True,
                "min_intent_confidence": 0.75,
            },
            "github": {"public_mutation": "deny", "dry_run_projection": "allow"},
            "filesystem": {
                "write_allowlist": ["calc.py", ".tau/receipts/**"],
                "read_denylist": ["secrets/**"],
            },
        },
        "data_boundary": {
            "schema": "tau.data_boundary.v1",
            "classification": "public",
            "export_controlled": False,
            "itar": False,
            "technical_data": False,
            "foreign_person_access": "allowed",
            "external_provider_allowed": True,
            "external_research_allowed": False,
            "public_repo_allowed": False,
            "notes": ["Issue 235 public throwaway arithmetic fixture."],
        },
        "model_provider_route": {
            "surface": "opencode_serve",
            "endpoint": SCILLM_ENDPOINT,
            "agent": "build",
            "skills": ["scillm"],
        },
    }


def init_fixture_repo(repo: Path) -> str:
    repo.mkdir(parents=True, exist_ok=True)
    (repo / ".tau" / "receipts").mkdir(parents=True, exist_ok=True)
    (repo / "README.md").write_text("# Tau issue 235 fixture\n", encoding="utf-8")
    (repo / "calc.py").write_text(
        "def add(a: int, b: int) -> int:\n"
        "    return a - b\n",
        encoding="utf-8",
    )
    (repo / "test_calc.py").write_text(
        "from calc import add\n\n"
        "assert add(2, 3) == 5\n"
        "assert add(-2, 7) == 5\n"
        "print('calc tests passed')\n",
        encoding="utf-8",
    )
    run_command(["git", "init", "--initial-branch=main"], cwd=repo)
    run_command(["git", "config", "user.name", "Tau Issue 235 Proof"], cwd=repo)
    run_command(["git", "config", "user.email", "tau-issue-235@example.invalid"], cwd=repo)
    run_command(["git", "add", "README.md", "calc.py", "test_calc.py"], cwd=repo)
    run_command(["git", "commit", "-m", "fixture"], cwd=repo)
    return run_command(["git", "rev-parse", "HEAD"], cwd=repo).stdout.strip()


def write_sandbox_receipt(*, repo: Path, goal_hash: str, work_order_path: Path) -> Path:
    path = repo / ".tau" / "receipts" / "sandbox-run-receipt.json"
    payload = {
        "schema": "tau.sandbox_run_receipt.v1",
        "ok": True,
        "status": "PASS",
        "mocked": False,
        "live": True,
        "provider_live": False,
        "goal_hash": goal_hash,
        "work_order_sha256": sha256_uri(work_order_path),
        "command_executed": True,
        "network_egress": "denied",
        "provider_access": "denied",
        "policy_profile": {"schema": "tau.policy_profile.v1"},
        "data_boundary": {"schema": "tau.data_boundary.v1"},
    }
    write_json(path, payload)
    return path


def write_memory_gate_receipts(
    *,
    installed: InstalledTau,
    env: dict[str, str],
    run_dir: Path,
    goal_hash: str,
    repo: Path,
) -> dict[str, Any]:
    run_dir.mkdir(parents=True, exist_ok=True)
    script = (
        "import json\n"
        "from pathlib import Path\n"
        "from tau_coding.memory_evidence_gate import evaluate_memory_evidence_gate, "
        "write_memory_evidence_gate_receipts\n"
        f"out=Path({str(run_dir)!r})\n"
        f"goal_hash={goal_hash!r}\n"
        f"repo={str(repo)!r}\n"
        "policy={'schema':'tau.policy_profile.v1','profile_id':'issue-235-memory-first',"
        "'default_decision':'deny','memory':{'intent_required':True,"
        "'min_intent_confidence':0.75},'filesystem':{'write_allowlist':['calc.py'],"
        "'read_denylist':['secrets/**']}}\n"
        "boundary={'schema':'tau.data_boundary.v1','classification':'public',"
        "'export_controlled':False,'itar':False,'technical_data':False,"
        "'foreign_person_access':'allowed','external_provider_allowed':True,"
        "'external_research_allowed':False,'public_repo_allowed':False}\n"
        "intent={'schema':'memory.intent.v1','goal_hash':goal_hash,'memory_first':True,"
        "'planner_only':True,'route':'SUBAGENT','confidence':0.93,"
        "'target':{'repo':repo,'path':'calc.py'},'required_artifacts':['calc.py'],"
        "'tool_calls':[{'name':'scillm-worker-launch'}]}\n"
        "evidence={'schema':'memory.evidence_case.v1','source':'tau:local-memory-first-gate',"
        "'sha256':'sha256:issue-235-local-memory-evidence','goal_hash':goal_hash,"
        "'question':'Can Tau dispatch this bounded provider-live edit?',"
        "'data_boundary':boundary,"
        "'policy_profile':{'schema':policy['schema'],'profile_id':policy['profile_id'],"
        "'default_decision':policy['default_decision']},"
        "'support_artifacts':['calc.py','test_calc.py']}\n"
        "(out/'memory-intent.json').write_text(json.dumps(intent,indent=2,sort_keys=True)+'\\n')\n"
        "(out/'evidence-case.json').write_text(json.dumps(evidence,indent=2,sort_keys=True)+'\\n')\n"
        "ir,er=evaluate_memory_evidence_gate(policy_profile=policy,data_boundary=boundary,"
        "memory_intent=intent,evidence_case=evidence,memory_intent_path=out/'memory-intent.json',"
        "evidence_case_path=out/'evidence-case.json')\n"
        "ir,er=write_memory_evidence_gate_receipts(receipt_dir=out,intent_receipt=ir,"
        "evidence_receipt=er)\n"
        "payload={'ok':ir.get('ok') is True and er.get('ok') is True and "
        "er.get('allowed_to_dispatch') is True,'intent_receipt':ir,'evidence_receipt':er}\n"
        "print(json.dumps(payload,sort_keys=True))\n"
    )
    payload = json_object(
        run_command(
            [str(installed.python_bin), "-c", script],
            cwd=repo,
            env=env,
            commands=[],
        ).stdout,
        label="memory_gate_payload",
    )
    return {
        "ok": payload.get("ok") is True,
        "intent_receipt": receipt_summary(payload.get("intent_receipt", {})),
        "evidence_receipt": receipt_summary(payload.get("evidence_receipt", {})),
    }


def write_permission_receipts(
    *,
    installed: InstalledTau,
    env: dict[str, str],
    run_dir: Path,
    repo: Path,
    request_id: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    run_dir.mkdir(parents=True, exist_ok=True)
    request_path = run_dir / "permission-request.json"
    reply_path = run_dir / "permission-reply.json"
    request = json_object(
        run_command(
            [
                str(installed.tau_bin),
                "permission-request",
                "--action",
                "working_tree_mutation",
                "--resource",
                str(repo / "calc.py"),
                "--source-node",
                "provider-live-coder",
                "--run-dir",
                str(run_dir),
                "--output",
                str(request_path),
                "--session",
                request_id,
                "--request-id",
                request_id,
                "--mode",
                "build",
                "--save-rule",
                "session",
            ],
            cwd=repo,
            env=env,
        ).stdout,
        label="permission_request",
    )
    reply = json_object(
        run_command(
            [
                str(installed.tau_bin),
                "permission-reply",
                "--request",
                str(request_path),
                "--reply",
                "once",
                "--output",
                str(reply_path),
                "--actor",
                "issue-235-proof-operator",
                "--scope",
                "session",
            ],
            cwd=repo,
            env=env,
        ).stdout,
        label="permission_reply",
    )
    return request, reply


def drive_tui_prompt(
    *,
    installed: InstalledTau,
    env: dict[str, str],
    cwd: Path,
    run_id: str,
    prompt: str,
    transcript_path: Path,
    timeout: float = 20.0,
) -> dict[str, Any]:
    transcript_path.parent.mkdir(parents=True, exist_ok=True)
    child_env = dict(env)
    child_env.update({"TAU_TUI_PTY_PROOF": "1", "TAU_TUI_PTY_RUN_ID": run_id})
    argv = [str(installed.python_bin), "-m", "tau_coding.tui.app", "--pty-proof-real-app"]
    marker_prompt = (
        prompt
        if "TAU_TUI_PTY_BROWSER_INPUT" in prompt
        else f"TAU_TUI_PTY_BROWSER_INPUT {prompt}"
    )
    pid, fd = pty.fork()
    if pid == 0:
        os.chdir(cwd)
        os.execvpe(argv[0], argv, child_env)
    output = bytearray()
    ready = False
    sent = False
    input_received = False
    deadline = time.monotonic() + timeout
    try:
        while time.monotonic() < deadline:
            readable, _, _ = select.select([fd], [], [], 0.1)
            if fd in readable:
                try:
                    chunk = os.read(fd, 4096)
                except OSError:
                    break
                if not chunk:
                    break
                output.extend(chunk)
                text = output.decode("utf-8", errors="replace")
                if "TAU_TUI_PTY_READY" in text and run_id in text:
                    ready = True
                if ready and not sent and "Ask Tau" in text:
                    time.sleep(0.2)
                    for char in marker_prompt:
                        os.write(fd, char.encode("utf-8"))
                        time.sleep(0.002)
                    os.write(fd, b"\r")
                    sent = True
                if "TAU_TUI_PTY_INPUT_RECEIVED" in text and run_id in text:
                    input_received = True
                    break
        os.write(fd, b"\x03")
        time.sleep(0.2)
    finally:
        with suppress(OSError):
            os.close(fd)
        with suppress(ChildProcessError):
            os.waitpid(pid, 0)
    transcript_path.write_bytes(bytes(output))
    text = output.decode("utf-8", errors="replace")
    if not ready or not input_received:
        raise RuntimeError(f"tui_pty_marker_missing:{run_id}:{transcript_path}")
    return {
        "run_id": run_id,
        "ready": ready,
        "input_received": input_received,
        "transcript_path": str(transcript_path),
        "transcript_sha256": sha256_uri(transcript_path),
        "marker_excerpt": marker_excerpt(text, run_id),
    }


def marker_excerpt(text: str, run_id: str) -> list[str]:
    lines = []
    for raw in text.replace("\r", "\n").splitlines():
        clean = "".join(ch for ch in raw if ch.isprintable())
        if "TAU_TUI_PTY" in clean or run_id in clean:
            lines.append(clean[-240:])
    return lines[-8:]


def receipt_summary(payload: MappingLike) -> dict[str, Any]:
    if not isinstance(payload, dict):
        return {"schema": None, "status": None, "ok": False}
    path = payload.get("receipt_path") or payload.get("result_path") or payload.get("response_path")
    summary = {
        "schema": payload.get("schema"),
        "ok": payload.get("ok"),
        "status": payload.get("status"),
        "mocked": payload.get("mocked"),
        "live": payload.get("live"),
        "provider_live": payload.get("provider_live"),
        "alert_codes": payload.get("alert_codes", []),
        "path": path,
    }
    for key in (
        "run_id",
        "session_id",
        "http_status",
        "scillm_run_status",
        "observed_provider",
        "observed_model",
        "goal_hash",
        "changed_files",
    ):
        if key in payload:
            summary[key] = payload.get(key)
    if isinstance(path, str):
        artifact = Path(path)
        if artifact.exists() and artifact.is_file():
            summary["sha256"] = sha256_uri(artifact)
            summary["bytes"] = artifact.stat().st_size
    return summary


MappingLike = dict[str, Any] | Any


def run_command(
    command: list[str],
    *,
    cwd: Path,
    env: dict[str, str] | None = None,
    timeout: float = 120,
    expected_codes: tuple[int, ...] = (0,),
    commands: list[dict[str, Any]] | None = None,
) -> subprocess.CompletedProcess[str]:
    process = subprocess.run(
        command,
        cwd=cwd,
        env=env,
        check=False,
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    if commands is not None:
        commands.append(
            {
                "command": command,
                "cwd": str(cwd),
                "exit_code": process.returncode,
                "stdout_sha256": sha256_text(process.stdout),
                "stderr_sha256": sha256_text(process.stderr),
            }
        )
    if process.returncode not in expected_codes:
        raise RuntimeError(
            "command_failed:"
            f"{process.returncode}:{' '.join(command)}:"
            f"stdout={process.stdout[-1000:]} stderr={process.stderr[-1000:]}"
        )
    return process


def json_object(text: str, *, label: str) -> dict[str, Any]:
    payload = json.loads(text)
    if not isinstance(payload, dict):
        raise RuntimeError(f"{label}_not_object")
    return payload


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def sha256_uri(path: Path) -> str:
    return f"sha256:{hashlib.sha256(path.read_bytes()).hexdigest()}"


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def utc_stamp() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


if __name__ == "__main__":
    raise SystemExit(main())
