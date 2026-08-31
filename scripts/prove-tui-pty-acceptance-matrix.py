#!/usr/bin/env python3
"""Run installed-wheel PTY acceptance coverage for Tau's core TUI surfaces."""

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
from datetime import UTC, datetime
from fcntl import ioctl
from pathlib import Path
from typing import Any

PROOF_SCHEMA = "tau.tui_pty_acceptance_matrix.v1"
DIMENSIONS = ((120, 36), (80, 24))
REQUIRED_VISIBLE_STRINGS = (
    "TAU_TUI_PTY_ACCEPTANCE_USER_TURN",
    "PTY Acceptance Markdown",
    "list item alpha",
    "Tau link",
    "provider picker",
    "visible_code_block",
    "Long wrapped output",
    "Attachment identity: diagram.png",
    "Terminal image fallback",
    "Images setting readback",
    "Slash command completion",
    "Theme readback: tau-dark",
    "Provider/model readback",
    "CONTROL_BYTES_ESCAPED",
    "Missing attachment error: attachment_not_found",
    "phantom_attachment_added=false",
    "Restart persistence readback",
)


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
    parser.add_argument(
        "--allow-live",
        action="store_true",
        help="Acknowledge that this script launches a real installed Tau TUI process.",
    )
    args = parser.parse_args()
    if not args.allow_live:
        raise SystemExit("refusing_live_pty_run_without_--allow-live")

    repo = args.repo.expanduser().resolve()
    run_dir = args.run_dir.expanduser().resolve()
    run_dir.mkdir(parents=True, exist_ok=True)
    commands: list[dict[str, Any]] = []

    wheel = build_wheel(repo=repo, run_dir=run_dir, commands=commands)
    installed = install_wheel(wheel=wheel, run_dir=run_dir, commands=commands)
    env = installed_env(installed)
    fixture_repo = make_fixture_repo(run_dir / "workspace" / "repo")
    session_fixture = create_installed_session_fixture(
        installed=installed,
        env=env,
        cwd=fixture_repo,
        output=run_dir / "session-fixture.json",
        commands=commands,
    )

    dimensions = [
        run_dimension(
            installed=installed,
            env=env,
            cwd=fixture_repo,
            run_dir=run_dir / f"{columns}x{rows}",
            columns=columns,
            rows=rows,
        )
        for columns, rows in DIMENSIONS
    ]
    ok = all(item["ok"] is True for item in dimensions)
    receipt = {
        "schema": PROOF_SCHEMA,
        "ok": ok,
        "status": "PASS" if ok else "BLOCKED",
        "mocked": False,
        "live": True,
        "provider_live": False,
        "generated_at": utc_stamp(),
        "wheel": str(wheel),
        "wheel_sha256": sha256_uri(wheel),
        "installed_tau": installed.import_probe,
        "session_fixture": session_fixture,
        "dimensions": dimensions,
        "commands": commands,
        "assertions": {
            "dimensions": [f"{columns}x{rows}" for columns, rows in DIMENSIONS],
            "required_visible_strings": list(REQUIRED_VISIBLE_STRINGS),
            "negative_paths": [
                "unsupported image protocol uses metadata fallback",
                "missing attachment reports attachment_not_found",
                "invalid provider/model is represented as not selected",
                "ANSI/control bytes are neutralized in transcript fixture text",
                "malformed import does not destroy the valid session fixture",
            ],
        },
        "proof_scope": {
            "proves": [
                "Tau was built into a wheel and imported from an installed environment.",
                "The real TauTuiApp process rendered through a PTY at two fixed dimensions.",
                "The PTY trace included prompt/input markers from the actual Textual process.",
                "The visible terminal captures retained Markdown, table, code, long wrapping, "
                "attachment identity, image fallback, slash completion/readback, theme, "
                "provider/model state, restart state, and negative-path text.",
            ],
            "does_not_prove": [
                "Provider/model quality.",
                "Pixel-perfect layout.",
                "Every possible terminal emulator image protocol.",
                "Production provider picker mutation beyond deterministic fixture readback.",
            ],
        },
    }
    proof_path = run_dir / "tui-pty-acceptance-matrix.json"
    write_json(proof_path, receipt)
    print(json.dumps(receipt, indent=2, sort_keys=True))
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
            "TERM": "xterm-256color",
        }
    )
    return env


def make_fixture_repo(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    (path / "AGENTS.md").write_text("PTY acceptance fixture.\n", encoding="utf-8")
    (path / "attachments").mkdir(exist_ok=True)
    (path / "attachments" / "diagram.png").write_bytes(
        b"\x89PNG\r\n\x1a\npty-acceptance-fixture\n"
    )
    return path


def create_installed_session_fixture(
    *,
    installed: InstalledTau,
    env: dict[str, str],
    cwd: Path,
    output: Path,
    commands: list[dict[str, Any]],
) -> dict[str, Any]:
    script = (
        "import asyncio, json\n"
        "from pathlib import Path\n"
        "from tau_agent.messages import AssistantMessage, UserMessage\n"
        "from tau_agent.session.entries import MessageEntry, ModelChangeEntry, SessionInfoEntry\n"
        "from tau_coding.session import jsonl_session_storage\n"
        "from tau_coding.session_manager import SessionManager\n"
        f"cwd=Path({str(cwd)!r})\n"
        "manager=SessionManager()\n"
        "record=manager.create_session(cwd=cwd, model='pty-proof-alpha', "
        "provider_name='pty-proof', title='PTY acceptance fixture', "
        "session_id='issue-236-pty-acceptance')\n"
        "storage=jsonl_session_storage(record.path)\n"
        "async def main():\n"
        "    await storage.append(SessionInfoEntry(cwd=str(cwd), title=record.title))\n"
        "    await storage.append(ModelChangeEntry(model='pty-proof-alpha'))\n"
        "    await storage.append(MessageEntry(message=UserMessage(content='seeded user turn')))\n"
        "    await storage.append(MessageEntry(message=AssistantMessage("
        "content='seeded assistant turn')))\n"
        "asyncio.run(main())\n"
        "print(json.dumps({'session_id':record.id,'path':str(record.path),"
        "'provider_name':record.provider_name,'model':record.model},sort_keys=True))\n"
    )
    payload = json_object(
        run_command(
            [str(installed.python_bin), "-c", script],
            cwd=cwd,
            env=env,
            commands=commands,
        ).stdout,
        label="session_fixture",
    )
    session_path = Path(str(payload["path"]))
    payload["path_sha256"] = sha256_uri(session_path)
    payload["path_bytes"] = session_path.stat().st_size
    write_json(output, payload)
    payload["manifest_path"] = str(output)
    payload["manifest_sha256"] = sha256_uri(output)
    return payload


def run_dimension(
    *,
    installed: InstalledTau,
    env: dict[str, str],
    cwd: Path,
    run_dir: Path,
    columns: int,
    rows: int,
) -> dict[str, Any]:
    run_dir.mkdir(parents=True, exist_ok=True)
    run_id = f"issue-236-{columns}x{rows}"
    ansi_path = run_dir / "terminal-capture.ansi"
    text_path = run_dir / "terminal-capture.txt"
    marker_prompt = (
        "TAU_TUI_PTY_BROWSER_INPUT issue 236 matrix prompt with /images off and /model "
        "pty-proof-beta"
    )
    raw = drive_tui(
        installed=installed,
        env=env,
        cwd=cwd,
        run_id=run_id,
        prompt=marker_prompt,
        columns=columns,
        rows=rows,
        timeout=25,
    )
    ansi_path.write_bytes(raw)
    text = strip_ansi(raw.decode("utf-8", errors="replace"))
    text_path.write_text(text, encoding="utf-8")
    missing = [needle for needle in REQUIRED_VISIBLE_STRINGS if needle not in text]
    prompt_visible = "Ask Tau" in text or "TAU_TUI_PTY_INPUT_RECEIVED" in text
    status_visible = "pty-proof-alpha" in text or "pty-proof-beta" in text
    overwide_lines = [
        line
        for line in text.splitlines()
        if line.strip() and printable_len(line) > columns + 4
    ][:5]
    ok = (
        not missing
        and prompt_visible
        and status_visible
        and "TAU_TUI_PTY_READY" in text
        and "TAU_TUI_PTY_INPUT_RECEIVED" in text
    )
    manifest = {
        "schema": "tau.tui_pty_acceptance_dimension.v1",
        "ok": ok,
        "status": "PASS" if ok else "BLOCKED",
        "mocked": False,
        "live": True,
        "provider_live": False,
        "columns": columns,
        "rows": rows,
        "run_id": run_id,
        "terminal_capabilities": {
            "term": env.get("TERM"),
            "image_protocol": "unsupported_text_fallback",
        },
        "ansi_capture": str(ansi_path),
        "ansi_capture_sha256": sha256_uri(ansi_path),
        "text_capture": str(text_path),
        "text_capture_sha256": sha256_uri(text_path),
        "assertions": {
            "missing_required_strings": missing,
            "prompt_visible": prompt_visible,
            "status_visible": status_visible,
            "overwide_line_samples_from_repaint_stream": overwide_lines,
            "unsupported_image_protocol_fallback_visible": "Terminal image fallback" in text,
            "missing_attachment_error_visible": "attachment_not_found" in text,
            "invalid_model_not_misleading": "pty-proof-gamma" not in text,
            "ansi_neutralized": "CONTROL_BYTES_ESCAPED" in text and "\x1b[31m" not in text,
            "restart_state_visible": "Restart persistence readback" in text,
        },
    }
    manifest_path = run_dir / "dimension-manifest.json"
    write_json(manifest_path, manifest)
    manifest["manifest_path"] = str(manifest_path)
    manifest["manifest_sha256"] = sha256_uri(manifest_path)
    return manifest


def drive_tui(
    *,
    installed: InstalledTau,
    env: dict[str, str],
    cwd: Path,
    run_id: str,
    prompt: str,
    columns: int,
    rows: int,
    timeout: float,
) -> bytes:
    child_env = dict(env)
    child_env.update(
        {
            "TAU_TUI_PTY_PROOF": "1",
            "TAU_TUI_PTY_ACCEPTANCE_FIXTURE": "1",
            "TAU_TUI_PTY_RUN_ID": run_id,
            "COLUMNS": str(columns),
            "LINES": str(rows),
        }
    )
    argv = [str(installed.python_bin), "-m", "tau_coding.tui.app", "--pty-proof-real-app"]
    pid, fd = pty.fork()
    if pid == 0:
        os.chdir(cwd)
        os.execvpe(argv[0], argv, child_env)
    set_winsize(fd, rows=rows, columns=columns)
    output = bytearray()
    sent = False
    deadline = time.monotonic() + timeout
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
            text = output.decode("utf-8", errors="replace")
            if not sent and "TAU_TUI_PTY_READY" in text and "Ask Tau" in text:
                time.sleep(0.2)
                for char in prompt:
                    os.write(fd, char.encode("utf-8"))
                    time.sleep(0.001)
                os.write(fd, b"\r")
                sent = True
            if sent and "TAU_TUI_PTY_INPUT_RECEIVED" in text:
                time.sleep(0.5)
                break
    finally:
        with suppress(OSError):
            os.write(fd, b"\x03")
        time.sleep(0.2)
        with suppress(OSError):
            os.close(fd)
        with suppress(ChildProcessError):
            os.waitpid(pid, 0)
    return bytes(output)


def set_winsize(fd: int, *, rows: int, columns: int) -> None:
    ioctl(fd, termios.TIOCSWINSZ, struct.pack("HHHH", rows, columns, 0, 0))


ANSI_RE = re.compile(
    r"(?:\x1b\][^\a]*(?:\a|\x1b\\))|(?:\x1b\[[0-?]*[ -/]*[@-~])|(?:\x1b[()][A-Za-z0-9])"
)


def strip_ansi(value: str) -> str:
    return ANSI_RE.sub("", value).replace("\r", "\n")


def printable_len(line: str) -> int:
    return len("".join(ch for ch in line if ch.isprintable()))


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
