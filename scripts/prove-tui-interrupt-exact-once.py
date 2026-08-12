#!/usr/bin/env python3
"""Run installed-wheel TUI interruption and queue exact-once acceptance proof."""

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
import urllib.error
import urllib.request
import venv
from contextlib import suppress
from dataclasses import dataclass
from datetime import UTC, datetime
from fcntl import ioctl
from pathlib import Path
from typing import Any

PROOF_SCHEMA = "tau.tui_interrupt_exact_once_proof.v1"
CONTROL_NAMESPACE = "tau.session_control"
CONTROL_SCHEMA = "tau.session_control.v1"


@dataclass(frozen=True)
class InstalledTau:
    root: Path
    home: Path
    bin_dir: Path
    site_packages: Path
    dependency_site: Path
    python_bin: Path
    import_probe: dict[str, Any]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, default=Path.cwd())
    parser.add_argument("--run-dir", type=Path, required=True)
    parser.add_argument("--allow-live", action="store_true")
    parser.add_argument("--require-scillm-live", action="store_true")
    args = parser.parse_args()
    if not args.allow_live:
        raise SystemExit("refusing_live_tui_interrupt_proof_without_--allow-live")

    repo = args.repo.expanduser().resolve()
    run_dir = args.run_dir.expanduser().resolve()
    run_dir.mkdir(parents=True, exist_ok=True)
    commands: list[dict[str, Any]] = []

    wheel = build_wheel(repo=repo, run_dir=run_dir, commands=commands)
    installed = install_wheel(wheel=wheel, run_dir=run_dir, commands=commands)
    fixture_repo = make_fixture_repo(run_dir / "workspace" / "repo")
    write_runner(run_dir / "run_tui.py")
    env = installed_env(installed, run_dir=run_dir)

    first = drive_tui_phase(
        installed=installed,
        env=env,
        cwd=fixture_repo,
        run_dir=run_dir / "phase-1-cancel",
        phase="cancel",
        timeout=35,
    )
    second = drive_tui_phase(
        installed=installed,
        env=env,
        cwd=fixture_repo,
        run_dir=run_dir / "phase-2-resume",
        phase="resume",
        timeout=35,
        session_id=phase_session_id(first),
    )

    session_path = latest_session_path(installed.home)
    entries = [json.loads(line) for line in session_path.read_text(encoding="utf-8").splitlines()]
    control_events = [
        entry
        for entry in entries
        if entry.get("type") == "custom"
        and entry.get("namespace") == CONTROL_NAMESPACE
        and isinstance(entry.get("data"), dict)
        and entry["data"].get("schema") == CONTROL_SCHEMA
    ]
    assertions = analyze_entries(
        entries,
        control_events,
        retained_content="retained follow-up exact once",
        removed_content="drop this queued item",
    )
    provider_log = read_jsonl(run_dir / "provider-events.jsonl")
    provider_assertions = {
        "cancel_observed": any(
            item.get("event") == "cancel_observed" and item.get("cancelled") is True
            for item in provider_log
        ),
        "provider_call_count": sum(1 for item in provider_log if item.get("event") == "call_start"),
    }
    controlled_ok = (
        first["ok"]
        and second["ok"]
        and all(assertions.values())
        and provider_assertions["cancel_observed"]
        and provider_assertions["provider_call_count"] >= 2
    )
    scillm_live = (
        run_scillm_live_tui_proof(wheel=wheel, run_dir=run_dir, commands=commands)
        if args.require_scillm_live
        else None
    )
    ok = controlled_ok and (scillm_live is None or scillm_live.get("ok") is True)
    receipt = {
        "schema": PROOF_SCHEMA,
        "status": "PASS" if ok else "BLOCKED",
        "ok": ok,
        "mocked": False,
        "live": True,
        "provider_live": bool(scillm_live and scillm_live.get("ok") is True),
        "controlled_provider": True,
        "generated_at": datetime.now(tz=UTC).replace(microsecond=0).isoformat(),
        "wheel": str(wheel),
        "wheel_sha256": sha256_uri(wheel),
        "installed_tau": installed.import_probe,
        "session_path": str(session_path),
        "session_sha256": sha256_uri(session_path),
        "control_event_count": len(control_events),
        "assertions": assertions,
        "provider_assertions": provider_assertions,
        "scillm_live": scillm_live,
        "phases": [first, second],
        "provider_log": str(run_dir / "provider-events.jsonl"),
        "commands": commands,
        "proof_scope": {
            "proves": [
                "Installed-wheel Tau launched the real Textual TUI through a PTY.",
                "Cancellation reached a durable CANCELLED terminal attempt before queue release.",
                "Late provider output after cancellation was recorded as ignored "
                "and not persisted.",
                "A removed queued follow-up stayed removed while a retained follow-up "
                "released once.",
                "Resume allocated a new turn and attempt identity before the retained "
                "item drained.",
            ],
            "does_not_prove": [
                "External provider transport stops computation immediately.",
                "Provider-live SciLLM quality; this controlled provider proof is test-only.",
                "Every possible queue editing gesture.",
            ],
        },
    }
    proof_path = run_dir / "tui-interrupt-exact-once-proof.json"
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
    write_json(
        home / ".tau" / "tui.json",
        {"keybindings": {"queue_follow_up": "f1", "dequeue_messages": "f2"}},
    )
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
        str(dependency_site) + "\n", encoding="utf-8"
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
        site_packages=site_packages,
        dependency_site=dependency_site,
        python_bin=python_bin,
        import_probe={
            **import_probe,
            "site_packages": str(site_packages),
            "dependency_site": str(dependency_site),
        },
    )


def installed_env(installed: InstalledTau, *, run_dir: Path) -> dict[str, str]:
    env = clean_env(home=installed.home, bin_dir=installed.bin_dir)
    env.update(
        {
            "TERM": "xterm-256color",
            "TAU_TUI_SESSION_CONTROL_PROOF": "1",
            "TAU_TUI_SESSION_CONTROL_PROVIDER_LOG": str(run_dir / "provider-events.jsonl"),
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


def make_fixture_repo(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    (path / "AGENTS.md").write_text(
        "TUI interruption exact-once proof fixture.\n",
        encoding="utf-8",
    )
    return path


def write_runner(path: Path) -> None:
    path.write_text(
        """
import asyncio, json, os
from pathlib import Path
from tau_coding.tui.app import run_tui_app

async def main():
    phase = os.environ.get("TAU_PROOF_PHASE", "")
    exact_session_id = os.environ.get("TAU_PROOF_SESSION_ID") or None
    initial_prompt = {
        "cancel": "active turn that will be cancelled",
        "resume": None,
    }.get(phase)
    session_id = await run_tui_app(
        model="session-control-proof",
        cwd=Path.cwd(),
        initial_prompt=initial_prompt,
        provider_name="session-control-proof",
        exact_session_id=exact_session_id,
        new_session=exact_session_id is None and os.environ.get("TAU_PROOF_CONTINUE") != "1",
        continue_session=exact_session_id is None and os.environ.get("TAU_PROOF_CONTINUE") == "1",
        no_skills=True,
        no_prompt_templates=True,
        no_themes=True,
        no_extensions=True,
        no_context_files=True,
        no_tools=True,
    )
    Path(os.environ["TAU_PROOF_RESULT"]).write_text(json.dumps({"session_id": session_id}) + "\\n")

asyncio.run(main())
""".lstrip(),
        encoding="utf-8",
    )


def write_scillm_runner(path: Path) -> None:
    path.write_text(
        """
import asyncio, json, os
from pathlib import Path
from tau_coding.provider_config import OpenAICompatibleProviderConfig, ProviderSettings
from tau_coding.tui.app import run_tui_app

async def main():
    phase = os.environ.get("TAU_PROOF_PHASE", "")
    exact_session_id = os.environ.get("TAU_PROOF_SESSION_ID") or None
    initial_prompt = {
        "scillm-cancel": (
            "Produce a long numbered list for an interruption proof. "
            "Do not call tools. Keep writing until interrupted."
        ),
        "scillm-resume": None,
    }.get(phase)
    provider = OpenAICompatibleProviderConfig(
        name="scillm-live",
        base_url="http://127.0.0.1:4001/v1",
        api_key_env="SCILLM_PROXY_KEY",
        models=("local-text",),
        default_model="local-text",
        headers={"X-Caller-Skill": "tau-issue-237"},
        timeout_seconds=120.0,
    )
    settings = ProviderSettings(default_provider=provider.name, providers=(provider,))
    session_id = await run_tui_app(
        model="local-text",
        cwd=Path.cwd(),
        initial_prompt=initial_prompt,
        provider_name="scillm-live",
        provider_settings=settings,
        exact_session_id=exact_session_id,
        new_session=exact_session_id is None and os.environ.get("TAU_PROOF_CONTINUE") != "1",
        continue_session=exact_session_id is None and os.environ.get("TAU_PROOF_CONTINUE") == "1",
        no_skills=True,
        no_prompt_templates=True,
        no_themes=True,
        no_extensions=True,
        no_context_files=True,
        no_tools=True,
    )
    Path(os.environ["TAU_PROOF_RESULT"]).write_text(json.dumps({"session_id": session_id}) + "\\n")

asyncio.run(main())
""".lstrip(),
        encoding="utf-8",
    )


def run_scillm_live_tui_proof(
    *,
    wheel: Path,
    run_dir: Path,
    commands: list[dict[str, Any]],
) -> dict[str, Any]:
    scillm_dir = run_dir / "scillm-live"
    scillm_dir.mkdir(parents=True, exist_ok=True)
    installed = install_wheel(wheel=wheel, run_dir=scillm_dir, commands=commands)
    fixture_repo = make_fixture_repo(scillm_dir / "workspace" / "repo")
    write_scillm_runner(scillm_dir / "run_tui.py")
    env = installed_env(installed, run_dir=scillm_dir)
    env.pop("TAU_TUI_SESSION_CONTROL_PROOF", None)
    env.pop("TAU_TUI_SESSION_CONTROL_PROVIDER_LOG", None)
    key = resolve_scillm_proxy_key()
    env["SCILLM_PROXY_KEY"] = key
    health = scillm_json_request(
        "GET",
        "http://127.0.0.1:4001/v1/scillm/health",
        key=key,
    )
    chat_probe = scillm_json_request(
        "POST",
        "http://127.0.0.1:4001/v1/chat/completions",
        key=key,
        payload={
            "model": "local-text",
            "messages": [
                {
                    "role": "user",
                    "content": "Reply with exactly: TAU_SCILLM_LIVE_OK",
                }
            ],
            "stream": False,
        },
    )
    first = drive_tui_phase(
        installed=installed,
        env=env,
        cwd=fixture_repo,
        run_dir=scillm_dir / "phase-1-cancel",
        phase="scillm-cancel",
        timeout=60,
    )
    second = drive_tui_phase(
        installed=installed,
        env=env,
        cwd=fixture_repo,
        run_dir=scillm_dir / "phase-2-resume",
        phase="scillm-resume",
        timeout=60,
        session_id=phase_session_id(first),
    )
    session_path = latest_session_path(installed.home)
    entries = [json.loads(line) for line in session_path.read_text(encoding="utf-8").splitlines()]
    control_events = [
        entry
        for entry in entries
        if entry.get("type") == "custom"
        and entry.get("namespace") == CONTROL_NAMESPACE
        and isinstance(entry.get("data"), dict)
        and entry["data"].get("schema") == CONTROL_SCHEMA
    ]
    assertions = analyze_entries(
        entries,
        control_events,
        retained_content="TAU_LIVE_RETAINED_FOLLOWUP",
        removed_content=None,
    )
    chat_text = (
        chat_probe.get("choices", [{}])[0]
        .get("message", {})
        .get("content", "")
    )
    ok = (
        health.get("status") == "ok"
        and "TAU_SCILLM_LIVE_OK" in str(chat_text)
        and first["ok"]
        and second["ok"]
        and assertions["cancelled_terminal_once"]
        and assertions["cancel_precedes_release"]
        and assertions["retained_follow_up_released_once"]
        and assertions["distinct_resume_turn_attempt_ids"]
        and assertions["late_output_not_accepted"]
    )
    return {
        "schema": "tau.tui_scillm_live_interrupt_proof.v1",
        "ok": ok,
        "status": "PASS" if ok else "BLOCKED",
        "mocked": False,
        "live": True,
        "provider_live": True,
        "provider_url": "http://127.0.0.1:4001/v1",
        "provider_model": "local-text",
        "health_status": health.get("status"),
        "chat_probe_ok": "TAU_SCILLM_LIVE_OK" in str(chat_text),
        "session_path": str(session_path),
        "session_sha256": sha256_uri(session_path),
        "control_event_count": len(control_events),
        "assertions": assertions,
        "phases": [first, second],
        "proof_scope": {
            "proves": [
                "Installed-wheel Tau launched the real Textual TUI through a PTY.",
                "The TUI used Tau's OpenAI-compatible provider route pointed at local SciLLM.",
                "A live SciLLM chat probe succeeded through the same authenticated endpoint.",
                "A queued follow-up survived interruption and released once after resume.",
            ],
            "does_not_prove": [
                "Provider/model semantic quality.",
                "External provider transport stops computation immediately.",
                "Every possible queue editing gesture.",
            ],
        },
    }


def drive_tui_phase(
    *,
    installed: InstalledTau,
    env: dict[str, str],
    cwd: Path,
    run_dir: Path,
    phase: str,
    timeout: float,
    session_id: str | None = None,
) -> dict[str, Any]:
    run_dir.mkdir(parents=True, exist_ok=True)
    result_path = run_dir / "runner-result.json"
    child_env = dict(env)
    child_env.update({"TAU_PROOF_RESULT": str(result_path), "TAU_PROOF_PHASE": phase})
    if session_id:
        child_env["TAU_PROOF_SESSION_ID"] = session_id
    elif phase in {"resume", "scillm-resume"}:
        child_env["TAU_PROOF_CONTINUE"] = "1"
    argv = [str(installed.python_bin), str(run_dir.parent / "run_tui.py")]
    output = drive_pty(argv=argv, env=child_env, cwd=cwd, phase=phase, timeout=timeout)
    ansi_path = run_dir / "terminal-capture.ansi"
    text_path = run_dir / "terminal-capture.txt"
    ansi_path.write_bytes(output)
    text = strip_ansi(output.decode("utf-8", errors="replace"))
    text_path.write_text(text, encoding="utf-8")
    if phase == "cancel":
        required = [
            "first cancellable chunk",
            "Cancellation requested",
            "Agent run cancelled",
            "follow-up",
        ]
    elif phase == "resume":
        required = [
            "resumed provider answer for released follow-up",
            "retained follow-up exact once",
        ]
    elif phase == "scillm-cancel":
        required = ["Cancellation requested", "Agent run cancelled", "follow-up"]
    elif phase == "scillm-resume":
        required = ["TAU_LIVE_RETAINED_FOLLOWUP"]
    else:
        raise RuntimeError(f"unknown_phase:{phase}")
    missing = [needle for needle in required if needle not in text]
    return {
        "phase": phase,
        "ok": not missing,
        "missing": missing,
        "ansi_capture": str(ansi_path),
        "ansi_capture_sha256": sha256_uri(ansi_path),
        "text_capture": str(text_path),
        "text_capture_sha256": sha256_uri(text_path),
        "result_path": str(result_path),
        "result_exists": result_path.exists(),
    }


def drive_pty(
    *,
    argv: list[str],
    env: dict[str, str],
    cwd: Path,
    phase: str,
    timeout: float,
) -> bytes:
    pid, fd = pty.fork()
    if pid == 0:
        os.chdir(cwd)
        os.execvpe(argv[0], argv, env)
    set_winsize(fd, rows=36, columns=120)
    output = bytearray()
    step = 0
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
            text = strip_ansi(output.decode("utf-8", errors="replace"))
            if phase == "cancel":
                if step == 0 and "first cancellable chunk" in text:
                    os.write(fd, b"\x15")
                    write_text(fd, "drop this queued item")
                    step = 1
                elif step == 1 and "drop this queued item" in text:
                    press_key(fd, "f1")
                    step = 2
                elif step == 2 and "follow-up · queued: drop this queued item" in text:
                    press_key(fd, "f2")
                    time.sleep(0.5)
                    os.write(fd, b"\x15")
                    time.sleep(0.2)
                    write_text(fd, "retained follow-up exact once")
                    step = 3
                elif step == 3 and "retained follow-up exact once" in text:
                    time.sleep(0.5)
                    press_key(fd, "f1")
                    step = 4
                elif step == 4 and "follow-up · queued: retained follow-up exact once" in text:
                    time.sleep(0.2)
                    os.write(fd, b"\x1b")
                    step = 5
                elif step == 5 and "Agent run cancelled" in text:
                    time.sleep(0.3)
                    os.write(fd, b"\x04")
                    step = 6
            else:
                if (
                    phase == "resume"
                    and step == 0
                    and "resumed provider answer for released follow-up" in text
                ):
                    time.sleep(0.3)
                    os.write(fd, b"\x04")
                    step = 1
                elif phase == "scillm-cancel":
                    if step == 0 and "esc Cancel" in text:
                        write_text_fast(fd, "TAU_LIVE_RETAINED_FOLLOWUP")
                        press_key(fd, "f1")
                        time.sleep(0.1)
                        os.write(fd, b"\x1b")
                        step = 1
                    elif step == 1 and "Agent run cancelled" in text:
                        time.sleep(0.3)
                        os.write(fd, b"\x04")
                        step = 2
                elif phase == "scillm-resume":
                    if step == 0 and "TAU_LIVE_RETAINED_FOLLOWUP" in text:
                        time.sleep(0.3)
                        os.write(fd, b"\x04")
                        step = 1
    finally:
        with suppress(OSError):
            os.write(fd, b"\x04")
        time.sleep(0.2)
        with suppress(OSError):
            os.close(fd)
        with suppress(ChildProcessError):
            os.waitpid(pid, 0)
    return bytes(output)


def write_text(fd: int, text: str) -> None:
    for char in text:
        os.write(fd, char.encode("utf-8"))
        time.sleep(0.02)


def write_text_fast(fd: int, text: str) -> None:
    os.write(fd, text.encode("utf-8"))
    time.sleep(0.05)


def press_key(fd: int, key: str) -> None:
    sequences = {
        "f1": b"\x1bOP",
        "f2": b"\x1bOQ",
    }
    os.write(fd, sequences[key])
    time.sleep(0.1)


def analyze_entries(
    entries: list[dict[str, Any]],
    control_events: list[dict[str, Any]],
    *,
    retained_content: str,
    removed_content: str | None,
) -> dict[str, bool]:
    data = [entry["data"] for entry in control_events]
    terminal_indexes = [
        index
        for index, item in enumerate(data)
        if item.get("event") == "attempt_terminal" and item.get("state") == "CANCELLED"
    ]
    release_indexes = [
        index
        for index, item in enumerate(data)
        if item.get("event") == "queue_state" and item.get("state") == "RELEASED"
    ]
    turn_ids = [
        item.get("turn_id")
        for item in data
        if item.get("event") == "turn_started" and isinstance(item.get("turn_id"), str)
    ]
    attempt_ids = [
        item.get("attempt_id")
        for item in data
        if item.get("event") == "turn_started" and isinstance(item.get("attempt_id"), str)
    ]
    queue_ids = [
        item.get("queue_id")
        for item in data
        if item.get("event") == "queue_state" and item.get("state") == "RELEASED"
    ]
    transcript_text = "\n".join(
        str((entry.get("message") or {}).get("content") or "")
        for entry in entries
        if entry.get("type") == "message"
    )
    return {
        "cancelled_terminal_once": len(terminal_indexes) == 1,
        "cancel_precedes_release": bool(terminal_indexes and release_indexes)
        and terminal_indexes[0] < release_indexes[0],
        "late_output_ignored_recorded": any(
            item.get("event") == "late_output_ignored" for item in data
        ),
        "late_output_not_accepted": "LATE_ASSISTANT_SHOULD_NOT_PERSIST" not in transcript_text
        and "LATE_PROVIDER_CHUNK_SHOULD_NOT_RENDER" not in transcript_text,
        "removed_item_not_released": all(
            not (
                item.get("state") == "RELEASED"
                and removed_content is not None
                and item.get("content") == removed_content
            )
            for item in data
            if item.get("event") == "queue_state"
        ),
        "retained_follow_up_released_once": len(queue_ids) == 1
        and any(
            item.get("state") == "RELEASED"
            and item.get("content") == retained_content
            and item.get("release_count") == 1
            for item in data
        ),
        "distinct_resume_turn_attempt_ids": len(set(turn_ids)) >= 2 and len(set(attempt_ids)) >= 2,
    }


def latest_session_path(home: Path) -> Path:
    sessions = sorted(
        (
            path
            for path in (home / ".tau" / "sessions").glob("**/*.jsonl")
            if path.name != "index.jsonl"
        ),
        key=lambda path: path.stat().st_mtime,
    )
    if not sessions:
        raise RuntimeError("no_session_jsonl_found")
    return sessions[-1]


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def phase_session_id(phase_result: dict[str, Any]) -> str:
    result_path = Path(str(phase_result["result_path"]))
    if not result_path.exists():
        raise RuntimeError(f"phase_result_missing:{result_path}")
    result = json.loads(result_path.read_text(encoding="utf-8"))
    session_id = result.get("session_id")
    if not isinstance(session_id, str) or not session_id:
        raise RuntimeError(f"phase_session_id_missing:{result_path}")
    return session_id


def resolve_scillm_proxy_key() -> str:
    candidates = [
        os.environ.get("SCILLM_MASTER_KEY"),
        os.environ.get("LITELLM_MASTER_KEY"),
        os.environ.get("SCILLM_PROXY_KEY"),
    ]
    for candidate in candidates:
        if candidate and scillm_key_is_accepted(candidate):
            return candidate
    try:
        completed = subprocess.run(
            [
                "docker",
                "inspect",
                "docker-scillm-proxy-1",
                "--format",
                "{{range .Config.Env}}{{println .}}{{end}}",
            ],
            text=True,
            capture_output=True,
            timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise RuntimeError(f"scillm_key_unavailable:{exc}") from exc
    if completed.returncode == 0:
        for line in completed.stdout.splitlines():
            name, sep, value = line.partition("=")
            if (
                sep
                and name in {"SCILLM_MASTER_KEY", "LITELLM_MASTER_KEY", "SCILLM_PROXY_KEY"}
                and value
                and scillm_key_is_accepted(value)
            ):
                return value
    raise RuntimeError("scillm_key_unavailable_or_rejected")


def scillm_key_is_accepted(key: str) -> bool:
    try:
        response = scillm_json_request(
            "GET",
            "http://127.0.0.1:4001/v1/scillm/health",
            key=key,
            timeout=5,
        )
    except RuntimeError:
        return False
    return response.get("status") == "ok"


def scillm_json_request(
    method: str,
    url: str,
    *,
    key: str,
    payload: dict[str, Any] | None = None,
    timeout: float = 60,
) -> dict[str, Any]:
    body = None if payload is None else json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=body,
        method=method,
        headers={
            "Authorization": f"Bearer {key}",
            "X-Caller-Skill": "tau-issue-237",
            "Content-Type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            data = response.read().decode("utf-8")
    except (OSError, urllib.error.HTTPError) as exc:
        raise RuntimeError(f"scillm_request_failed:{url}:{exc}") from exc
    parsed = json.loads(data)
    if not isinstance(parsed, dict):
        raise RuntimeError(f"scillm_response_not_object:{url}")
    return parsed


def run_command(
    argv: list[str],
    *,
    cwd: Path,
    commands: list[dict[str, Any]],
    env: dict[str, str] | None = None,
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
    if completed.returncode != 0:
        raise RuntimeError(completed.stderr or completed.stdout)
    return completed


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
