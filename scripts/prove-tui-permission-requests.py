#!/usr/bin/env python3
"""Run installed-wheel TUI permission request approval proof."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import pty
import re
import select
import struct
import subprocess
import sysconfig
import termios
import time
import venv
from contextlib import suppress
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from fcntl import ioctl
from pathlib import Path
from typing import Any

PROOF_SCHEMA = "tau.tui_permission_requests_proof.v1"


@dataclass(frozen=True)
class InstalledTau:
    root: Path
    home: Path
    bin_dir: Path
    site_packages: Path
    dependency_site: Path
    python_bin: Path
    tau_bin: Path
    import_probe: dict[str, Any]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, default=Path.cwd())
    parser.add_argument("--run-dir", type=Path, required=True)
    parser.add_argument("--allow-live", action="store_true")
    args = parser.parse_args()
    if not args.allow_live:
        raise SystemExit("refusing_live_tui_permission_proof_without_--allow-live")
    repo = args.repo.expanduser().resolve()
    run_dir = args.run_dir.expanduser().resolve()
    run_dir.mkdir(parents=True, exist_ok=True)
    commands: list[dict[str, Any]] = []

    wheel = build_wheel(repo=repo, run_dir=run_dir, commands=commands)
    installed = install_wheel(wheel=wheel, run_dir=run_dir, commands=commands)
    fixture_repo = make_fixture_repo(run_dir / "workspace" / "repo")
    permissions_dir = (
        fixture_repo / "experiments" / "goal-locked-subagents" / "proofs" / "permissions"
    )
    permissions_dir.mkdir(parents=True, exist_ok=True)
    write_runner(run_dir / "run_tui.py")
    env = installed_env(installed)
    expires_at = (datetime.now(UTC) + timedelta(minutes=20)).replace(microsecond=0)
    expired_at = (datetime.now(UTC) - timedelta(minutes=1)).replace(microsecond=0)

    approve_request = create_request(
        installed,
        env=env,
        cwd=fixture_repo,
        run_dir=permissions_dir,
        request_id="req-approve",
        resource="allowed-target.txt",
        nonce="nonce-approve",
        expires_at=expires_at,
        commands=commands,
    )
    approve_phase = drive_tui_phase(
        installed=installed,
        env=env,
        cwd=fixture_repo,
        run_dir=run_dir / "phase-approve",
        key="a",
        required=("Permission Requests", "req-approve", "PENDING"),
    )
    approve_reply = permissions_dir / "permission-reply-req-approve.json"
    approve_readback = drive_tui_phase(
        installed=installed,
        env=env,
        cwd=fixture_repo,
        run_dir=run_dir / "phase-approve-readback",
        key=None,
        required=("Permission Requests", "req-approve", "APPROVED"),
    )

    admit = gate_check(
        installed,
        env=env,
        cwd=fixture_repo,
        run_dir=permissions_dir,
        request=approve_request,
        reply=approve_reply,
        resource="allowed-target.txt",
        nonce="nonce-approve",
        output=permissions_dir / "gate-admit.json",
        commands=commands,
        expect_ok=True,
    )
    negatives = {
        "replay": gate_check(
            installed,
            env=env,
            cwd=fixture_repo,
            run_dir=permissions_dir,
            request=approve_request,
            reply=approve_reply,
            resource="allowed-target.txt",
            nonce="nonce-approve",
            output=permissions_dir / "gate-replay.json",
            commands=commands,
            expect_ok=False,
        ),
        "alter_resource": gate_check(
            installed,
            env=env,
            cwd=fixture_repo,
            run_dir=permissions_dir / "alter-resource",
            request=approve_request,
            reply=approve_reply,
            resource="other-target.txt",
            nonce="nonce-approve",
            output=permissions_dir / "gate-alter-resource.json",
            commands=commands,
            expect_ok=False,
        ),
        "alter_action": gate_check(
            installed,
            env=env,
            cwd=fixture_repo,
            run_dir=permissions_dir / "alter-action",
            request=approve_request,
            reply=approve_reply,
            resource="allowed-target.txt",
            nonce="nonce-approve",
            output=permissions_dir / "gate-alter-action.json",
            commands=commands,
            expect_ok=False,
            action="memory_upsert",
        ),
        "alter_goal": gate_check(
            installed,
            env=env,
            cwd=fixture_repo,
            run_dir=permissions_dir / "alter-goal",
            request=approve_request,
            reply=approve_reply,
            resource="allowed-target.txt",
            nonce="nonce-approve",
            output=permissions_dir / "gate-alter-goal.json",
            commands=commands,
            expect_ok=False,
            goal_hash="goal-other",
        ),
        "alter_session": gate_check(
            installed,
            env=env,
            cwd=fixture_repo,
            run_dir=permissions_dir / "alter-session",
            request=approve_request,
            reply=approve_reply,
            resource="allowed-target.txt",
            nonce="nonce-approve",
            output=permissions_dir / "gate-alter-session.json",
            commands=commands,
            expect_ok=False,
            session_id="session-other",
        ),
    }

    deny_request = create_request(
        installed,
        env=env,
        cwd=fixture_repo,
        run_dir=permissions_dir,
        request_id="req-deny",
        resource="denied-target.txt",
        nonce="nonce-deny",
        expires_at=expires_at,
        commands=commands,
    )
    deny_phase = drive_tui_phase(
        installed=installed,
        env=env,
        cwd=fixture_repo,
        run_dir=run_dir / "phase-deny",
        key="d",
        required=("Permission Requests", "req-deny", "PENDING"),
    )
    deny_reply = permissions_dir / "permission-reply-req-deny.json"
    deny_readback = drive_tui_phase(
        installed=installed,
        env=env,
        cwd=fixture_repo,
        run_dir=run_dir / "phase-deny-readback",
        key=None,
        required=("Permission Requests", "req-deny", "DENIED"),
    )
    negatives["denied_request"] = gate_check(
        installed,
        env=env,
        cwd=fixture_repo,
        run_dir=permissions_dir / "denied",
        request=deny_request,
        reply=deny_reply,
        resource="denied-target.txt",
        nonce="nonce-deny",
        output=permissions_dir / "gate-denied.json",
        commands=commands,
        expect_ok=False,
    )

    cancel_request = create_request(
        installed,
        env=env,
        cwd=fixture_repo,
        run_dir=permissions_dir,
        request_id="req-cancel",
        resource="cancel-target.txt",
        nonce="nonce-cancel",
        expires_at=expires_at,
        commands=commands,
    )
    cancel_phase = drive_tui_phase(
        installed=installed,
        env=env,
        cwd=fixture_repo,
        run_dir=run_dir / "phase-cancel",
        key="escape",
        required=("Permission Requests", "req-cancel", "PENDING"),
    )
    cancel_reply_absent = not (permissions_dir / "permission-reply-req-cancel.json").exists()

    expired_request = create_request(
        installed,
        env=env,
        cwd=fixture_repo,
        run_dir=permissions_dir,
        request_id="req-expired",
        resource="expired-target.txt",
        nonce="nonce-expired",
        expires_at=expired_at,
        commands=commands,
    )
    expired_reply = reply_check(
        installed,
        env=env,
        cwd=fixture_repo,
        request=expired_request,
        output=permissions_dir / "permission-reply-req-expired.json",
        reply="once",
        commands=commands,
        expect_ok=False,
    )
    malformed_request = permissions_dir / "permission-request-req-malformed.json"
    malformed_request.write_text("{not-json\n", encoding="utf-8")
    malformed_reply = reply_check(
        installed,
        env=env,
        cwd=fixture_repo,
        request=malformed_request,
        output=permissions_dir / "permission-reply-req-malformed.json",
        reply="once",
        commands=commands,
        expect_ok=False,
    )
    final_readback = drive_tui_phase(
        installed=installed,
        env=env,
        cwd=fixture_repo,
        run_dir=run_dir / "phase-final-readback",
        key=None,
        required=("req-expired", "EXPIRED", "req-malformed", "MALFORMED"),
    )
    ok = (
        approve_phase["ok"]
        and approve_readback["ok"]
        and deny_phase["ok"]
        and deny_readback["ok"]
        and cancel_phase["ok"]
        and final_readback["ok"]
        and cancel_reply_absent
        and admit["ok"] is True
        and all(item["ok"] is False and item["exit_code"] != 0 for item in negatives.values())
        and expired_reply["ok"] is False
        and malformed_reply["ok"] is False
    )
    receipt = {
        "schema": PROOF_SCHEMA,
        "status": "PASS" if ok else "BLOCKED",
        "ok": ok,
        "mocked": False,
        "live": True,
        "provider_live": False,
        "generated_at": datetime.now(tz=UTC).replace(microsecond=0).isoformat(),
        "wheel": str(wheel),
        "wheel_sha256": sha256_uri(wheel),
        "installed_tau": installed.import_probe,
        "permissions_dir": str(permissions_dir),
        "requests": {
            "approved": str(approve_request),
            "denied": str(deny_request),
            "cancelled": str(cancel_request),
            "expired": str(expired_request),
            "malformed": str(malformed_request),
        },
        "replies": {
            "approved": read_json(approve_reply),
            "denied": read_json(deny_reply),
            "cancel_reply_absent": cancel_reply_absent,
            "expired_attempt": expired_reply,
            "malformed_attempt": malformed_reply,
        },
        "gate": {"admit": admit, "negatives": negatives},
        "phases": [
            approve_phase,
            approve_readback,
            deny_phase,
            deny_readback,
            cancel_phase,
            final_readback,
        ],
        "commands": commands,
        "proof_scope": {
            "proves": [
                "Installed-wheel Tau created real permission request receipts through public CLI.",
                "Actual Textual TUI opened /permissions through a PTY and approved one request.",
                "Actual Textual TUI denied a second request and cancelled a third without reply.",
                (
                    "Reply receipts were read back from authoritative request/reply files "
                    "after restart."
                ),
                "Permission gate admitted the matching operation once and rejected replay plus "
                "action/resource/goal/session/denied/expired/malformed negative cases.",
            ],
            "does_not_prove": [
                "Provider/model behavior.",
                "Every possible terminal size.",
                "Execution of the governed mutation body after admission.",
            ],
        },
    }
    proof_path = run_dir / "tui-permission-requests-proof.json"
    write_json(proof_path, receipt)
    print(json.dumps(receipt, indent=2, sort_keys=True))
    return 0 if ok else 1


def build_wheel(*, repo: Path, run_dir: Path, commands: list[dict[str, Any]]) -> Path:
    wheelhouse = run_dir / "wheelhouse"
    wheelhouse.mkdir(parents=True, exist_ok=True)
    run_command(
        ["uv", "build", "--wheel", "--out-dir", str(wheelhouse)],
        cwd=repo,
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
    (home / ".tau").mkdir(parents=True, exist_ok=True)
    write_json(home / ".tau" / "tui.json", {"keybindings": {}})
    venv.EnvBuilder(with_pip=True).create(environment)
    bin_dir = environment / ("Scripts" if os.name == "nt" else "bin")
    python_bin = bin_dir / "python"
    tau_bin = bin_dir / "tau"
    site_packages = Path(
        run_command(
            [str(python_bin), "-c", "import sysconfig; print(sysconfig.get_path('purelib'))"],
            cwd=root,
            commands=commands,
        ).stdout.strip()
    ).resolve()
    dependency_site = Path(sysconfig.get_path("purelib")).resolve()
    (site_packages / "tau-locked-dependencies.pth").write_text(
        str(dependency_site) + "\n",
        encoding="utf-8",
    )
    env = clean_env(home=home, bin_dir=bin_dir)
    run_command(
        [str(python_bin), "-m", "pip", "install", "--quiet", "--no-index", "--no-deps", str(wheel)],
        cwd=root,
        env=env,
        commands=commands,
    )
    import_probe = json.loads(
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
        ).stdout
    )
    tau_import = Path(str(import_probe["tau_coding"])).resolve()
    if not tau_import.is_relative_to(site_packages):
        raise RuntimeError(f"installed_tau_import_not_from_wheel:{tau_import}")
    return InstalledTau(
        root=root,
        home=home,
        bin_dir=bin_dir,
        site_packages=site_packages,
        dependency_site=dependency_site,
        python_bin=python_bin,
        tau_bin=tau_bin,
        import_probe={
            **import_probe,
            "site_packages": str(site_packages),
            "dependency_site": str(dependency_site),
        },
    )


def make_fixture_repo(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    (path / "AGENTS.md").write_text("TUI permission proof fixture.\n", encoding="utf-8")
    return path


def write_runner(path: Path) -> None:
    path.write_text(
        """
import asyncio
from pathlib import Path
from tau_coding.provider_config import OpenAICompatibleProviderConfig, ProviderSettings
from tau_coding.tui.app import run_tui_app

async def main():
    provider = OpenAICompatibleProviderConfig(
        name="permission-proof",
        base_url="http://127.0.0.1:9/v1",
        api_key_env="TAU_PERMISSION_PROOF_KEY",
        models=("permission-proof",),
        default_model="permission-proof",
    )
    settings = ProviderSettings(default_provider=provider.name, providers=(provider,))
    await run_tui_app(
        model="permission-proof",
        cwd=Path.cwd(),
        provider_name="permission-proof",
        provider_settings=settings,
        new_session=True,
        no_skills=True,
        no_prompt_templates=True,
        no_themes=True,
        no_extensions=True,
        no_context_files=True,
        no_tools=True,
    )

asyncio.run(main())
""".lstrip(),
        encoding="utf-8",
    )


def installed_env(installed: InstalledTau) -> dict[str, str]:
    env = clean_env(home=installed.home, bin_dir=installed.bin_dir)
    env.update(
        {
            "TERM": "xterm-256color",
            "TAU_PERMISSION_PROOF_KEY": "unused",
            "TAU_TUI_PERMISSION_PROOF_OPEN": "1",
        }
    )
    return env


def clean_env(*, home: Path, bin_dir: Path) -> dict[str, str]:
    env = {
        key: value
        for key, value in os.environ.items()
        if key not in {"PYTHONHOME", "PYTHONPATH"}
    }
    env.update({"HOME": str(home), "PATH": f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}"})
    return env


def create_request(
    installed: InstalledTau,
    *,
    env: dict[str, str],
    cwd: Path,
    run_dir: Path,
    request_id: str,
    resource: str,
    nonce: str,
    expires_at: datetime,
    commands: list[dict[str, Any]],
) -> Path:
    output = run_dir / f"permission-request-{request_id}.json"
    run_command(
        [
            str(installed.tau_bin),
            "permission-request",
            "--action",
            "working_tree_mutation",
            "--resource",
            resource,
            "--source-node",
            "node-permission-proof",
            "--run-dir",
            str(run_dir),
            "--output",
            str(output),
            "--request-id",
            request_id,
            "--session",
            "session-permission-proof",
            "--goal-hash",
            "goal-permission-proof",
            "--active-goal",
            "approve or deny pending permission requests inside the Tau TUI",
            "--scope",
            "once",
            "--nonce",
            nonce,
            "--expires-at",
            expires_at.isoformat().replace("+00:00", "Z"),
            "--turn",
            "turn-permission-proof",
            "--attempt",
            "attempt-permission-proof",
        ],
        cwd=cwd,
        env=env,
        commands=commands,
    )
    return output


def reply_check(
    installed: InstalledTau,
    *,
    env: dict[str, str],
    cwd: Path,
    request: Path,
    output: Path,
    reply: str,
    commands: list[dict[str, Any]],
    expect_ok: bool,
) -> dict[str, Any]:
    completed = run_command(
        [
            str(installed.tau_bin),
            "permission-reply",
            "--request",
            str(request),
            "--reply",
            reply,
            "--output",
            str(output),
            "--actor",
            "human:tui-proof",
        ],
        cwd=cwd,
        env=env,
        commands=commands,
        check=False,
    )
    payload = read_json_from_stdout_or_file(completed.stdout, output)
    if expect_ok and completed.returncode != 0:
        raise RuntimeError(completed.stderr or completed.stdout)
    return {"exit_code": completed.returncode, **payload}


def gate_check(
    installed: InstalledTau,
    *,
    env: dict[str, str],
    cwd: Path,
    run_dir: Path,
    request: Path,
    reply: Path,
    resource: str,
    nonce: str,
    output: Path,
    commands: list[dict[str, Any]],
    expect_ok: bool,
    action: str = "working_tree_mutation",
    goal_hash: str = "goal-permission-proof",
    session_id: str = "session-permission-proof",
) -> dict[str, Any]:
    completed = run_command(
        [
            str(installed.tau_bin),
            "permission-gate-check",
            "--request",
            str(request),
            "--reply",
            str(reply),
            "--requested-action",
            action,
            "--resource",
            resource,
            "--run-dir",
            str(run_dir),
            "--output",
            str(output),
            "--session",
            session_id,
            "--goal-hash",
            goal_hash,
            "--nonce",
            nonce,
        ],
        cwd=cwd,
        env=env,
        commands=commands,
        check=False,
    )
    payload = read_json_from_stdout_or_file(completed.stdout, output)
    if expect_ok and completed.returncode != 0:
        raise RuntimeError(completed.stderr or completed.stdout)
    return {"exit_code": completed.returncode, **payload}


def drive_tui_phase(
    *,
    installed: InstalledTau,
    env: dict[str, str],
    cwd: Path,
    run_dir: Path,
    key: str | None,
    required: tuple[str, ...],
) -> dict[str, Any]:
    run_dir.mkdir(parents=True, exist_ok=True)
    output = drive_pty(
        argv=[str(installed.python_bin), str(run_dir.parent / "run_tui.py")],
        env=env,
        cwd=cwd,
        key=key,
    )
    ansi_path = run_dir / "terminal-capture.ansi"
    text_path = run_dir / "terminal-capture.txt"
    ansi_path.write_bytes(output)
    text = strip_ansi(output.decode("utf-8", errors="replace"))
    text_path.write_text(text, encoding="utf-8")
    missing = [needle for needle in required if needle not in text]
    return {
        "ok": not missing,
        "missing": missing,
        "key": key,
        "ansi_capture": str(ansi_path),
        "ansi_capture_sha256": sha256_uri(ansi_path),
        "text_capture": str(text_path),
        "text_capture_sha256": sha256_uri(text_path),
    }


def drive_pty(*, argv: list[str], env: dict[str, str], cwd: Path, key: str | None) -> bytes:
    pid, fd = pty.fork()
    if pid == 0:
        os.chdir(cwd)
        os.execvpe(argv[0], argv, env)
    set_winsize(fd, rows=36, columns=120)
    output = bytearray()
    step = 0
    deadline = time.monotonic() + 25
    try:
        while time.monotonic() < deadline:
            readable, _, _ = select.select([fd], [], [], 0.1)
            if fd not in readable:
                continue
            try:
                chunk = os.read(fd, 8192)
            except OSError:
                break
            if not chunk:
                break
            output.extend(chunk)
            text = strip_ansi(output.decode("utf-8", errors="replace"))
            if step == 0 and "Permission Requests" in text:
                time.sleep(0.3)
                if key == "a":
                    os.write(fd, b"a")
                elif key == "d":
                    os.write(fd, b"d")
                elif key == "escape":
                    os.write(fd, b"\x1b")
                else:
                    os.write(fd, b"\x1b")
                step = 1
            elif step == 1:
                time.sleep(0.8)
                os.write(fd, b"\x04")
                step = 2
    finally:
        with suppress(OSError):
            os.write(fd, b"\x04")
        time.sleep(0.2)
        with suppress(OSError):
            os.close(fd)
        with suppress(ChildProcessError):
            os.waitpid(pid, 0)
    return bytes(output)


def write_text_fast(fd: int, text: str) -> None:
    os.write(fd, text.encode("utf-8"))
    time.sleep(0.05)


def run_command(
    argv: list[str],
    *,
    cwd: Path,
    commands: list[dict[str, Any]],
    env: dict[str, str] | None = None,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    completed = subprocess.run(argv, cwd=cwd, env=env, text=True, capture_output=True, timeout=180)
    commands.append(
        {
            "command": argv,
            "cwd": str(cwd),
            "exit_code": completed.returncode,
            "stdout_sha256": sha256_bytes(completed.stdout.encode()),
            "stderr_sha256": sha256_bytes(completed.stderr.encode()),
        }
    )
    if check and completed.returncode != 0:
        raise RuntimeError(completed.stderr or completed.stdout)
    return completed


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def read_json_from_stdout_or_file(stdout: str, path: Path) -> dict[str, Any]:
    if stdout.strip():
        return json.loads(stdout)
    if path.exists():
        return read_json(path)
    return {"ok": False, "status": "BLOCKED", "errors": ["no JSON output"]}


def set_winsize(fd: int, *, rows: int, columns: int) -> None:
    ioctl(fd, termios.TIOCSWINSZ, struct.pack("HHHH", rows, columns, 0, 0))


ANSI_RE = re.compile(
    r"(?:\x1b\][^\a]*(?:\a|\x1b\\))|(?:\x1b\[[0-?]*[ -/]*[@-~])|(?:\x1b[()][A-Za-z0-9])"
)


def strip_ansi(value: str) -> str:
    return ANSI_RE.sub("", value).replace("\r", "\n")


def sha256_uri(path: Path) -> str:
    return "sha256:" + sha256_bytes(path.read_bytes())


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


if __name__ == "__main__":
    raise SystemExit(main())
