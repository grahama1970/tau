#!/usr/bin/env python3
"""Verify the packaged workflow catalog and execute the installed wheel live."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sysconfig
import tempfile
import venv
import zipfile
from pathlib import Path
from typing import Any

WORKFLOW_ID = "repository-readiness"
HUMAN_GOAL = "Determine whether this checkout is ready for focused work."


def _run(
    command: list[str],
    *,
    cwd: Path,
    env: dict[str, str] | None = None,
    timeout: float = 120,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        cwd=cwd,
        env=env,
        check=True,
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def _sha256(path: Path) -> str:
    return f"sha256:{hashlib.sha256(path.read_bytes()).hexdigest()}"


def _json_object(text: str, *, label: str) -> dict[str, Any]:
    payload = json.loads(text)
    if not isinstance(payload, dict):
        raise RuntimeError(f"{label}_not_object")
    return payload


def _git_fixture(path: Path) -> str:
    path.mkdir(parents=True)
    _run(["git", "init", "--initial-branch=main"], cwd=path)
    _run(["git", "config", "user.name", "Tau Wheel Proof"], cwd=path)
    _run(["git", "config", "user.email", "tau-wheel@example.invalid"], cwd=path)
    (path / "README.md").write_text("# Installed Tau workflow fixture\n", encoding="utf-8")
    _run(["git", "add", "README.md"], cwd=path)
    _run(["git", "commit", "-m", "fixture"], cwd=path)
    return _run(["git", "rev-parse", "HEAD"], cwd=path).stdout.strip()


def _archive_contract(wheel: Path) -> dict[str, object]:
    definition = "tau_coding/workflows/definitions/repository-readiness.json"
    template = "tau_coding/workflows/templates/repository-readiness.json"
    index = "tau_coding/dag_viewer/static/index.html"
    with zipfile.ZipFile(wheel) as archive:
        names = set(archive.namelist())
        for required in (definition, template, index):
            if required not in names:
                raise RuntimeError(f"workflow_wheel_resource_missing:{required}")
        definition_payload = _json_object(archive.read(definition).decode(), label="definition")
        template_payload = _json_object(archive.read(template).decode(), label="template")
        if definition_payload.get("workflow_id") != WORKFLOW_ID:
            raise RuntimeError("workflow_wheel_definition_invalid")
        nodes = template_payload.get("nodes")
        if not isinstance(nodes, list) or [node.get("node_id") for node in nodes] != [
            "inspect-repository",
            "validate-readiness",
            "publish-readiness",
        ]:
            raise RuntimeError("workflow_wheel_template_topology_invalid")
        index_body = archive.read(index)
        assets = []
        for match in re.findall(rb'(?:src|href)="(/assets/[^"]+)"', index_body):
            packaged = f"tau_coding/dag_viewer/static/{match.decode().lstrip('/')}"
            if packaged not in names:
                raise RuntimeError(f"workflow_wheel_viewer_asset_missing:{packaged}")
            assets.append(packaged)
    return {
        "definition": definition,
        "template": template,
        "viewer_index": index,
        "viewer_assets": sorted(assets),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--wheel", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    wheel = args.wheel.expanduser().resolve()
    output = args.output.expanduser().resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    if not wheel.is_file():
        raise RuntimeError(f"workflow_wheel_missing:{wheel}")
    archive = _archive_contract(wheel)

    with tempfile.TemporaryDirectory(prefix="tau-workflow-wheel-") as temporary:
        root = Path(temporary)
        environment = root / "venv"
        dependency_site = Path(sysconfig.get_path("purelib")).resolve()
        if not (dependency_site / "pydantic").is_dir():
            raise RuntimeError(f"locked_project_dependency_site_invalid:{dependency_site}")
        venv.EnvBuilder(with_pip=True).create(environment)
        bin_dir = environment / ("Scripts" if os.name == "nt" else "bin")
        environment_site = Path(
            _run(
                [
                    str(bin_dir / "python"),
                    "-c",
                    "import sysconfig; print(sysconfig.get_path('purelib'))",
                ],
                cwd=root,
            ).stdout.strip()
        ).resolve()
        (environment_site / "tau-locked-dependencies.pth").write_text(
            str(dependency_site) + "\n", encoding="utf-8"
        )
        clean_env = {
            key: value
            for key, value in os.environ.items()
            if key not in {"PYTHONHOME", "PYTHONPATH"}
        }
        clean_env["PIP_NO_INDEX"] = "1"
        _run(
            [str(bin_dir / "python"), "-m", "pip", "install", "--quiet", "--no-deps", str(wheel)],
            cwd=root,
            env=clean_env,
        )
        import_probe = _json_object(
            _run(
                [
                    str(bin_dir / "python"),
                    "-c",
                    (
                        "import json, pathlib, pydantic, tau_coding; "
                        "print(json.dumps({'pydantic': pydantic.__file__, "
                        "'tau_coding': tau_coding.__file__}))"
                    ),
                ],
                cwd=root,
                env=clean_env,
            ).stdout,
            label="installed_workflow_import_probe",
        )
        tau_import = Path(str(import_probe.get("tau_coding"))).resolve()
        if not tau_import.is_relative_to(environment_site):
            raise RuntimeError(f"installed_tau_import_not_from_wheel:{tau_import}")
        pydantic_import = Path(str(import_probe.get("pydantic"))).resolve()
        if not pydantic_import.is_relative_to(dependency_site):
            raise RuntimeError(f"installed_dependency_not_from_locked_site:{pydantic_import}")
        catalog_process = _run(
            [str(bin_dir / "tau"), "workflows", "list", "--json"],
            cwd=root,
            env=clean_env,
        )
        catalog = _json_object(catalog_process.stdout, label="installed_workflow_catalog")
        workflows = catalog.get("workflows")
        if not isinstance(workflows, list) or len(workflows) != 1:
            raise RuntimeError("installed_workflow_catalog_count_invalid")
        if workflows[0].get("workflow_id") != WORKFLOW_ID:
            raise RuntimeError("installed_workflow_catalog_id_invalid")
        if workflows[0].get("topology") != "LINEAR":
            raise RuntimeError("installed_workflow_catalog_topology_invalid")
        description_process = _run(
            [str(bin_dir / "tau"), "workflows", "describe", WORKFLOW_ID, "--json"],
            cwd=root,
            env=clean_env,
        )
        description = _json_object(
            description_process.stdout, label="installed_workflow_description"
        )

        fixture = root / "fixture"
        head_sha = _git_fixture(fixture)
        run_dir = root / "run"
        run_process = _run(
            [
                str(bin_dir / "tau"),
                "workflows",
                "run",
                WORKFLOW_ID,
                "--repo",
                str(fixture),
                "--goal",
                HUMAN_GOAL,
                "--require-clean",
                "--run-dir",
                str(run_dir),
            ],
            cwd=root,
            env=clean_env,
        )
        run_receipt = _json_object(run_process.stdout, label="installed_workflow_run")
        if run_receipt.get("status") != "PASS" or run_receipt.get("ok") is not True:
            raise RuntimeError("installed_workflow_run_not_pass")

        result_json = run_dir / "results" / "repository-readiness.json"
        result_markdown = run_dir / "results" / "repository-readiness.md"
        if not result_json.is_file() or not result_markdown.is_file():
            raise RuntimeError("installed_workflow_result_missing")
        result = _json_object(result_json.read_text(encoding="utf-8"), label="readiness_result")
        repository = result.get("repository")
        if result.get("status") != "READY" or not isinstance(repository, dict):
            raise RuntimeError("installed_workflow_result_invalid")
        if repository.get("head_sha") != head_sha or repository.get("dirty") is not False:
            raise RuntimeError("installed_workflow_repository_evidence_invalid")
        receipt_paths = sorted((run_dir / "receipts").glob("*.json"))
        if len(receipt_paths) != 3:
            raise RuntimeError("installed_workflow_node_receipt_count_invalid")
        goal_hashes = {
            _json_object(path.read_text(encoding="utf-8"), label=path.stem).get("goal_hash")
            for path in receipt_paths
        }
        if len(goal_hashes) != 1 or not next(iter(goal_hashes), "").startswith("sha256:"):
            raise RuntimeError("installed_workflow_goal_hash_propagation_invalid")

        proof = {
            "schema": "tau.repository_readiness_wheel_proof.v1",
            "status": "PASS",
            "mocked": False,
            "live": True,
            "provider_live": False,
            "wheel": str(wheel),
            "wheel_sha256": _sha256(wheel),
            "dependency_site": str(dependency_site),
            "installed_environment_site": str(environment_site),
            "import_probe": import_probe,
            "packaged_resources": archive,
            "installed_catalog": catalog,
            "installed_description": description,
            "run_receipt": run_receipt,
            "fixture_head_sha": head_sha,
            "goal_hash": next(iter(goal_hashes)),
            "node_receipts": [
                {"path": str(path.relative_to(run_dir)), "sha256": _sha256(path)}
                for path in receipt_paths
            ],
            "result_artifacts": [
                {
                    "kind": "repository_readiness_json",
                    "path": "results/repository-readiness.json",
                    "sha256": _sha256(result_json),
                    "size_bytes": result_json.stat().st_size,
                },
                {
                    "kind": "repository_readiness_markdown",
                    "path": "results/repository-readiness.md",
                    "sha256": _sha256(result_markdown),
                    "size_bytes": result_markdown.stat().st_size,
                },
            ],
            "what_was_exercised": [
                "The built wheel contained the packaged workflow definition and template.",
                "The installed tau executable listed and described repository-readiness.",
                (
                    "The installed tau executable ran all three local workflow nodes "
                    "against a clean Git fixture."
                ),
                "The installed workflow produced and hash-bound both readiness result artifacts.",
            ],
            "remains_unverified": [
                "Provider or model execution.",
                "Repository test-suite correctness.",
                "Production deployment readiness.",
            ],
        }
    output.write_text(json.dumps(proof, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(proof, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
