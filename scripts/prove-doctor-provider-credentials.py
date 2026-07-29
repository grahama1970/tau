#!/usr/bin/env python3
"""Live proof that tau doctor fails closed on unusable provider credentials."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

API_ENVS = (
    "OPENAI_API_KEY",
    "OPENAI_CODEX_ACCESS_TOKEN",
    "ANTHROPIC_AUTH_TOKEN",
    "ANTHROPIC_API_KEY",
    "OPENROUTER_API_KEY",
    "HF_TOKEN",
    "CHUTES_API_TOKEN",
)


class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        self.send_response(200 if self.path == "/health" else 404)
        self.end_headers()
        self.wfile.write(b"ok")

    def log_message(self, _format: str, *_args: object) -> None:
        return


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", type=Path, default=Path.cwd())
    parser.add_argument("--run-dir", type=Path, required=True)
    parser.add_argument(
        "--allow-live",
        action="store_true",
        help="Acknowledge this proof runs live local CLI and skill-wrapper commands.",
    )
    args = parser.parse_args()
    if not args.allow_live:
        raise RuntimeError("--allow-live is required for this live proof")

    repo = args.repo.expanduser().resolve()
    run_dir = args.run_dir.expanduser().resolve()
    run_dir.mkdir(parents=True, exist_ok=True)
    uv_bin = shutil.which("uv")
    if uv_bin is None:
        raise RuntimeError("uv is required for this proof")

    server = ThreadingHTTPServer(("127.0.0.1", 0), HealthHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    memory_url = f"http://127.0.0.1:{server.server_address[1]}"
    try:
        receipt = run_proof(repo=repo, run_dir=run_dir, uv_bin=uv_bin, memory_url=memory_url)
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
    receipt_path = run_dir / "doctor-provider-credentials-proof.json"
    receipt_path.write_text(json.dumps(receipt, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(receipt, indent=2, sort_keys=True))
    return 0 if receipt["status"] == "PASS" else 1


def run_proof(*, repo: Path, run_dir: Path, uv_bin: str, memory_url: str) -> dict[str, Any]:
    dist_dir = run_dir / "dist"
    venv_dir = run_dir / "venv"
    missing_home = run_dir / "home-missing"
    stub_home = run_dir / "home-stubbed"
    for path in (dist_dir, missing_home, stub_home):
        path.mkdir(parents=True, exist_ok=True)
    build = _run([uv_bin, "build", "--wheel", "--out-dir", str(dist_dir)], cwd=repo)
    wheels = sorted(dist_dir.glob("tau-*.whl"))
    wheel = wheels[-1] if wheels else None
    venv = _run([uv_bin, "venv", str(venv_dir)], cwd=repo)
    install = (
        _run(
            [uv_bin, "pip", "install", "--python", str(venv_dir / "bin" / "python"), str(wheel)],
            cwd=repo,
        )
        if wheel is not None
        else _skipped("wheel_missing")
    )

    _write_stub_credentials(stub_home)
    missing_env = _doctor_env(
        home=missing_home,
        uv_bin=uv_bin,
        memory_url=memory_url,
        api_credentials=False,
        repo=repo,
    )
    stub_env = _doctor_env(
        home=stub_home,
        uv_bin=uv_bin,
        memory_url=memory_url,
        api_credentials=True,
        repo=repo,
    )
    installed_tau = str(venv_dir / "bin" / "tau")
    skill_run = Path.home() / "workspace/experiments/agent-skills/skills/tau/run.sh"
    installed_missing = _doctor_call([installed_tau, "doctor"], cwd=repo, env=missing_env)
    installed_stubbed = _doctor_call([installed_tau, "doctor"], cwd=repo, env=stub_env)
    skill_missing = _doctor_call([str(skill_run), "doctor"], cwd=repo, env=missing_env)
    skill_stubbed = _doctor_call([str(skill_run), "doctor"], cwd=repo, env=stub_env)

    checks = {
        "build_ok": build["exit_code"] == 0 and wheel is not None,
        "venv_ok": venv["exit_code"] == 0,
        "install_ok": install["exit_code"] == 0,
        "installed_missing_degraded": _missing_payload_ok(installed_missing.get("payload")),
        "installed_stubbed_pass": _stubbed_payload_ok(installed_stubbed.get("payload")),
        "skill_missing_degraded": _missing_payload_ok(
            _skill_runtime_payload(skill_missing.get("payload")),
        ),
        "skill_stubbed_pass": _stubbed_payload_ok(
            _skill_runtime_payload(skill_stubbed.get("payload")),
        ),
    }
    return {
        "schema": "tau.doctor_provider_credentials_proof.v1",
        "status": "PASS" if all(checks.values()) else "FAIL",
        "ok": all(checks.values()),
        "mocked": False,
        "live": True,
        "provider_live": False,
        "repo": str(repo),
        "run_dir": str(run_dir),
        "wheel": str(wheel) if wheel is not None else None,
        "memory_url": memory_url,
        "checks": checks,
        "commands": {
            "build": build,
            "venv": venv,
            "install": install,
            "installed_missing": _summarize_doctor_call(installed_missing),
            "installed_stubbed": _summarize_doctor_call(installed_stubbed),
            "skill_missing": _summarize_doctor_call(skill_missing),
            "skill_stubbed": _summarize_doctor_call(skill_stubbed),
        },
        "proof_boundary": {
            "proves": [
                "Installed-wheel tau doctor degrades when configured provider "
                "credentials are missing.",
                "Installed-wheel tau doctor returns PASS when every configured provider "
                "has a credential.",
                "skills/tau/run.sh doctor relays the same runtime doctor credential status.",
                "Doctor checked credentials and local health without model/provider calls.",
            ],
            "does_not_prove": [
                "Provider/model semantic quality.",
                "That an API key or OAuth token is accepted by the remote provider.",
                "Revoked-token detection without a provider or SciLLM auth preflight.",
            ],
        },
    }


def _write_stub_credentials(home: Path) -> None:
    credential_dir = home / ".tau"
    credential_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "openai-codex": {
            "type": "oauth",
            "access": "access-token",
            "refresh": "refresh-token",
            "expires": int(time.time() * 1000) + 3_600_000,
            "account_id": "account-1",
        }
    }
    (credential_dir / "credentials.json").write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _doctor_env(
    *,
    home: Path,
    uv_bin: str,
    memory_url: str,
    api_credentials: bool,
    repo: Path,
) -> dict[str, str]:
    env = dict(os.environ)
    env["HOME"] = str(home)
    env["UV_BIN"] = uv_bin
    env["TAU_MEMORY_URL"] = memory_url
    env["TAU_ROOT"] = str(repo)
    env["PATH"] = os.environ.get("PATH", "")
    for name in API_ENVS:
        env.pop(name, None)
    if api_credentials:
        env.update(
            {
                "OPENAI_API_KEY": "openai-key",
                "ANTHROPIC_API_KEY": "anthropic-key",
                "OPENROUTER_API_KEY": "openrouter-key",
                "HF_TOKEN": "huggingface-key",
                "CHUTES_API_TOKEN": "chutes-key",
            }
        )
    return env


def _run(command: list[str], *, cwd: Path, env: dict[str, str] | None = None) -> dict[str, Any]:
    try:
        completed = subprocess.run(
            command,
            cwd=cwd,
            env=env,
            text=True,
            capture_output=True,
            timeout=120,
            check=False,
        )
    except FileNotFoundError as exc:
        return {
            "command": command,
            "cwd": str(cwd),
            "exit_code": 127,
            "stdout_tail": "",
            "stderr_tail": str(exc),
        }
    except subprocess.TimeoutExpired as exc:
        return {
            "command": command,
            "cwd": str(cwd),
            "exit_code": 124,
            "stdout_tail": str(exc.stdout or "")[-4000:],
            "stderr_tail": str(exc.stderr or "")[-4000:],
        }
    return {
        "command": command,
        "cwd": str(cwd),
        "exit_code": completed.returncode,
        "stdout": completed.stdout,
        "stdout_tail": completed.stdout[-4000:],
        "stderr_tail": completed.stderr[-4000:],
    }


def _doctor_call(
    command: list[str],
    *,
    cwd: Path,
    env: dict[str, str],
) -> dict[str, Any]:
    result = _run(command, cwd=cwd, env=env)
    try:
        payload = json.loads(result["stdout"])
    except json.JSONDecodeError:
        payload = None
    result["payload"] = payload
    result.pop("stdout", None)
    return result


def _skill_runtime_payload(payload: object) -> dict[str, Any] | None:
    if not isinstance(payload, dict):
        return None
    runtime = payload.get("tau_runtime_doctor")
    return runtime if isinstance(runtime, dict) else None


def _missing_payload_ok(payload: object) -> bool:
    if not isinstance(payload, dict):
        return False
    if payload.get("schema") != "tau.doctor.v1" or payload.get("status") == "PASS":
        return False
    provider_settings = payload.get("provider_settings")
    if not isinstance(provider_settings, dict):
        return False
    unusable = provider_settings.get("unusable_provider_credentials")
    warnings = payload.get("warnings")
    if not isinstance(unusable, list) or not isinstance(warnings, list) or not warnings:
        return False
    names = {
        item.get("name")
        for item in unusable
        if isinstance(item, dict) and item.get("reason_code") == "credential_missing"
    }
    warning_text = "\n".join(str(item) for item in warnings)
    return names == {
        "openai",
        "openai-codex",
        "anthropic",
        "openrouter",
        "huggingface",
        "chutes",
    } and all(name in warning_text for name in names)


def _stubbed_payload_ok(payload: object) -> bool:
    if not isinstance(payload, dict):
        return False
    provider_settings = payload.get("provider_settings")
    return (
        payload.get("schema") == "tau.doctor.v1"
        and payload.get("status") == "PASS"
        and isinstance(provider_settings, dict)
        and provider_settings.get("unusable_provider_credentials") == []
    )


def _summarize_doctor_call(call: dict[str, Any]) -> dict[str, Any]:
    payload = call.get("payload")
    runtime = _skill_runtime_payload(payload)
    effective = runtime or payload
    summary: dict[str, Any] = {
        "command": call["command"],
        "cwd": call["cwd"],
        "exit_code": call["exit_code"],
        "stderr_tail": call["stderr_tail"],
        "payload_status": effective.get("status") if isinstance(effective, dict) else None,
        "payload_warnings": effective.get("warnings") if isinstance(effective, dict) else None,
    }
    if isinstance(effective, dict):
        provider_settings = effective.get("provider_settings")
        if isinstance(provider_settings, dict):
            summary["unusable_provider_credentials"] = (
                provider_settings.get("unusable_provider_credentials")
            )
    return summary


def _skipped(reason: str) -> dict[str, Any]:
    return {
        "command": [],
        "cwd": "",
        "exit_code": 1,
        "stdout_tail": "",
        "stderr_tail": reason,
    }


if __name__ == "__main__":
    sys.exit(main())
