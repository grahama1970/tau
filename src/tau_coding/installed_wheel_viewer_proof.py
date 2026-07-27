"""Installed-wheel DAG viewer proof for Tau."""

from __future__ import annotations

import hashlib
import json
import os
import re
import socket
import subprocess
import tempfile
import time
import urllib.error
import urllib.request
import venv
import zipfile
from pathlib import Path
from typing import Any

INSTALLED_WHEEL_VIEWER_PROOF_SCHEMA = "tau.installed_wheel_viewer_proof.v1"


def write_installed_wheel_viewer_proof(
    output: Path,
    *,
    allow_live_browser: bool,
    project_root: Path | None = None,
) -> dict[str, Any]:
    """Build/install Tau and prove the packaged viewer runs without runtime Node."""

    if not allow_live_browser:
        raise RuntimeError("--allow-live-browser is required")

    resolved_output = output.expanduser().resolve()
    proof_dir = resolved_output.parent
    proof_dir.mkdir(parents=True, exist_ok=True)
    root = (project_root or Path.cwd()).expanduser().resolve()
    if not (root / "pyproject.toml").is_file():
        raise RuntimeError(f"project root must contain pyproject.toml: {root}")

    desktop_screenshot = proof_dir / "installed-wheel-viewer-desktop.png"
    mobile_screenshot = proof_dir / "installed-wheel-viewer-mobile.png"
    with tempfile.TemporaryDirectory(prefix="tau-installed-wheel-viewer-proof-") as temporary:
        temp_root = Path(temporary)
        dist_dir = proof_dir / "dist"
        dist_dir.mkdir(parents=True, exist_ok=True)
        _run(["uv", "build", "--wheel", "--out-dir", str(dist_dir)], cwd=root)
        wheels = sorted(dist_dir.glob("tau-*.whl"), key=lambda path: path.stat().st_mtime)
        if not wheels:
            raise RuntimeError("installed_wheel_viewer_proof_wheel_missing")
        wheel = wheels[-1].resolve()
        wheel_static = _inspect_wheel_static_assets(wheel)

        environment = temp_root / "venv"
        venv.EnvBuilder(with_pip=True).create(environment)
        bin_dir = environment / ("Scripts" if os.name == "nt" else "bin")
        python = bin_dir / "python"
        tau = bin_dir / "tau"
        _run([str(python), "-m", "pip", "install", "--quiet", str(wheel)])

        capabilities = _run_json([str(tau), "dag-view-capabilities", "--json"])
        if capabilities.get("read_only") is not True:
            raise RuntimeError("installed_wheel_viewer_capabilities_not_read_only")

        run_dir = temp_root / "run"
        _write_fixture_script(temp_root / "create_run.py")
        _run([str(python), str(temp_root / "create_run.py"), str(run_dir)])

        port = _open_port()
        server = subprocess.Popen(
            [
                str(tau),
                "dag-view",
                "--run-dir",
                str(run_dir),
                "--host",
                "127.0.0.1",
                "--port",
                str(port),
                "--no-open",
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        try:
            base_url = f"http://127.0.0.1:{port}"
            _wait_for_server(base_url, server)
            http_checks = _read_only_http_checks(base_url)
            chrome = _find_chrome()
            _chrome_screenshot(chrome, f"{base_url}/", desktop_screenshot, window="1440,1000")
            _chrome_screenshot(
                chrome,
                f"{base_url}/",
                mobile_screenshot,
                window="390,844",
                mobile=True,
            )
        finally:
            server.terminate()
            try:
                server.wait(timeout=5)
            except subprocess.TimeoutExpired:
                server.kill()
                server.wait(timeout=5)

    payload = {
        "schema": INSTALLED_WHEEL_VIEWER_PROOF_SCHEMA,
        "status": "PASS",
        "mocked": False,
        "live": True,
        "provider_live": False,
        "wheel": {
            "path": str(wheel),
            "sha256": _sha256(wheel),
            "static_index_present": wheel_static["static_index_present"],
            "static_asset_count": wheel_static["static_asset_count"],
            "static_assets": wheel_static["static_assets"],
        },
        "runtime": {
            "installed_tau": str(tau),
            "node_commands_after_wheel_install": [],
            "no_runtime_node_required": True,
            "commands_after_wheel_install": [
                str(python),
                str(tau),
                chrome,
            ],
        },
        "http": http_checks,
        "screenshots": {
            "desktop": _screenshot_artifact(desktop_screenshot),
            "mobile": _screenshot_artifact(mobile_screenshot),
        },
        "proof_boundary": {
            "proves": [
                "A freshly built Tau wheel contains the DAG viewer static index and assets.",
                "An isolated venv can install the wheel and run the packaged tau CLI.",
                "The installed tau CLI serves the packaged DAG viewer over loopback.",
                "The viewer accepts read-only GET requests and rejects a POST mutation attempt.",
                "Chrome can render desktop and mobile screenshots without Node "
                "after wheel install.",
            ],
            "does_not_prove": [
                "Provider semantic correctness.",
                "That every possible terminal/browser environment can render the viewer.",
            ],
        },
    }
    resolved_output.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return payload


def _inspect_wheel_static_assets(wheel: Path) -> dict[str, Any]:
    with zipfile.ZipFile(wheel) as archive:
        names = set(archive.namelist())
        index_name = "tau_coding/dag_viewer/static/index.html"
        if index_name not in names:
            raise RuntimeError("dag_viewer_wheel_index_missing")
        index = archive.read(index_name)
        assets: list[str] = []
        for asset in re.findall(rb'(?:src|href)="(/assets/[^"]+)"', index):
            packaged = f"tau_coding/dag_viewer/static/{asset.decode().lstrip('/')}"
            if packaged not in names:
                raise RuntimeError(f"dag_viewer_wheel_asset_missing:{packaged}")
            assets.append(packaged)
        if not assets:
            raise RuntimeError("dag_viewer_wheel_assets_missing")
        return {
            "static_index_present": True,
            "static_asset_count": len(assets),
            "static_assets": assets,
        }


def _write_fixture_script(path: Path) -> None:
    path.write_text(
        """
from pathlib import Path
import sys

from tau_coding.dag_runtime.compiler import compile_generic_dag_plan
from tau_coding.dag_runtime.run_store import SqliteDagRunStore
from tau_coding.dag_runtime.scheduler import run_dag_plan

run_dir = Path(sys.argv[1])
run_dir.mkdir(parents=True, exist_ok=True)
plan = compile_generic_dag_plan(
    {
        "schema": "tau.generic_dag_spec.v1",
        "run_id": "installed-wheel-viewer",
        "run_dir": str(run_dir),
        "nodes": [
            {
                "node_id": "worker",
                "role": "deterministic",
                "command": ["true"],
                "receipt_path": str(run_dir / "worker.json"),
            }
        ],
    },
    source_path=run_dir / "dag.json",
)
with SqliteDagRunStore(run_dir / "dag-run.sqlite3") as store:
    run_dag_plan(
        plan,
        run_store=store,
        run_id="installed-wheel-viewer",
        execute_node=lambda node, inputs, attempt: {
            "node_id": node.node_id,
            "status": "PASS",
            "verdict": "PASS",
        },
    )
""".lstrip(),
        encoding="utf-8",
    )


def _read_only_http_checks(base_url: str) -> dict[str, Any]:
    index = _fetch(f"{base_url}/")
    if b'<div id="root"></div>' not in index["body"]:
        raise RuntimeError("installed_wheel_viewer_index_invalid")
    asset_paths = re.findall(rb'(?:src|href)="(/assets/[^"]+)"', index["body"])
    asset_checks = []
    for raw_asset in asset_paths:
        asset_path = raw_asset.decode()
        asset = _fetch(f"{base_url}{asset_path}")
        if not asset["body"]:
            raise RuntimeError(f"installed_wheel_viewer_asset_empty:{asset_path}")
        asset_checks.append(
            {
                "path": asset_path,
                "status": asset["status"],
                "bytes": len(asset["body"]),
                "content_type": asset["content_type"],
            }
        )
    state = _fetch_json(f"{base_url}/api/v1/state")
    if state.get("schema") != "tau.dag_view_snapshot.v2":
        raise RuntimeError("installed_wheel_viewer_state_invalid")
    capabilities = _fetch_json(f"{base_url}/api/v1/capabilities")
    if capabilities.get("read_only") is not True:
        raise RuntimeError("installed_wheel_viewer_server_not_read_only")
    mutation = _request_status(f"{base_url}/api/v1/state", method="POST")
    if mutation != 405:
        raise RuntimeError(f"installed_wheel_viewer_mutation_not_rejected:{mutation}")
    return {
        "index_status": index["status"],
        "index_bytes": len(index["body"]),
        "asset_checks": asset_checks,
        "state_schema": state.get("schema"),
        "capabilities_read_only": capabilities.get("read_only"),
        "mutating_method": {"method": "POST", "path": "/api/v1/state", "status": mutation},
    }


def _wait_for_server(base_url: str, process: subprocess.Popen[str]) -> None:
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        if process.poll() is not None:
            stderr = process.stderr.read() if process.stderr else ""
            raise RuntimeError(f"installed_wheel_viewer_server_exited:{stderr}")
        try:
            _fetch_json(f"{base_url}/healthz", timeout=1)
            return
        except OSError:
            time.sleep(0.1)
    stderr = process.stderr.read() if process.stderr else ""
    raise RuntimeError(f"installed_wheel_viewer_server_unavailable:{stderr}")


def _fetch(url: str, *, timeout: float = 5) -> dict[str, Any]:
    with urllib.request.urlopen(url, timeout=timeout) as response:  # noqa: S310 - loopback URL
        return {
            "status": int(response.status),
            "body": response.read(),
            "content_type": response.headers.get("Content-Type", ""),
        }


def _fetch_json(url: str, *, timeout: float = 5) -> dict[str, Any]:
    payload = json.loads(_fetch(url, timeout=timeout)["body"])
    if not isinstance(payload, dict):
        raise RuntimeError(f"installed_wheel_viewer_json_not_object:{url}")
    return payload


def _request_status(url: str, *, method: str) -> int:
    request = urllib.request.Request(url, method=method)
    try:
        with urllib.request.urlopen(request, timeout=5) as response:  # noqa: S310
            return int(response.status)
    except urllib.error.HTTPError as exc:
        return int(exc.code)


def _chrome_screenshot(
    chrome: str,
    url: str,
    screenshot: Path,
    *,
    window: str,
    mobile: bool = False,
) -> None:
    screenshot.parent.mkdir(parents=True, exist_ok=True)
    command = [
        chrome,
        "--headless=new",
        "--disable-gpu",
        "--no-sandbox",
        "--run-all-compositor-stages-before-draw",
        "--virtual-time-budget=5000",
        f"--window-size={window}",
        f"--screenshot={screenshot}",
        url,
    ]
    if mobile:
        command.insert(-1, "--user-agent=Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X)")
    _run(command, timeout=45)
    if not screenshot.is_file() or screenshot.stat().st_size <= 1024:
        raise RuntimeError(f"installed_wheel_viewer_screenshot_missing:{screenshot}")


def _find_chrome() -> str:
    candidates = (
        os.environ.get("TAU_CHROME"),
        "google-chrome",
        "google-chrome-stable",
        "chromium",
        "chromium-browser",
        "/snap/bin/chromium",
    )
    for candidate in candidates:
        if not candidate:
            continue
        if Path(candidate).is_file():
            return candidate
        found = _which(candidate)
        if found is not None:
            return found
    raise RuntimeError("installed_wheel_viewer_chrome_missing")


def _which(command: str) -> str | None:
    for folder in os.environ.get("PATH", "").split(os.pathsep):
        candidate = Path(folder) / command
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return str(candidate)
    return None


def _open_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _run(
    command: list[str],
    *,
    cwd: Path | None = None,
    timeout: float = 120,
) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        command,
        cwd=cwd,
        check=False,
        text=True,
        capture_output=True,
        timeout=timeout,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"command_failed:{command}:stdout={result.stdout.strip()}:stderr={result.stderr.strip()}"
        )
    return result


def _run_json(command: list[str]) -> dict[str, Any]:
    payload = json.loads(_run(command).stdout)
    if not isinstance(payload, dict):
        raise RuntimeError(f"command_json_not_object:{command}")
    return payload


def _screenshot_artifact(path: Path) -> dict[str, Any]:
    return {
        "path": str(path),
        "bytes": path.stat().st_size,
        "sha256": _sha256(path),
    }


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return f"sha256:{digest.hexdigest()}"
