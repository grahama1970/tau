from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

GENERIC_DAG_SPEC_SCHEMA = "tau.generic_dag_spec.v1"
GENERIC_DAG_NODE_RECEIPT_SCHEMA = "tau.generic_dag_node_receipt.v1"
MAP_CHILD_SCHEMA = "tau.map_child_dag_conformance.v1"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo-root", required=True)
    parser.add_argument("--root", required=True)
    parser.add_argument("--receipt", required=True)
    parser.add_argument("--goal-hash", required=True)
    args = parser.parse_args()

    repo_root = Path(args.repo_root).expanduser().resolve()
    root = Path(args.root).expanduser().resolve()
    receipt_path = Path(args.receipt).expanduser().resolve()
    root.mkdir(parents=True, exist_ok=True)
    items = [
        {"item_id": "alpha", "value": 1},
        {"item_id": "bravo", "value": 2},
        {"item_id": "charlie", "value": 3},
    ]
    child_receipts: list[dict[str, Any]] = []
    child_commands: list[list[str]] = []
    errors: list[str] = []
    env = dict(os.environ)
    src_path = str(repo_root / "src")
    env["PYTHONPATH"] = (
        src_path
        if not env.get("PYTHONPATH")
        else f"{src_path}{os.pathsep}{env['PYTHONPATH']}"
    )

    for item in items:
        child_id = f"child-{item['item_id']}"
        child_root = root / "children" / item["item_id"]
        child_root.mkdir(parents=True, exist_ok=True)
        child_receipt_path = child_root / "receipt.json"
        child_spec_path = child_root / "dag.json"
        child_code = (
            "import json\n"
            "from pathlib import Path\n"
            f"receipt_path = Path({json.dumps(str(child_receipt_path))})\n"
            f"payload = {json.dumps(_child_receipt_payload(child_id, item, args.goal_hash))}\n"
            "receipt_path.parent.mkdir(parents=True, exist_ok=True)\n"
            "receipt_path.write_text(\n"
            "    json.dumps(payload, indent=2, sort_keys=True) + '\\n',\n"
            "    encoding='utf-8',\n"
            ")\n"
            "print(json.dumps(payload, sort_keys=True))\n"
        )
        write_json(
            child_spec_path,
            {
                "schema": GENERIC_DAG_SPEC_SCHEMA,
                "run_id": child_id,
                "run_dir": str(child_root / "run"),
                "goal_hash": args.goal_hash,
                "nodes": [
                    {
                        "node_id": child_id,
                        "receipt_path": str(child_receipt_path),
                        "timeout_seconds": 30,
                        "command": [sys.executable, "-c", child_code],
                    }
                ],
            },
        )
        command = ["uv", "run", "tau", "dag-run", str(child_spec_path), "--no-resume"]
        child_commands.append(command)
        completed = subprocess.run(
            command,
            cwd=str(repo_root),
            check=False,
            capture_output=True,
            text=True,
            timeout=60,
            env=env,
        )
        stdout_path = child_root / "dag-run.stdout"
        stderr_path = child_root / "dag-run.stderr"
        stdout_path.write_text(completed.stdout, encoding="utf-8")
        stderr_path.write_text(completed.stderr, encoding="utf-8")
        dag_receipt = parse_json_stdout(completed.stdout)
        node_receipt = read_json(child_receipt_path)
        child_status = dag_receipt.get("status")
        if (
            completed.returncode != 0
            or child_status != "PASS"
            or node_receipt.get("status") != "PASS"
        ):
            errors.append(f"{child_id}:child_dag_failed:{completed.returncode}:{child_status}")
        child_receipts.append(
            {
                "item_id": item["item_id"],
                "child_id": child_id,
                "status": child_status,
                "exit_code": completed.returncode,
                "spec_path": str(child_spec_path),
                "receipt_path": str(child_receipt_path),
                "receipt_sha256": (
                    file_sha256(child_receipt_path) if child_receipt_path.is_file() else None
                ),
                "stdout_path": str(stdout_path),
                "stderr_path": str(stderr_path),
            }
        )

    deterministic_child_ids = [f"child-{item['item_id']}" for item in items]
    exact_join_cardinality = len(child_receipts) == len(items) and {
        item["child_id"] for item in child_receipts
    } == set(deterministic_child_ids)
    all_children_passed = all(item.get("status") == "PASS" for item in child_receipts)
    if not exact_join_cardinality:
        errors.append("map_exact_join_cardinality_failed")
    if not all_children_passed:
        errors.append("child_dag_pass_contract_failed")
    status = "PASS" if not errors else "BLOCKED"
    aggregate_path = root / "map-child-dag-conformance.json"
    aggregate = {
        "schema": MAP_CHILD_SCHEMA,
        "status": status,
        "mocked": False,
        "live": True,
        "provider_live": False,
        "map_node": {
            "item_count": len(items),
            "deterministic_child_ids": deterministic_child_ids,
            "exact_join_cardinality": exact_join_cardinality,
        },
        "child_dag": {
            "child_count": len(child_receipts),
            "all_children_passed": all_children_passed,
            "child_receipts": child_receipts,
        },
        "commands_run": child_commands,
        "errors": errors,
    }
    write_json(aggregate_path, aggregate)
    receipt = {
        "schema": GENERIC_DAG_NODE_RECEIPT_SCHEMA,
        "node_id": "map-items",
        "status": status,
        "verdict": "PASS" if status == "PASS" else "BLOCKED",
        "mocked": False,
        "live": True,
        "provider_live": False,
        "goal_hash": args.goal_hash,
        "artifacts": [artifact(aggregate_path, MAP_CHILD_SCHEMA)],
        "commands_run": child_commands,
        "errors": errors,
        "policy_exceptions": [],
        "handoff_summary": (
            f"Mapped {len(items)} items into {len(child_receipts)} child DAGs "
            f"with exact cardinality {exact_join_cardinality}"
        ),
    }
    write_json(receipt_path, receipt)
    print(json.dumps(aggregate, indent=2, sort_keys=True))
    return 0 if status == "PASS" else 2


def _child_receipt_payload(child_id: str, item: dict[str, Any], goal_hash: str) -> dict[str, Any]:
    return {
        "schema": GENERIC_DAG_NODE_RECEIPT_SCHEMA,
        "node_id": child_id,
        "status": "PASS",
        "verdict": "PASS",
        "mocked": False,
        "live": True,
        "provider_live": False,
        "goal_hash": goal_hash,
        "accepted_output": {
            "item_id": item["item_id"],
            "value": item["value"],
            "result": item["value"] * 2,
        },
        "artifacts": [],
        "commands_run": [],
        "errors": [],
        "policy_exceptions": [],
        "handoff_summary": f"Processed map item {item['item_id']}",
    }


def parse_json_stdout(stdout: str) -> dict[str, Any]:
    text = stdout.strip()
    start = text.find("{")
    end = text.rfind("}")
    if start < 0 or end < start:
        return {}
    try:
        payload = json.loads(text[start : end + 1])
    except json.JSONDecodeError:
        return {}
    return payload if isinstance(payload, dict) else {}


def read_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def artifact(path: Path, schema: str) -> dict[str, Any]:
    return {
        "path": str(path),
        "schema": schema,
        "sha256": file_sha256(path),
        "bytes": path.stat().st_size,
    }


def file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


if __name__ == "__main__":
    raise SystemExit(main())
