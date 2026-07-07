"""Local LSP-style coding evidence receipts.

These receipts provide a deterministic local diagnostics/symbol/rename-plan
surface. They intentionally do not claim semantic correctness or full IDE
parity.
"""

from __future__ import annotations

import ast
import fnmatch
import hashlib
import io
import json
import shutil
import subprocess
import tokenize
from collections.abc import Iterable, Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from tau_coding.policy_profile import (
    DATA_BOUNDARY_SCHEMA,
    POLICY_PROFILE_SCHEMA,
    validate_data_boundary,
    validate_policy_profile,
)

LSP_DIAGNOSTICS_RECEIPT_SCHEMA = "tau.lsp_diagnostics_receipt.v1"
LSP_SYMBOL_RECEIPT_SCHEMA = "tau.lsp_symbol_receipt.v1"
LSP_RENAME_RECEIPT_SCHEMA = "tau.lsp_rename_receipt.v1"

DEFAULT_INCLUDE_GLOBS = ("*.py",)


def write_lsp_diagnostics_receipt(
    *,
    workspace: Path,
    output_path: Path,
    goal_hash: str | None = None,
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
        goal_hash=goal_hash,
    )
    all_files = _workspace_files(resolved_workspace, DEFAULT_INCLUDE_GLOBS)
    files, read_denied_paths = _apply_policy_read_denylist(
        all_files,
        workspace=resolved_workspace,
        policy_profile=policy_profile,
    )
    alerts.extend(
        _alert(
            "policy_read_denied",
            f"policy_profile.filesystem.read_denylist denied {path}",
        )
        for path in read_denied_paths
    )
    diagnostics: list[dict[str, Any]] = []
    server_used = "python_ast_parse_adapter"
    server_available = resolved_workspace.is_dir()
    ruff = shutil.which("ruff")
    if server_available and ruff and not read_denied_paths:
        server_used = "ruff_json_adapter"
        diagnostics = _ruff_diagnostics(ruff, resolved_workspace)
    elif server_available:
        diagnostics = _ast_diagnostics(files)
        if not files:
            server_available = False

    if required and not server_available:
        alerts.append(_alert("lsp_server_unavailable", "required diagnostics adapter unavailable"))

    severity_counts = _severity_counts(diagnostics)
    baseline = _read_baseline_receipt(
        baseline_receipt_path,
        alerts,
        expected_goal_hash=goal_hash,
    )
    baseline_counts = (
        _normalize_severity_counts(baseline.get("severity_counts"))
        if baseline is not None
        else None
    )
    diagnostic_delta = _diagnostic_delta(severity_counts, baseline_counts)
    diagnostics_increased = (
        _diagnostics_increased(diagnostic_delta)
        if diagnostic_delta is not None
        else "NOT_EVALUATED"
    )
    if diagnostics_increased is True:
        alerts.append(
            _alert(
                "lsp_diagnostics_regressed",
                "diagnostic severity counts increased relative to the baseline receipt",
            )
        )
    ok = not alerts
    payload = {
        "schema": LSP_DIAGNOSTICS_RECEIPT_SCHEMA,
        "ok": ok,
        "status": "PASS" if ok else "BLOCKED",
        "mocked": False,
        "live": True,
        "provider_live": False,
        "goal_hash": goal_hash,
        "zero_trust": zero_trust,
        "policy_profile": policy_profile,
        "data_boundary": data_boundary,
        "workspace": str(resolved_workspace),
        "language_server_used": server_used,
        "server_available": server_available,
        "files_inspected": [str(path) for path in files],
        "policy_read_denied_paths": read_denied_paths,
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
        "baseline_receipt_artifact": _optional_artifact_summary(baseline_receipt_path),
        "baseline_severity_counts": baseline_counts,
        "diagnostic_delta": diagnostic_delta,
        "diagnostics_increased": diagnostics_increased,
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
    goal_hash: str | None = None,
    zero_trust: bool = False,
    policy_profile: Mapping[str, Any] | None = None,
    data_boundary: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    resolved_workspace = workspace.expanduser().resolve()
    alerts = _coding_policy_alerts(
        zero_trust=zero_trust,
        policy_profile=policy_profile,
        data_boundary=data_boundary,
        goal_hash=goal_hash,
    )
    files, read_denied_paths = _apply_policy_read_denylist(
        _workspace_files(resolved_workspace, DEFAULT_INCLUDE_GLOBS),
        workspace=resolved_workspace,
        policy_profile=policy_profile,
    )
    alerts.extend(
        _alert(
            "policy_read_denied",
            f"policy_profile.filesystem.read_denylist denied {path}",
        )
        for path in read_denied_paths
    )
    if not query.isidentifier():
        alerts.append(_alert("invalid_query", "symbol query must be a valid Python identifier"))
    references = _symbol_references(files, query)
    ok = not alerts
    payload = {
        "schema": LSP_SYMBOL_RECEIPT_SCHEMA,
        "ok": ok,
        "status": "PASS" if ok else "BLOCKED",
        "mocked": False,
        "live": True,
        "provider_live": False,
        "goal_hash": goal_hash,
        "zero_trust": zero_trust,
        "policy_profile": policy_profile,
        "data_boundary": data_boundary,
        "workspace": str(resolved_workspace),
        "language_server_used": "python_ast_symbol_adapter",
        "query": query,
        "files_inspected": [str(path) for path in files],
        "policy_read_denied_paths": read_denied_paths,
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
    goal_hash: str | None = None,
    zero_trust: bool = False,
    policy_profile: Mapping[str, Any] | None = None,
    data_boundary: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    symbol_receipt_path = output_path.with_name(output_path.stem + ".symbols.tmp.json")
    symbol_receipt = write_lsp_symbol_receipt(
        workspace=workspace,
        query=symbol,
        output_path=symbol_receipt_path,
        goal_hash=goal_hash,
        zero_trust=zero_trust,
        policy_profile=policy_profile,
        data_boundary=data_boundary,
    )
    references = symbol_receipt["references"]
    alerts = list(symbol_receipt.get("alerts", []))
    if not symbol.isidentifier():
        alerts.append(_alert("invalid_symbol", "rename symbol must be a valid identifier"))
    if not new_name.isidentifier():
        alerts.append(_alert("invalid_new_name", "rename new_name must be a valid identifier"))
    if symbol == new_name:
        alerts.append(_alert("rename_noop", "rename new_name must differ from symbol"))
    if not references:
        alerts.append(_alert("symbol_not_found", "rename symbol was not found in workspace"))
    planned_edits = [
        _planned_rename_edit(
            reference=item,
            symbol=symbol,
            new_name=new_name,
            workspace=workspace,
            policy_profile=policy_profile,
        )
        for item in references
    ]
    policy_write_denied_paths = [
        item["file"] for item in planned_edits if item.get("policy_write_allowed") is False
    ]
    if policy_write_denied_paths:
        alerts.append(
            _alert(
                "policy_write_disallowed",
                "policy_profile.filesystem.write_allowlist denied planned edits: "
                f"{policy_write_denied_paths}",
            )
        )
    ok = not alerts
    payload = {
        "schema": LSP_RENAME_RECEIPT_SCHEMA,
        "ok": ok,
        "status": "PASS" if ok else "BLOCKED",
        "mocked": False,
        "live": True,
        "provider_live": False,
        "goal_hash": goal_hash,
        "zero_trust": zero_trust,
        "policy_profile": policy_profile,
        "data_boundary": data_boundary,
        "workspace": str(workspace.expanduser().resolve()),
        "language_server_used": "python_ast_symbol_adapter",
        "symbol": symbol,
        "new_name": new_name,
        "applied": False,
        "symbol_receipt_artifact": _artifact_summary(symbol_receipt_path),
        "reference_count": len(references),
        "policy_read_denied_paths": list(symbol_receipt.get("policy_read_denied_paths", [])),
        "policy_write_denied_paths": policy_write_denied_paths,
        "inspected_artifacts": list(symbol_receipt.get("inspected_artifacts", [])),
        "references": references,
        "planned_edits": planned_edits,
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
    if not query.isidentifier():
        return references
    for path in files:
        text = path.read_text(encoding="utf-8")
        lines = text.splitlines()
        try:
            tokens = tokenize.generate_tokens(io.StringIO(text).readline)
            for token in tokens:
                if token.type != tokenize.NAME or token.string != query:
                    continue
                line_number = token.start[0]
                line = lines[line_number - 1] if 0 < line_number <= len(lines) else ""
                references.append(
                    {
                        "file": str(path),
                        "line": line_number,
                        "column": token.start[1] + 1,
                        "text": line.strip(),
                    }
                )
        except tokenize.TokenError:
            continue
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


def _apply_policy_read_denylist(
    files: list[Path],
    *,
    workspace: Path,
    policy_profile: Mapping[str, Any] | None,
) -> tuple[list[Path], list[str]]:
    denylist = _policy_read_denylist(policy_profile)
    if denylist is None:
        return files, []
    allowed: list[Path] = []
    denied: list[str] = []
    for path in files:
        try:
            relative = path.resolve().relative_to(workspace).as_posix()
        except ValueError:
            relative = path.name
        if any(fnmatch.fnmatch(relative, _normalize_policy_glob(pattern)) for pattern in denylist):
            denied.append(relative)
        else:
            allowed.append(path)
    return allowed, denied


def _policy_read_denylist(policy_profile: Mapping[str, Any] | None) -> list[str] | None:
    if policy_profile is None:
        return None
    filesystem = policy_profile.get("filesystem")
    if not isinstance(filesystem, Mapping):
        return None
    read_denylist = filesystem.get("read_denylist")
    if not _is_string_list(read_denylist):
        return None
    return [item for item in read_denylist]


def _planned_rename_edit(
    *,
    reference: Mapping[str, Any],
    symbol: str,
    new_name: str,
    workspace: Path,
    policy_profile: Mapping[str, Any] | None,
) -> dict[str, Any]:
    path = _relative_reference_path(reference.get("file"), workspace)
    write_allowlist = _policy_write_allowlist(policy_profile)
    policy_write_allowed = (
        None
        if write_allowlist is None
        else any(
            fnmatch.fnmatch(path, _normalize_policy_glob(pattern))
            for pattern in write_allowlist
        )
    )
    return {
        "file": path,
        "line": reference.get("line"),
        "old": symbol,
        "new": new_name,
        "policy_write_allowed": policy_write_allowed,
    }


def _relative_reference_path(value: object, workspace: Path) -> str:
    path = str(value or "")
    if not path:
        return path
    candidate = Path(path).expanduser()
    if not candidate.is_absolute():
        return path
    try:
        return candidate.resolve().relative_to(workspace.expanduser().resolve()).as_posix()
    except ValueError:
        return path


def _policy_write_allowlist(policy_profile: Mapping[str, Any] | None) -> list[str] | None:
    if policy_profile is None:
        return None
    filesystem = policy_profile.get("filesystem")
    if not isinstance(filesystem, Mapping):
        return None
    write_allowlist = filesystem.get("write_allowlist")
    if not _is_string_list(write_allowlist):
        return None
    return [item for item in write_allowlist]


def _normalize_policy_glob(pattern: str) -> str:
    return pattern.removeprefix("./")


def _file_artifacts(paths: Iterable[Path]) -> list[dict[str, Any]]:
    artifacts: list[dict[str, Any]] = []
    for path in paths:
        resolved = path.expanduser().resolve()
        if not resolved.exists():
            continue
        artifacts.append(
            {
                "path": str(resolved),
                "exists": True,
                "sha256": f"sha256:{hashlib.sha256(resolved.read_bytes()).hexdigest()}",
                "bytes": resolved.stat().st_size,
            }
        )
    return artifacts


def _artifact_summary(path: Path) -> dict[str, Any]:
    resolved = path.expanduser().resolve()
    if not resolved.exists() or not resolved.is_file():
        return {"path": str(resolved), "exists": False, "sha256": None, "bytes": None}
    return {
        "path": str(resolved),
        "exists": True,
        "sha256": f"sha256:{hashlib.sha256(resolved.read_bytes()).hexdigest()}",
        "bytes": resolved.stat().st_size,
    }


def _optional_artifact_summary(path: Path | None) -> dict[str, Any] | None:
    if path is None:
        return None
    return _artifact_summary(path)


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
    *,
    expected_goal_hash: str | None,
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
    if payload.get("ok") is not True or payload.get("status") != "PASS":
        alerts.append(
            _alert(
                "baseline_receipt_not_pass",
                "baseline diagnostics receipt must be PASS with ok:true",
            )
        )
        return None
    baseline_goal_hash = payload.get("goal_hash")
    if expected_goal_hash is not None:
        if not isinstance(baseline_goal_hash, str) or not baseline_goal_hash:
            alerts.append(
                _alert(
                    "baseline_receipt_missing_goal_hash",
                    "baseline diagnostics receipt must include goal_hash",
                )
            )
            return None
        if baseline_goal_hash != expected_goal_hash:
            alerts.append(
                _alert(
                    "baseline_receipt_goal_hash_mismatch",
                    "baseline diagnostics receipt goal_hash does not match current receipt",
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
    goal_hash: str | None,
) -> list[dict[str, Any]]:
    alerts: list[dict[str, Any]] = []
    if zero_trust and not goal_hash:
        alerts.append(_alert("missing_goal_hash", "zero-trust LSP receipt requires goal_hash"))
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
    elif isinstance(policy_profile, Mapping):
        errors = validate_policy_profile(dict(policy_profile))
        if errors:
            alerts.append(
                _alert("invalid_policy_profile", "policy_profile is invalid", errors=errors)
            )
        filesystem = policy_profile.get("filesystem")
        if isinstance(filesystem, Mapping):
            read_denylist = filesystem.get("read_denylist")
            if read_denylist is not None and not _is_string_list(read_denylist):
                alerts.append(
                    _alert(
                        "invalid_policy_read_denylist",
                        "policy_profile.filesystem.read_denylist must be a list of strings",
                    )
                )
            write_allowlist = filesystem.get("write_allowlist")
            if write_allowlist is not None and not _is_string_list(write_allowlist):
                alerts.append(
                    _alert(
                        "invalid_policy_write_allowlist",
                        "policy_profile.filesystem.write_allowlist must be a list of strings",
                    )
                )
    if data_boundary is not None and data_boundary.get("schema") != DATA_BOUNDARY_SCHEMA:
        alerts.append(_alert("invalid_data_boundary_schema", "data_boundary schema is invalid"))
    elif data_boundary is not None:
        errors = validate_data_boundary(dict(data_boundary))
        if errors:
            alerts.append(
                _alert("invalid_data_boundary", "data_boundary is invalid", errors=errors)
            )
        if data_boundary.get("classification") == "classified-not-allowed":
            alerts.append(
                _alert(
                    "classified_not_allowed",
                    "classified-not-allowed data may not be routed to LSP evidence",
                )
            )
    return alerts


def _is_string_list(value: object) -> bool:
    return isinstance(value, list) and all(isinstance(item, str) for item in value)


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


def _alert(code: str, message: str, *, errors: list[str] | None = None) -> dict[str, Any]:
    alert: dict[str, Any] = {"severity": "BLOCK", "code": code, "message": message}
    if errors:
        alert["errors"] = errors
    return alert


def _utc_stamp() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")
