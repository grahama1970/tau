"""Read-only command workers for the Tau operator-reference workflow."""

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

from tau_coding.workflows.materialize import (
    OPERATOR_CLI_PROBE_MANIFEST,
    OPERATOR_SOURCE_PATHS,
)

NODE_RECEIPT_SCHEMA = "tau.generic_dag_node_receipt.v1"
SOURCE_SCHEMA = "tau.operator_source_collection.v1"
PROBE_SCHEMA = "tau.operator_cli_capture.v1"
DRAFT_SCHEMA = "tau.operator_reference_draft.v1"
RESULT_SCHEMA = "tau.operator_reference.v1"


def main() -> int:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)
    collect_parser = subparsers.add_parser("collect")
    _common(collect_parser)
    collect_parser.add_argument("--output", type=Path, required=True)
    capture_parser = subparsers.add_parser("capture")
    _common(capture_parser)
    capture_parser.add_argument("--output", type=Path, required=True)
    compose_parser = subparsers.add_parser("compose")
    _common(compose_parser)
    compose_parser.add_argument("--json-output", type=Path, required=True)
    compose_parser.add_argument("--markdown-output", type=Path, required=True)
    validate_parser = subparsers.add_parser("validate")
    _common(validate_parser)
    validate_parser.add_argument("--json-output", type=Path, required=True)
    validate_parser.add_argument("--markdown-output", type=Path, required=True)
    args = parser.parse_args()
    request = _read_json(args.request, label="operator reference request")
    _validate_request_contract(request)
    delay = float(args.step_delay_seconds)
    if delay < 0:
        raise RuntimeError("step_delay_seconds must be non-negative")
    if delay:
        time.sleep(delay)
    if args.command == "collect":
        _collect(request, output=args.output, receipt=args.receipt)
    elif args.command == "capture":
        _capture(request, output=args.output, receipt=args.receipt)
    elif args.command == "compose":
        _compose(
            request,
            json_output=args.json_output,
            markdown_output=args.markdown_output,
            receipt=args.receipt,
        )
    else:
        _validate(
            request,
            json_output=args.json_output,
            markdown_output=args.markdown_output,
            receipt=args.receipt,
        )
    return 0


def _common(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--request", type=Path, required=True)
    parser.add_argument("--receipt", type=Path, required=True)
    parser.add_argument("--step-delay-seconds", type=float, default=0.0)


def _collect(request: dict[str, Any], *, output: Path, receipt: Path) -> None:
    snapshot = _collect_sources(request)
    _write_json(output, snapshot)
    artifact = _artifact("operator_sources", output)
    accepted_output = {**snapshot, "artifacts": [artifact]}
    _write_json(
        receipt,
        _receipt(
            request,
            node_id="collect-operator-sources",
            status="PASS",
            artifacts=[artifact],
            accepted_output=accepted_output,
            commands_run=[f"read {path}" for path in OPERATOR_SOURCE_PATHS],
            errors=[],
            handoff="The fixed local Tau source snapshot is available.",
        ),
    )


def _capture(request: dict[str, Any], *, output: Path, receipt: Path) -> None:
    _accepted_input(SOURCE_SCHEMA)
    capture = _capture_cli(request)
    errors = _probe_errors(capture)
    if errors:
        _write_json(
            receipt,
            _receipt(
                request,
                node_id="capture-operator-cli",
                status="BLOCKED",
                artifacts=[],
                accepted_output=None,
                commands_run=_public_probe_commands(),
                errors=errors,
                handoff="A fixed public Tau CLI probe failed.",
            ),
        )
        return
    _write_json(output, capture)
    artifact = _artifact("operator_cli_capture", output)
    _write_json(
        receipt,
        _receipt(
            request,
            node_id="capture-operator-cli",
            status="PASS",
            artifacts=[artifact],
            accepted_output={**capture, "artifacts": [artifact]},
            commands_run=_public_probe_commands(),
            errors=[],
            handoff="Versioned local Tau CLI evidence is available.",
        ),
    )


def _compose(
    request: dict[str, Any],
    *,
    json_output: Path,
    markdown_output: Path,
    receipt: Path,
) -> None:
    sources = _accepted_input(SOURCE_SCHEMA)
    probes = _accepted_input(PROBE_SCHEMA)
    report = _build_report(request, sources=sources, probes=probes)
    markdown = _render_markdown(report)
    _write_json(json_output, report)
    _write_text(markdown_output, markdown)
    artifacts = [
        _artifact("operator_reference_draft_json", json_output),
        _artifact("operator_reference_draft_markdown", markdown_output),
    ]
    _write_json(
        receipt,
        _receipt(
            request,
            node_id="compose-operator-reference",
            status="PASS",
            artifacts=artifacts,
            accepted_output={
                "schema": DRAFT_SCHEMA,
                "summary": "Tau operator-reference drafts were composed under intermediate.",
                "report_sha256": _canonical_sha256(report),
                "markdown_sha256": _text_sha256(markdown),
                "artifacts": artifacts,
            },
            commands_run=["deterministic operator reference rendering"],
            errors=[],
            handoff="Drafts are ready for independent validation.",
        ),
    )


def _validate(
    request: dict[str, Any],
    *,
    json_output: Path,
    markdown_output: Path,
    receipt: Path,
) -> None:
    _accepted_input(SOURCE_SCHEMA)
    _accepted_input(PROBE_SCHEMA)
    draft = _accepted_input(DRAFT_SCHEMA)

    fresh_sources = _collect_sources(request)
    fresh_probes = _capture_cli(request)
    fresh_report = _build_report(request, sources=fresh_sources, probes=fresh_probes)
    fresh_markdown = _render_markdown(fresh_report)
    errors = _probe_errors(fresh_probes)
    if not _required_workflow_present(request, fresh_probes):
        errors.insert(0, "required_workflow_missing")
    if draft.get("report_sha256") != _canonical_sha256(fresh_report):
        errors.append("operator_reference_draft_json_mismatch")
    if draft.get("markdown_sha256") != _text_sha256(fresh_markdown):
        errors.append("operator_reference_draft_markdown_mismatch")
    draft_report, draft_markdown = _read_drafts(draft, errors=errors)
    if draft_report is not None and draft_report != fresh_report:
        errors.append("operator_reference_draft_json_mismatch")
    if draft_markdown is not None and draft_markdown != fresh_markdown:
        errors.append("operator_reference_draft_markdown_mismatch")
    if errors:
        _write_json(
            receipt,
            _receipt(
                request,
                node_id="validate-operator-reference",
                status="BLOCKED",
                artifacts=[],
                accepted_output=None,
                commands_run=[
                    *[f"read {path}" for path in OPERATOR_SOURCE_PATHS],
                    *_public_probe_commands(),
                    "deterministic operator reference rendering",
                ],
                errors=_deduplicate(errors),
                handoff="Operator-reference validation blocked publication.",
            ),
        )
        return

    _atomic_publish(
        json_output=json_output,
        markdown_output=markdown_output,
        report=fresh_report,
        markdown=fresh_markdown,
    )
    artifacts = [
        _artifact("tau_operator_reference_json", json_output),
        _artifact("tau_operator_reference_markdown", markdown_output),
    ]
    _write_json(
        receipt,
        _receipt(
            request,
            node_id="validate-operator-reference",
            status="PASS",
            artifacts=artifacts,
            accepted_output={
                "schema": RESULT_SCHEMA,
                "status": "ACCEPTED",
                "summary": fresh_report["summary"],
                "independently_recomputed": True,
                "artifacts": artifacts,
            },
            commands_run=[
                *[f"read {path}" for path in OPERATOR_SOURCE_PATHS],
                *_public_probe_commands(),
                "deterministic operator reference rendering",
                "atomic results directory publication",
            ],
            errors=[],
            handoff="The independently validated Tau operator reference is published.",
        ),
    )


def _collect_sources(request: dict[str, Any]) -> dict[str, Any]:
    repo = Path(_required_string(request, "repo_path")).resolve()
    sources: list[dict[str, Any]] = []
    for relative in OPERATOR_SOURCE_PATHS:
        path = repo / relative
        try:
            content = path.read_text(encoding="utf-8")
        except OSError as exc:
            raise RuntimeError(f"operator reference source is unavailable: {relative}") from exc
        sources.append(
            {
                "path": relative,
                "sha256": _text_sha256(content),
                "bytes": len(content.encode("utf-8")),
                "title": _source_title(relative, content),
                "headings": _markdown_headings(content) if relative.endswith(".md") else [],
            }
        )
    return {
        "schema": SOURCE_SCHEMA,
        "repo_path": str(repo),
        "source_set_version": 1,
        "source_paths": list(OPERATOR_SOURCE_PATHS),
        "sources": sources,
    }


def _capture_cli(request: dict[str, Any]) -> dict[str, Any]:
    executable = Path(_required_string(request, "tau_executable")).resolve()
    if not executable.is_file():
        raise RuntimeError(f"local tau executable is unavailable: {executable}")
    results: list[dict[str, Any]] = []
    probes = OPERATOR_CLI_PROBE_MANIFEST["probes"]
    if not isinstance(probes, list):
        raise RuntimeError("operator CLI probe manifest probes are invalid")
    for probe in probes:
        if not isinstance(probe, dict) or not isinstance(probe.get("argv"), list):
            raise RuntimeError("operator CLI probe is invalid")
        public_argv = [str(value) for value in probe["argv"]]
        actual_argv = [str(executable), *public_argv[1:]]
        completed = subprocess.run(
            actual_argv,
            check=False,
            capture_output=True,
            text=True,
            timeout=20,
            env={**os.environ, "NO_COLOR": "1", "TERM": "dumb"},
        )
        results.append(
            {
                "probe_id": probe["probe_id"],
                "public_argv": public_argv,
                "actual_argv": actual_argv,
                "output_format": probe["output_format"],
                "exit_code": completed.returncode,
                "stdout": completed.stdout,
                "stderr": completed.stderr,
                "stdout_sha256": _text_sha256(completed.stdout),
                "stderr_sha256": _text_sha256(completed.stderr),
            }
        )
    return {
        "schema": PROBE_SCHEMA,
        "manifest": OPERATOR_CLI_PROBE_MANIFEST,
        "tau_executable": str(executable),
        "results": results,
    }


def _build_report(
    request: dict[str, Any],
    *,
    sources: dict[str, Any],
    probes: dict[str, Any],
) -> dict[str, Any]:
    goal = request["goal"]
    return {
        "schema": RESULT_SCHEMA,
        "status": "ACCEPTED",
        "summary": "Tau operator reference validated from fixed local source and CLI evidence.",
        "goal": goal,
        "required_workflow": request["required_workflow"],
        "source_evidence": {
            "schema": sources["schema"],
            "source_set_version": sources["source_set_version"],
            "repo_path": sources["repo_path"],
            "sources": sources["sources"],
        },
        "cli_evidence": {
            "schema": probes["schema"],
            "manifest": probes["manifest"],
            "tau_executable": probes["tau_executable"],
            "results": probes["results"],
        },
        "proof_boundary": {
            "mocked": False,
            "live": True,
            "provider_live": False,
            "proves": [
                "The fixed local Tau source set was read without repository mutation.",
                "The versioned public CLI probes were executed through the local Tau executable.",
                "The validator independently recomputed source, CLI, and rendering evidence.",
            ],
            "does_not_prove": [
                "Provider or model quality.",
                "Networked integrations.",
                "Commands outside the fixed public probe manifest.",
            ],
        },
    }


def _render_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# Tau Operator Reference",
        "",
        f"**Status:** {report['status']}",
        "",
        f"**Goal hash:** `{report['goal']['goal_hash']}`",
        "",
        f"**Required workflow:** `{report['required_workflow']}`",
        "",
        "## Fixed Local Sources",
        "",
        "| Path | SHA-256 | Title |",
        "| --- | --- | --- |",
    ]
    for source in report["source_evidence"]["sources"]:
        lines.append(f"| `{source['path']}` | `{source['sha256']}` | {source['title']} |")
    lines.extend(["", "## Public CLI Probes", ""])
    for result in report["cli_evidence"]["results"]:
        command = " ".join(result["public_argv"])
        lines.extend(
            [
                f"### `{command}`",
                "",
                f"Exit code: `{result['exit_code']}`",
                "",
                "```text",
                result["stdout"].rstrip(),
                "```",
                "",
            ]
        )
    lines.extend(
        [
            "## Proof Boundary",
            "",
            "mocked: false  ",
            "live: true  ",
            "provider_live: false",
            "",
        ]
    )
    return "\n".join(lines)


def _probe_errors(capture: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    results = capture.get("results")
    if not isinstance(results, list):
        return ["cli_probe_results_invalid"]
    for result in results:
        if not isinstance(result, dict):
            errors.append("cli_probe_result_invalid")
            continue
        probe_id = str(result.get("probe_id", "unknown"))
        if result.get("exit_code") != 0:
            errors.append(f"cli_probe_failed:{probe_id}")
            continue
        if result.get("output_format") == "json":
            try:
                json.loads(str(result.get("stdout", "")))
            except json.JSONDecodeError:
                errors.append(f"cli_probe_invalid_json:{probe_id}")
        elif probe_id == "workflow-run-help" and "Usage:" not in str(
            result.get("stdout", "")
        ):
            errors.append("cli_probe_invalid_help:workflow-run-help")
    return errors


def _required_workflow_present(request: dict[str, Any], capture: dict[str, Any]) -> bool:
    result = _probe_result(capture, "workflow-catalog")
    try:
        payload = json.loads(str(result["stdout"]))
    except (KeyError, json.JSONDecodeError):
        return False
    workflows = payload.get("workflows") if isinstance(payload, dict) else None
    if not isinstance(workflows, list):
        return False
    required = request["required_workflow"]
    return any(
        isinstance(workflow, dict) and workflow.get("workflow_id") == required
        for workflow in workflows
    )


def _probe_result(capture: dict[str, Any], probe_id: str) -> dict[str, Any]:
    results = capture.get("results")
    if isinstance(results, list):
        for result in results:
            if isinstance(result, dict) and result.get("probe_id") == probe_id:
                return result
    return {}


def _read_drafts(
    draft: dict[str, Any],
    *,
    errors: list[str],
) -> tuple[dict[str, Any] | None, str | None]:
    json_path = _draft_artifact_path(draft, "operator_reference_draft_json")
    markdown_path = _draft_artifact_path(draft, "operator_reference_draft_markdown")
    report: dict[str, Any] | None = None
    markdown: str | None = None
    if json_path is None:
        errors.append("operator_reference_draft_json_unavailable")
    else:
        try:
            report = _read_json(json_path, label="operator reference JSON draft")
        except RuntimeError:
            errors.append("operator_reference_draft_json_unavailable")
    if markdown_path is None:
        errors.append("operator_reference_draft_markdown_unavailable")
    else:
        try:
            markdown = markdown_path.read_text(encoding="utf-8")
        except OSError:
            errors.append("operator_reference_draft_markdown_unavailable")
    return report, markdown


def _draft_artifact_path(draft: dict[str, Any], kind: str) -> Path | None:
    artifacts = draft.get("artifacts")
    if not isinstance(artifacts, list):
        return None
    for artifact in artifacts:
        if not isinstance(artifact, dict) or artifact.get("kind") != kind:
            continue
        value = artifact.get("path")
        if not isinstance(value, str):
            return None
        path = Path(value).resolve()
        if path.parent.name != "intermediate":
            return None
        return path
    return None


def _accepted_input(schema: str) -> dict[str, Any]:
    context_value = os.environ.get("TAU_GENERIC_DAG_CONTEXT")
    if not context_value:
        raise RuntimeError("TAU_GENERIC_DAG_CONTEXT is required")
    context = _read_json(Path(context_value), label="generic DAG context")
    inputs = context.get("accepted_inputs")
    if not isinstance(inputs, list):
        raise RuntimeError("generic DAG accepted_inputs is missing")
    for item in inputs:
        if not isinstance(item, dict):
            continue
        if item.get("schema") == schema:
            return item
        accepted = item.get("accepted_output")
        if isinstance(accepted, dict) and accepted.get("schema") == schema:
            return accepted
    raise RuntimeError(f"accepted input missing schema {schema}")


def _validate_request_contract(request: dict[str, Any]) -> None:
    if request.get("schema") != "tau.operator_reference_request.v1":
        raise RuntimeError("operator reference request schema is invalid")
    if request.get("source_paths") != list(OPERATOR_SOURCE_PATHS):
        raise RuntimeError("operator reference source set is invalid")
    if request.get("cli_probe_manifest") != OPERATOR_CLI_PROBE_MANIFEST:
        raise RuntimeError("operator CLI probe manifest is invalid")
    _required_string(request, "repo_path")
    _required_string(request, "required_workflow")
    _required_string(request, "tau_executable")
    _goal_hash(request)


def _receipt(
    request: dict[str, Any],
    *,
    node_id: str,
    status: str,
    artifacts: list[dict[str, str]],
    accepted_output: dict[str, Any] | None,
    commands_run: list[str],
    errors: list[str],
    handoff: str,
) -> dict[str, Any]:
    return {
        "schema": NODE_RECEIPT_SCHEMA,
        "node_id": node_id,
        "status": status,
        "verdict": status,
        "mocked": False,
        "live": True,
        "provider_live": False,
        "goal_hash": _goal_hash(request),
        "artifacts": artifacts,
        "accepted_output": accepted_output,
        "commands_run": commands_run,
        "errors": errors,
        "policy_exceptions": [],
        "side_effects": [],
        "handoff_summary": handoff,
    }


def _goal_hash(request: dict[str, Any]) -> str:
    goal = request.get("goal")
    if not isinstance(goal, dict) or not isinstance(goal.get("goal_hash"), str):
        raise RuntimeError("operator reference request goal_hash is missing")
    goal_hash = str(goal["goal_hash"])
    if len(goal_hash) != 71 or not goal_hash.startswith("sha256:"):
        raise RuntimeError("operator reference request goal_hash is invalid")
    return goal_hash


def _source_title(relative: str, content: str) -> str:
    if relative == "pyproject.toml":
        parsed = tomllib.loads(content)
        project = parsed.get("project")
        if isinstance(project, dict) and isinstance(project.get("name"), str):
            return str(project["name"])
        return "pyproject"
    headings = _markdown_headings(content)
    return headings[0] if headings else Path(relative).stem


def _markdown_headings(content: str) -> list[str]:
    headings: list[str] = []
    for line in content.splitlines():
        stripped = line.lstrip()
        if stripped.startswith("#") and " " in stripped:
            marker, _, title = stripped.partition(" ")
            if marker == "#" * len(marker) and title.strip():
                headings.append(title.strip())
    return headings


def _public_probe_commands() -> list[str]:
    probes = OPERATOR_CLI_PROBE_MANIFEST["probes"]
    if not isinstance(probes, list):
        return []
    return [
        " ".join(str(value) for value in probe["argv"])
        for probe in probes
        if isinstance(probe, dict) and isinstance(probe.get("argv"), list)
    ]


def _atomic_publish(
    *,
    json_output: Path,
    markdown_output: Path,
    report: dict[str, Any],
    markdown: str,
) -> None:
    if json_output.parent != markdown_output.parent:
        raise RuntimeError("operator reference results must share one directory")
    results_dir = json_output.parent
    if results_dir.exists():
        raise RuntimeError(f"operator reference results already exist: {results_dir}")
    staging = results_dir.with_name(f".{results_dir.name}.tmp-{os.getpid()}")
    if staging.exists():
        shutil.rmtree(staging)
    staging.mkdir(parents=True)
    try:
        _write_json(staging / json_output.name, report)
        _write_text(staging / markdown_output.name, markdown)
        os.replace(staging, results_dir)
    finally:
        if staging.exists():
            shutil.rmtree(staging)


def _artifact(kind: str, path: Path) -> dict[str, str]:
    return {"kind": kind, "path": str(path.resolve()), "sha256": _file_sha256(path)}


def _file_sha256(path: Path) -> str:
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


def _text_sha256(value: str) -> str:
    return "sha256:" + hashlib.sha256(value.encode("utf-8")).hexdigest()


def _canonical_sha256(payload: object) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def _required_string(payload: dict[str, Any], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value:
        raise RuntimeError(f"operator reference request missing {key}")
    return value


def _read_json(path: Path, *, label: str) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"{label} is unavailable: {path}") from exc
    if not isinstance(payload, dict):
        raise RuntimeError(f"{label} must be an object")
    return payload


def _write_json(path: Path, payload: object) -> None:
    _write_text(path, json.dumps(payload, indent=2, sort_keys=True) + "\n")


def _write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _deduplicate(values: list[str]) -> list[str]:
    return list(dict.fromkeys(values))


if __name__ == "__main__":
    raise SystemExit(main())
