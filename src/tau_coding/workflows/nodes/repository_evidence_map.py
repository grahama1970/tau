"""Read-only workers for the repository evidence-map workflow."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import time
import tomllib
from pathlib import Path
from typing import Any

RECEIPT_SCHEMA = "tau.generic_dag_node_receipt.v1"
INVENTORY_SCHEMA = "tau.repository_inventory.v1"
DOCUMENTATION_SCHEMA = "tau.repository_documentation_analysis.v1"
TEST_SCHEMA = "tau.repository_test_analysis.v1"
PACKAGE_SCHEMA = "tau.repository_package_analysis.v1"
RESULT_SCHEMA = "tau.repository_evidence_map.v1"


def main() -> int:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)
    for command in ("inventory", "documentation", "tests", "package"):
        command_parser = subparsers.add_parser(command)
        _common(command_parser)
        command_parser.add_argument("--output", type=Path, required=True)
    publish_parser = subparsers.add_parser("publish")
    _common(publish_parser)
    publish_parser.add_argument("--json-output", type=Path, required=True)
    publish_parser.add_argument("--markdown-output", type=Path, required=True)
    args = parser.parse_args()
    request = _read_json(args.request, "repository evidence-map request")
    _validate_request(request)
    if args.step_delay_seconds < 0:
        raise RuntimeError("step_delay_seconds must be non-negative")
    if args.step_delay_seconds:
        time.sleep(args.step_delay_seconds)
    if args.command == "inventory":
        _inventory(request, args.output, args.receipt)
    elif args.command == "documentation":
        _documentation(request, args.output, args.receipt)
    elif args.command == "tests":
        _tests(request, args.output, args.receipt)
    elif args.command == "package":
        _package(request, args.output, args.receipt)
    else:
        _publish(request, args.json_output, args.markdown_output, args.receipt)
    return 0


def _common(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--request", type=Path, required=True)
    parser.add_argument("--receipt", type=Path, required=True)
    parser.add_argument("--step-delay-seconds", type=float, default=0.0)


def _inventory(request: dict[str, Any], output: Path, receipt: Path) -> None:
    repo = Path(request["repo_path"])
    git_root = _git(repo, "rev-parse", "--show-toplevel").strip()
    head = _git(repo, "rev-parse", "HEAD").strip()
    tracked = sorted(value for value in _git(repo, "ls-files", "-z").split("\0") if value)
    inventory_basis = {"git_root": git_root, "head_sha": head, "tracked_files": tracked}
    inventory = {
        "schema": INVENTORY_SCHEMA,
        **inventory_basis,
        "tracked_file_count": len(tracked),
        "inventory_sha256": _canonical_sha256(inventory_basis),
    }
    _write_json(output, inventory)
    artifact = _artifact("repository_inventory", output)
    _write_receipt(
        receipt,
        request,
        "inventory-repository",
        "PASS",
        {
            **inventory,
            "summary": f"Inventoried {len(tracked)} tracked files.",
            "artifacts": [artifact],
        },
        [artifact],
        ["git rev-parse --show-toplevel", "git rev-parse HEAD", "git ls-files -z"],
        [],
    )


def _documentation(request: dict[str, Any], output: Path, receipt: Path) -> None:
    inventory = _accepted(INVENTORY_SCHEMA)
    repo = Path(request["repo_path"])
    files = [
        path
        for path in inventory["tracked_files"]
        if isinstance(path, str)
        and path.lower().endswith(".md")
        and (path == "README.md" or path.startswith("docs/"))
    ]
    entries = []
    for relative in files:
        content = (repo / relative).read_text(encoding="utf-8")
        entries.append(
            {
                "path": relative,
                "sha256": _text_sha256(content),
                "headings": _headings(content),
            }
        )
    analysis = {
        "schema": DOCUMENTATION_SCHEMA,
        "inventory_sha256": inventory["inventory_sha256"],
        "document_count": len(entries),
        "documents": entries,
    }
    _analysis_pass(
        request, output, receipt, "analyze-documentation", analysis, "documentation_analysis"
    )


def _tests(request: dict[str, Any], output: Path, receipt: Path) -> None:
    inventory = _accepted(INVENTORY_SCHEMA)
    files = [
        path
        for path in inventory["tracked_files"]
        if isinstance(path, str)
        and (path.startswith("tests/") or Path(path).name.startswith("test_"))
    ]
    if request["require_tests"] and not files:
        _write_receipt(
            receipt,
            request,
            "analyze-tests",
            "BLOCKED",
            None,
            [],
            ["deterministic test-surface analysis"],
            ["test_surface_missing"],
        )
        return
    analysis = {
        "schema": TEST_SCHEMA,
        "inventory_sha256": inventory["inventory_sha256"],
        "test_file_count": len(files),
        "test_files": files,
        "required": request["require_tests"],
    }
    _analysis_pass(request, output, receipt, "analyze-tests", analysis, "test_analysis")


def _package(request: dict[str, Any], output: Path, receipt: Path) -> None:
    inventory = _accepted(INVENTORY_SCHEMA)
    repo = Path(request["repo_path"])
    tracked = inventory["tracked_files"]
    present = isinstance(tracked, list) and "pyproject.toml" in tracked
    metadata: dict[str, Any] = {"name": None, "version": None, "scripts": []}
    if present:
        payload = tomllib.loads((repo / "pyproject.toml").read_text(encoding="utf-8"))
        project = payload.get("project")
        if isinstance(project, dict):
            metadata = {
                "name": project.get("name") if isinstance(project.get("name"), str) else None,
                "version": (
                    project.get("version") if isinstance(project.get("version"), str) else None
                ),
                "scripts": sorted(project.get("scripts", {}))
                if isinstance(project.get("scripts"), dict)
                else [],
            }
    analysis = {
        "schema": PACKAGE_SCHEMA,
        "inventory_sha256": inventory["inventory_sha256"],
        "pyproject_present": present,
        **metadata,
    }
    _analysis_pass(request, output, receipt, "analyze-package", analysis, "package_analysis")


def _analysis_pass(
    request: dict[str, Any],
    output: Path,
    receipt: Path,
    node_id: str,
    analysis: dict[str, Any],
    artifact_kind: str,
) -> None:
    _write_json(output, analysis)
    artifact = _artifact(artifact_kind, output)
    summary = {
        "analyze-documentation": "Documentation surface analyzed.",
        "analyze-tests": "Test surface analyzed.",
        "analyze-package": "Package metadata analyzed.",
    }[node_id]
    _write_receipt(
        receipt,
        request,
        node_id,
        "PASS",
        {**analysis, "summary": summary, "artifacts": [artifact]},
        [artifact],
        [f"deterministic {artifact_kind.replace('_', ' ')}"],
        [],
    )


def _publish(
    request: dict[str, Any], json_output: Path, markdown_output: Path, receipt: Path
) -> None:
    inventory = _accepted(INVENTORY_SCHEMA)
    documentation = _accepted(DOCUMENTATION_SCHEMA)
    tests = _accepted(TEST_SCHEMA)
    package = _accepted(PACKAGE_SCHEMA)
    inventory_hash = inventory.get("inventory_sha256")
    if not isinstance(inventory_hash, str) or any(
        branch.get("inventory_sha256") != inventory_hash
        for branch in (documentation, tests, package)
    ):
        _write_receipt(
            receipt,
            request,
            "publish-evidence-map",
            "BLOCKED",
            None,
            [],
            ["validate accepted branch inventory bindings"],
            ["branch_inventory_hash_mismatch"],
        )
        return
    report = {
        "schema": RESULT_SCHEMA,
        "status": "ACCEPTED",
        "goal": request["goal"],
        "repository": {
            "path": inventory["git_root"],
            "head_sha": inventory["head_sha"],
            "tracked_file_count": inventory["tracked_file_count"],
            "inventory_sha256": inventory_hash,
        },
        "documentation": documentation,
        "tests": tests,
        "package": package,
        "summary": "Repository evidence map validated from three concurrent analyses.",
        "proof_scope": {
            "proves": [
                "The named Git repository was inventoried with fixed read-only commands.",
                "Documentation, test, and package surfaces share one accepted inventory hash.",
            ],
            "does_not_prove": [
                "The repository test suite passes.",
                "Documentation is semantically correct.",
                "Provider or model quality.",
                "Production deployment readiness.",
            ],
        },
    }
    markdown = _render_markdown(report)
    _atomic_publish(json_output, markdown_output, report, markdown)
    artifacts = [
        _artifact("repository_evidence_map_json", json_output),
        _artifact("repository_evidence_map_markdown", markdown_output),
    ]
    _write_receipt(
        receipt,
        request,
        "publish-evidence-map",
        "PASS",
        {
            "schema": RESULT_SCHEMA,
            "status": report["status"],
            "summary": report["summary"],
            "inventory_sha256": inventory_hash,
            "artifacts": artifacts,
        },
        artifacts,
        ["validate three accepted branch outputs", "atomic result publication"],
        [],
    )


def _render_markdown(report: dict[str, Any]) -> str:
    repository = report["repository"]
    documentation = report["documentation"]
    tests = report["tests"]
    package = report["package"]
    return "\n".join(
        [
            "# Repository Evidence Map",
            "",
            f"**Status:** {report['status']}",
            f"**Repository:** `{repository['path']}`",
            f"**HEAD:** `{repository['head_sha']}`",
            f"**Tracked files:** {repository['tracked_file_count']}",
            "",
            "## Surfaces",
            "",
            f"- Documentation files: {documentation['document_count']}",
            f"- Test files: {tests['test_file_count']}",
            f"- Package: {package['name'] or 'not declared'}",
            "",
            str(report["summary"]),
            "",
        ]
    )


def _accepted(schema: str) -> dict[str, Any]:
    context_path = os.environ.get("TAU_GENERIC_DAG_CONTEXT")
    if not context_path:
        raise RuntimeError("TAU_GENERIC_DAG_CONTEXT is required")
    context = _read_json(Path(context_path), "generic DAG context")
    inputs = context.get("accepted_inputs")
    if not isinstance(inputs, list):
        raise RuntimeError("generic DAG accepted_inputs is missing")
    for item in inputs:
        if isinstance(item, dict):
            accepted = item.get("accepted_output")
            candidate = accepted if isinstance(accepted, dict) else item
            if candidate.get("schema") == schema:
                return candidate
    raise RuntimeError(f"accepted input missing schema {schema}")


def _write_receipt(
    path: Path,
    request: dict[str, Any],
    node_id: str,
    status: str,
    accepted_output: dict[str, Any] | None,
    artifacts: list[dict[str, str]],
    commands: list[str],
    errors: list[str],
) -> None:
    _write_json(
        path,
        {
            "schema": RECEIPT_SCHEMA,
            "node_id": node_id,
            "status": status,
            "verdict": status,
            "mocked": False,
            "live": True,
            "provider_live": False,
            "goal_hash": _goal_hash(request),
            "artifacts": artifacts,
            "accepted_output": accepted_output,
            "commands_run": commands,
            "errors": errors,
            "policy_exceptions": [],
            "side_effects": [],
            "handoff_summary": (
                f"{node_id} accepted output is available."
                if status == "PASS"
                else f"{node_id} blocked: {errors[0]}"
            ),
        },
    )


def _validate_request(request: dict[str, Any]) -> None:
    if request.get("schema") != "tau.repository_evidence_map_request.v1":
        raise RuntimeError("repository evidence-map request schema is invalid")
    if not isinstance(request.get("repo_path"), str) or not request["repo_path"]:
        raise RuntimeError("repository evidence-map repo_path is invalid")
    if not isinstance(request.get("human_goal"), str) or not request["human_goal"]:
        raise RuntimeError("repository evidence-map human_goal is invalid")
    if not isinstance(request.get("require_tests"), bool):
        raise RuntimeError("repository evidence-map require_tests is invalid")
    _goal_hash(request)


def _goal_hash(request: dict[str, Any]) -> str:
    goal = request.get("goal")
    value = goal.get("goal_hash") if isinstance(goal, dict) else None
    if not isinstance(value, str) or not value.startswith("sha256:") or len(value) != 71:
        raise RuntimeError("repository evidence-map goal_hash is invalid")
    return value


def _git(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(repo), *args],
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode:
        raise RuntimeError(f"git {' '.join(args)} failed: {result.stderr.strip()}")
    return result.stdout


def _headings(content: str) -> list[str]:
    values = []
    for line in content.splitlines():
        stripped = line.lstrip()
        marker, separator, title = stripped.partition(" ")
        if separator and marker.startswith("#") and marker == "#" * len(marker) and title.strip():
            values.append(title.strip())
    return values


def _atomic_publish(
    json_output: Path,
    markdown_output: Path,
    report: dict[str, Any],
    markdown: str,
) -> None:
    if json_output.parent != markdown_output.parent:
        raise RuntimeError("repository evidence-map results must share one directory")
    results_dir = json_output.parent
    if results_dir.exists():
        raise RuntimeError(f"repository evidence-map results already exist: {results_dir}")
    staging = results_dir.with_name(f".{results_dir.name}.tmp-{os.getpid()}")
    if staging.exists():
        shutil.rmtree(staging)
    staging.mkdir(parents=True)
    try:
        _write_json(staging / json_output.name, report)
        (staging / markdown_output.name).write_text(markdown, encoding="utf-8")
        os.replace(staging, results_dir)
    finally:
        if staging.exists():
            shutil.rmtree(staging)


def _artifact(kind: str, path: Path) -> dict[str, str]:
    return {
        "kind": kind,
        "path": str(path.resolve()),
        "sha256": "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest(),
    }


def _canonical_sha256(payload: object) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def _text_sha256(value: str) -> str:
    return "sha256:" + hashlib.sha256(value.encode()).hexdigest()


def _read_json(path: Path, label: str) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"{label} is unavailable: {path}") from exc
    if not isinstance(payload, dict):
        raise RuntimeError(f"{label} must be an object")
    return payload


def _write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


if __name__ == "__main__":
    raise SystemExit(main())
