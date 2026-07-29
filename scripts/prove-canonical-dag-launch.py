#!/usr/bin/env python3
"""Prove clean-checkout canonical DAG discovery and launch ergonomics.

The proof runs the same Tau CLI commands a new evaluator would run:

1. ``tau canonical-dags --json``
2. ``tau canonical-dag-launch <dag-id>`` for every catalog entry

It writes a receipt that reads back every launch receipt and rejects missing run
ids, output receipts, viewer URLs, or mocked-only evidence.
"""

from __future__ import annotations

import argparse
import json
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

RECEIPT_SCHEMA = "tau.canonical_dag_launch_surface_proof.v1"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, default=Path.cwd())
    parser.add_argument(
        "--run-root",
        type=Path,
        default=Path("/tmp/tau-canonical-dag-launch-proof"),
    )
    parser.add_argument("--receipt", type=Path)
    parser.add_argument("--uv-bin", default="uv")
    parser.add_argument("--timeout-seconds", type=int, default=180)
    args = parser.parse_args()

    repo = args.repo.expanduser().resolve()
    run_root = args.run_root.expanduser().resolve()
    run_root.mkdir(parents=True, exist_ok=True)
    receipt_path = (
        args.receipt.expanduser().resolve()
        if args.receipt
        else run_root / "canonical-dag-launch-proof-receipt.json"
    )
    logs_dir = run_root / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)

    tau_prefix = [args.uv_bin, "run", "--project", str(repo), "tau"]
    catalog_record = run_command(
        [*tau_prefix, "canonical-dags", "--json"],
        cwd=repo,
        timeout=args.timeout_seconds,
        stdout_path=logs_dir / "canonical-dags.stdout.json",
        stderr_path=logs_dir / "canonical-dags.stderr.txt",
    )
    catalog = parse_json_payload(catalog_record["stdout"])
    dag_ids = [
        str(dag["dag_id"])
        for dag in catalog.get("dags", [])
        if isinstance(dag, dict) and isinstance(dag.get("dag_id"), str)
    ] if isinstance(catalog, dict) else []

    records: list[dict[str, Any]] = []
    errors: list[str] = []
    if not isinstance(catalog, dict):
        errors.append("catalog_missing_json")
    elif catalog.get("ok") is not True:
        errors.append(f"catalog_status:{catalog.get('status')!r}")
    if len(dag_ids) != 5:
        errors.append(f"catalog_count:{len(dag_ids)}")

    for dag_id in dag_ids:
        record = run_command(
            [
                *tau_prefix,
                "canonical-dag-launch",
                dag_id,
                "--repo",
                str(repo),
                "--run-root",
                str(run_root / "runs"),
                "--timeout-seconds",
                str(args.timeout_seconds),
            ],
            cwd=repo,
            timeout=args.timeout_seconds + 60,
            stdout_path=logs_dir / f"{dag_id}.stdout.json",
            stderr_path=logs_dir / f"{dag_id}.stderr.txt",
        )
        payload = parse_json_payload(record["stdout"])
        record["payload"] = payload
        record["checks"] = launch_payload_errors(dag_id, payload, record["exit_code"])
        if record["checks"]:
            errors.extend(f"{dag_id}:{error}" for error in record["checks"])
        records.append({key: value for key, value in record.items() if key != "stdout"})

    receipt = {
        "schema": RECEIPT_SCHEMA,
        "ok": not errors,
        "status": "PASS" if not errors else "BLOCKED",
        "mocked": False,
        "live": True,
        "provider_live": any(
            isinstance(record.get("payload"), dict)
            and record["payload"].get("provider_live") is True
            for record in records
        ),
        "repo": str(repo),
        "run_root": str(run_root),
        "catalog_command": catalog_record["command"],
        "catalog_stdout_path": catalog_record["stdout_path"],
        "catalog_stderr_path": catalog_record["stderr_path"],
        "catalog_count": len(dag_ids),
        "dag_ids": dag_ids,
        "launch_count": len(records),
        "launches": records,
        "errors": errors,
        "proof_scope": {
            "proves": [
                "A clean evaluator can discover exactly five canonical DAGs through Tau CLI",
                "Each canonical DAG can be launched through Tau CLI without editing JSON",
                (
                    "Each launch reads back a live, non-mocked run record, output "
                    "receipt path, and viewer URL"
                ),
            ],
            "does_not_prove": [
                "semantic quality of the five useful outputs",
                "dynamic browser rendering",
                "durable crash-safe resume",
                "human approval rollback",
                "human acceptance of the immutable goal",
            ],
        },
        "timestamp": utc_stamp(),
    }
    receipt_path.parent.mkdir(parents=True, exist_ok=True)
    receipt_path.write_text(json.dumps(receipt, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(receipt, indent=2, sort_keys=True))
    return 0 if receipt["ok"] else 1


def run_command(
    command: list[str],
    *,
    cwd: Path,
    timeout: int,
    stdout_path: Path,
    stderr_path: Path,
) -> dict[str, Any]:
    completed = subprocess.run(
        command,
        cwd=cwd,
        text=True,
        capture_output=True,
        timeout=timeout,
        check=False,
    )
    stdout_path.write_text(completed.stdout, encoding="utf-8")
    stderr_path.write_text(completed.stderr, encoding="utf-8")
    return {
        "command": command,
        "exit_code": completed.returncode,
        "stdout": completed.stdout,
        "stdout_path": str(stdout_path),
        "stderr_path": str(stderr_path),
    }


def launch_payload_errors(dag_id: str, payload: dict[str, Any] | None, exit_code: int) -> list[str]:
    errors: list[str] = []
    if exit_code != 0:
        errors.append(f"exit_code:{exit_code}")
    if not isinstance(payload, dict):
        return [*errors, "missing_json_payload"]
    if payload.get("schema") != "tau.canonical_dag_launch_receipt.v1":
        errors.append(f"schema:{payload.get('schema')!r}")
    if payload.get("ok") is not True:
        errors.append(f"status:{payload.get('status')!r}")
    if payload.get("mocked") is not False:
        errors.append(f"mocked:{payload.get('mocked')!r}")
    if payload.get("live") is not True:
        errors.append(f"live:{payload.get('live')!r}")
    selected = payload.get("canonical_dag")
    if not isinstance(selected, dict) or selected.get("dag_id") != dag_id:
        errors.append("canonical_dag_mismatch")
    for key in ("run_id", "run_dir", "output_receipt_path", "viewer_url"):
        if not isinstance(payload.get(key), str) or not payload[key]:
            errors.append(f"missing_{key}")
    return errors


def parse_json_payload(text: str) -> dict[str, Any] | None:
    decoder = json.JSONDecoder()
    stripped = text.strip()
    for index, char in enumerate(stripped):
        if char != "{":
            continue
        try:
            payload, end = decoder.raw_decode(stripped[index:])
        except json.JSONDecodeError:
            continue
        if stripped[index + end :].strip():
            continue
        return payload if isinstance(payload, dict) else None
    return None


def utc_stamp() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


if __name__ == "__main__":
    raise SystemExit(main())
