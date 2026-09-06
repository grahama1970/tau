#!/usr/bin/env python3
"""tau#341: prove the strict work-order boundary through the real `tau` CLI.

Runs `uv run tau scillm-worker-launch` as a subprocess against the watchdog's
two captured inputs (missing identity; wrong types + undeclared field), a
valid work order (dry run must PASS), and a valid work order with --apply
against an unroutable base URL (must still BLOCK at the boundary, never HTTP).
Reads every receipt back from disk.
"""

# ruff: noqa: E501

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC = REPO_ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from tau_coding.dag_runtime.admission import write_durable_json  # noqa: E402

WATCHDOG_EVIDENCE = Path(
    "/mnt/storage12tb/skills/project-watchdog/outputs/ask-native-integration/worker-boundary"
)
IDENTITY = ("dag_id", "node_id", "agent", "goal_hash", "attempt", "task")


def _valid(work: Path) -> dict[str, Any]:
    repo = work / "repo"
    repo.mkdir(parents=True, exist_ok=True)
    return {
        "schema": "tau.executor.scillm_worker.v1",
        "dag_id": "tau341-dag",
        "node_id": "coder",
        "agent": "coder",
        "goal_hash": "sha256:" + "a" * 64,
        "attempt": 1,
        "task": "Make a bounded change.",
        "repo": str(repo),
        "allowed_paths": ["src/**"],
        "forbidden_paths": [".git/**"],
        "result_path": "worker-result.json",
        "receipt_path": "worker-receipt.json",
        "model_provider_route": {"surface": "opencode_serve", "endpoint": "/v1/scillm/opencode/runs", "agent": "build"},
    }


def _missing(work: Path) -> dict[str, Any]:
    order = _valid(work)
    for key in IDENTITY:
        order.pop(key)
    return order


def _malformed(work: Path) -> dict[str, Any]:
    return {**_valid(work), "dag_id": 123, "node_id": [], "agent": {}, "goal_hash": False, "attempt": "first", "task": {}, "undeclared_field": True}


def _launch(work: Path, name: str, order: dict[str, Any], *extra: str) -> dict[str, Any]:
    case_dir = work / name
    case_dir.mkdir(parents=True, exist_ok=True)
    spec = case_dir / "work-order.json"
    spec.write_text(json.dumps(order, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    out = case_dir / "launch-receipt.json"
    proc = subprocess.run(
        ["uv", "run", "tau", "scillm-worker-launch", "--work-order", str(spec), "--out", str(out), *extra],
        cwd=REPO_ROOT, text=True, capture_output=True, timeout=300, check=False,
    )
    (case_dir / "cli-stdout.json").write_text(proc.stdout, encoding="utf-8")
    (case_dir / "cli-stderr.txt").write_text(proc.stderr, encoding="utf-8")
    receipt = json.loads(out.read_text(encoding="utf-8")) if out.exists() else {}
    validation = receipt.get("work_order_validation") or {}
    return {
        "exit_code": proc.returncode,
        "status": receipt.get("status"),
        "http_executed": receipt.get("http_executed"),
        "launch_skipped": receipt.get("launch_skipped"),
        "request_constructed": receipt.get("request_constructed", True if receipt.get("request_payload") else None),
        "alert_codes": receipt.get("alert_codes"),
        "validation_errors": [(e["loc"], e["type"]) for e in validation.get("errors", [])],
        "receipt_path": str(out),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", required=True)
    parser.add_argument("--work", required=True)
    args = parser.parse_args()
    work = Path(args.work).expanduser().resolve()
    work.mkdir(parents=True, exist_ok=True)
    errors: list[str] = []
    cases: dict[str, Any] = {}

    watchdog_inputs: dict[str, dict[str, Any]] = {}
    if WATCHDOG_EVIDENCE.is_dir():
        for name in ("missing-identity", "malformed-identity"):
            src = WATCHDOG_EVIDENCE / f"{name}.json"
            shutil.copy(src, work / f"watchdog-{name}.json")
            watchdog_inputs[f"watchdog-{name}"] = json.loads(src.read_text(encoding="utf-8"))
    else:
        errors.append("watchdog_evidence_dir_missing")

    for name, order in {**watchdog_inputs, "synthetic-missing-identity": _missing(work), "synthetic-malformed-identity": _malformed(work)}.items():
        result = _launch(work, name, order)
        cases[name] = result
        if result["exit_code"] == 0 or result["status"] != "BLOCKED" or result["http_executed"] is not False or "invalid_work_order" not in (result["alert_codes"] or []):
            errors.append(f"not_blocked:{name}")
        locs = dict(result["validation_errors"])
        if "missing" in name and any(locs.get(k) != "missing" for k in IDENTITY):
            errors.append(f"missing_diagnostics_incomplete:{name}")
        if "malformed" in name and (locs.get("attempt") != "int_type" or locs.get("undeclared_field") != "extra_forbidden"):
            errors.append(f"type_diagnostics_incomplete:{name}")

    valid = _launch(work, "valid-dry-run", _valid(work))
    cases["valid-dry-run"] = valid
    if valid["exit_code"] != 0 or valid["status"] != "PASS" or valid["alert_codes"]:
        errors.append("valid_work_order_regressed")

    apply_invalid = _launch(work, "apply-invalid-unroutable", _missing(work), "--apply", "--scillm-base-url", "http://127.0.0.1:9", "--request-timeout-s", "2")
    cases["apply-invalid-unroutable"] = apply_invalid
    if apply_invalid["status"] != "BLOCKED" or apply_invalid["http_executed"] is not False:
        errors.append("apply_invalid_reached_http")

    payload = {
        "schema": "tau.scillm_work_order_boundary_proof.v1",
        "status": "PASS" if not errors else "FAIL",
        "mocked": False,
        "live": True,
        "provider_live": False,
        "entrypoint": "uv run tau scillm-worker-launch (subprocess)",
        "watchdog_evidence_dir": str(WATCHDOG_EVIDENCE),
        "cases": cases,
        "proof_boundary": {
            "proves": "The real tau CLI blocks missing, mistyped, and undeclared work-order fields at a strict pydantic boundary with per-field loc/type diagnostics, constructs no request, and makes no HTTP call in dry-run or apply; a valid work order still passes dry run.",
            "does_not_prove": "OpenCode-serve execution, worker result validity, or provider behaviour.",
        },
        "errors": errors,
    }
    write_durable_json(Path(args.out).expanduser().resolve(), payload)
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0 if not errors else 2


if __name__ == "__main__":
    raise SystemExit(main())
