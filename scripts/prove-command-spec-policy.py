#!/usr/bin/env python3
"""Run installed-wheel proof for declarative custom slash command policy."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import pty
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

PROOF_SCHEMA = "tau.command_spec_policy_live_proof.v1"
COMMAND_SPEC_SCHEMA = "tau.command_spec_policy.v1"
APPROVAL_PACKET_SCHEMA = "tau.human_approval_packet.v1"
LOCAL_SIGNATURE_PREFIX = "local-signature-sha256:"
EXPECTED_REASON_CODES = {
    "built_in_name_collision",
    "duplicate_custom_name_same_precedence",
    "missing_or_unknown_schema_version",
    "path_escape",
    "undeclared_subprocess",
    "undeclared_network_or_provider",
    "side_effect_permission_required",
    "route_not_present",
    "limit_outside_policy",
    "forbidden_control_plane_mutation",
}


@dataclass(frozen=True)
class InstalledTau:
    root: Path
    home: Path
    bin_dir: Path
    python_bin: Path
    site_packages: Path
    dependency_site: Path
    import_probe: dict[str, Any]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, default=Path.cwd())
    parser.add_argument("--run-dir", type=Path, required=True)
    parser.add_argument("--allow-live", action="store_true")
    args = parser.parse_args()
    if not args.allow_live:
        raise SystemExit("refusing_live_command_spec_proof_without_--allow-live")

    repo = args.repo.expanduser().resolve()
    run_dir = args.run_dir.expanduser().resolve()
    run_dir.mkdir(parents=True, exist_ok=True)
    commands: list[dict[str, Any]] = []

    wheel = build_wheel(repo=repo, run_dir=run_dir, commands=commands)
    installed = install_wheel(wheel=wheel, run_dir=run_dir, commands=commands)
    workspace = make_fixture_repo(run_dir / "workspace" / "repo")
    write_manifests(workspace=workspace, home=installed.home)

    loader_probe = inspect_loader(installed=installed, workspace=workspace, commands=commands)
    approval_receipt = write_approval_receipt(
        installed=installed,
        workspace=workspace,
        run_dir=run_dir / "approval",
        commands=commands,
    )
    completion_execution = drive_real_tui(
        installed=installed,
        cwd=workspace,
        run_dir=run_dir / "phase-completion-execution",
        input_chunks=(b"/custom", b"-read r"),
        required=("Project read resource command", "resources"),
    )
    read_only_execution = drive_real_tui(
        installed=installed,
        cwd=workspace,
        run_dir=run_dir / "phase-read-only-execution",
        submit_text="/custom-read resources",
        required=("Skills: 0", "Resource diagnostics:"),
    )
    restart_readback = drive_real_tui(
        installed=installed,
        cwd=workspace,
        run_dir=run_dir / "phase-restart-readback",
        input_chunks=(b"/custom",),
        required=("Project read resource command",),
    )
    side_effect_blocked = drive_real_tui(
        installed=installed,
        cwd=workspace,
        run_dir=run_dir / "phase-side-effect-blocked",
        submit_text="/insert-note resources",
        required=("permission_required", "permission_request=", "policy_receipt="),
    )
    side_effect_approved = drive_real_tui(
        installed=installed,
        cwd=workspace,
        run_dir=run_dir / "phase-side-effect-approved",
        submit_text=f"/insert-note resources --approval-receipt {approval_receipt}",
        required=(
            "command_policy_receipt=",
            "command_route_receipt=",
        ),
    )
    receipt_probe = inspect_receipts(workspace / ".tau" / "receipts" / "command-specs")

    reason_codes = set(loader_probe["reason_codes"])
    ok = (
        loader_probe["ok"]
        and reason_codes >= EXPECTED_REASON_CODES
        and completion_execution["ok"]
        and read_only_execution["ok"]
        and restart_readback["ok"]
        and side_effect_blocked["ok"]
        and side_effect_approved["ok"]
        and receipt_probe["ok"]
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
        "workspace": str(workspace),
        "loader_probe": loader_probe,
        "completion_execution": completion_execution,
        "read_only_execution": read_only_execution,
        "restart_readback": restart_readback,
        "side_effect_blocked": side_effect_blocked,
        "side_effect_approved": side_effect_approved,
        "approval_receipt": str(approval_receipt),
        "receipt_probe": receipt_probe,
        "commands": commands,
        "proof_scope": {
            "proves": [
                (
                    "Installed-wheel Tau discovers declarative command manifests from "
                    "user/project resource directories."
                ),
                "Project command specs deterministically override lower-precedence user specs.",
                "Accepted command specs appear in the real TUI slash completion surface.",
                "Declared enum argument completions appear in the real TUI completion surface.",
                (
                    "A read-only command spec executes through an allowlisted built-in "
                    "Tau command route."
                ),
                "A side-effect command spec stops at Tau's approval boundary before mutation.",
                (
                    "The same side-effect command executes after an action/resource-bound "
                    "approval receipt."
                ),
                "Policy and route execution receipts are emitted for command-spec execution.",
                (
                    "Named negative manifests are rejected before registration with "
                    "stable reason codes."
                ),
            ],
            "does_not_prove": [
                "Provider-live behavior.",
                (
                    "Arbitrary plugin imports, package installation, shell, network, "
                    "or provider routes."
                ),
                "Full parity with Pi/OpenCode/Codex/Claude extension systems.",
            ],
        },
    }
    proof_path = run_dir / "command-spec-policy-proof.json"
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
    venv.EnvBuilder(with_pip=True).create(environment)
    bin_dir = environment / ("Scripts" if os.name == "nt" else "bin")
    python_bin = bin_dir / "python"
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
                "import json, tau_coding; print(json.dumps({'tau_coding': tau_coding.__file__}))",
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
        python_bin=python_bin,
        site_packages=site_packages,
        dependency_site=dependency_site,
        import_probe={
            **import_probe,
            "site_packages": str(site_packages),
            "dependency_site": str(dependency_site),
        },
    )


def make_fixture_repo(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    (path / "AGENTS.md").write_text("# Fixture\n", encoding="utf-8")
    (path / "GOAL.md").write_text("# Fixture Goal\n", encoding="utf-8")
    (path / "notes").mkdir(exist_ok=True)
    (path / "notes" / "allowed.txt").write_text("allowed resource\n", encoding="utf-8")
    outside = path.parent / "outside.txt"
    outside.write_text("outside\n", encoding="utf-8")
    escape = path / "escape-link"
    with suppress(FileExistsError):
        escape.symlink_to(outside)
    run_command(["git", "init"], cwd=path, commands=[])
    return path


def write_manifests(*, workspace: Path, home: Path) -> None:
    user_dir = home / ".tau" / "commands"
    project_dir = workspace / ".tau" / "commands"
    user_dir.mkdir(parents=True, exist_ok=True)
    project_dir.mkdir(parents=True, exist_ok=True)
    write_json(
        user_dir / "custom-read.json",
        manifest("custom-read", "User lower precedence", "session"),
    )
    write_json(
        project_dir / "custom-read.json",
        manifest("custom-read", "Project read resource command", "resources"),
    )
    write_json(
        project_dir / "insert-note.json",
        {
            **manifest("insert-note", "Insert note with approval", "resources"),
            "route": {
                "type": "editor_insert",
                "text_by_enum": {"resources": "Inserted from command spec."},
            },
            "side_effect_class": "prompt_editor_insert",
            "required_permission_class": "working_tree_mutation",
        },
    )
    write_json(
        project_dir / "builtin-collision.json",
        manifest("resources", "Collision", "session"),
    )
    write_json(project_dir / "duplicate-a.json", manifest("same-name", "Duplicate A", "session"))
    write_json(project_dir / "duplicate-b.json", manifest("same-name", "Duplicate B", "session"))
    missing_schema = manifest("missing-schema", "Missing schema", "session")
    missing_schema.pop("schema")
    write_json(project_dir / "missing-schema.json", missing_schema)
    write_json(
        project_dir / "path-escape.json",
        {**manifest("path-escape", "Path escape", "session"), "resources": ["escape-link"]},
    )
    write_json(
        project_dir / "subprocess.json",
        {
            **manifest("subprocess-cmd", "Subprocess denied", "session"),
            "requirements": {"network": False, "subprocess": True, "provider": False},
        },
    )
    write_json(
        project_dir / "network.json",
        {
            **manifest("network-cmd", "Network denied", "session"),
            "requirements": {"network": True, "subprocess": False, "provider": True},
        },
    )
    write_json(
        project_dir / "side-effect-no-permission.json",
        {
            **manifest("side-effect-no-permission", "Missing permission class", "session"),
            "side_effect_class": "prompt_editor_insert",
        },
    )
    write_json(
        project_dir / "route-missing.json",
        {
            **manifest("route-missing", "Missing route", "session"),
            "route": {"type": "builtin_command", "command": "__missing__"},
        },
    )
    write_json(
        project_dir / "limit.json",
        {
            **manifest("limit-cmd", "Bad limit", "session"),
            "limits": {"timeout_seconds": 99999, "max_output_bytes": 99999999},
        },
    )
    write_json(
        project_dir / "forbidden.json",
        manifest("policy", "Forbidden control plane", "session"),
    )


def manifest(name: str, description: str, route_command: str) -> dict[str, Any]:
    return {
        "schema": COMMAND_SPEC_SCHEMA,
        "name": name,
        "description": description,
        "usage": f"/{name} <topic>",
        "arguments": [
            {
                "name": "topic",
                "type": "enum",
                "required": True,
                "values": [{"value": "resources", "description": "Show Tau resources"}],
            }
        ],
        "route": {
            "type": "builtin_command",
            "command": route_command,
            "args_by_enum": {"resources": ""},
        },
        "input_types": ["enum"],
        "output_types": ["text", "receipt"],
        "side_effect_class": "read_only",
        "required_permission_class": None,
        "resources": ["notes/allowed.txt"],
        "limits": {"timeout_seconds": 30, "max_output_bytes": 8192},
        "requirements": {"network": False, "subprocess": False, "provider": False},
        "receipts": {"expected": [COMMAND_SPEC_SCHEMA, "tau.command_execution_receipt.v1"]},
    }


def inspect_loader(
    *,
    installed: InstalledTau,
    workspace: Path,
    commands: list[dict[str, Any]],
) -> dict[str, Any]:
    code = """
import json
from pathlib import Path
from tau_coding.command_specs import load_command_specs_with_diagnostics
from tau_coding.resources import TauResourcePaths
paths = TauResourcePaths(cwd=Path.cwd())
loaded = load_command_specs_with_diagnostics(paths)
print(json.dumps({
  "accepted": [
    {
      "name": spec.name,
      "description": spec.description,
      "path": str(spec.path),
      "precedence": spec.precedence,
    }
    for spec in loaded.accepted
  ],
  "diagnostics": [
    {
      "kind": item.kind,
      "name": item.name,
      "message": item.message,
      "path": str(item.path) if item.path else None,
    }
    for item in loaded.diagnostics
  ],
}, sort_keys=True))
"""
    result = run_command(
        [str(installed.python_bin), "-c", code],
        cwd=workspace,
        env=clean_env(home=installed.home, bin_dir=installed.bin_dir),
        commands=commands,
    )
    payload = json.loads(result.stdout)
    diagnostics_text = "\n".join(item["message"] for item in payload["diagnostics"])
    reason_codes = sorted(code for code in EXPECTED_REASON_CODES if code in diagnostics_text)
    accepted = {item["name"]: item for item in payload["accepted"]}
    return {
        "ok": (
            accepted.get("custom-read", {}).get("description") == "Project read resource command"
            and "insert-note" in accepted
        ),
        "accepted": payload["accepted"],
        "diagnostics": payload["diagnostics"],
        "reason_codes": reason_codes,
    }


def write_approval_receipt(
    *,
    installed: InstalledTau,
    workspace: Path,
    run_dir: Path,
    commands: list[dict[str, Any]],
) -> Path:
    run_dir.mkdir(parents=True, exist_ok=True)
    packet = {
        "schema": APPROVAL_PACKET_SCHEMA,
        "approved": True,
        "actor": {"id": "human:local-proof", "auth_method": "local-signature"},
        "action": "working_tree_mutation",
        "target": {
            "id": "command-spec:insert-note",
            "command": "insert-note",
            "resources_sha256": resources_digest(["notes/allowed.txt"]),
        },
        "reason": "Authorize command-spec proof insertion.",
        "evidence": ["tau issue 240 proof"],
        "nonce": "issue-240-command-spec-proof",
        "expires_at": (datetime.now(UTC) + timedelta(hours=1)).isoformat(),
    }
    packet["signature"] = LOCAL_SIGNATURE_PREFIX + canonical_digest(packet)
    packet_path = run_dir / "approval-packet.json"
    write_json(packet_path, packet)
    receipt_path = run_dir / "approval-receipt.json"
    code = f"""
from pathlib import Path
from tau_coding.approval_gate import evaluate_approval_gate
evaluate_approval_gate(
  approval_packet=Path({str(packet_path)!r}),
  requested_action="working_tree_mutation",
  run_dir=Path({str(run_dir)!r}),
  output=Path({str(receipt_path)!r}),
  expected_target={{
    "command": "insert-note",
    "resources_sha256": {resources_digest(["notes/allowed.txt"])!r},
  }},
)
"""
    run_command(
        [str(installed.python_bin), "-c", code],
        cwd=workspace,
        env=clean_env(home=installed.home, bin_dir=installed.bin_dir),
        commands=commands,
    )
    payload = read_json(receipt_path)
    if payload.get("status") != "PASS":
        raise RuntimeError(f"approval_receipt_not_pass:{payload}")
    return receipt_path


def drive_real_tui(
    *,
    installed: InstalledTau,
    cwd: Path,
    run_dir: Path,
    required: tuple[str, ...],
    input_chunks: tuple[bytes, ...] = (),
    submit_text: str | None = None,
) -> dict[str, Any]:
    run_dir.mkdir(parents=True, exist_ok=True)
    capture_path = run_dir / "terminal-capture.txt"
    env = clean_env(home=installed.home, bin_dir=installed.bin_dir)
    env.update(
        {
            "TERM": "xterm-256color",
            "PYTHONUNBUFFERED": "1",
            "TAU_TUI_PTY_PROOF": "1",
            "TAU_TUI_PTY_RUN_ID": run_dir.name,
        }
    )
    if submit_text is not None:
        env["TAU_TUI_PTY_SUBMIT_TEXT"] = submit_text
    argv = [str(installed.python_bin), "-m", "tau_coding.tui.app", "--pty-proof-real-app"]
    pid, fd = pty.fork()
    if pid == 0:
        os.chdir(cwd)
        os.execvpe(argv[0], argv, env)
    set_winsize(fd, rows=40, cols=140)
    raw = bytearray()
    stripped = ""
    sent = 0
    deadline = time.monotonic() + 18
    try:
        while time.monotonic() < deadline:
            ready, _, _ = select.select([fd], [], [], 0.1)
            if ready:
                try:
                    chunk = os.read(fd, 8192)
                except OSError:
                    break
                if not chunk:
                    break
                raw.extend(chunk)
                stripped = strip_ansi(raw.decode("utf-8", errors="ignore"))
            if sent == 0 and "TAU_TUI_PTY_READY" in stripped:
                time.sleep(0.4)
            if sent < len(input_chunks) and (
                sent > 0 or "TAU_TUI_PTY_READY" in stripped
            ):
                os.write(fd, input_chunks[sent])
                sent += 1
                time.sleep(0.8)
            if all(item in stripped for item in required):
                break
    finally:
        with suppress(ProcessLookupError):
            os.kill(pid, 15)
        with suppress(OSError):
            os.close(fd)
        with suppress(ChildProcessError):
            os.waitpid(pid, 0)
    capture_path.write_text(stripped, encoding="utf-8")
    missing = [item for item in required if item not in stripped]
    return {
        "ok": not missing and sent == len(input_chunks),
        "missing": missing,
        "sent_chunks": sent,
        "submit_text": submit_text,
        "required": list(required),
        "capture": str(capture_path),
    }


def inspect_receipts(receipt_dir: Path) -> dict[str, Any]:
    receipts = [read_json(path) for path in sorted(receipt_dir.glob("*.json"))]
    policy = [
        item for item in receipts if item.get("schema") == COMMAND_SPEC_SCHEMA
    ]
    route = [
        item for item in receipts if item.get("schema") == "tau.command_execution_receipt.v1"
    ]
    permission = [
        item for item in receipts if item.get("schema") == "tau.permission_request_receipt.v1"
    ]
    return {
        "ok": (
            _has_receipt(policy, command="custom-read", status="PASS")
            and _has_receipt(policy, command="insert-note", status="BLOCKED")
            and _has_receipt(policy, command="insert-note", status="PASS")
            and len(route) >= 2
            and bool(permission)
        ),
        "dir": str(receipt_dir),
        "policy_count": len(policy),
        "route_count": len(route),
        "permission_count": len(permission),
        "commands": [item.get("command") for item in policy],
    }


def _has_receipt(
    receipts: list[dict[str, Any]],
    *,
    command: str,
    status: str,
) -> bool:
    return any(
        item.get("command") == command and item.get("status") == status for item in receipts
    )


def run_command(
    argv: list[str],
    *,
    cwd: Path,
    commands: list[dict[str, Any]],
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    started = time.monotonic()
    result = subprocess.run(
        argv,
        cwd=cwd,
        env=env,
        text=True,
        capture_output=True,
        timeout=120,
        check=False,
    )
    record = {
        "argv": argv,
        "cwd": str(cwd),
        "exit_code": result.returncode,
        "duration_seconds": round(time.monotonic() - started, 3),
        "stdout_tail": result.stdout[-2000:],
        "stderr_tail": result.stderr[-2000:],
    }
    commands.append(record)
    if result.returncode != 0:
        raise RuntimeError(json.dumps(record, indent=2))
    return result


def clean_env(*, home: Path, bin_dir: Path) -> dict[str, str]:
    env = {
        "HOME": str(home),
        "PATH": f"{bin_dir}:{os.environ.get('PATH', '')}",
        "PYTHONNOUSERSITE": "1",
        "PYTHONUNBUFFERED": "1",
    }
    for key in ("LANG", "LC_ALL"):
        if key in os.environ:
            env[key] = os.environ[key]
    return env


def set_winsize(fd: int, *, rows: int, cols: int) -> None:
    ioctl(fd, termios.TIOCSWINSZ, struct.pack("HHHH", rows, cols, 0, 0))


def strip_ansi(value: str) -> str:
    import re

    return re.sub(r"\x1b\[[0-?]*[ -/]*[@-~]", "", value)


def resources_digest(resources: list[str]) -> str:
    payload = json.dumps(resources, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(payload).hexdigest()


def canonical_digest(packet: dict[str, Any]) -> str:
    canonical = dict(packet)
    canonical.pop("signature", None)
    encoded = json.dumps(canonical, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def sha256_uri(path: Path) -> str:
    return f"sha256:{hashlib.sha256(path.read_bytes()).hexdigest()}"


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


if __name__ == "__main__":
    raise SystemExit(main())
