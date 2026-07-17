#!/usr/bin/env python3
"""Verify approved-release-bundle from an offline installed Tau wheel."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sysconfig
import tempfile
import venv
from pathlib import Path
from typing import Any

WORKFLOW_IDS = [
    "approved-release-bundle",
    "repository-evidence-map",
    "repository-readiness",
    "tau-operator-reference",
]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--wheel", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    wheel = args.wheel.resolve()
    with tempfile.TemporaryDirectory(prefix="tau-approved-release-wheel-") as temporary:
        root = Path(temporary)
        environment = root / "venv"
        venv.EnvBuilder(with_pip=True).create(environment)
        bin_dir = environment / ("Scripts" if os.name == "nt" else "bin")
        environment_site = Path(
            _run(
                [
                    str(bin_dir / "python"),
                    "-c",
                    "import sysconfig; print(sysconfig.get_path('purelib'))",
                ],
                root,
            ).stdout.strip()
        )
        dependency_site = Path(sysconfig.get_path("purelib")).resolve()
        (environment_site / "tau-dependencies.pth").write_text(
            str(dependency_site) + "\n", encoding="utf-8"
        )
        env = {
            **os.environ,
            "PATH": f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}",
            "PIP_NO_INDEX": "1",
            "PIP_DISABLE_PIP_VERSION_CHECK": "1",
            "HTTP_PROXY": "http://127.0.0.1:9",
            "HTTPS_PROXY": "http://127.0.0.1:9",
            "ALL_PROXY": "http://127.0.0.1:9",
            "NO_PROXY": "127.0.0.1,localhost",
        }
        env.pop("PYTHONPATH", None)
        env.pop("PYTHONHOME", None)
        _run(
            [
                str(bin_dir / "python"),
                "-m",
                "pip",
                "install",
                "--quiet",
                "--no-index",
                "--no-deps",
                str(wheel),
            ],
            root,
            env,
        )
        import_path = Path(
            _run(
                [str(bin_dir / "python"), "-c", "import tau_coding; print(tau_coding.__file__)"],
                root,
                env,
            ).stdout.strip()
        ).resolve()
        if not import_path.is_relative_to(environment_site.resolve()):
            raise RuntimeError(f"tau_coding not imported from wheel: {import_path}")
        catalog = _json(
            _run([str(bin_dir / "tau"), "workflows", "list", "--json"], root, env).stdout
        )
        if [item["workflow_id"] for item in catalog["workflows"]] != WORKFLOW_IDS:
            raise RuntimeError("installed workflow catalog mismatch")
        repo = _git_repo(root / "repo")
        run_dir = root / "run"
        publish_path = root / "published"
        command = [
            str(bin_dir / "tau"),
            "workflows",
            "run",
            "approved-release-bundle",
            "--repo",
            str(repo),
            "--goal",
            "Publish an approved release bundle.",
            "--publish-path",
            str(publish_path),
            "--run-dir",
            str(run_dir),
        ]
        first = _json(_run(command, root, env, expected=(1,)).stdout)
        _run([str(bin_dir / "tau"), "workflows", "approve", str(run_dir)], root, env)
        final = _json(
            _run([str(bin_dir / "tau"), "workflows", "resume", str(run_dir)], root, env).stdout
        )
        dag = _json((run_dir / "run-receipt.json").read_text(encoding="utf-8"))
        notes = next(item for item in dag["nodes"] if item["node_id"] == "draft-release-notes")
        if first["status"] != "BLOCKED" or final["status"] != "PASS":
            raise RuntimeError("installed approval lifecycle failed")
        if [item["review_verdict"] for item in notes["attempts"]] != ["REVISE", "PASS"]:
            raise RuntimeError("installed revision lifecycle mismatch")
        result_path = run_dir / "results" / "approved-release-bundle.json"
        published_path = publish_path / result_path.name
        if result_path.read_bytes() != published_path.read_bytes():
            raise RuntimeError("installed publication differs from result")
        proof = {
            "schema": "tau.approved_release_wheel_proof.v1",
            "status": "PASS",
            "mocked": False,
            "live": True,
            "provider_live": False,
            "wheel": str(wheel),
            "wheel_sha256": _sha256(wheel),
            "installed_import": str(import_path),
            "installed_workflow_ids": WORKFLOW_IDS,
            "approval_boundary": first["status"],
            "final_status": final["status"],
            "review_verdicts": ["REVISE", "PASS"],
            "result_sha256": _sha256(result_path),
            "published_sha256": _sha256(published_path),
        }
    output = args.output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(proof, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(proof, indent=2, sort_keys=True))
    return 0


def _git_repo(path: Path) -> Path:
    path.mkdir()
    (path / "README.md").write_text("# Wheel release fixture\n", encoding="utf-8")
    subprocess.run(["git", "init", "-q", "-b", "main", str(path)], check=True)
    subprocess.run(["git", "-C", str(path), "add", "README.md"], check=True)
    subprocess.run(
        [
            "git",
            "-C",
            str(path),
            "-c",
            "user.name=Tau",
            "-c",
            "user.email=tau@example.invalid",
            "commit",
            "-qm",
            "fixture",
        ],
        check=True,
    )
    return path


def _run(
    command: list[str],
    cwd: Path,
    env: dict[str, str] | None = None,
    expected: tuple[int, ...] = (0,),
) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        command, cwd=cwd, env=env, capture_output=True, text=True, timeout=120, check=False
    )
    if result.returncode not in expected:
        raise RuntimeError(
            f"command failed {result.returncode}: {' '.join(command)}\n{result.stderr}"
        )
    return result


def _json(value: str) -> dict[str, Any]:
    payload = json.loads(value)
    if not isinstance(payload, dict):
        raise RuntimeError("JSON object expected")
    return payload


def _sha256(path: Path) -> str:
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


if __name__ == "__main__":
    raise SystemExit(main())
