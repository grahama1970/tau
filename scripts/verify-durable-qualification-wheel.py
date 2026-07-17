#!/usr/bin/env python3
"""Verify durable-repository-qualification from an offline installed wheel."""

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
    "durable-repository-qualification",
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
    with tempfile.TemporaryDirectory(prefix="tau-durable-wheel-") as temporary:
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
        catalog = _json_text(
            _run([str(bin_dir / "tau"), "workflows", "list", "--json"], root, env).stdout
        )
        if [item["workflow_id"] for item in catalog["workflows"]] != WORKFLOW_IDS:
            raise RuntimeError("installed workflow catalog mismatch")

        repo = _git_repo(root / "repo")
        run_dir = root / "run"
        publish_path = root / "published"
        first = _json_text(
            _run(
                [
                    str(bin_dir / "tau"),
                    "workflows",
                    "run",
                    "durable-repository-qualification",
                    "--repo",
                    str(repo),
                    "--goal",
                    "Qualify this installed repository durably.",
                    "--publish-path",
                    str(publish_path),
                    "--run-dir",
                    str(run_dir),
                    "--inject-test-branch-failure",
                ],
                root,
                env,
                expected=(1,),
            ).stdout
        )
        repair = _json_text(
            _run(
                [
                    str(bin_dir / "tau"),
                    "workflows",
                    "repair",
                    str(run_dir),
                    "--node",
                    "qualify-tests",
                ],
                root,
                env,
            ).stdout
        )
        approval_wait = _json_text(
            _run(
                [str(bin_dir / "tau"), "workflows", "resume", str(run_dir)],
                root,
                env,
                expected=(1,),
            ).stdout
        )
        _run(
            [str(bin_dir / "tau"), "workflows", "approve", str(run_dir)], root, env
        )
        final = _json_text(
            _run(
                [str(bin_dir / "tau"), "workflows", "resume", str(run_dir)], root, env
            ).stdout
        )
        repeated = _json_text(
            _run(
                [str(bin_dir / "tau"), "workflows", "resume", str(run_dir)], root, env
            ).stdout
        )

        ledger = _json_file(publish_path / "publication-ledger.json")
        receipt = _json_file(run_dir / "run-receipt.json")
        if first.get("status") != "BLOCKED" or repair.get("status") != "PASS":
            raise RuntimeError("installed repair boundary failed")
        if approval_wait.get("status") != "BLOCKED":
            raise RuntimeError("installed approval boundary failed")
        if final.get("status") != "PASS" or repeated.get("status") != "PASS":
            raise RuntimeError("installed durable completion failed")
        if ledger.get("effect_count") != 1:
            raise RuntimeError("installed publication was not idempotent")
        reused = {
            item["node_id"]: item.get("resumed")
            for item in receipt["nodes"]
            if item["node_id"] in {
                "capture-repository",
                "qualify-documentation",
                "qualify-package",
            }
        }
        if reused != {
            "capture-repository": True,
            "qualify-documentation": True,
            "qualify-package": True,
        }:
            raise RuntimeError(f"installed accepted work was not reused: {reused}")
        result_json = run_dir / "results" / "durable-repository-qualification.json"
        result_markdown = run_dir / "results" / "durable-repository-qualification.md"
        if (publish_path / result_json.name).read_bytes() != result_json.read_bytes():
            raise RuntimeError("installed JSON publication mismatch")
        if (publish_path / result_markdown.name).read_bytes() != result_markdown.read_bytes():
            raise RuntimeError("installed Markdown publication mismatch")

        proof = {
            "schema": "tau.durable_qualification_wheel_proof.v1",
            "status": "PASS",
            "mocked": False,
            "live": True,
            "provider_live": False,
            "wheel": str(wheel),
            "wheel_sha256": _sha256(wheel),
            "installed_import": str(import_path),
            "installed_workflow_ids": WORKFLOW_IDS,
            "initial_blocker": "targeted_repair_required",
            "repair_packet": repair["repair_packet_path"],
            "approval_status": approval_wait["status"],
            "final_status": final["status"],
            "repeated_resume_status": repeated["status"],
            "reused_node_ids": sorted(reused),
            "publication_effect_count": ledger["effect_count"],
            "result_json_sha256": _sha256(result_json),
            "result_markdown_sha256": _sha256(result_markdown),
        }
    output = args.output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(proof, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(proof, indent=2, sort_keys=True))
    return 0


def _git_repo(path: Path) -> Path:
    path.mkdir()
    (path / "README.md").write_text("# Durable wheel fixture\n", encoding="utf-8")
    (path / "tests").mkdir()
    (path / "tests" / "test_fixture.py").write_text(
        "def test_fixture():\n    assert True\n", encoding="utf-8"
    )
    (path / "pyproject.toml").write_text(
        '[project]\nname = "fixture"\nversion = "0.1.0"\n', encoding="utf-8"
    )
    subprocess.run(["git", "init", "-q", "-b", "main", str(path)], check=True)
    subprocess.run(["git", "-C", str(path), "add", "."], check=True)
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
        command, cwd=cwd, env=env, capture_output=True, text=True, timeout=180, check=False
    )
    if result.returncode not in expected:
        raise RuntimeError(
            f"command failed {result.returncode}: {' '.join(command)}\n{result.stderr}"
        )
    return result


def _json_text(value: str) -> dict[str, Any]:
    payload = json.loads(value)
    if not isinstance(payload, dict):
        raise RuntimeError("JSON object expected")
    return payload


def _json_file(path: Path) -> dict[str, Any]:
    return _json_text(path.read_text(encoding="utf-8"))


def _sha256(path: Path) -> str:
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


if __name__ == "__main__":
    raise SystemExit(main())
