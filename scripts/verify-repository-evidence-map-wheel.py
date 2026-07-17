#!/usr/bin/env python3
"""Verify repository-evidence-map from an offline installed Tau wheel."""

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
    output = args.output.resolve()
    if not wheel.is_file():
        raise RuntimeError(f"wheel missing: {wheel}")
    with tempfile.TemporaryDirectory(prefix="tau-evidence-map-wheel-") as temporary:
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
        positive_repo = root / "positive-repo"
        negative_repo = root / "negative-repo"
        _git_repo(positive_repo, with_tests=True)
        _git_repo(negative_repo, with_tests=False)
        positive_dir = root / "positive-run"
        negative_dir = root / "negative-run"
        positive = _json_text(
            _run(_command(bin_dir, positive_repo, positive_dir), root, env).stdout
        )
        negative_process = _run(
            _command(bin_dir, negative_repo, negative_dir),
            root,
            env,
            expected=(1,),
        )
        negative = _json_text(negative_process.stdout)
        dag_receipt = _json_file(positive_dir / "run-receipt.json")
        if positive.get("status") != "PASS" or dag_receipt.get("max_observed_concurrency") != 3:
            raise RuntimeError("installed positive concurrency failed")
        test_receipt = _json_file(negative_dir / "receipts" / "analyze-tests.json")
        if negative.get("status") != "BLOCKED" or test_receipt.get("errors") != [
            "test_surface_missing"
        ]:
            raise RuntimeError("installed negative blocker mismatch")
        if (negative_dir / "receipts" / "publish-evidence-map.json").exists():
            raise RuntimeError("installed negative publisher dispatched")
        if (negative_dir / "results").exists():
            raise RuntimeError("installed negative results exist")
        result_json = positive_dir / "results" / "repository-evidence-map.json"
        result_markdown = positive_dir / "results" / "repository-evidence-map.md"
        proof = {
            "schema": "tau.repository_evidence_map_wheel_proof.v1",
            "status": "PASS",
            "mocked": False,
            "live": True,
            "provider_live": False,
            "wheel": str(wheel),
            "wheel_sha256": _sha256(wheel),
            "installed_import": str(import_path),
            "installed_workflow_ids": WORKFLOW_IDS,
            "positive": {
                "status": positive["status"],
                "max_observed_concurrency": dag_receipt["max_observed_concurrency"],
                "json_sha256": _sha256(result_json),
                "markdown_sha256": _sha256(result_markdown),
            },
            "negative": {
                "status": negative["status"],
                "blocker": test_receipt["errors"][0],
                "publisher_dispatched": False,
                "result_files": [],
            },
            "proof_scope": {
                "proves": [
                    "The offline installed wheel exposes exactly three canonical workflows.",
                    "The installed fan-out workflow reaches concurrency three and publishes.",
                    "The installed negative path blocks exactly and publishes nothing.",
                ],
                "does_not_prove": [
                    "The repository test suite passes.",
                    "Provider or model quality.",
                    "Production deployment readiness.",
                ],
            },
        }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(proof, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(proof, indent=2, sort_keys=True))
    return 0


def _command(bin_dir: Path, repo: Path, run_dir: Path) -> list[str]:
    return [
        str(bin_dir / "tau"),
        "workflows",
        "run",
        "repository-evidence-map",
        "--repo",
        str(repo),
        "--goal",
        "Map this repository for focused work.",
        "--require-tests",
        "--run-dir",
        str(run_dir),
    ]


def _git_repo(path: Path, *, with_tests: bool) -> None:
    path.mkdir()
    (path / "README.md").write_text("# Wheel Fixture\n", encoding="utf-8")
    (path / "pyproject.toml").write_text(
        '[project]\nname = "wheel-fixture"\nversion = "0.1.0"\n', encoding="utf-8"
    )
    if with_tests:
        (path / "tests").mkdir()
        (path / "tests" / "test_fixture.py").write_text("def test_fixture():\n    assert True\n")
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


def _run(
    command: list[str],
    cwd: Path,
    env: dict[str, str] | None = None,
    expected: tuple[int, ...] = (0,),
) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        command, cwd=cwd, env=env, check=False, capture_output=True, text=True, timeout=120
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
