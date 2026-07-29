#!/usr/bin/env python3
"""Generate a reviewer-ready immutable-goal acceptance readback.

The readback is intentionally not an acceptance claim. It verifies the canonical
audit bundle, indexes proof artifacts, and records the exact unmet criteria.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


class ReadbackError(RuntimeError):
    pass


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return f"sha256:{digest.hexdigest()}"


def _load_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ReadbackError(f"invalid_json:{path}:{exc}") from exc
    if not isinstance(payload, dict):
        raise ReadbackError(f"json_not_object:{path}")
    return payload


def _manifest_records(manifest: dict[str, Any]) -> list[dict[str, Any]]:
    records = manifest.get("artifacts")
    if not isinstance(records, list):
        raise ReadbackError("manifest_artifacts_not_list")
    return [record for record in records if isinstance(record, dict)]


def _check_artifact_manifest(manifest_path: Path) -> list[dict[str, Any]]:
    manifest = _load_json(manifest_path)
    root = manifest_path.parent.resolve()
    records = _manifest_records(manifest)
    if manifest.get("artifact_count") != len(records):
        raise ReadbackError("artifact_count_mismatch")
    checked: list[dict[str, Any]] = []
    for record in records:
        rel = str(record.get("path") or "")
        expected_sha = str(record.get("sha256") or "")
        expected_bytes = int(record.get("bytes") or 0)
        if not rel or rel.startswith("/") or ".." in Path(rel).parts:
            raise ReadbackError(f"unsafe_manifest_path:{rel}")
        path = root / rel
        if not path.exists():
            raise ReadbackError(f"manifest_artifact_missing:{rel}")
        actual_sha = _sha256(path)
        actual_bytes = path.stat().st_size
        if actual_sha != expected_sha:
            raise ReadbackError(f"manifest_artifact_hash_mismatch:{rel}")
        if actual_bytes != expected_bytes:
            raise ReadbackError(f"manifest_artifact_size_mismatch:{rel}")
        checked.append({
            "path": rel,
            "sha256": actual_sha,
            "bytes": actual_bytes,
        })
    return checked


def _relative_artifact_path(path_value: str, *, manifest_root: Path, original_root: str | None) -> Path:
    path = Path(path_value)
    if path.is_absolute():
        if original_root:
            try:
                return manifest_root / path.relative_to(original_root)
            except ValueError:
                pass
        return manifest_root / path.name
    return manifest_root / path


def _check_audit(
    *,
    audit_path: Path,
    manifest_path: Path,
    expected_source_ref: str | None,
) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]]]:
    audit = _load_json(audit_path)
    manifest = _load_json(manifest_path)
    manifest_root = manifest_path.parent.resolve()
    original_root = manifest.get("artifact_root")
    if not isinstance(original_root, str):
        original_root = None

    if audit.get("schema") != "tau.immutable_goal_audit.v1":
        raise ReadbackError("audit_schema_mismatch")
    if audit.get("status") != "PASS":
        raise ReadbackError("audit_status_not_pass")
    if audit.get("mocked") is not False or audit.get("live") is not True:
        raise ReadbackError("audit_live_mocked_flags_invalid")
    if expected_source_ref and audit.get("source_ref") != expected_source_ref:
        raise ReadbackError("audit_source_ref_mismatch")

    criteria = audit.get("criteria")
    if not isinstance(criteria, list) or len(criteria) != 10:
        raise ReadbackError("audit_criteria_count_mismatch")
    for criterion in criteria[:9]:
        if criterion.get("status") != "ESTABLISHED":
            raise ReadbackError(f"criterion_not_established:{criterion.get('criterion')}")
    if criteria[9].get("criterion") != 10 or criteria[9].get("status") != "MISSING":
        raise ReadbackError("human_acceptance_criterion_not_missing")
    if audit.get("first_unmet_criterion") != 10:
        raise ReadbackError("first_unmet_criterion_not_10")

    proof_index: list[dict[str, Any]] = []
    for proof in audit.get("supplied_proofs", []):
        if not isinstance(proof, dict):
            raise ReadbackError("supplied_proof_not_object")
        if proof.get("status") != "PASS" or proof.get("mocked") is not False or proof.get("live") is not True:
            raise ReadbackError(f"supplied_proof_invalid:{proof.get('label')}")
        path = _relative_artifact_path(str(proof.get("path") or ""), manifest_root=manifest_root, original_root=original_root)
        if not path.exists():
            raise ReadbackError(f"supplied_proof_missing:{proof.get('label')}")
        if _sha256(path) != proof.get("sha256"):
            raise ReadbackError(f"supplied_proof_hash_mismatch:{proof.get('label')}")
        proof_index.append({
            "label": proof.get("label"),
            "path": str(path),
            "sha256": proof.get("sha256"),
            "schema": proof.get("schema"),
            "live": proof.get("live"),
            "mocked": proof.get("mocked"),
            "provider_live": proof.get("provider_live"),
        })

    screenshot_index: list[dict[str, Any]] = []
    for proof in audit.get("supplied_proofs", []):
        for screenshot in proof.get("screenshots") or []:
            if not isinstance(screenshot, dict):
                raise ReadbackError("screenshot_not_object")
            path = _relative_artifact_path(
                str(screenshot.get("path") or ""),
                manifest_root=manifest_root,
                original_root=original_root,
            )
            if not path.exists():
                raise ReadbackError(f"screenshot_missing:{proof.get('label')}:{screenshot.get('kind')}")
            if _sha256(path) != screenshot.get("sha256"):
                raise ReadbackError(f"screenshot_hash_mismatch:{proof.get('label')}:{screenshot.get('kind')}")
            screenshot_index.append({
                "label": proof.get("label"),
                "kind": screenshot.get("kind"),
                "path": str(path),
                "sha256": screenshot.get("sha256"),
                "bytes": path.stat().st_size,
            })

    return audit, proof_index, screenshot_index


def _write_markdown(path: Path, readback: dict[str, Any]) -> None:
    unmet = readback["remaining_unmet_criteria"]
    criteria_lines = [
        f"- Criterion {item['criterion']}: {item['status']}"
        for item in readback["criteria"]
    ]
    proof_lines = [
        f"- `{item['label']}`: `{item['sha256']}`"
        for item in readback["proof_index"]
    ]
    text = f"""# Tau Immutable Goal Acceptance Readback

Status: {readback['status']}

Immutable Goal: {readback['immutable_goal_status']}

Source ref: `{readback['source_ref']}`

## Summary

This bundle read back the canonical Tau proof artifact manifest, immutable-goal
audit receipt, supplied proof receipts, and screenshots. It is reviewer-ready
evidence, not human acceptance.

## Criteria

{chr(10).join(criteria_lines)}

## Remaining Unmet Criteria

{json.dumps(unmet, indent=2)}

## Proof Index

{chr(10).join(proof_lines)}

## Boundaries

- mocked: {str(readback['mocked']).lower()}
- live: {str(readback['live']).lower()}
- provider_live: {str(readback['provider_live']).lower()}
- Human acceptance remains required before the immutable goal can be marked achieved.
"""
    path.write_text(text, encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--audit", type=Path, required=True)
    parser.add_argument("--artifact-manifest", type=Path, required=True)
    parser.add_argument("--project-state", type=Path)
    parser.add_argument("--source-ref")
    parser.add_argument("--out-dir", type=Path, required=True)
    args = parser.parse_args()

    checked_artifacts = _check_artifact_manifest(args.artifact_manifest.resolve())
    audit, proof_index, screenshot_index = _check_audit(
        audit_path=args.audit.resolve(),
        manifest_path=args.artifact_manifest.resolve(),
        expected_source_ref=args.source_ref,
    )
    project_state = _load_json(args.project_state.resolve()) if args.project_state else None
    criteria = audit["criteria"]
    unmet = [
        {
            "criterion": item.get("criterion"),
            "status": item.get("status"),
            "evidence": item.get("evidence"),
        }
        for item in criteria
        if item.get("status") != "ESTABLISHED"
    ]
    out_dir = args.out_dir.resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    readback = {
        "schema": "tau.immutable_goal_acceptance_readback.v1",
        "created_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "status": "HUMAN_ACCEPTANCE_REQUIRED" if unmet == [{
            "criterion": 10,
            "status": "MISSING",
            "evidence": ["Human acceptance is not recorded by this automated audit."],
        }] else "BLOCKED",
        "immutable_goal_status": "NOT_MET",
        "mocked": False,
        "live": True,
        "provider_live": bool(audit.get("provider_live")),
        "source_ref": audit.get("source_ref"),
        "audit_path": str(args.audit.resolve()),
        "audit_sha256": _sha256(args.audit.resolve()),
        "artifact_manifest_path": str(args.artifact_manifest.resolve()),
        "artifact_manifest_sha256": _sha256(args.artifact_manifest.resolve()),
        "checked_artifact_count": len(checked_artifacts),
        "criteria": [
            {
                "criterion": item.get("criterion"),
                "status": item.get("status"),
                "evidence_count": len(item.get("evidence") or []),
            }
            for item in criteria
        ],
        "remaining_unmet_criteria": unmet,
        "proof_index": proof_index,
        "screenshot_index": screenshot_index,
        "command_count": len(audit.get("commands") or []),
        "claims": audit.get("claims"),
        "project_state": {
            "path": str(args.project_state.resolve()) if args.project_state else None,
            "gap_count": project_state.get("gap_count") if project_state else None,
        },
        "human_acceptance": {
            "required": True,
            "status": "MISSING",
            "accepted": False,
            "acceptance_line": "Human acceptance is not recorded by this automated audit.",
        },
    }
    json_path = out_dir / "acceptance-readback.json"
    md_path = out_dir / "acceptance-readback.md"
    json_path.write_text(json.dumps(readback, indent=2, sort_keys=True), encoding="utf-8")
    _write_markdown(md_path, readback)
    print(json.dumps({
        "status": readback["status"],
        "immutable_goal_status": readback["immutable_goal_status"],
        "readback_json": str(json_path),
        "readback_markdown": str(md_path),
        "remaining_unmet_criteria": unmet,
        "checked_artifact_count": len(checked_artifacts),
        "proof_count": len(proof_index),
        "screenshot_count": len(screenshot_index),
    }, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
