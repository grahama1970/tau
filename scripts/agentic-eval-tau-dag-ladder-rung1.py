#!/usr/bin/env python3
"""Prove the Tau packaged DAG ladder manifest and rung-1 clean-checkout run.

This proof is intentionally narrow for issue #331: it establishes the ladder
contract and proves rung 1 from a separate clean clone of the current Git HEAD.
It does not claim the other four rungs are complete.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

RECEIPT_SCHEMA = "tau.dag_ladder_rung1_clean_checkout_proof.v1"
MANIFEST_SCHEMA = "tau.canonical_dag_ladder_manifest.v1"
MANIFEST_PATH = Path("docs/proofs/acceptance/canonical-dag-ladder-manifest.json")
EXPECTED_RUNGS = [
    (1, "repository-readiness", "LINEAR"),
    (2, "tau-operator-reference", "MULTI_STEP_SEQUENTIAL"),
    (3, "repository-evidence-map", "FAN_OUT_FAN_IN"),
    (4, "approved-release-bundle", "MIXED_RETRY_APPROVAL"),
    (5, "durable-repository-qualification", "DURABLE_MIXED_REPAIR_APPROVAL"),
]


@dataclass(frozen=True)
class CommandRecord:
    argv: list[str]
    cwd: str
    exit_code: int
    stdout_path: str
    stderr_path: str
    stdout_sha256: str
    stderr_sha256: str


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, default=Path.cwd())
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--work-root", type=Path)
    parser.add_argument("--uv-bin", default="uv")
    parser.add_argument("--timeout-seconds", type=int, default=180)
    args = parser.parse_args()

    source_repo = args.repo.expanduser().resolve()
    out = args.out.expanduser().resolve()
    out.parent.mkdir(parents=True, exist_ok=True)
    work_root = args.work_root.expanduser().resolve() if args.work_root else None

    with workspace(work_root) as root:
        logs = root / "logs"
        logs.mkdir(parents=True, exist_ok=True)
        clean_repo = root / "clean-checkout"
        run_dir = root / "rung1-run"
        bootstrap: list[CommandRecord] = []
        errors: list[str] = []

        source_head = run_git(source_repo, "rev-parse", "HEAD")
        bootstrap.append(
            run_command(
                ["git", "clone", "--quiet", "--no-hardlinks", str(source_repo), str(clean_repo)],
                cwd=root,
                timeout=args.timeout_seconds,
                stdout_path=logs / "git-clone.stdout.txt",
                stderr_path=logs / "git-clone.stderr.txt",
            )
        )
        if bootstrap[-1].exit_code == 0:
            bootstrap.append(
                run_command(
                    ["git", "checkout", "--quiet", source_head],
                    cwd=clean_repo,
                    timeout=args.timeout_seconds,
                    stdout_path=logs / "git-checkout.stdout.txt",
                    stderr_path=logs / "git-checkout.stderr.txt",
                )
            )
        clean_status = run_git(clean_repo, "status", "--porcelain") if clean_repo.exists() else None
        clean_head = run_git(clean_repo, "rev-parse", "HEAD") if clean_repo.exists() else None
        if clean_head != source_head:
            errors.append(f"clean_checkout_head_mismatch:{clean_head}!={source_head}")
        if clean_status != "":
            errors.append("clean_checkout_not_clean")

        manifest_result = inspect_manifest(clean_repo if clean_repo.exists() else source_repo)
        errors.extend(manifest_result["errors"])

        catalog_record: CommandRecord | None = None
        workflow_record: CommandRecord | None = None
        catalog: dict[str, Any] | None = None
        workflow_receipt: dict[str, Any] | None = None
        rung1_result: dict[str, Any] = {"errors": ["workflow_not_run"]}
        negative_controls: dict[str, Any] = {"errors": ["workflow_not_run"]}

        if not errors:
            catalog_record = run_command(
                [args.uv_bin, "run", "--project", str(clean_repo), "tau", "workflows", "list", "--json"],
                cwd=clean_repo,
                timeout=args.timeout_seconds,
                stdout_path=logs / "workflows-list.stdout.json",
                stderr_path=logs / "workflows-list.stderr.txt",
            )
            catalog = parse_json(catalog_record.stdout_path)
            errors.extend(inspect_catalog(catalog))

        if not errors:
            workflow_record = run_command(
                [
                    args.uv_bin,
                    "run",
                    "--project",
                    str(clean_repo),
                    "tau",
                    "workflows",
                    "run",
                    "repository-readiness",
                    "--repo",
                    str(clean_repo),
                    "--goal",
                    "Prove Tau packaged DAG ladder rung 1 from a clean checkout.",
                    "--run-dir",
                    str(run_dir),
                    "--require-clean",
                    "--no-browser-open",
                ],
                cwd=clean_repo,
                timeout=args.timeout_seconds + 60,
                stdout_path=logs / "rung1-workflow.stdout.json",
                stderr_path=logs / "rung1-workflow.stderr.txt",
            )
            workflow_receipt = parse_json(workflow_record.stdout_path)
            rung1_result = inspect_rung1_workflow(
                workflow_receipt,
                exit_code=workflow_record.exit_code,
                expected_head=source_head,
            )
            errors.extend(rung1_result["errors"])
            negative_controls = run_negative_controls(
                workflow_receipt,
                out_dir=root / "negative-controls",
            )
            errors.extend(negative_controls["errors"])

        receipt = {
            "schema": RECEIPT_SCHEMA,
            "ok": not errors,
            "status": "PASS" if not errors else "FAIL",
            "mocked": False,
            "live": True,
            "provider_live": False,
            "source_repo": str(source_repo),
            "source_head": source_head,
            "clean_checkout": {
                "path": str(clean_repo),
                "head": clean_head,
                "status_porcelain": clean_status,
                "bootstrap_commands": [record.__dict__ for record in bootstrap],
            },
            "ladder_manifest": manifest_result,
            "catalog_command": catalog_record.__dict__ if catalog_record else None,
            "workflow_command": workflow_record.__dict__ if workflow_record else None,
            "rung1": rung1_result,
            "negative_controls": negative_controls,
            "errors": errors,
            "proof_scope": {
                "proves": [
                    "The committed ladder manifest names the five packaged Tau workflow rungs in order.",
                    "Rung 1 runs from a separate clean clone of the current Git HEAD.",
                    "The rung-1 run writes a useful repository-readiness result and generic DAG run receipt.",
                    "The proof independently reads back artifact digests and catches mutated or missing artifact references.",
                ],
                "does_not_prove": [
                    "Rungs 2-5 execute from clean checkout.",
                    "Provider-live execution.",
                    "Dynamic React Flow transition rendering.",
                    "Human acceptance of the full GOAL.md outcome.",
                ],
            },
            "created_at": utc_stamp(),
        }
        out.write_text(json.dumps(receipt, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        print(json.dumps({"status": receipt["status"], "proof": str(out), "errors": errors}, sort_keys=True))
        return 0 if receipt["ok"] else 1


class workspace:
    def __init__(self, requested: Path | None) -> None:
        self.requested = requested
        self.tmp: tempfile.TemporaryDirectory[str] | None = None
        self.path: Path | None = None

    def __enter__(self) -> Path:
        if self.requested is not None:
            self.requested.mkdir(parents=True, exist_ok=True)
            self.path = self.requested
            return self.path
        self.tmp = tempfile.TemporaryDirectory(prefix="tau-dag-ladder-rung1-")
        self.path = Path(self.tmp.name)
        return self.path

    def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
        if self.tmp is not None:
            self.tmp.cleanup()


def inspect_manifest(repo: Path) -> dict[str, Any]:
    path = repo / MANIFEST_PATH
    errors: list[str] = []
    data: dict[str, Any] | None = None
    if not path.is_file():
        errors.append("manifest_missing")
    else:
        data = json.loads(path.read_text(encoding="utf-8"))
        if data.get("schema") != MANIFEST_SCHEMA:
            errors.append(f"manifest_schema:{data.get('schema')!r}")
        rungs = data.get("rungs")
        if not isinstance(rungs, list) or len(rungs) != 5:
            errors.append(f"manifest_rung_count:{len(rungs) if isinstance(rungs, list) else 'invalid'}")
        else:
            observed = [
                (item.get("rung"), item.get("workflow_id"), item.get("topology"))
                for item in rungs
                if isinstance(item, dict)
            ]
            if observed != EXPECTED_RUNGS:
                errors.append(f"manifest_rung_order:{observed!r}")
            for item in rungs:
                if not isinstance(item, dict):
                    continue
                if not item.get("acceptance_boundary"):
                    errors.append(f"manifest_missing_acceptance_boundary:{item.get('workflow_id')}")
                proof_required = item.get("proof_required")
                if not isinstance(proof_required, list) or not proof_required:
                    errors.append(f"manifest_missing_proof_required:{item.get('workflow_id')}")
        if data.get("does_not_claim_goal_complete") is not True:
            errors.append("manifest_goal_completion_claim_not_excluded")
    return {
        "path": str(path),
        "sha256": file_sha256(path) if path.is_file() else None,
        "schema": data.get("schema") if isinstance(data, dict) else None,
        "rung_count": len(data.get("rungs", [])) if isinstance(data, dict) and isinstance(data.get("rungs"), list) else 0,
        "errors": errors,
    }


def inspect_catalog(catalog: dict[str, Any] | None) -> list[str]:
    errors: list[str] = []
    if not isinstance(catalog, dict):
        return ["workflow_catalog_missing_json"]
    if catalog.get("schema") != "tau.workflow_catalog.v1":
        errors.append(f"workflow_catalog_schema:{catalog.get('schema')!r}")
    workflows = catalog.get("workflows")
    if not isinstance(workflows, list):
        return [*errors, "workflow_catalog_missing_workflows"]
    observed = sorted(
        (item.get("rung"), item.get("workflow_id"), item.get("topology"))
        for item in workflows
        if isinstance(item, dict)
    )
    if observed != EXPECTED_RUNGS:
        errors.append(f"workflow_catalog_rung_mismatch:{observed!r}")
    return errors


def inspect_rung1_workflow(
    receipt: dict[str, Any] | None,
    *,
    exit_code: int,
    expected_head: str,
) -> dict[str, Any]:
    errors: list[str] = []
    if exit_code != 0:
        errors.append(f"workflow_exit_code:{exit_code}")
    if not isinstance(receipt, dict):
        return {"errors": [*errors, "workflow_missing_json"]}
    if receipt.get("schema") != "tau.workflow_run_receipt.v1":
        errors.append(f"workflow_schema:{receipt.get('schema')!r}")
    for key, expected in {
        "workflow_id": "repository-readiness",
        "status": "PASS",
        "ok": True,
        "mocked": False,
        "live": True,
        "provider_live": False,
    }.items():
        if receipt.get(key) != expected:
            errors.append(f"workflow_{key}:{receipt.get(key)!r}")
    result = receipt.get("result") if isinstance(receipt.get("result"), dict) else {}
    repository = result.get("repository") if isinstance(result.get("repository"), dict) else {}
    policy = result.get("policy") if isinstance(result.get("policy"), dict) else {}
    if result.get("schema") != "tau.repository_readiness_report.v1":
        errors.append(f"result_schema:{result.get('schema')!r}")
    if result.get("status") != "READY":
        errors.append(f"result_status:{result.get('status')!r}")
    if repository.get("dirty") is not False:
        errors.append(f"repository_dirty:{repository.get('dirty')!r}")
    if repository.get("head_sha") != expected_head:
        errors.append(f"repository_head:{repository.get('head_sha')!r}!={expected_head}")
    if policy.get("require_clean") is not True or policy.get("validation") != "PASS":
        errors.append("clean_policy_not_passed")
    goal_hash = result.get("goal", {}).get("goal_hash") if isinstance(result.get("goal"), dict) else None
    run_receipt_path = Path(str(receipt.get("run_receipt_path"))) if receipt.get("run_receipt_path") else None
    run_receipt = json.loads(run_receipt_path.read_text(encoding="utf-8")) if run_receipt_path and run_receipt_path.is_file() else None
    run_errors = inspect_run_receipt(run_receipt, expected_goal_hash=goal_hash)
    artifact_errors, artifact_rows = verify_artifact_digests(run_receipt)
    errors.extend(run_errors)
    errors.extend(artifact_errors)
    return {
        "workflow_id": receipt.get("workflow_id"),
        "status": receipt.get("status"),
        "run_dir": receipt.get("run_dir"),
        "run_receipt_path": receipt.get("run_receipt_path"),
        "source_dag_path": receipt.get("source_dag_path"),
        "result_schema": result.get("schema"),
        "result_status": result.get("status"),
        "repository_head": repository.get("head_sha"),
        "repository_dirty": repository.get("dirty"),
        "goal_hash": goal_hash,
        "run_receipt_sha256": file_sha256(run_receipt_path) if run_receipt_path and run_receipt_path.is_file() else None,
        "artifact_readback": artifact_rows,
        "errors": errors,
    }


def inspect_run_receipt(receipt: dict[str, Any] | None, *, expected_goal_hash: str | None) -> list[str]:
    errors: list[str] = []
    if not isinstance(receipt, dict):
        return ["run_receipt_missing_json"]
    if receipt.get("schema") != "tau.generic_dag_run_receipt.v1":
        errors.append(f"run_receipt_schema:{receipt.get('schema')!r}")
    if receipt.get("status") != "PASS" or receipt.get("ok") is not True:
        errors.append(f"run_receipt_status:{receipt.get('status')!r}")
    if receipt.get("mocked") is not False or receipt.get("live") is not True:
        errors.append("run_receipt_boundary_invalid")
    if receipt.get("node_count") != 3 or receipt.get("completed_node_count") != 3:
        errors.append("rung1_node_count_mismatch")
    if receipt.get("max_observed_concurrency") != 1:
        errors.append("rung1_not_linear")
    nodes = receipt.get("nodes") if isinstance(receipt.get("nodes"), list) else []
    if [node.get("node_id") for node in nodes if isinstance(node, dict)] != [
        "inspect-repository",
        "validate-readiness",
        "publish-readiness",
    ]:
        errors.append("rung1_node_order_mismatch")
    if expected_goal_hash:
        for node in nodes:
            if not isinstance(node, dict):
                continue
            for command_result in node.get("command_results", []) or []:
                if not isinstance(command_result, dict):
                    continue
                lease = command_result.get("runtime_endpoint_lease")
                if isinstance(lease, dict) and lease.get("goal_hash") != expected_goal_hash:
                    errors.append(f"goal_hash_mismatch:{node.get('node_id')}")
    for required in ("events_jsonl", "checkpoint_path", "current_state_path"):
        path = Path(str(receipt.get(required))) if receipt.get(required) else None
        if path is None or not path.is_file():
            errors.append(f"missing_{required}")
    return errors


def verify_artifact_digests(receipt: dict[str, Any] | None) -> tuple[list[str], list[dict[str, Any]]]:
    errors: list[str] = []
    rows: list[dict[str, Any]] = []
    if not isinstance(receipt, dict):
        return ["artifact_receipt_missing"], rows
    for node in receipt.get("nodes", []) or []:
        if not isinstance(node, dict):
            continue
        for artifact in node.get("artifacts", []) or []:
            if not isinstance(artifact, dict):
                continue
            path_value = artifact.get("path")
            expected = artifact.get("sha256")
            row = {"node_id": node.get("node_id"), "path": path_value, "expected_sha256": expected}
            if not isinstance(path_value, str) or not isinstance(expected, str):
                errors.append(f"artifact_reference_malformed:{node.get('node_id')}")
                row["status"] = "MALFORMED"
            else:
                path = Path(path_value)
                if not path.is_file():
                    errors.append(f"artifact_missing:{path_value}")
                    row["status"] = "MISSING"
                else:
                    actual = file_sha256(path)
                    row["actual_sha256"] = actual
                    row["status"] = "PASS" if actual == expected else "MISMATCH"
                    if actual != expected:
                        errors.append(f"artifact_digest_mismatch:{path_value}")
            rows.append(row)
    if not rows:
        errors.append("no_artifact_rows")
    return errors, rows


def run_negative_controls(receipt: dict[str, Any] | None, *, out_dir: Path) -> dict[str, Any]:
    out_dir.mkdir(parents=True, exist_ok=True)
    if not isinstance(receipt, dict) or not receipt.get("run_receipt_path"):
        return {"errors": ["negative_missing_source_receipt"]}
    source = json.loads(Path(str(receipt["run_receipt_path"])).read_text(encoding="utf-8"))
    controls: list[dict[str, Any]] = []
    errors: list[str] = []

    mutated = json.loads(json.dumps(source))
    first_artifact = first_artifact_ref(mutated)
    if first_artifact is None:
        return {"errors": ["negative_no_artifact_to_mutate"]}
    first_artifact["sha256"] = "sha256:" + "0" * 64
    mutated_path = out_dir / "mutated-artifact-digest-run-receipt.json"
    mutated_path.write_text(json.dumps(mutated, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    detected_errors, _ = verify_artifact_digests(mutated)
    detected = any(error.startswith("artifact_digest_mismatch:") for error in detected_errors)
    controls.append({
        "name": "mutated_artifact_digest",
        "receipt_path": str(mutated_path),
        "detected": detected,
        "errors": detected_errors,
    })
    if not detected:
        errors.append("negative_mutated_artifact_digest_not_detected")

    missing = json.loads(json.dumps(source))
    first_artifact = first_artifact_ref(missing)
    if first_artifact is not None:
        first_artifact["path"] = str(out_dir / "missing-artifact.json")
    missing_path = out_dir / "missing-artifact-run-receipt.json"
    missing_path.write_text(json.dumps(missing, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    detected_errors, _ = verify_artifact_digests(missing)
    detected = any(error.startswith("artifact_missing:") for error in detected_errors)
    controls.append({
        "name": "missing_artifact",
        "receipt_path": str(missing_path),
        "detected": detected,
        "errors": detected_errors,
    })
    if not detected:
        errors.append("negative_missing_artifact_not_detected")

    return {"controls": controls, "errors": errors}


def first_artifact_ref(receipt: dict[str, Any]) -> dict[str, Any] | None:
    for node in receipt.get("nodes", []) or []:
        if not isinstance(node, dict):
            continue
        for artifact in node.get("artifacts", []) or []:
            if isinstance(artifact, dict):
                return artifact
    return None


def run_git(repo: Path, *args: str) -> str:
    completed = subprocess.run(
        ["git", "-C", str(repo), *args],
        text=True,
        capture_output=True,
        check=True,
    )
    return completed.stdout.strip()


def run_command(
    argv: list[str],
    *,
    cwd: Path,
    timeout: int,
    stdout_path: Path,
    stderr_path: Path,
) -> CommandRecord:
    completed = subprocess.run(
        argv,
        cwd=cwd,
        text=True,
        capture_output=True,
        timeout=timeout,
        check=False,
    )
    stdout_path.write_text(completed.stdout, encoding="utf-8")
    stderr_path.write_text(completed.stderr, encoding="utf-8")
    return CommandRecord(
        argv=argv,
        cwd=str(cwd),
        exit_code=completed.returncode,
        stdout_path=str(stdout_path),
        stderr_path=str(stderr_path),
        stdout_sha256=file_sha256(stdout_path),
        stderr_sha256=file_sha256(stderr_path),
    )


def parse_json(path_value: str) -> dict[str, Any] | None:
    text = Path(path_value).read_text(encoding="utf-8").strip()
    decoder = json.JSONDecoder()
    for index, char in enumerate(text):
        if char != "{":
            continue
        try:
            payload, _ = decoder.raw_decode(text[index:])
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict):
            return payload
    return None


def file_sha256(path: Path) -> str:
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


def utc_stamp() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


if __name__ == "__main__":
    raise SystemExit(main())
