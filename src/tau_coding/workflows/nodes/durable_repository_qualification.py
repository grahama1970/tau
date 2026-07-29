"""Deterministic nodes for durable repository qualification."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any

RECEIPT_SCHEMA = "tau.generic_dag_node_receipt.v1"


def main() -> int:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)
    for command in ("capture", "documentation", "package", "reconcile"):
        item = subparsers.add_parser(command)
        _request_receipt(item)
        item.add_argument("--output", type=Path, required=True)
        item.add_argument("--step-delay-seconds", type=float, default=0.0)
    tests = subparsers.add_parser("tests")
    _request_receipt(tests)
    tests.add_argument("--repair-packet", type=Path, required=True)
    tests.add_argument("--output", type=Path, required=True)
    tests.add_argument("--step-delay-seconds", type=float, default=0.0)
    produce = subparsers.add_parser("produce")
    _request_receipt(produce)
    subparsers.add_parser("validate-transaction")
    subparsers.add_parser("review")
    publish = subparsers.add_parser("publish")
    publish.add_argument("--request", type=Path, required=True)
    publish.add_argument("--json-output", type=Path, required=True)
    publish.add_argument("--markdown-output", type=Path, required=True)
    publish.add_argument("--ledger", type=Path, required=True)
    finalize = subparsers.add_parser("finalize")
    _request_receipt(finalize)
    finalize.add_argument("--json-output", type=Path, required=True)
    args = parser.parse_args()

    if args.command == "validate-transaction":
        _validate_transaction()
        return 0
    if args.command == "review":
        _review()
        return 0
    request = _request(args.request)
    if hasattr(args, "step_delay_seconds"):
        if args.step_delay_seconds < 0:
            raise RuntimeError("step_delay_seconds must be non-negative")
        tests_failure_delay = (
            args.command == "tests"
            and request.get("inject_test_branch_failure") is True
            and not args.repair_packet.is_file()
        )
        delay_factors = {
            "documentation": 0.5,
            "package": 0.75,
            "tests": 150.0 if tests_failure_delay else 1.25,
            "reconcile": 0.5,
        }
        time.sleep(args.step_delay_seconds * delay_factors.get(args.command, 1.0))
    if args.command == "capture":
        _capture(request, args.output, args.receipt)
    elif args.command == "documentation":
        _qualify_paths(
            request,
            args.output,
            args.receipt,
            node_id="qualify-documentation",
            schema="tau.documentation_qualification.v1",
            path_filter=lambda path: (
                path.lower().endswith((".md", ".rst", ".txt")) or "docs/" in path.lower()
            ),
        )
    elif args.command == "tests":
        _qualify_tests(request, args.repair_packet, args.output, args.receipt)
    elif args.command == "package":
        _qualify_paths(
            request,
            args.output,
            args.receipt,
            node_id="qualify-package",
            schema="tau.package_qualification.v1",
            path_filter=lambda path: (
                Path(path).name in {"pyproject.toml", "package.json", "Cargo.toml", "go.mod"}
            ),
        )
    elif args.command == "reconcile":
        _reconcile(request, args.output, args.receipt)
    elif args.command == "produce":
        _produce(request, args.receipt)
    elif args.command == "publish":
        _publish(request, args.json_output, args.markdown_output, args.ledger)
    else:
        _finalize(request, args.json_output, args.receipt)
    return 0


def _request_receipt(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--request", type=Path, required=True)
    parser.add_argument("--receipt", type=Path, required=True)


def _capture(request: dict[str, Any], output: Path, receipt: Path) -> None:
    repo = Path(request["repo_path"])
    root = _git(repo, "rev-parse", "--show-toplevel").strip()
    tracked = sorted(line for line in _git(repo, "ls-files", "-z").split("\0") if line)
    payload = {
        "schema": "tau.durable_repository_capture.v1",
        "repo_path": root,
        "head_sha": _git(repo, "rev-parse", "HEAD").strip(),
        "branch": _git(repo, "branch", "--show-current").strip(),
        "tracked_paths": tracked,
        "tracked_path_count": len(tracked),
        "summary": "Repository identity and tracked-file snapshot captured.",
    }
    _accept_file(request, receipt, "capture-repository", output, payload, "repository_capture")


def _qualify_paths(
    request: dict[str, Any],
    output: Path,
    receipt: Path,
    *,
    node_id: str,
    schema: str,
    path_filter: Any,
) -> None:
    capture = _accepted("tau.durable_repository_capture.v1")
    matches = [path for path in capture["tracked_paths"] if path_filter(path)]
    payload = {
        "schema": schema,
        "status": "PASS",
        "head_sha": capture["head_sha"],
        "matching_paths": matches[:200],
        "matching_path_count": len(matches),
        "summary": f"{node_id} completed against the accepted repository snapshot.",
    }
    _accept_file(request, receipt, node_id, output, payload, node_id.replace("qualify-", ""))


def _qualify_tests(
    request: dict[str, Any], repair_packet: Path, output: Path, receipt: Path
) -> None:
    capture = _accepted("tau.durable_repository_capture.v1")
    repaired = False
    if request["inject_test_branch_failure"]:
        if not repair_packet.is_file():
            _node_receipt(
                receipt,
                request,
                "qualify-tests",
                None,
                [],
                status="BLOCKED",
                errors=["targeted_repair_required"],
            )
            return
        packet = _read_json(repair_packet, "targeted repair packet")
        expected = {
            "schema": "tau.workflow_repair_packet.v1",
            "authorized": True,
            "node_id": "qualify-tests",
            "goal_hash": request["goal"]["goal_hash"],
            "request_sha256": request["request_sha256"],
        }
        if any(packet.get(key) != value for key, value in expected.items()):
            _node_receipt(
                receipt,
                request,
                "qualify-tests",
                None,
                [],
                status="BLOCKED",
                errors=["targeted_repair_packet_mismatch"],
            )
            return
        repaired = True
    test_paths = [
        path
        for path in capture["tracked_paths"]
        if "test" in Path(path).name.lower() or "/tests/" in f"/{path.lower()}"
    ]
    payload = {
        "schema": "tau.test_qualification.v1",
        "status": "REPAIRED" if repaired else "PASS",
        "head_sha": capture["head_sha"],
        "test_paths": test_paths[:200],
        "test_path_count": len(test_paths),
        "repair_applied": repaired,
        "summary": (
            "Test qualification branch was repaired and accepted."
            if repaired
            else "Test qualification completed against the accepted repository snapshot."
        ),
    }
    _accept_file(request, receipt, "qualify-tests", output, payload, "test_qualification")


def _reconcile(request: dict[str, Any], output: Path, receipt: Path) -> None:
    documentation = _accepted("tau.documentation_qualification.v1")
    tests = _accepted("tau.test_qualification.v1")
    package = _accepted("tau.package_qualification.v1")
    head_shas = {documentation["head_sha"], tests["head_sha"], package["head_sha"]}
    if len(head_shas) != 1:
        raise RuntimeError("qualification_branch_head_mismatch")
    payload = {
        "schema": "tau.reconciled_repository_qualification.v1",
        "status": "QUALIFIED",
        "goal": request["goal"],
        "repository": {"path": request["repo_path"], "head_sha": head_shas.pop()},
        "branches": {
            "documentation": documentation,
            "tests": tests,
            "package": package,
        },
        "summary": "Accepted repository qualification branches were reconciled.",
    }
    _accept_file(
        request,
        receipt,
        "reconcile-qualification",
        output,
        payload,
        "reconciled_qualification",
    )


def _produce(request: dict[str, Any], receipt: Path) -> None:
    context, context_path = _transaction_context("TAU_GENERIC_DAG_CONTEXT")
    qualification = _accepted_from_items(
        context["accepted_inputs"], "tau.reconciled_repository_qualification.v1"
    )
    work_order = _read_json(Path(context["work_order"]["path"]), "publication work order")
    root = Path(work_order["artifact_root"])
    json_path = root / "durable-repository-qualification.json"
    markdown_path = root / "durable-repository-qualification.md"
    report = {
        "schema": "tau.durable_repository_qualification.v1",
        "status": "QUALIFIED",
        "goal": request["goal"],
        "repository": qualification["repository"],
        "branches": qualification["branches"],
        "publish_path": request["publish_path"],
        "summary": "Repository qualification published after durable recovery and approval.",
        "proof_scope": {
            "proves": [
                "The named repository snapshot was qualified by three accepted local branches.",
                "The exact accepted qualification was bound to human approval.",
            ],
            "does_not_prove": [
                "The repository test suite passes.",
                "Provider or model quality.",
                "Production deployment readiness.",
            ],
        },
    }
    _write_json(json_path, report)
    markdown_path.parent.mkdir(parents=True, exist_ok=True)
    markdown_path.write_text(
        "# Durable Repository Qualification\n\n"
        f"**Status:** {report['status']}\n\n{report['summary']}\n",
        encoding="utf-8",
    )
    _candidate_manifest(context, context_path, [json_path, markdown_path])
    _producer_receipt(receipt, context, request)


def _validate_transaction() -> None:
    context, _ = _transaction_context("TAU_GENERIC_DAG_VALIDATION_CONTEXT")
    _write_json(
        Path(context["output_contract"]["validation_receipt_path"]),
        {
            "schema": "tau.generic_artifact_validation.v1",
            "status": "PASS",
            "node_id": context["node_id"],
            "transaction_id": context["transaction_id"],
            "attempt": context["attempt"],
            "validator_id": context["validator_id"],
            "validation_context_sha256": os.environ["TAU_GENERIC_DAG_VALIDATION_CONTEXT_SHA256"],
            "candidate_manifest_sha256": context["candidate_manifest_sha256"],
        },
    )


def _review() -> None:
    context, _ = _transaction_context("TAU_GENERIC_DAG_REVIEW_CONTEXT")
    findings = _review_findings(context)
    verdict = "PASS" if not findings else "BLOCKED"
    _write_json(
        Path(context["output_contract"]["review_feedback_path"]),
        {
            "schema": "tau.generic_artifact_review.v1",
            "transaction_id": context["transaction_id"],
            "node_id": context["node_id"],
            "attempt": context["attempt"],
            "producer_id": context["producer_id"],
            "reviewer_id": context["reviewer_id"],
            "review_context_sha256": os.environ["TAU_GENERIC_DAG_REVIEW_CONTEXT_SHA256"],
            "candidate_manifest_sha256": context["candidate_manifest_sha256"],
            "goal_hash": context["goal_hash"],
            "verdict": verdict,
            "mocked": False,
            "live": True,
            "provider_live": False,
            "summary": (
                "Qualification artifacts accepted for exact human approval."
                if verdict == "PASS"
                else "Qualification artifacts failed deterministic review."
            ),
            "findings": findings,
        },
    )


def _review_findings(context: dict[str, Any]) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    artifacts = context.get("validated_artifacts")
    if not isinstance(artifacts, list):
        return [
            _finding(
                "validated_artifacts_missing",
                ["qualification_json", "qualification_markdown"],
            )
        ]
    by_kind = {str(item.get("kind")): item for item in artifacts if isinstance(item, dict)}
    json_artifact = by_kind.get("qualification_json")
    markdown_artifact = by_kind.get("qualification_markdown")
    if not isinstance(json_artifact, dict):
        findings.append(_finding("qualification_json_missing", ["qualification_json"]))
    if not isinstance(markdown_artifact, dict):
        findings.append(_finding("qualification_markdown_missing", ["qualification_markdown"]))
    if json_artifact is not None:
        report = _read_artifact_json(json_artifact, findings)
        if report is not None:
            _check_report(report, context, findings)
    if markdown_artifact is not None:
        text = _read_artifact_text(markdown_artifact, findings)
        if text is not None and "Durable Repository Qualification" not in text:
            findings.append(
                _finding(
                    "qualification_markdown_title_missing",
                    ["qualification_markdown"],
                )
            )
    return findings


def _read_artifact_json(
    artifact: dict[str, Any], findings: list[dict[str, Any]]
) -> dict[str, Any] | None:
    path = _artifact_path(artifact, findings)
    if path is None:
        return None
    if _artifact_hash(path, artifact, findings) is None:
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        findings.append(_finding("qualification_json_invalid", ["qualification_json"]))
        return None
    if not isinstance(payload, dict):
        findings.append(_finding("qualification_json_not_object", ["qualification_json"]))
        return None
    return payload


def _read_artifact_text(artifact: dict[str, Any], findings: list[dict[str, Any]]) -> str | None:
    path = _artifact_path(artifact, findings)
    if path is None:
        return None
    if _artifact_hash(path, artifact, findings) is None:
        return None
    return path.read_text(encoding="utf-8")


def _artifact_path(artifact: dict[str, Any], findings: list[dict[str, Any]]) -> Path | None:
    path_value = artifact.get("path")
    artifact_id = str(artifact.get("artifact_id") or artifact.get("kind") or "artifact")
    if not isinstance(path_value, str) or not path_value.strip():
        findings.append(_finding("artifact_path_missing", [artifact_id]))
        return None
    path = Path(path_value)
    if not path.is_file():
        findings.append(_finding("artifact_file_missing", [artifact_id]))
        return None
    return path


def _artifact_hash(
    path: Path, artifact: dict[str, Any], findings: list[dict[str, Any]]
) -> str | None:
    artifact_id = str(artifact.get("artifact_id") or artifact.get("kind") or "artifact")
    expected = artifact.get("sha256")
    actual = _sha256(path)
    if expected != actual:
        findings.append(_finding("artifact_sha256_mismatch", [artifact_id]))
        return None
    return actual


def _check_report(
    report: dict[str, Any],
    context: dict[str, Any],
    findings: list[dict[str, Any]],
) -> None:
    expected = {
        "schema": "tau.durable_repository_qualification.v1",
        "status": "QUALIFIED",
    }
    for key, value in expected.items():
        if report.get(key) != value:
            findings.append(_finding(f"qualification_{key}_invalid", ["qualification_json"]))
    goal = report.get("goal")
    if not isinstance(goal, dict) or goal.get("goal_hash") != context.get("goal_hash"):
        findings.append(_finding("qualification_goal_hash_mismatch", ["qualification_json"]))
    repository = report.get("repository")
    if not isinstance(repository, dict) or not repository.get("head_sha"):
        findings.append(_finding("qualification_repository_missing", ["qualification_json"]))
    branches = report.get("branches")
    if not isinstance(branches, dict) or not branches:
        findings.append(_finding("qualification_branches_missing", ["qualification_json"]))


def _finding(reason: str, artifact_ids: list[str]) -> dict[str, Any]:
    return {
        "severity": "BLOCK",
        "reason": reason,
        "artifact_ids": artifact_ids,
    }


def _publish(
    request: dict[str, Any], json_output: Path, markdown_output: Path, ledger_path: Path
) -> None:
    context = _read_json(Path(os.environ["TAU_GENERIC_DAG_CONTEXT"]), "continuation")
    artifacts = context.get("artifacts")
    if not isinstance(artifacts, list) or len(artifacts) != 2:
        raise RuntimeError("qualification publication requires two artifacts")
    by_kind = {item["kind"]: item for item in artifacts if isinstance(item, dict)}
    source_json = Path(by_kind["qualification_json"]["path"])
    source_markdown = Path(by_kind["qualification_markdown"]["path"])
    publish_path = Path(request["publish_path"])
    idempotency_key = hashlib.sha256(
        (request["request_sha256"] + _sha256(source_json) + _sha256(source_markdown)).encode()
    ).hexdigest()
    ledger = {
        "schema": "tau.qualification_publication_ledger.v1",
        "status": "COMMITTED",
        "idempotency_key": idempotency_key,
        "effect_count": 1,
        "publish_path": str(publish_path),
        "artifacts": {
            json_output.name: _sha256(source_json),
            markdown_output.name: _sha256(source_markdown),
        },
    }
    embedded_ledger = publish_path / "publication-ledger.json"
    if publish_path.exists():
        existing = _read_json(embedded_ledger, "embedded publication ledger")
        if existing != ledger:
            raise RuntimeError("publication_idempotency_conflict")
        _verify_published(publish_path, ledger)
    else:
        stage = publish_path.with_name(f".{publish_path.name}.tmp-{os.getpid()}")
        stage.mkdir(parents=True)
        try:
            shutil.copy2(source_json, stage / json_output.name)
            shutil.copy2(source_markdown, stage / markdown_output.name)
            _write_json(stage / "publication-ledger.json", ledger)
            os.replace(stage, publish_path)
        finally:
            shutil.rmtree(stage, ignore_errors=True)
    _write_json(ledger_path, ledger)
    _ensure_results(source_json, source_markdown, json_output, markdown_output)


def _verify_published(publish_path: Path, ledger: dict[str, Any]) -> None:
    for name, expected in ledger["artifacts"].items():
        path = publish_path / name
        if not path.is_file() or _sha256(path) != expected:
            raise RuntimeError("published_qualification_hash_mismatch")


def _ensure_results(
    source_json: Path, source_markdown: Path, json_output: Path, markdown_output: Path
) -> None:
    if json_output.parent.exists():
        if (
            not json_output.is_file()
            or not markdown_output.is_file()
            or _sha256(json_output) != _sha256(source_json)
            or _sha256(markdown_output) != _sha256(source_markdown)
        ):
            raise RuntimeError("qualification_result_hash_mismatch")
        return
    stage = json_output.parent.with_name(f".{json_output.parent.name}.tmp-{os.getpid()}")
    stage.mkdir(parents=True)
    try:
        shutil.copy2(source_json, stage / json_output.name)
        shutil.copy2(source_markdown, stage / markdown_output.name)
        os.replace(stage, json_output.parent)
    finally:
        shutil.rmtree(stage, ignore_errors=True)


def _finalize(request: dict[str, Any], json_output: Path, receipt: Path) -> None:
    report = _read_json(json_output, "durable qualification result")
    artifacts = [
        _artifact("durable_qualification_json", json_output),
        _artifact("durable_qualification_markdown", json_output.with_suffix(".md")),
    ]
    accepted = {
        "schema": report["schema"],
        "status": report["status"],
        "summary": report["summary"],
        "publish_path": report["publish_path"],
        "artifacts": artifacts,
    }
    _node_receipt(receipt, request, "finalize-qualification", accepted, artifacts)


def _accept_file(
    request: dict[str, Any],
    receipt: Path,
    node_id: str,
    output: Path,
    payload: dict[str, Any],
    kind: str,
) -> None:
    _write_json(output, payload)
    artifact = _artifact(kind, output)
    _node_receipt(receipt, request, node_id, payload | {"artifacts": [artifact]}, [artifact])


def _candidate_manifest(context: dict[str, Any], context_path: Path, paths: list[Path]) -> None:
    kinds = ("qualification_json", "qualification_markdown")
    media = ("application/json", "text/markdown")
    _write_json(
        Path(context["output_contract"]["candidate_manifest_path"]),
        {
            "schema": "tau.media_artifact_manifest.v1",
            "transaction_id": context["transaction_id"],
            "node_id": context["node_id"],
            "attempt": context["attempt"],
            "producer_id": context["producer_id"],
            "work_order_sha256": context["work_order"]["sha256"],
            "attempt_context_sha256": _sha256(context_path),
            "artifacts": [
                {
                    "artifact_id": kind,
                    "kind": kind,
                    "media_type": media_type,
                    "path": str(path),
                    "sha256": _sha256(path),
                    "bytes": path.stat().st_size,
                }
                for path, kind, media_type in zip(paths, kinds, media, strict=True)
            ],
        },
    )


def _producer_receipt(path: Path, context: dict[str, Any], request: dict[str, Any]) -> None:
    _write_json(
        path,
        {
            "schema": RECEIPT_SCHEMA,
            "node_id": context["node_id"],
            "status": "PASS",
            "verdict": "PASS",
            "mocked": False,
            "live": True,
            "provider_live": False,
            "goal_hash": request["goal"]["goal_hash"],
            "artifacts": [],
            "commands_run": ["deterministic qualification producer"],
            "errors": [],
            "policy_exceptions": [],
            "handoff_summary": "Qualification candidate produced for validation.",
            "work_order_sha256": context["work_order"]["sha256"],
        },
    )


def _node_receipt(
    path: Path,
    request: dict[str, Any],
    node_id: str,
    accepted_output: dict[str, Any] | None,
    artifacts: list[dict[str, str]],
    *,
    status: str = "PASS",
    errors: list[str] | None = None,
) -> None:
    values = errors or []
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
            "goal_hash": request["goal"]["goal_hash"],
            "artifacts": artifacts,
            "accepted_output": accepted_output,
            "commands_run": [f"durable-qualification:{node_id}"],
            "errors": values,
            "policy_exceptions": [],
            "side_effects": [],
            "handoff_summary": (
                f"{node_id} accepted output is available."
                if status == "PASS"
                else f"{node_id} blocked: {values[0]}"
            ),
        },
    )


def _accepted(schema: str) -> dict[str, Any]:
    context, _ = _transaction_context("TAU_GENERIC_DAG_CONTEXT")
    return _accepted_from_items(context.get("accepted_inputs"), schema)


def _accepted_from_items(items: Any, schema: str) -> dict[str, Any]:
    if not isinstance(items, list):
        raise RuntimeError("generic DAG accepted inputs are missing")
    for item in items:
        if isinstance(item, dict):
            accepted = item.get("accepted_output")
            candidate = accepted if isinstance(accepted, dict) else item
            if candidate.get("schema") == schema:
                return candidate
    raise RuntimeError(f"accepted input missing schema {schema}")


def _transaction_context(name: str) -> tuple[dict[str, Any], Path]:
    value = os.environ.get(name)
    if not value:
        raise RuntimeError(f"{name} is required")
    path = Path(value)
    return _read_json(path, name), path


def _request(path: Path) -> dict[str, Any]:
    payload = _read_json(path, "durable qualification request")
    if payload.get("schema") != "tau.durable_repository_qualification_request.v1":
        raise RuntimeError("durable qualification request schema is invalid")
    return payload


def _git(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(repo), *args], capture_output=True, text=True, check=False
    )
    if result.returncode:
        raise RuntimeError(f"git {' '.join(args)} failed: {result.stderr.strip()}")
    return result.stdout


def _artifact(kind: str, path: Path) -> dict[str, str]:
    return {"kind": kind, "path": str(path), "sha256": _sha256(path)}


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _read_json(path: Path, label: str) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"{label} is unavailable: {exc}") from exc
    if not isinstance(payload, dict):
        raise RuntimeError(f"{label} must be an object")
    return payload


def _write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


if __name__ == "__main__":
    raise SystemExit(main())
