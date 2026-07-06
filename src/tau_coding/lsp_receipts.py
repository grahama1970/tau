"""Local LSP-style coding evidence receipts.

These receipts provide a deterministic local diagnostics/symbol/rename-plan
surface. They intentionally do not claim semantic correctness or full IDE
parity.
"""

from __future__ import annotations

import ast
import hashlib
import json
import shutil
import subprocess
from collections.abc import Iterable, Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from tau_coding.policy_profile import DATA_BOUNDARY_SCHEMA, POLICY_PROFILE_SCHEMA

LSP_DIAGNOSTICS_RECEIPT_SCHEMA = "tau.lsp_diagnostics_receipt.v1"
LSP_SYMBOL_RECEIPT_SCHEMA = "tau.lsp_symbol_receipt.v1"
LSP_RENAME_RECEIPT_SCHEMA = "tau.lsp_rename_receipt.v1"

DEFAULT_INCLUDE_GLOBS = ("*.py",)


def write_lsp_diagnostics_receipt(
    *,
    workspace: Path,
    output_path: Path,
    required: bool = False,
    zero_trust: bool = False,
    policy_profile: Mapping[str, Any] | None = None,
    data_boundary: Mapping[str, Any] | None = None,
    baseline_receipt_path: Path | None = None,
) -> dict[str, Any]:
    resolved_workspace = workspace.expanduser().resolve()
    alerts = _coding_policy_alerts(
        zero_trust=zero_trust,
        policy_profile=policy_profile,
        data_boundary=data_boundary,
    )
    files = _workspace_files(resolved_workspace, DEFAULT_INCLUDE_GLOBS)
    diagnostics: list[dict[str, Any]] = []
    server_used = "python_ast_parse_adapter"
    server_available = resolved_workspace.is_dir()
    ruff = shutil.which("ruff")
    if server_available and ruff:
        server_used = "ruff_json_adapter"
        diagnostics = _ruff_diagnostics(ruff, resolved_workspace)
    elif server_available:
        diagnostics = _ast_diagnostics(files)
        if not files:
            server_available = False

    if required and not server_available:
        alerts.append(_alert("lsp_server_unavailable", "required diagnostics adapter unavailable"))

    severity_counts = _severity_counts(diagnostics)
    baseline = _read_baseline_receipt(baseline_receipt_path, alerts)
    baseline_counts = (
        _normalize_severity_counts(baseline.get("severity_counts"))
        if baseline is not None
        else None
    )
    diagnostic_delta = _diagnostic_delta(severity_counts, baseline_counts)
    ok = not alerts
    payload = {
        "schema": LSP_DIAGNOSTICS_RECEIPT_SCHEMA,
        "ok": ok,
        "status": "PASS" if ok else "BLOCKED",
        "mocked": False,
        "live": True,
        "provider_live": False,
        "zero_trust": zero_trust,
        "policy_profile": policy_profile,
        "data_boundary": data_boundary,
        "workspace": str(resolved_workspace),
        "language_server_used": server_used,
        "server_available": server_available,
        "files_inspected": [str(path) for path in files],
        "inspected_artifacts": _file_artifacts(files),
        "file_count": len(files),
        "diagnostics": diagnostics,
        "diagnostic_count": len(diagnostics),
        "severity_counts": severity_counts,
        "baseline_receipt_path": (
            str(baseline_receipt_path.expanduser().resolve())
            if baseline_receipt_path is not None
            else None
        ),
        "baseline_severity_counts": baseline_counts,
        "diagnostic_delta": diagnostic_delta,
        "diagnostics_increased": (
            _diagnostics_increased(diagnostic_delta)
            if diagnostic_delta is not None
            else "NOT_EVALUATED"
        ),
        "alerts": alerts,
        "alert_codes": [alert["code"] for alert in alerts],
        "proof_scope": _lsp_proof_scope("diagnostics"),
        "timestamp": _utc_stamp(),
    }
    _write_json(output_path, payload)
    payload["receipt_path"] = str(output_path.expanduser().resolve())
    _write_json(output_path, payload)
    return payload


def write_lsp_symbol_receipt(
    *,
    workspace: Path,
    query: str,
    output_path: Path,
    zero_trust: bool = False,
    policy_profile: Mapping[str, Any] | None = None,
    data_boundary: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    resolved_workspace = workspace.expanduser().resolve()
    alerts = _coding_policy_alerts(
        zero_trust=zero_trust,
        policy_profile=policy_profile,
        data_boundary=data_boundary,
    )
    files = _workspace_files(resolved_workspace, DEFAULT_INCLUDE_GLOBS)
    references = _symbol_references(files, query)
    ok = not alerts
    payload = {
        "schema": LSP_SYMBOL_RECEIPT_SCHEMA,
        "ok": ok,
        "status": "PASS" if ok else "BLOCKED",
        "mocked": False,
        "live": True,
        "provider_live": False,
        "zero_trust": zero_trust,
        "policy_profile": policy_profile,
        "data_boundary": data_boundary,
        "workspace": str(resolved_workspace),
        "language_server_used": "python_ast_symbol_adapter",
        "query": query,
        "files_inspected": [str(path) for path in files],
        "inspected_artifacts": _file_artifacts(files),
        "reference_count": len(references),
        "references": references,
        "alerts": alerts,
        "alert_codes": [alert["code"] for alert in alerts],
        "proof_scope": _lsp_proof_scope("symbols"),
        "timestamp": _utc_stamp(),
    }
    payload["receipt_path"] = str(output_path.expanduser().resolve())
    _write_json(output_path, payload)
    return payload


def write_lsp_rename_plan_receipt(
    *,
    workspace: Path,
    symbol: str,
    new_name: str,
    output_path: Path,
    zero_trust: bool = False,
    policy_profile: Mapping[str, Any] | None = None,
    data_boundary: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    symbol_receipt = write_lsp_symbol_receipt(
        workspace=workspace,
        query=symbol,
        output_path=output_path.with_name(output_path.stem + ".symbols.tmp.json"),
        zero_trust=zero_trust,
        policy_profile=policy_profile,
        data_boundary=data_boundary,
    )
    references = symbol_receipt["references"]
    alerts = list(symbol_receipt.get("alerts", []))
    ok = not alerts
    payload = {
        "schema": LSP_RENAME_RECEIPT_SCHEMA,
        "ok": ok,
        "status": "PASS" if ok else "BLOCKED",
        "mocked": False,
        "live": True,
        "provider_live": False,
        "zero_trust": zero_trust,
        "policy_profile": policy_profile,
        "data_boundary": data_boundary,
        "workspace": str(workspace.expanduser().resolve()),
        "language_server_used": "python_ast_symbol_adapter",
        "symbol": symbol,
        "new_name": new_name,
        "applied": False,
        "reference_count": len(references),
        "inspected_artifacts": list(symbol_receipt.get("inspected_artifacts", [])),
        "references": references,
        "planned_edits": [
            {"file": item["file"], "line": item["line"], "old": symbol, "new": new_name}
            for item in references
        ],
        "alerts": alerts,
        "alert_codes": [alert["code"] for alert in alerts],
        "proof_scope": _lsp_proof_scope("rename plan"),
        "timestamp": _utc_stamp(),
    }
    payload["receipt_path"] = str(output_path.expanduser().resolve())
    _write_json(output_path, payload)
    return payload


def _ruff_diagnostics(ruff: str, workspace: Path) -> list[dict[str, Any]]:
    completed = subprocess.run(
        [ruff, "check", "--output-format", "json", "."],
        cwd=workspace,
        check=False,
        capture_output=True,
        text=True,
        timeout=60,
    )
    try:
        raw = json.loads(completed.stdout or "[]")
    except json.JSONDecodeError:
        raw = []
    diagnostics: list[dict[str, Any]] = []
    if isinstance(raw, list):
        for item in raw:
            if not isinstance(item, Mapping):
                continue
            location = item.get("location") if isinstance(item.get("location"), Mapping) else {}
            diagnostics.append(
                {
                    "source": "ruff",
                    "severity": "error",
                    "code": item.get("code"),
                    "message": item.get("message"),
                    "file": item.get("filename"),
                    "line": location.get("row"),
                    "column": location.get("column"),
                }
            )
    return diagnostics


def _ast_diagnostics(files: list[Path]) -> list[dict[str, Any]]:
    diagnostics: list[dict[str, Any]] = []
    for path in files:
        try:
            ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except SyntaxError as exc:
            diagnostics.append(
                {
                    "source": "python_ast",
                    "severity": "error",
                    "code": "syntax_error",
                    "message": exc.msg,
                    "file": str(path),
                    "line": exc.lineno,
                    "column": exc.offset,
                }
            )
    return diagnostics


def _symbol_references(files: list[Path], query: str) -> list[dict[str, Any]]:
    references: list[dict[str, Any]] = []
    for path in files:
        lines = path.read_text(encoding="utf-8").splitlines()
        for line_number, line in enumerate(lines, start=1):
            if query in line:
                references.append({"file": str(path), "line": line_number, "text": line.strip()})
    return references


def _workspace_files(workspace: Path, globs: Iterable[str]) -> list[Path]:
    if not workspace.is_dir():
        return []
    files: list[Path] = []
    ignored_parts = {".git", ".venv", "__pycache__", "node_modules"}
    for pattern in globs:
        for path in workspace.rglob(pattern):
            if any(part in ignored_parts for part in path.parts):
                continue
            if path.is_file():
                files.append(path.resolve())
    return sorted(set(files))


def _file_artifacts(paths: Iterable[Path]) -> list[dict[str, Any]]:
    artifacts: list[dict[str, Any]] = []
    for path in paths:
        resolved = path.expanduser().resolve()
        if not resolved.exists():
            continue
        artifacts.append(
            {
                "path": str(resolved),
                "sha256": f"sha256:{hashlib.sha256(resolved.read_bytes()).hexdigest()}",
                "bytes": resolved.stat().st_size,
            }
        )
    return artifacts


def _severity_counts(diagnostics: list[dict[str, Any]]) -> dict[str, int]:
    counts = {"error": 0, "warning": 0, "information": 0, "hint": 0}
    for item in diagnostics:
        severity = item.get("severity")
        if severity in counts:
            counts[severity] += 1
        else:
            counts["information"] += 1
    return counts


def _read_baseline_receipt(
    baseline_receipt_path: Path | None,
    alerts: list[dict[str, Any]],
) -> dict[str, Any] | None:
    if baseline_receipt_path is None:
        return None
    resolved = baseline_receipt_path.expanduser().resolve()
    try:
        payload = json.loads(resolved.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        alerts.append(
            _alert("baseline_receipt_unreadable", f"baseline receipt is unreadable: {exc}")
        )
        return None
    if not isinstance(payload, dict):
        alerts.append(_alert("baseline_receipt_not_object", "baseline receipt must be an object"))
        return None
    if payload.get("schema") != LSP_DIAGNOSTICS_RECEIPT_SCHEMA:
        alerts.append(
            _alert(
                "invalid_baseline_receipt_schema",
                f"baseline receipt schema must be {LSP_DIAGNOSTICS_RECEIPT_SCHEMA}",
            )
        )
        return None
    return payload


def _normalize_severity_counts(value: object) -> dict[str, int]:
    counts = {"error": 0, "warning": 0, "information": 0, "hint": 0}
    if not isinstance(value, Mapping):
        return counts
    for key in counts:
        raw = value.get(key)
        if isinstance(raw, int) and raw >= 0:
            counts[key] = raw
    return counts


def _diagnostic_delta(
    current: dict[str, int],
    baseline: dict[str, int] | None,
) -> dict[str, int] | None:
    if baseline is None:
        return None
    return {key: current.get(key, 0) - baseline.get(key, 0) for key in sorted(current)}


def _diagnostics_increased(delta: dict[str, int]) -> bool:
    return any(value > 0 for value in delta.values())


def _coding_policy_alerts(
    *,
    zero_trust: bool,
    policy_profile: Mapping[str, Any] | None,
    data_boundary: Mapping[str, Any] | None,
) -> list[dict[str, Any]]:
    alerts: list[dict[str, Any]] = []
    if zero_trust and policy_profile is None:
        alerts.append(
            _alert("missing_policy_profile", "zero-trust diagnostics require policy_profile")
        )
    if zero_trust and data_boundary is None:
        alerts.append(
            _alert("missing_data_boundary", "zero-trust diagnostics require data_boundary")
        )
    if policy_profile is not None and policy_profile.get("schema") != POLICY_PROFILE_SCHEMA:
        alerts.append(_alert("invalid_policy_profile_schema", "policy_profile schema is invalid"))
    if data_boundary is not None and data_boundary.get("schema") != DATA_BOUNDARY_SCHEMA:
        alerts.append(_alert("invalid_data_boundary_schema", "data_boundary schema is invalid"))
    return alerts


def _lsp_proof_scope(kind: str) -> dict[str, list[str]]:
    return {
        "proves": [
            f"Tau collected local {kind} evidence for the requested workspace.",
            "Tau wrote a typed receipt for the coding evidence.",
        ],
        "does_not_prove": [
            "Semantic correctness of the code.",
            "The full test suite passes.",
            "Runtime behavior is correct.",
            "The adapter is equivalent to every IDE language server.",
        ],
    }


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    resolved = path.expanduser().resolve()
    resolved.parent.mkdir(parents=True, exist_ok=True)
    resolved.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _alert(code: str, message: str) -> dict[str, str]:
    return {"severity": "BLOCK", "code": code, "message": message}


def _utc_stamp() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")
