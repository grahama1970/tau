"""Deterministic workers for the approved release-bundle workflow."""

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
    for command in ("prepare", "manifest", "policy", "assemble"):
        item = subparsers.add_parser(command)
        _request_receipt(item)
        item.add_argument("--output", type=Path, required=True)
        item.add_argument("--step-delay-seconds", type=float, default=0.0)
    for command in ("produce-notes", "produce-bundle"):
        item = subparsers.add_parser(command)
        _request_receipt(item)
    subparsers.add_parser("validate-transaction")
    subparsers.add_parser("review-notes")
    subparsers.add_parser("review-bundle")
    publish = subparsers.add_parser("publish")
    publish.add_argument("--request", type=Path, required=True)
    publish.add_argument("--json-output", type=Path, required=True)
    publish.add_argument("--markdown-output", type=Path, required=True)
    publish.add_argument("--rollback-receipt", type=Path, required=True)
    finalize = subparsers.add_parser("finalize")
    _request_receipt(finalize)
    finalize.add_argument("--json-output", type=Path, required=True)
    args = parser.parse_args()

    if args.command == "validate-transaction":
        _validate_transaction()
        return 0
    if args.command in {"review-notes", "review-bundle"}:
        _review(revise_first=args.command == "review-notes")
        return 0
    request = _request(args.request)
    if args.command in {"prepare", "manifest", "policy", "assemble"}:
        if args.step_delay_seconds < 0:
            raise RuntimeError("step_delay_seconds must be non-negative")
        if args.step_delay_seconds:
            time.sleep(args.step_delay_seconds)
    if args.command == "prepare":
        _prepare(request, args.output, args.receipt)
    elif args.command == "manifest":
        _manifest(request, args.output, args.receipt)
    elif args.command == "policy":
        _policy(request, args.output, args.receipt)
    elif args.command == "assemble":
        _assemble(request, args.output, args.receipt)
    elif args.command == "produce-notes":
        _produce_notes(request, args.receipt)
    elif args.command == "produce-bundle":
        _produce_bundle(request, args.receipt)
    elif args.command == "publish":
        _publish(request, args.json_output, args.markdown_output, args.rollback_receipt)
    else:
        _finalize(request, args.json_output, args.receipt)
    return 0


def _request_receipt(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--request", type=Path, required=True)
    parser.add_argument("--receipt", type=Path, required=True)


def _prepare(request: dict[str, Any], output: Path, receipt: Path) -> None:
    repo = Path(request["repo_path"])
    root = _git(repo, "rev-parse", "--show-toplevel").strip()
    head = _git(repo, "rev-parse", "HEAD").strip()
    branch = _git(repo, "branch", "--show-current").strip()
    payload = {
        "schema": "tau.release_preparation.v1",
        "repo_path": root,
        "head_sha": head,
        "branch": branch,
        "summary": "Release context prepared from the requested Git repository.",
    }
    _write_json(output, payload)
    artifact = _artifact("release_preparation", output)
    _node_receipt(
        receipt, request, "prepare-release", payload | {"artifacts": [artifact]}, [artifact]
    )


def _manifest(request: dict[str, Any], output: Path, receipt: Path) -> None:
    prepared = _accepted("tau.release_preparation.v1")
    payload = {
        "schema": "tau.release_manifest.v1",
        "repository": prepared["repo_path"],
        "head_sha": prepared["head_sha"],
        "branch": prepared["branch"],
        "summary": "Release manifest built for the prepared commit.",
    }
    _write_json(output, payload)
    artifact = _artifact("release_manifest", output)
    _node_receipt(
        receipt,
        request,
        "build-release-manifest",
        payload | {"artifacts": [artifact]},
        [artifact],
    )


def _policy(request: dict[str, Any], output: Path, receipt: Path) -> None:
    _accepted("tau.release_preparation.v1")
    if request["force_terminal_failure"]:
        _node_receipt(
            receipt,
            request,
            "verify-release-policy",
            None,
            [],
            status="BLOCKED",
            errors=["release_policy_rejected"],
        )
        return
    payload = {
        "schema": "tau.release_policy_validation.v1",
        "status": "PASS",
        "summary": "Deterministic release policy passed.",
    }
    _write_json(output, payload)
    artifact = _artifact("release_policy", output)
    _node_receipt(
        receipt,
        request,
        "verify-release-policy",
        payload | {"artifacts": [artifact]},
        [artifact],
    )


def _assemble(request: dict[str, Any], output: Path, receipt: Path) -> None:
    notes = _transaction_artifact("release_notes")
    manifest = _accepted("tau.release_manifest.v1")
    policy = _accepted("tau.release_policy_validation.v1")
    payload = {
        "schema": "tau.assembled_release_bundle.v1",
        "goal": request["goal"],
        "repository": {
            "path": manifest["repository"],
            "head_sha": manifest["head_sha"],
            "branch": manifest["branch"],
        },
        "notes": notes,
        "policy": policy,
        "summary": "Accepted release evidence assembled for publication approval.",
    }
    _write_json(output, payload)
    artifact = _artifact("assembled_release_bundle", output)
    _node_receipt(
        receipt,
        request,
        "assemble-release-bundle",
        payload | {"artifacts": [artifact]},
        [artifact],
    )


def _produce_notes(request: dict[str, Any], receipt: Path) -> None:
    context, context_path = _transaction_context("TAU_GENERIC_DAG_CONTEXT")
    attempt = int(context["attempt"])
    prepared = _accepted_from_items(context["accepted_inputs"], "tau.release_preparation.v1")
    work_order = _read_json(Path(context["work_order"]["path"]), "release notes work order")
    artifact_root = Path(work_order["artifact_root"])
    artifact = artifact_root / f"release-notes-{attempt}.json"
    payload = {
        "schema": "tau.release_notes.v1",
        "head_sha": prepared["head_sha"],
        "revision": attempt,
        "summary": (
            "Initial release notes draft."
            if attempt == 1
            else "Release notes revised and ready for publication."
        ),
    }
    _write_json(artifact, payload)
    _candidate_manifest(context, context_path, artifact, "release_notes", "application/json")
    _producer_receipt(receipt, context, request)


def _produce_bundle(request: dict[str, Any], receipt: Path) -> None:
    context, context_path = _transaction_context("TAU_GENERIC_DAG_CONTEXT")
    assembled = _accepted_from_items(context["accepted_inputs"], "tau.assembled_release_bundle.v1")
    work_order = _read_json(Path(context["work_order"]["path"]), "publication work order")
    artifact_root = Path(work_order["artifact_root"])
    json_path = artifact_root / "approved-release-bundle.json"
    markdown_path = artifact_root / "approved-release-bundle.md"
    report = {
        "schema": "tau.approved_release_bundle.v1",
        "status": "APPROVED",
        "goal": request["goal"],
        "repository": assembled["repository"],
        "notes": assembled["notes"],
        "policy": assembled["policy"],
        "publish_path": request["publish_path"],
        "summary": "Approved release bundle published after exact human approval.",
        "proof_scope": {
            "proves": [
                "The exact accepted release bundle was bound to a human approval packet.",
                "The published files passed deterministic post-write hash verification.",
            ],
            "does_not_prove": ["Provider or model quality.", "Production deployment readiness."],
        },
    }
    _write_json(json_path, report)
    markdown_path.write_text(
        "# Approved Release Bundle\n\n"
        f"**Status:** {report['status']}\n\n{report['summary']}\n",
        encoding="utf-8",
    )
    _candidate_manifest_many(
        context,
        context_path,
        [
            (json_path, "release_bundle_json", "application/json"),
            (markdown_path, "release_bundle_markdown", "text/markdown"),
        ],
    )
    _producer_receipt(receipt, context, request)


def _validate_transaction() -> None:
    context, _ = _transaction_context("TAU_GENERIC_DAG_VALIDATION_CONTEXT")
    output = Path(context["output_contract"]["validation_receipt_path"])
    _write_json(
        output,
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


def _review(*, revise_first: bool) -> None:
    context, _ = _transaction_context("TAU_GENERIC_DAG_REVIEW_CONTEXT")
    attempt = int(context["attempt"])
    verdict = "REVISE" if revise_first and attempt == 1 else "PASS"
    findings = []
    if verdict == "REVISE":
        findings = [
            {
                "finding_id": "release-notes-detail",
                "code": "RELEASE_NOTES_REVISION_REQUIRED",
                "severity": "ERROR",
                "message": "The first draft must state that it was revised.",
                "artifact_ids": ["release_notes"],
                "revision_instruction": "Revise the release notes summary before acceptance.",
            }
        ]
    output = Path(context["output_contract"]["review_feedback_path"])
    _write_json(
        output,
        {
            "schema": "tau.generic_artifact_review.v1",
            "transaction_id": context["transaction_id"],
            "node_id": context["node_id"],
            "attempt": attempt,
            "producer_id": context["producer_id"],
            "reviewer_id": context["reviewer_id"],
            "review_context_sha256": os.environ["TAU_GENERIC_DAG_REVIEW_CONTEXT_SHA256"],
            "candidate_manifest_sha256": context["candidate_manifest_sha256"],
            "verdict": verdict,
            "mocked": False,
            "live": True,
            "provider_live": False,
            "summary": "Revision required." if verdict == "REVISE" else "Artifact accepted.",
            "findings": findings,
        },
    )


def _publish(
    request: dict[str, Any], json_output: Path, markdown_output: Path, rollback: Path
) -> None:
    delay = request.get("step_delay_seconds", 0.0)
    if isinstance(delay, (int, float)) and not isinstance(delay, bool) and delay > 0:
        time.sleep(float(delay))
    context = _read_json(Path(os.environ["TAU_GENERIC_DAG_CONTEXT"]), "continuation context")
    artifacts = context.get("artifacts")
    if not isinstance(artifacts, list) or len(artifacts) != 2:
        raise RuntimeError("publication requires two accepted artifacts")
    by_kind = {item["kind"]: item for item in artifacts if isinstance(item, dict)}
    source_json = Path(by_kind["release_bundle_json"]["path"])
    source_markdown = Path(by_kind["release_bundle_markdown"]["path"])
    publish_path = Path(request["publish_path"])
    results_dir = json_output.parent
    if publish_path.exists() or results_dir.exists():
        raise RuntimeError("publication target already exists")
    external_stage = publish_path.with_name(f".{publish_path.name}.tmp-{os.getpid()}")
    results_stage = results_dir.with_name(f".{results_dir.name}.tmp-{os.getpid()}")
    for stage in (external_stage, results_stage):
        stage.mkdir(parents=True)
    try:
        for stage in (external_stage, results_stage):
            shutil.copy2(source_json, stage / json_output.name)
            shutil.copy2(source_markdown, stage / markdown_output.name)
        os.replace(external_stage, publish_path)
        os.replace(results_stage, results_dir)
        expected = {_sha256(source_json), _sha256(source_markdown)}
        actual = {
            _sha256(publish_path / json_output.name),
            _sha256(publish_path / markdown_output.name),
        }
        if request["simulate_publish_verification_failure"]:
            actual.add("forced-verification-failure")
        if actual != expected:
            shutil.rmtree(publish_path, ignore_errors=True)
            shutil.rmtree(results_dir, ignore_errors=True)
            _write_json(
                rollback,
                {
                    "schema": "tau.publication_rollback_receipt.v1",
                    "status": "ROLLED_BACK",
                    "reason": "post_write_verification_failed",
                    "publish_path": str(publish_path),
                },
            )
            raise RuntimeError("post_write_verification_failed")
    finally:
        shutil.rmtree(external_stage, ignore_errors=True)
        shutil.rmtree(results_stage, ignore_errors=True)


def _finalize(request: dict[str, Any], json_output: Path, receipt: Path) -> None:
    report = _read_json(json_output, "approved release result")
    artifacts = [
        _artifact("approved_release_bundle_json", json_output),
        _artifact("approved_release_bundle_markdown", json_output.with_suffix(".md")),
    ]
    accepted = {
        "schema": report["schema"],
        "status": report["status"],
        "summary": report["summary"],
        "publish_path": report["publish_path"],
        "artifacts": artifacts,
    }
    _node_receipt(receipt, request, "finalize-approved-release", accepted, artifacts)


def _candidate_manifest(
    context: dict[str, Any], context_path: Path, artifact: Path, kind: str, media_type: str
) -> None:
    _candidate_manifest_many(context, context_path, [(artifact, kind, media_type)])


def _candidate_manifest_many(
    context: dict[str, Any],
    context_path: Path,
    artifacts: list[tuple[Path, str, str]],
) -> None:
    manifest = {
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
            for path, kind, media_type in artifacts
        ],
    }
    _write_json(Path(context["output_contract"]["candidate_manifest_path"]), manifest)


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
            "commands_run": ["deterministic artifact producer"],
            "errors": [],
            "policy_exceptions": [],
            "handoff_summary": "Candidate artifact produced for independent validation and review.",
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
            "commands_run": ["deterministic local release operation"],
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


def _transaction_artifact(kind: str) -> dict[str, Any]:
    context, _ = _transaction_context("TAU_GENERIC_DAG_CONTEXT")
    for item in context.get("accepted_inputs", []):
        if not isinstance(item, dict):
            continue
        projection = item.get("accepted_output")
        candidate = projection if isinstance(projection, dict) else item
        for artifact in candidate.get("artifacts", []):
            if isinstance(artifact, dict) and artifact.get("kind") == kind:
                return _read_json(Path(artifact["path"]), kind)
    raise RuntimeError(f"accepted transaction artifact missing kind {kind}")


def _transaction_context(name: str) -> tuple[dict[str, Any], Path]:
    value = os.environ.get(name)
    if not value:
        raise RuntimeError(f"{name} is required")
    path = Path(value)
    return _read_json(path, name), path


def _request(path: Path) -> dict[str, Any]:
    payload = _read_json(path, "approved release request")
    if payload.get("schema") != "tau.approved_release_request.v1":
        raise RuntimeError("approved release request schema is invalid")
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
