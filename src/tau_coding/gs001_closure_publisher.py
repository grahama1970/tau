"""GS001 closure-publisher replay receipt.

This module is intentionally narrow: it publishes a Tau terminal receipt for a
committed pdf_oxide GS001 closure-state bundle after checking that the bundle
and the Tau DAG contract carry the same current goal hash.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from tau_coding.project_dag import load_dag_contract_payload, validate_dag_contract

SCHEMA = "tau.gs001_closure_publisher_receipt.v1"
EXPECTED_NODE_ID = "closure-publisher"


def publish_gs001_closure_receipt(
    *,
    repo_root: Path,
    dag_contract_path: Path,
    closure_state_path: Path,
    terminal_receipt_path: Path,
    visual_receipt_path: Path,
    output_path: Path,
    expected_goal_hash: str | None = None,
) -> dict[str, Any]:
    root = repo_root.expanduser().resolve()
    contract_path = _resolve(root, dag_contract_path)
    closure_path = _resolve(root, closure_state_path)
    terminal_path = _resolve(root, terminal_receipt_path)
    visual_path = _resolve(root, visual_receipt_path)
    html_path = closure_path.with_name("gs001-closure-page.html")
    screenshot_path = visual_path.with_name("gs001-closure-page.png")
    out = output_path.expanduser().resolve()

    contract_payload = load_dag_contract_payload(contract_path)
    contract = validate_dag_contract(contract_payload)
    closure_state = _read_object(closure_path, "closure state")
    terminal = _read_object(terminal_path, "terminal receipt")
    visual = _read_object(visual_path, "visual receipt")

    errors: list[str] = []
    goal_hash = str(contract.goal.get("goal_hash") or "")
    if expected_goal_hash is not None and goal_hash != expected_goal_hash:
        errors.append("stale_goal_hash")
    for label, observed in (
        ("closure_state.goal_hash", closure_state.get("goal_hash")),
        ("closure_state.dag_goal_hash", closure_state.get("dag_goal_hash")),
        ("terminal_receipt.goal_hash", terminal.get("goal_hash")),
    ):
        if observed != goal_hash:
            errors.append(f"{label}_mismatch")
    if EXPECTED_NODE_ID not in contract.nodes:
        errors.append("closure_publisher_node_missing")
    if EXPECTED_NODE_ID not in contract.terminal_nodes:
        errors.append("closure_publisher_terminal_node_missing")
    if not html_path.is_file():
        errors.append("closure_page_html_missing")
    if not screenshot_path.is_file():
        errors.append("closure_page_png_missing")

    terminal_status = _terminal_status(closure_state, errors)
    receipt = {
        "schema": SCHEMA,
        "ok": not errors,
        "status": "PASS" if not errors else "BLOCKED",
        "verdict": "PASS" if not errors else "STALE_GOAL_HASH",
        "mocked": False,
        "live": True,
        "provider_live": False,
        "dag_id": contract.dag_id,
        "node_id": EXPECTED_NODE_ID,
        "goal_hash": goal_hash,
        "expected_goal_hash": expected_goal_hash,
        "terminal_status": terminal_status if not errors else "stale_goal_hash",
        "repo_root": str(root),
        "source_commit": _git_rev_parse(root),
        "references": [
            _artifact_ref(root, "dag_contract", contract_path),
            _artifact_ref(root, "closure_state_json", closure_path),
            _artifact_ref(root, "closure_page_html", html_path),
            _artifact_ref(root, "terminal_receipt_json", terminal_path),
            _artifact_ref(root, "visual_receipt_json", visual_path),
            _artifact_ref(root, "visual_screenshot_png", screenshot_path),
        ],
        "closure_counts": closure_state.get("counts"),
        "blocking_items": closure_state.get("blocking_items"),
        "terminal_reason": terminal.get("terminal_reason") or closure_state.get("terminal_reason"),
        "errors": errors,
        "proof_scope": {
            "mocked": False,
            "live": True,
            "proves": [
                "The GS001 Tau DAG contract parses under Tau's dag contract validator.",
                "The closure-publisher terminal receipt is bound to the current DAG goal hash.",
                "The committed closure-state JSON, HTML page, visual receipt, and screenshot are hash-referenced.",
            ],
            "does_not_prove": [
                "Human acceptance.",
                "Full GS001 anti-overfit replay beyond the committed closure-state bundle.",
                "Provider or model semantic quality.",
            ],
        },
    }
    _write_json(out, receipt)
    return receipt


def _terminal_status(closure_state: dict[str, Any], errors: list[str]) -> str:
    if errors:
        return "stale_goal_hash"
    criteria = closure_state.get("criteria")
    blocking = closure_state.get("blocking_items")
    rows = (
        [item for item in criteria if isinstance(item, dict)] if isinstance(criteria, list) else []
    )
    blockers = (
        [item for item in blocking if isinstance(item, dict)] if isinstance(blocking, list) else []
    )
    if any(item.get("status") == "PENDING_HUMAN" for item in rows + blockers):
        return "pending_human"
    if blockers:
        return "pending_human"
    return "accepted"


def _resolve(root: Path, path: Path) -> Path:
    expanded = path.expanduser()
    return expanded.resolve() if expanded.is_absolute() else (root / expanded).resolve()


def _read_object(path: Path, label: str) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"{label} unavailable: {exc}") from exc
    if not isinstance(payload, dict):
        raise RuntimeError(f"{label} must be an object")
    return payload


def _artifact_ref(root: Path, kind: str, path: Path) -> dict[str, Any]:
    return {
        "kind": kind,
        "path": str(path),
        "repo_relative_path": _repo_relative(root, path),
        "sha256": f"sha256:{_sha256(path)}",
        "bytes": path.stat().st_size,
    }


def _repo_relative(root: Path, path: Path) -> str:
    try:
        return str(path.relative_to(root))
    except ValueError:
        return str(path)


def _git_rev_parse(root: Path) -> str | None:
    head = root / ".git" / "HEAD"
    if not head.exists():
        return None
    import subprocess

    result = subprocess.run(
        ["git", "-C", str(root), "rev-parse", "HEAD"],
        capture_output=True,
        text=True,
        check=False,
    )
    return result.stdout.strip() if result.returncode == 0 else None


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
