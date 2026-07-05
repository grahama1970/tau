"""Compliance evidence packaging for zero-trust Tau runs."""

from __future__ import annotations

import hashlib
import json
import shutil
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

COMPLIANCE_PACKAGE_SCHEMA = "tau.compliance_evidence_package.v1"

NON_CLAIMS = [
    "ITAR compliance.",
    "Export-control legal sufficiency.",
    "Complete sandbox enforcement.",
    "Human identity verification unless a provenance receipt exists.",
    "Provider/model semantic quality.",
    "That Memory facts are true.",
    "That an evidence case is sufficient for closure.",
    "That a DAG or agent swarm is trustworthy.",
]

EXPECTED_FILES = {
    "dag_receipt": "dag-receipt.json",
    "dag_contract": "dag-contract.json",
    "goal": "goal.json",
    "policy_profile": "policy-profile.json",
    "data_boundary": "data-boundary.json",
    "zero_trust_preflight": "zero-trust-preflight-receipt.json",
    "memory_intent_gate": "memory-intent-gate-receipt.json",
    "evidence_case_gate": "evidence-case-gate-receipt.json",
    "evidence_validation": "evidence-validation-receipt.json",
}

RECEIPT_DIRECTORIES = {
    "command-policy-receipts": ("command-policy", "command_policy"),
    "research-source-receipts": ("research-source", "research_source"),
    "approval-receipts": ("approval",),
    "herdr-lease-receipts": ("herdr-lease", "workspace-lease", "session-ownership"),
    "github-apply-policy-receipts": ("github-apply-policy",),
    "browser-cdp-proof-receipts": ("browser-cdp-proof",),
}


def build_compliance_evidence_package(
    *,
    run_dir: Path,
    out_dir: Path,
    force: bool = False,
) -> dict[str, Any]:
    """Package existing Tau run artifacts for external review."""

    resolved_run = run_dir.expanduser().resolve()
    resolved_out = out_dir.expanduser().resolve()
    if not resolved_run.exists() or not resolved_run.is_dir():
        return _blocked_receipt(
            run_dir=resolved_run,
            out_dir=resolved_out,
            errors=[f"run_dir does not exist or is not a directory: {resolved_run}"],
        )
    if resolved_out.exists() and any(resolved_out.iterdir()) and not force:
        return _blocked_receipt(
            run_dir=resolved_run,
            out_dir=resolved_out,
            errors=[f"out_dir is not empty: {resolved_out}"],
        )
    if force and resolved_out.exists():
        shutil.rmtree(resolved_out)
    resolved_out.mkdir(parents=True, exist_ok=True)

    items: list[dict[str, Any]] = []
    missing: list[dict[str, str]] = []

    dag_receipt = _read_optional_json(resolved_run / "dag-receipt.json")
    _copy_expected_file(
        source=resolved_run / "dag-receipt.json",
        destination=resolved_out / EXPECTED_FILES["dag_receipt"],
        kind="dag_receipt",
        items=items,
        missing=missing,
    )

    dag_contract, contract_source = _resolve_dag_contract(dag_receipt)
    if dag_contract is not None:
        _write_generated_json(
            destination=resolved_out / EXPECTED_FILES["dag_contract"],
            payload=dag_contract,
            kind="dag_contract",
            source_path=contract_source,
            items=items,
        )
        goal = dag_contract.get("goal") if isinstance(dag_contract.get("goal"), Mapping) else None
        if goal is not None:
            _write_generated_json(
                destination=resolved_out / EXPECTED_FILES["goal"],
                payload=dict(goal),
                kind="goal",
                source_path=contract_source,
                items=items,
            )
        else:
            missing.append({"kind": "goal", "reason": "dag contract has no goal object"})
        _package_policy_or_boundary(
            contract=dag_contract,
            contract_source=contract_source,
            contract_key="policy_profile",
            destination=resolved_out / EXPECTED_FILES["policy_profile"],
            kind="policy_profile",
            items=items,
            missing=missing,
        )
        _package_policy_or_boundary(
            contract=dag_contract,
            contract_source=contract_source,
            contract_key="data_boundary",
            destination=resolved_out / EXPECTED_FILES["data_boundary"],
            kind="data_boundary",
            items=items,
            missing=missing,
        )
    else:
        for kind in ("dag_contract", "goal", "policy_profile", "data_boundary"):
            missing.append({"kind": kind, "reason": "no readable DAG contract was found"})

    for kind, filename in EXPECTED_FILES.items():
        if kind in {"dag_receipt", "dag_contract", "goal", "policy_profile", "data_boundary"}:
            continue
        source = _receipt_source_from_dag_receipt(dag_receipt, field=f"{kind}_receipt")
        if source is None:
            source = resolved_run / filename
        _copy_expected_file(
            source=source,
            destination=resolved_out / filename,
            kind=kind,
            items=items,
            missing=missing,
        )

    for directory_name, markers in RECEIPT_DIRECTORIES.items():
        copied = _copy_matching_receipts(
            run_dir=resolved_run,
            out_dir=resolved_out / directory_name,
            markers=markers,
            items=items,
        )
        if copied == 0:
            missing.append({"kind": directory_name, "reason": "no matching receipts found"})

    non_claims_path = resolved_out / "non-claims.md"
    non_claims_path.write_text(_non_claims_markdown(), encoding="utf-8")
    items.append(_item_for_path(non_claims_path, kind="non_claims", source_path=None))

    manifest = {
        "schema": COMPLIANCE_PACKAGE_SCHEMA,
        "ok": True,
        "status": "PASS",
        "mocked": False,
        "live": False,
        "provider_live": False,
        "checked_at": _utc_stamp(),
        "run_dir": str(resolved_run),
        "package_dir": str(resolved_out),
        "item_count": len(items),
        "missing_expected_items": missing,
        "items": items,
        "proof_scope": {
            "proves": [
                "Tau collected existing run artifacts into a review package.",
                (
                    "Tau recorded package item hashes and source paths for copied or "
                    "derived artifacts."
                ),
                "Tau preserved explicit non-claims for high-stakes review.",
            ],
            "does_not_prove": NON_CLAIMS,
        },
    }
    manifest_path = resolved_out / "package-manifest.json"
    manifest_path.write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    manifest["manifest_path"] = str(manifest_path)
    manifest["manifest_sha256"] = f"sha256:{_sha256(manifest_path)}"
    manifest_path.write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return manifest


def _resolve_dag_contract(
    dag_receipt: Mapping[str, Any] | None,
) -> tuple[dict[str, Any] | None, Path | None]:
    if not isinstance(dag_receipt, Mapping):
        return None, None
    contract_path = dag_receipt.get("contract_path")
    if not isinstance(contract_path, str) or not contract_path:
        return None, None
    path = Path(contract_path).expanduser().resolve()
    payload = _read_optional_json(path)
    return (payload or None), path


def _package_policy_or_boundary(
    *,
    contract: Mapping[str, Any],
    contract_source: Path | None,
    contract_key: str,
    destination: Path,
    kind: str,
    items: list[dict[str, Any]],
    missing: list[dict[str, str]],
) -> None:
    value = contract.get(contract_key)
    if isinstance(value, Mapping):
        _write_generated_json(
            destination=destination,
            payload=dict(value),
            kind=kind,
            source_path=contract_source,
            items=items,
        )
        return
    if isinstance(value, str) and contract_source is not None:
        path = Path(value)
        if not path.is_absolute():
            path = contract_source.parent / path
        if path.exists():
            _copy_expected_file(
                source=path,
                destination=destination,
                kind=kind,
                items=items,
                missing=missing,
            )
            return
    missing.append({"kind": kind, "reason": f"dag contract has no readable {contract_key}"})


def _receipt_source_from_dag_receipt(
    dag_receipt: Mapping[str, Any] | None,
    *,
    field: str,
) -> Path | None:
    if not isinstance(dag_receipt, Mapping):
        return None
    value = dag_receipt.get(field)
    if isinstance(value, str) and value:
        return Path(value).expanduser().resolve()
    return None


def _copy_expected_file(
    *,
    source: Path,
    destination: Path,
    kind: str,
    items: list[dict[str, Any]],
    missing: list[dict[str, str]],
) -> None:
    resolved_source = source.expanduser().resolve()
    if not resolved_source.exists() or not resolved_source.is_file():
        missing.append({"kind": kind, "reason": f"missing source: {resolved_source}"})
        return
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(resolved_source, destination)
    items.append(_item_for_path(destination, kind=kind, source_path=resolved_source))


def _write_generated_json(
    *,
    destination: Path,
    payload: Mapping[str, Any],
    kind: str,
    source_path: Path | None,
    items: list[dict[str, Any]],
) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    items.append(_item_for_path(destination, kind=kind, source_path=source_path))


def _copy_matching_receipts(
    *,
    run_dir: Path,
    out_dir: Path,
    markers: tuple[str, ...],
    items: list[dict[str, Any]],
) -> int:
    copied = 0
    for path in sorted(run_dir.rglob("*.json")):
        relative = path.relative_to(run_dir)
        lower = str(relative).lower()
        if not any(marker in lower for marker in markers):
            continue
        if path.name in {"package-manifest.json"}:
            continue
        out_dir.mkdir(parents=True, exist_ok=True)
        destination = out_dir / relative.name
        if destination.exists():
            destination = out_dir / _safe_relative_name(relative)
        shutil.copy2(path, destination)
        items.append(_item_for_path(destination, kind=out_dir.name, source_path=path))
        copied += 1
    return copied


def _safe_relative_name(path: Path) -> str:
    return "__".join(path.parts)


def _item_for_path(path: Path, *, kind: str, source_path: Path | None) -> dict[str, Any]:
    payload = _read_optional_json(path)
    item = {
        "kind": kind,
        "path": str(path),
        "sha256": f"sha256:{_sha256(path)}",
        "bytes": path.stat().st_size,
    }
    if source_path is not None:
        item["source_path"] = str(source_path)
        if source_path.exists() and source_path.is_file():
            item["source_sha256"] = f"sha256:{_sha256(source_path)}"
    if isinstance(payload, Mapping) and isinstance(payload.get("schema"), str):
        item["schema"] = payload["schema"]
    return item


def _read_optional_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _blocked_receipt(*, run_dir: Path, out_dir: Path, errors: list[str]) -> dict[str, Any]:
    return {
        "schema": COMPLIANCE_PACKAGE_SCHEMA,
        "ok": False,
        "status": "BLOCKED",
        "mocked": False,
        "live": False,
        "provider_live": False,
        "checked_at": _utc_stamp(),
        "run_dir": str(run_dir),
        "package_dir": str(out_dir),
        "errors": errors,
        "proof_scope": {
            "proves": [
                "Tau refused to package because required local package inputs were invalid."
            ],
            "does_not_prove": NON_CLAIMS,
        },
    }


def _non_claims_markdown() -> str:
    lines = [
        "# Non-Claims",
        "",
        "This package is evidence for a compliance process. It is not compliance.",
        "",
        "It does not prove:",
        "",
    ]
    lines.extend(f"- {item}" for item in NON_CLAIMS)
    lines.append("")
    return "\n".join(lines)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _utc_stamp() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")
