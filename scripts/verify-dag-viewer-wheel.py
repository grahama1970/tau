#!/usr/bin/env python3
"""Verify packaged DAG viewer assets and execute the installed-wheel server."""

from __future__ import annotations

import argparse
import json
import os
import re
import socket
import subprocess
import tempfile
import time
import urllib.request
import venv
import zipfile
from pathlib import Path

from tau_coding.dag_runtime.compiler import compile_generic_dag_plan
from tau_coding.dag_runtime.run_store import SqliteDagRunStore
from tau_coding.dag_runtime.scheduler import run_dag_plan


def _fixture_run(run_dir: Path) -> None:
    plan = compile_generic_dag_plan(
        {
            "schema": "tau.generic_dag_spec.v1",
            "run_id": "wheel-viewer",
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
            run_id="wheel-viewer",
            execute_node=lambda node, inputs, attempt: {
                "node_id": node.node_id,
                "status": "PASS",
                "verdict": "PASS",
            },
        )


def _open_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("wheel", type=Path)
    args = parser.parse_args()
    wheel = args.wheel.expanduser().resolve()
    with zipfile.ZipFile(wheel) as archive:
        names = set(archive.namelist())
        index_name = "tau_coding/dag_viewer/static/index.html"
        if index_name not in names:
            raise RuntimeError("dag_viewer_wheel_index_missing")
        index = archive.read(index_name)
        for asset in re.findall(rb'(?:src|href)="(/assets/[^"]+)"', index):
            packaged = f"tau_coding/dag_viewer/static/{asset.decode().lstrip('/')}"
            if packaged not in names:
                raise RuntimeError(f"dag_viewer_wheel_asset_missing:{packaged}")

    with tempfile.TemporaryDirectory(prefix="tau-dag-viewer-wheel-") as temporary:
        root = Path(temporary)
        environment = root / "venv"
        venv.EnvBuilder(with_pip=True).create(environment)
        bin_dir = environment / ("Scripts" if os.name == "nt" else "bin")
        subprocess.run(
            [str(bin_dir / "python"), "-m", "pip", "install", "--quiet", str(wheel)],
            check=True,
        )
        capabilities = subprocess.run(
            [str(bin_dir / "tau"), "dag-view-capabilities", "--json"],
            check=True,
            capture_output=True,
            text=True,
        )
        if json.loads(capabilities.stdout).get("read_only") is not True:
            raise RuntimeError("dag_viewer_wheel_capabilities_invalid")
        run_dir = root / "run"
        run_dir.mkdir()
        _fixture_run(run_dir)
        port = _open_port()
        process = subprocess.Popen(
            [
                str(bin_dir / "tau"),
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
            deadline = time.monotonic() + 10
            while True:
                try:
                    with urllib.request.urlopen(  # noqa: S310 - fixed loopback URL
                        f"http://127.0.0.1:{port}/", timeout=1
                    ) as response:
                        html = response.read()
                    with urllib.request.urlopen(  # noqa: S310 - fixed loopback URL
                        f"http://127.0.0.1:{port}/api/v1/state", timeout=1
                    ) as response:
                        state = json.loads(response.read())
                    break
                except OSError as exc:
                    if process.poll() is not None or time.monotonic() >= deadline:
                        stderr = process.stderr.read() if process.stderr else ""
                        raise RuntimeError(
                            f"dag_viewer_wheel_server_failed:{stderr}"
                        ) from exc
                    time.sleep(0.1)
            if b'<div id="root"></div>' not in html:
                raise RuntimeError("dag_viewer_wheel_html_invalid")
            if state.get("schema") != "tau.dag_live_snapshot.v1":
                raise RuntimeError("dag_viewer_wheel_state_invalid")
        finally:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
    print(
        json.dumps(
            {
                "schema": "tau.dag_viewer_wheel_verification.v1",
                "status": "PASS",
                "mocked": False,
                "live": True,
                "provider_live": False,
                "wheel": str(wheel),
            },
            indent=2,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
