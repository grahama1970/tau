#!/usr/bin/env python3
"""Verify the packaged operator-reference workflow using only an installed wheel."""

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

WORKFLOW_ID = "tau-operator-reference"
REQUIRED_WORKFLOW = "repository-readiness"
ABSENT_WORKFLOW = "deliberately-absent-workflow"
NODE_IDS = (
    "collect-operator-sources",
    "capture-operator-cli",
    "compose-operator-reference",
    "validate-operator-reference",
)


def _run(
    command: list[str],
    *,
    cwd: Path,
    env: dict[str, str] | None = None,
    timeout: float = 120,
    expected_codes: tuple[int, ...] = (0,),
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
    if process.returncode not in expected_codes:
        raise RuntimeError(
            f"command_failed:{process.returncode}:{' '.join(command)}:{process.stderr}"
        )
    return process


def _sha256(path: Path) -> str:
    return f"sha256:{hashlib.sha256(path.read_bytes()).hexdigest()}"


def _json_object(text: str, *, label: str) -> dict[str, Any]:
    payload = json.loads(text)
    if not isinstance(payload, dict):
        raise RuntimeError(f"{label}_not_object")
    return payload


def _archive_contract(wheel: Path) -> dict[str, object]:
    definitions = {
        "repository-readiness": (
            "tau_coding/workflows/definitions/repository-readiness.json",
            "tau_coding/workflows/templates/repository-readiness.json",
        ),
        WORKFLOW_ID: (
            "tau_coding/workflows/definitions/tau-operator-reference.json",
            "tau_coding/workflows/templates/tau-operator-reference.json",
        ),
    }
    index = "tau_coding/dag_viewer/static/index.html"
    with zipfile.ZipFile(wheel) as archive:
        names = set(archive.namelist())
        for definition, template in definitions.values():
            if definition not in names:
                raise RuntimeError(f"operator_reference_wheel_resource_missing:{definition}")
            if template not in names:
                raise RuntimeError(f"operator_reference_wheel_resource_missing:{template}")
        if index not in names:
            raise RuntimeError(f"operator_reference_wheel_resource_missing:{index}")
        operator_definition = _json_object(
            archive.read(definitions[WORKFLOW_ID][0]).decode(), label="operator_definition"
        )
        operator_template = _json_object(
            archive.read(definitions[WORKFLOW_ID][1]).decode(), label="operator_template"
        )
        if operator_definition.get("workflow_id") != WORKFLOW_ID:
            raise RuntimeError("operator_reference_wheel_definition_invalid")
        nodes = operator_template.get("nodes")
        if not isinstance(nodes, list) or [node.get("node_id") for node in nodes] != list(NODE_IDS):
            raise RuntimeError("operator_reference_wheel_template_topology_invalid")
        index_body = archive.read(index)
        assets = []
        for match in re.findall(rb'(?:src|href)="(/assets/[^"]+)"', index_body):
            packaged = f"tau_coding/dag_viewer/static/{match.decode().lstrip('/')}"
            if packaged not in names:
                raise RuntimeError(f"operator_reference_wheel_viewer_asset_missing:{packaged}")
            assets.append(packaged)
    return {
        "definitions": {key: value[0] for key, value in definitions.items()},
        "templates": {key: value[1] for key, value in definitions.items()},
        "viewer_index": index,
        "viewer_assets": sorted(assets),
    }


def _network_blocker(environment_site: Path) -> Path:
    path = environment_site / "sitecustomize.py"
    path.write_text(
        """import os
import socket

if os.environ.get("TAU_PROOF_NO_NETWORK") == "1":
    _original_connect = socket.socket.connect

    def _blocked_connect(self, address):
        if self.family in (socket.AF_INET, socket.AF_INET6):
            raise PermissionError("tau_operator_reference_wheel_network_disabled")
        return _original_connect(self, address)

    socket.socket.connect = _blocked_connect
""",
        encoding="utf-8",
    )
    return path


def _workflow_command(
    bin_dir: Path,
    *,
    repo_path: Path,
    required_workflow: str,
    run_dir: Path,
) -> list[str]:
    return [
        str(bin_dir / "tau"),
        "workflows",
        "run",
        WORKFLOW_ID,
        "--repo",
        str(repo_path),
        "--required-workflow",
        required_workflow,
        "--run-dir",
        str(run_dir),
    ]


def _node_receipts(run_dir: Path, *, negative: bool) -> list[dict[str, object]]:
    receipts = []
    for index, node_id in enumerate(NODE_IDS):
        path = run_dir / "receipts" / f"{node_id}.json"
        payload = _json_object(path.read_text(encoding="utf-8"), label=f"{node_id}_receipt")
        expected_status = "BLOCKED" if negative and index == 3 else "PASS"
        if payload.get("node_id") != node_id or payload.get("status") != expected_status:
            raise RuntimeError(f"installed_operator_reference_node_invalid:{node_id}")
        if expected_status == "PASS" and payload.get("accepted_output") is None:
            raise RuntimeError(f"installed_operator_reference_node_not_accepted:{node_id}")
        if expected_status == "BLOCKED" and payload.get("errors") != ["required_workflow_missing"]:
            raise RuntimeError("installed_operator_reference_negative_error_invalid")
        receipts.append(
            {
                "node_id": node_id,
                "status": expected_status,
                "path": str(path.relative_to(run_dir)),
                "sha256": _sha256(path),
            }
        )
    return receipts


def _positive_result(run_dir: Path) -> tuple[Path, Path, dict[str, Any]]:
    result_json = run_dir / "results" / "tau-operator-reference.json"
    result_markdown = run_dir / "results" / "tau-operator-reference.md"
    if not result_json.is_file() or not result_markdown.is_file():
        raise RuntimeError("installed_operator_reference_result_missing")
    result = _json_object(result_json.read_text(encoding="utf-8"), label="operator_reference")
    return result_json, result_markdown, result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--wheel", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    wheel = args.wheel.expanduser().resolve()
    output = args.output.expanduser().resolve()
    repo_root = Path(__file__).resolve().parents[1]
    output.parent.mkdir(parents=True, exist_ok=True)
    if not wheel.is_file():
        raise RuntimeError(f"operator_reference_wheel_missing:{wheel}")
    archive = _archive_contract(wheel)

    with tempfile.TemporaryDirectory(prefix="tau-operator-reference-wheel-") as temporary:
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
        clean_env.update(
            {
                "PIP_NO_INDEX": "1",
                "PIP_DISABLE_PIP_VERSION_CHECK": "1",
                "HTTP_PROXY": "http://127.0.0.1:9",
                "HTTPS_PROXY": "http://127.0.0.1:9",
                "ALL_PROXY": "http://127.0.0.1:9",
                "NO_PROXY": "127.0.0.1,localhost",
                "PATH": f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}",
            }
        )
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
            cwd=root,
            env=clean_env,
        )
        network_blocker = _network_blocker(environment_site)
        clean_env["TAU_PROOF_NO_NETWORK"] = "1"
        import_probe = _json_object(
            _run(
                [
                    str(bin_dir / "python"),
                    "-c",
                    (
                        "import json, pydantic, tau_coding; "
                        "print(json.dumps({'pydantic': pydantic.__file__, "
                        "'tau_coding': tau_coding.__file__}))"
                    ),
                ],
                cwd=root,
                env=clean_env,
            ).stdout,
            label="installed_operator_reference_import_probe",
        )
        tau_import = Path(str(import_probe.get("tau_coding"))).resolve()
        if not tau_import.is_relative_to(environment_site):
            raise RuntimeError(f"installed_tau_import_not_from_wheel:{tau_import}")
        pydantic_import = Path(str(import_probe.get("pydantic"))).resolve()
        if not pydantic_import.is_relative_to(dependency_site):
            raise RuntimeError(f"installed_dependency_not_from_locked_site:{pydantic_import}")

        catalog = _json_object(
            _run(
                [str(bin_dir / "tau"), "workflows", "list", "--json"],
                cwd=root,
                env=clean_env,
            ).stdout,
            label="installed_operator_reference_catalog",
        )
        workflows = catalog.get("workflows")
        expected_ids = ["repository-readiness", WORKFLOW_ID]
        if (
            not isinstance(workflows, list)
            or [workflow.get("workflow_id") for workflow in workflows] != expected_ids
        ):
            raise RuntimeError("installed_operator_reference_catalog_invalid")
        description = _json_object(
            _run(
                [str(bin_dir / "tau"), "workflows", "describe", WORKFLOW_ID, "--json"],
                cwd=root,
                env=clean_env,
            ).stdout,
            label="installed_operator_reference_description",
        )

        positive_runs = []
        positive_results = []
        for run_number in (1, 2):
            run_dir = root / f"positive-run-{run_number}"
            process = _run(
                _workflow_command(
                    bin_dir,
                    repo_path=repo_root,
                    required_workflow=REQUIRED_WORKFLOW,
                    run_dir=run_dir,
                ),
                cwd=root,
                env=clean_env,
            )
            run_receipt = _json_object(
                process.stdout, label=f"installed_operator_reference_positive_{run_number}"
            )
            if run_receipt.get("status") != "PASS" or run_receipt.get("ok") is not True:
                raise RuntimeError("installed_operator_reference_positive_not_pass")
            result_json, result_markdown, result = _positive_result(run_dir)
            cli_evidence = result.get("cli_evidence")
            installed_tau = (bin_dir / "tau").resolve()
            if (
                not isinstance(cli_evidence, dict)
                or Path(str(cli_evidence.get("tau_executable"))).resolve() != installed_tau
            ):
                raise RuntimeError("installed_operator_reference_cli_probe_not_from_wheel")
            positive_runs.append(
                {
                    "run_receipt": run_receipt,
                    "node_receipts": _node_receipts(run_dir, negative=False),
                    "result": result,
                }
            )
            positive_results.append((result_json, result_markdown))
        first_json, first_markdown = positive_results[0]
        second_json, second_markdown = positive_results[1]
        if first_json.read_bytes() != second_json.read_bytes():
            raise RuntimeError("installed_operator_reference_json_not_reproducible")
        if first_markdown.read_bytes() != second_markdown.read_bytes():
            raise RuntimeError("installed_operator_reference_markdown_not_reproducible")

        negative_run_dir = root / "negative-run"
        negative_process = _run(
            _workflow_command(
                bin_dir,
                repo_path=repo_root,
                required_workflow=ABSENT_WORKFLOW,
                run_dir=negative_run_dir,
            ),
            cwd=root,
            env=clean_env,
            expected_codes=(0, 1),
        )
        negative_receipt = _json_object(
            negative_process.stdout, label="installed_operator_reference_negative"
        )
        if negative_receipt.get("status") != "BLOCKED" or negative_receipt.get("ok") is not False:
            raise RuntimeError("installed_operator_reference_negative_not_blocked")
        negative_nodes = _node_receipts(negative_run_dir, negative=True)
        results_dir = negative_run_dir / "results"
        result_files = (
            sorted(path.name for path in results_dir.iterdir() if path.is_file())
            if results_dir.is_dir()
            else []
        )
        if result_files:
            raise RuntimeError("installed_operator_reference_negative_results_present")

        proof = {
            "schema": "tau.operator_reference_wheel_proof.v1",
            "status": "PASS",
            "mocked": False,
            "live": True,
            "provider_live": False,
            "wheel": str(wheel),
            "wheel_sha256": _sha256(wheel),
            "network": "disabled",
            "network_blocker": str(network_blocker),
            "dependency_site": str(dependency_site),
            "installed_environment_site": str(environment_site),
            "import_probe": import_probe,
            "packaged_resources": archive,
            "installed_catalog": catalog,
            "installed_description": description,
            "positive_runs": positive_runs,
            "negative_run": {
                "returncode": negative_process.returncode,
                "run_receipt": negative_receipt,
                "node_receipts": negative_nodes,
                "result_files": result_files,
            },
            "reproducible_results": {
                "byte_identical": True,
                "json_sha256": _sha256(first_json),
                "markdown_sha256": _sha256(first_markdown),
            },
            "result_artifacts": [
                {
                    "kind": "tau_operator_reference_json",
                    "path": "results/tau-operator-reference.json",
                    "sha256": _sha256(first_json),
                    "size_bytes": first_json.stat().st_size,
                },
                {
                    "kind": "tau_operator_reference_markdown",
                    "path": "results/tau-operator-reference.md",
                    "sha256": _sha256(first_markdown),
                    "size_bytes": first_markdown.stat().st_size,
                },
            ],
            "what_was_exercised": [
                "The wheel was installed without dependency resolution or network access.",
                "tau_coding imported from the temporary wheel installation.",
                "The installed catalog contained exactly the two locked workflows.",
                "The installed CLI ran positive and deliberately absent-workflow paths.",
                "Two positive runs produced byte-identical JSON and Markdown results.",
            ],
            "remains_unverified": [
                "Provider or model execution quality.",
                "Network-backed documentation freshness.",
                "Production deployment readiness.",
            ],
            "proof_scope": {
                "proves": [
                    (
                        "The built wheel contains and runs the locked operator-reference "
                        "contract offline."
                    ),
                    "The installed CLI fails closed when the required workflow is absent.",
                    "The installed workflow's positive result files are reproducible.",
                ],
                "does_not_prove": [
                    "Provider or model execution quality.",
                    "Network-backed documentation freshness.",
                    "Production deployment readiness.",
                ],
            },
        }
    output.write_text(json.dumps(proof, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(proof, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
