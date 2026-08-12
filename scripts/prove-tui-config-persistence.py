#!/usr/bin/env python3
"""Run installed-wheel proof for allowlisted TUI config persistence."""

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
from dataclasses import dataclass
from datetime import UTC, datetime
from fcntl import ioctl
from pathlib import Path
from typing import Any

PROOF_SCHEMA = "tau.tui_config_persistence_proof.v1"
SAFE_TUI_CASES: tuple[tuple[str, object], ...] = (
    ("theme", "tau-light"),
    ("show_images", False),
    ("auto_resize_images", False),
    ("image_width_cells", 80),
    ("sidebar_position", "left"),
    ("show_hardware_cursor", False),
    ("show_terminal_progress", True),
    ("auto_copy_selection", True),
    ("quiet_startup", True),
    ("collapse_changelog", True),
    ("turn_notification", "bell"),
)
FORBIDDEN_CASES: tuple[tuple[str, object], ...] = (
    ("credential_name", "secret"),
    ("api_key_env", "OPENAI_API_KEY"),
    ("base_url", "http://127.0.0.1:9/v1"),
    ("permission_rules", "allow"),
    ("default_project_trust", "always"),
    ("immutable_goal", "changed"),
    ("filesystem_allowlist", ["/"]),
)


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
    args = parser.parse_args()
    if not args.allow_live:
        raise SystemExit("refusing_live_tui_config_proof_without_--allow-live")

    repo = args.repo.expanduser().resolve()
    run_dir = args.run_dir.expanduser().resolve()
    run_dir.mkdir(parents=True, exist_ok=True)
    commands: list[dict[str, Any]] = []

    wheel = build_wheel(repo=repo, run_dir=run_dir, commands=commands)
    installed = install_wheel(wheel=wheel, run_dir=run_dir, commands=commands)
    workspace = make_fixture_repo(run_dir / "workspace" / "repo")

    tui_path = installed.home / ".tau" / "tui.json"
    write_json(
        tui_path,
        {
            "theme": "tau-dark",
            "show_images": True,
            "future_tau_key": {"preserve": True},
        },
    )

    tui_phase = drive_tui_config_phase(
        installed=installed,
        cwd=workspace,
        run_dir=run_dir / "phase-tui-show-images",
        query="show images",
        key=b"\r",
        required=("Config", "Show images", "Config saved: show_images"),
    )
    restart_phase = drive_tui_config_phase(
        installed=installed,
        cwd=workspace,
        run_dir=run_dir / "phase-restart-readback",
        query="show images",
        key=b"\x1b",
        required=("Config", "Show images: off"),
    )
    after_tui = read_json(tui_path)

    safe_mutations = [
        mutate_tui_setting(installed, key=key, value=value, commands=commands)
        for key, value in SAFE_TUI_CASES
    ]
    after_safe = read_json(tui_path)
    unknown_preserved = after_safe.get("future_tau_key") == {"preserve": True}

    provider_result = provider_selection_probe(installed, commands=commands)
    atomic_failure = atomic_failure_probe(installed, commands=commands)
    concurrent_result = concurrent_write_probe(installed, run_dir=run_dir, commands=commands)
    corrupt_reload = corrupt_reload_probe(installed, commands=commands)
    forbidden = [
        mutate_tui_setting(installed, key=key, value=value, commands=commands)
        for key, value in FORBIDDEN_CASES
    ]

    ok = (
        tui_phase["ok"]
        and restart_phase["ok"]
        and after_tui.get("show_images") is False
        and all(item.get("accepted") is True for item in safe_mutations)
        and unknown_preserved
        and provider_result["ok"]
        and atomic_failure["ok"]
        and concurrent_result["ok"]
        and corrupt_reload["ok"]
        and all(
            item.get("accepted") is False
            and "tui_setting_not_allowlisted" in item.get("errors", ())
            for item in forbidden
        )
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
        "tui_settings_path": str(tui_path),
        "tui_phase": tui_phase,
        "restart_phase": restart_phase,
        "safe_mutations": safe_mutations,
        "unknown_key_preserved": unknown_preserved,
        "provider_selection": provider_result,
        "atomic_failure": atomic_failure,
        "concurrent_writes": concurrent_result,
        "corrupt_reload": corrupt_reload,
        "forbidden": forbidden,
        "commands": commands,
        "proof_scope": {
            "proves": [
                "Installed-wheel Tau opened the actual safe /config picker through a PTY.",
                (
                    "A TUI-selected allowlisted setting was persisted, read back, "
                    "and visible after restart."
                ),
                "Every declared safe TUI setting mutates through the installed public config API.",
                "Unknown TUI keys are preserved by the allowlisted mutation path.",
                "Atomic failure before replace leaves the previous config byte-identical.",
                "Concurrent writes produce a complete loadable config, not a torn file.",
                (
                    "Forbidden setting categories are rejected before write with "
                    "tui_setting_not_allowlisted."
                ),
                "Provider/model default selection is limited to existing configured entries.",
            ],
            "does_not_prove": [
                "Provider/model execution.",
                "A generic JSON settings editor.",
                "Mutation of credentials, endpoints, policy, approvals, goals, or allowlists.",
            ],
        },
    }
    proof_path = run_dir / "tui-config-persistence-proof.json"
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
        site_packages=site_packages,
        dependency_site=dependency_site,
        python_bin=python_bin,
        import_probe={
            **import_probe,
            "site_packages": str(site_packages),
            "dependency_site": str(dependency_site),
        },
    )


def make_fixture_repo(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    (path / "AGENTS.md").write_text("TUI config proof fixture.\n", encoding="utf-8")
    return path


def drive_tui_config_phase(
    *,
    installed: InstalledTau,
    cwd: Path,
    run_dir: Path,
    query: str,
    key: bytes,
    required: tuple[str, ...],
) -> dict[str, Any]:
    run_dir.mkdir(parents=True, exist_ok=True)
    master_fd, slave_fd = pty.openpty()
    ioctl(slave_fd, termios.TIOCSWINSZ, struct.pack("HHHH", 32, 120, 0, 0))
    env = clean_env(home=installed.home, bin_dir=installed.bin_dir)
    env.update(
        {
            "TERM": "xterm-256color",
            "TAU_TUI_PTY_PROOF": "1",
            "TAU_TUI_PTY_RUN_ID": run_dir.name,
            "TAU_TUI_CONFIG_PROOF_OPEN": "1",
            "TAU_TUI_CONFIG_PROOF_QUERY": query,
        }
    )
    proc = subprocess.Popen(
        [str(installed.python_bin), "-m", "tau_coding.tui.app", "--pty-proof-real-app"],
        cwd=cwd,
        env=env,
        stdin=slave_fd,
        stdout=slave_fd,
        stderr=slave_fd,
        close_fds=True,
    )
    os.close(slave_fd)
    capture = bytearray()
    try:
        wait_for(master_fd, capture, "TAU_TUI_PTY_READY", timeout=10)
        wait_for(master_fd, capture, "Config", timeout=10)
        os.write(master_fd, key)
        time.sleep(1.0)
        drain(master_fd, capture, timeout=1.0)
    finally:
        with contextlib_suppress(ProcessLookupError):
            proc.terminate()
        try:
            proc.wait(timeout=2)
        except subprocess.TimeoutExpired:
            proc.kill()
        os.close(master_fd)
    ansi_path = run_dir / "terminal-capture.ansi"
    text_path = run_dir / "terminal-capture.txt"
    ansi_path.write_bytes(bytes(capture))
    text = strip_ansi(bytes(capture).decode("utf-8", errors="replace"))
    text_path.write_text(text, encoding="utf-8")
    missing = [needle for needle in required if needle not in text]
    return {
        "ok": not missing,
        "missing": missing,
        "query": query,
        "key": key.decode("utf-8", errors="replace"),
        "ansi_capture": str(ansi_path),
        "ansi_capture_sha256": sha256_uri(ansi_path),
        "text_capture": str(text_path),
        "text_capture_sha256": sha256_uri(text_path),
    }


def mutate_tui_setting(
    installed: InstalledTau,
    *,
    key: str,
    value: object,
    commands: list[dict[str, Any]],
    extra_env: dict[str, str] | None = None,
) -> dict[str, Any]:
    script = (
        "import json, sys; "
        "from tau_coding.tui.config import mutate_allowlisted_tui_setting; "
        "key=json.loads(sys.argv[1]); value=json.loads(sys.argv[2]); "
        "result=mutate_allowlisted_tui_setting(key, value, actor_id='proof:api'); "
        "print(json.dumps(result, sort_keys=True))"
    )
    env = clean_env(home=installed.home, bin_dir=installed.bin_dir)
    if extra_env:
        env.update(extra_env)
    completed = run_command(
        [str(installed.python_bin), "-c", script, json.dumps(key), json.dumps(value)],
        cwd=installed.root,
        env=env,
        commands=commands,
        check=False,
    )
    return json.loads(completed.stdout)


def provider_selection_probe(
    installed: InstalledTau,
    *,
    commands: list[dict[str, Any]],
) -> dict[str, Any]:
    script = r"""
import json
from pathlib import Path
from tau_coding.provider_config import (
    OpenAICompatibleProviderConfig,
    ProviderSettings,
    load_provider_settings,
    save_provider_settings,
    save_existing_default_provider_model,
)
settings = ProviderSettings(
    default_provider="alpha",
    providers=(
        OpenAICompatibleProviderConfig(
            name="alpha",
            base_url="http://alpha.invalid/v1",
            api_key_env="ALPHA_KEY",
            models=("alpha-a",),
            default_model="alpha-a",
        ),
        OpenAICompatibleProviderConfig(
            name="beta",
            base_url="http://beta.invalid/v1",
            api_key_env="BETA_KEY",
            models=("beta-a", "beta-b"),
            default_model="beta-a",
        ),
    ),
)
path = save_provider_settings(settings)
before = json.loads(path.read_text())
updated = save_existing_default_provider_model(provider_name="beta", model="beta-b")
after = json.loads(path.read_text())
blocked = None
try:
    save_existing_default_provider_model(provider_name="beta", model="new-unconfigured-model")
except Exception as exc:
    blocked = str(exc)
print(json.dumps({
    "ok": (
        updated.default_provider == "beta"
        and updated.get_provider("beta").default_model == "beta-b"
        and before["providers"][1]["base_url"] == after["providers"][1]["base_url"]
        and before["providers"][1]["api_key_env"] == after["providers"][1]["api_key_env"]
        and before["providers"][1]["models"] == after["providers"][1]["models"]
        and blocked is not None
    ),
    "path": str(path),
    "before_sha256": "sha256:" + __import__("hashlib").sha256(
        json.dumps(before, sort_keys=True).encode()
    ).hexdigest(),
    "after_sha256": "sha256:" + __import__("hashlib").sha256(
        json.dumps(after, sort_keys=True).encode()
    ).hexdigest(),
    "blocked_unconfigured_model": blocked,
}, sort_keys=True))
"""
    completed = run_command(
        [str(installed.python_bin), "-c", script],
        cwd=installed.root,
        env=clean_env(home=installed.home, bin_dir=installed.bin_dir),
        commands=commands,
    )
    return json.loads(completed.stdout)


def atomic_failure_probe(
    installed: InstalledTau,
    *,
    commands: list[dict[str, Any]],
) -> dict[str, Any]:
    path = installed.home / ".tau" / "tui.json"
    before = path.read_bytes()
    result = mutate_tui_setting(
        installed,
        key="sidebar_position",
        value="right",
        commands=commands,
        extra_env={"TAU_TUI_CONFIG_FAIL_BEFORE_REPLACE": "1"},
    )
    after = path.read_bytes()
    temp_files = sorted(path.parent.glob(f".{path.name}.*.tmp"))
    return {
        "ok": result.get("accepted") is False and before == after and not temp_files,
        "result": result,
        "before_sha256": sha256_bytes(before),
        "after_sha256": sha256_bytes(after),
        "temporary_files": [str(item) for item in temp_files],
    }


def concurrent_write_probe(
    installed: InstalledTau,
    *,
    run_dir: Path,
    commands: list[dict[str, Any]],
) -> dict[str, Any]:
    worker = run_dir / "concurrent_mutate.py"
    barrier = run_dir / "barrier"
    worker.write_text(
        """
import json, sys, time
from pathlib import Path
from tau_coding.tui.config import load_tui_settings, mutate_allowlisted_tui_setting
barrier = Path(sys.argv[1])
key = sys.argv[2]
value = json.loads(sys.argv[3])
while not barrier.exists():
    time.sleep(0.01)
result = mutate_allowlisted_tui_setting(key, value, actor_id='proof:concurrent')
readback = load_tui_settings()
print(json.dumps({'result': result, 'readback': readback.to_json()}, sort_keys=True))
""".lstrip(),
        encoding="utf-8",
    )
    env = clean_env(home=installed.home, bin_dir=installed.bin_dir)
    procs = [
        subprocess.Popen(
            [str(installed.python_bin), str(worker), str(barrier), "image_width_cells", "120"],
            cwd=installed.root,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        ),
        subprocess.Popen(
            [
                str(installed.python_bin),
                str(worker),
                str(barrier),
                "sidebar_position",
                json.dumps("off"),
            ],
            cwd=installed.root,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        ),
    ]
    barrier.write_text("go\n", encoding="utf-8")
    outputs: list[dict[str, Any]] = []
    for proc in procs:
        stdout, stderr = proc.communicate(timeout=15)
        commands.append(
            {
                "command": [str(installed.python_bin), str(worker), "..."],
                "cwd": str(installed.root),
                "exit_code": proc.returncode,
                "stdout_sha256": sha256_text(stdout),
                "stderr_sha256": sha256_text(stderr),
            }
        )
        if proc.returncode == 0:
            outputs.append(json.loads(stdout))
    final = read_json(installed.home / ".tau" / "tui.json")
    load_result = run_command(
        [
            str(installed.python_bin),
            "-c",
            (
                "from tau_coding.tui.config import load_tui_settings; "
                "print(load_tui_settings().to_json()['sidebar_position'])"
            ),
        ],
        cwd=installed.root,
        env=env,
        commands=commands,
    )
    return {
        "ok": len(outputs) == 2 and load_result.returncode == 0 and isinstance(final, dict),
        "outputs": outputs,
        "final": final,
    }


def corrupt_reload_probe(
    installed: InstalledTau,
    *,
    commands: list[dict[str, Any]],
) -> dict[str, Any]:
    path = installed.home / ".tau" / "tui.json"
    before = path.read_text(encoding="utf-8")
    path.write_text("{not-json\n", encoding="utf-8")
    completed = run_command(
        [
            str(installed.python_bin),
            "-c",
            "from tau_coding.tui.config import load_tui_settings; load_tui_settings()",
        ],
        cwd=installed.root,
        env=clean_env(home=installed.home, bin_dir=installed.bin_dir),
        commands=commands,
        check=False,
    )
    blocked = completed.returncode != 0
    path.write_text(before, encoding="utf-8")
    readback = run_command(
        [
            str(installed.python_bin),
            "-c",
            (
                "from tau_coding.tui.config import load_tui_settings; "
                "print(load_tui_settings().to_json()['theme'])"
            ),
        ],
        cwd=installed.root,
        env=clean_env(home=installed.home, bin_dir=installed.bin_dir),
        commands=commands,
    )
    return {
        "ok": blocked and readback.returncode == 0,
        "corrupt_exit_code": completed.returncode,
        "restored_theme": readback.stdout.strip(),
    }


def run_command(
    command: list[str],
    *,
    cwd: Path,
    commands: list[dict[str, Any]],
    env: dict[str, str] | None = None,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    completed = subprocess.run(
        command,
        cwd=cwd,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )
    commands.append(
        {
            "command": command,
            "cwd": str(cwd),
            "exit_code": completed.returncode,
            "stdout_sha256": sha256_text(completed.stdout),
            "stderr_sha256": sha256_text(completed.stderr),
        }
    )
    if check and completed.returncode != 0:
        raise RuntimeError(completed.stderr or completed.stdout)
    return completed


def wait_for(master_fd: int, capture: bytearray, needle: str, *, timeout: float) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        drain(master_fd, capture, timeout=0.1)
        if needle in capture.decode("utf-8", errors="replace"):
            return
    raise TimeoutError(f"timed out waiting for {needle!r}")


def drain(master_fd: int, capture: bytearray, *, timeout: float) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        readable, _, _ = select.select([master_fd], [], [], 0.05)
        if not readable:
            continue
        try:
            chunk = os.read(master_fd, 8192)
        except OSError:
            return
        if not chunk:
            return
        capture.extend(chunk)


def clean_env(*, home: Path, bin_dir: Path) -> dict[str, str]:
    env = {
        key: value
        for key, value in os.environ.items()
        if key not in {"PYTHONHOME", "PYTHONPATH"}
    }
    env.update({"HOME": str(home), "PATH": f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}"})
    return env


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, data: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def sha256_uri(path: Path) -> str:
    return sha256_bytes(path.read_bytes())


def sha256_bytes(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


def sha256_text(text: str) -> str:
    return "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()


def strip_ansi(text: str) -> str:
    return __import__("re").sub(r"\x1b\[[0-?]*[ -/]*[@-~]", "", text)


class contextlib_suppress:
    def __init__(self, *exceptions: type[BaseException]) -> None:
        self.exceptions = exceptions

    def __enter__(self) -> None:
        return None

    def __exit__(self, exc_type: object, exc: object, tb: object) -> bool:
        return isinstance(exc, self.exceptions)


if __name__ == "__main__":
    raise SystemExit(main())
