"""Agentic eval coverage guard for Tau source-visible feature claims."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from tau_coding.source_feature_inventory import (
    SELF_MANIFEST,
    build_source_inventory,
    load_source_coverage_records,
    reconcile_source_inventory,
    write_json,
)


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _positive(out: Path) -> int:
    root = _repo_root()
    artifact_dir = out.parent
    inventory = build_source_inventory(root)
    records = load_source_coverage_records(root)
    reconciliation = reconcile_source_inventory(root, inventory, records)
    inventory_path = artifact_dir / "source-inventory.json"
    reconciliation_path = artifact_dir / "reconciliation-report.json"
    write_json(inventory_path, inventory)
    write_json(reconciliation_path, reconciliation)
    payload = _receipt(
        mode="positive",
        ok=reconciliation["ok"] is True,
        inventory_path=inventory_path,
        reconciliation_path=reconciliation_path,
        reconciliation=reconciliation,
    )
    write_json(out, payload)
    print(json.dumps({"status": payload["status"], "proof": str(out)}, sort_keys=True))
    return 0 if payload["ok"] else 1


def _adversarial(out: Path, mode: str) -> int:
    root = _repo_root()
    inventory = build_source_inventory(root)
    records = load_source_coverage_records(root)
    reports = None
    manifests = None
    if mode == "missing-capability-claim":
        manifests = _manifest_records_without_claim(root)
    elif mode == "unmanifested-cli-command":
        cli_path = root / "src/tau_coding/cli.py"
        cli_source = cli_path.read_text(encoding="utf-8")
        marker = '    if not print_requested and command == "feature-inventory":\n'
        injection = (
            "    if not print_requested and command == "
            '"unmanifested-negative-control":\n'
            "        raise typer.Exit()\n\n"
            f"{marker}"
        )
        if marker not in cli_source:
            raise RuntimeError("feature-inventory command marker not found for negative control")
        cli_source = cli_source.replace(marker, injection, 1)
        inventory = build_source_inventory(root, cli_source_text=cli_source)
    elif mode == "orphan-eval-manifest":
        manifests = _manifest_records_with_orphan(root)
    elif mode == "duplicate-feature-owner":
        records = _records_with_duplicate_owner(records)
    elif mode == "missing-retained-report":
        reports = _report_map_without_one(root)
    elif mode == "stale-waiver":
        records = _records_with_stale_waiver(records)
    else:
        raise ValueError(f"unknown adversarial mode: {mode}")
    reconciliation = reconcile_source_inventory(
        root,
        inventory,
        records,
        reports=reports,
        manifests=manifests,
    )
    expected_code = _expected_code(mode)
    detected = any(
        isinstance(finding, dict) and finding.get("code") == expected_code
        for finding in reconciliation.get("findings", [])
    )
    receipt_path = out
    payload = _receipt(
        mode=mode,
        ok=detected,
        inventory_path=None,
        reconciliation_path=None,
        reconciliation=reconciliation,
        extra={
            "adversarial_detection": detected,
            "expected_failure_code": expected_code,
            "negative_case_receipt": True,
        },
    )
    write_json(receipt_path, payload)
    print(
        json.dumps(
            {
                "status": "PASS" if detected else "FAIL",
                "proof": str(receipt_path),
                "detected": detected,
                "expected_failure_code": expected_code,
            },
            sort_keys=True,
        )
    )
    return 0 if detected else 1


def _receipt(
    *,
    mode: str,
    ok: bool,
    inventory_path: Path | None,
    reconciliation_path: Path | None,
    reconciliation: dict[str, Any],
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "schema": "tau.feature_agentic_eval_coverage_receipt.v2",
        "mode": mode,
        "ok": ok,
        "status": "PASS" if ok else "FAIL",
        "mocked": False,
        "live": True,
        "inventory_path": str(inventory_path) if inventory_path else None,
        "reconciliation_path": str(reconciliation_path) if reconciliation_path else None,
        "manifest_count": reconciliation.get("manifest_count"),
        "claim_count": reconciliation.get("claim_count"),
        "covered_claim_count": reconciliation.get("covered_claim_count"),
        "inventory_feature_count": reconciliation.get("inventory_feature_count"),
        "finding_count": len(reconciliation.get("findings", []))
        if isinstance(reconciliation.get("findings"), list)
        else None,
        "findings": reconciliation.get("findings", []),
    }
    if extra:
        payload.update(extra)
    return payload


def _manifest_records_without_claim(root: Path) -> list[dict[str, Any]]:
    manifests = _manifest_records(root)
    for manifest in manifests:
        if manifest["path"] == "evals/tau_terminal_dag_watch_agentic_eval.json":
            manifest["claim_ids"] = []
            manifest["claims"] = []
            break
    return manifests


def _manifest_records_with_orphan(root: Path) -> list[dict[str, Any]]:
    manifests = _manifest_records(root)
    manifests.append(
        {
            "path": "evals/orphan_negative_control_agentic_eval.json",
            "skill": "orphan-negative-control",
            "claim_ids": ["tau.orphan_negative_control"],
            "claims": [
                {
                    "id": "tau.orphan_negative_control",
                    "criticality": "critical",
                    "evidence_required": {"live_e2e": True},
                }
            ],
            "case_count": 1,
            "has_negative_or_adversarial": True,
            "has_real_world_case": True,
        }
    )
    return manifests


def _records_with_duplicate_owner(records: dict[str, Any]) -> dict[str, Any]:
    mutated = json.loads(json.dumps(records))
    for record in mutated["records"]:
        if record.get("status") == "CLAIMED":
            duplicate = dict(record)
            duplicate["claim_id"] = "tau.feature_agentic_eval_coverage"
            mutated["records"].append(duplicate)
            return mutated
    raise RuntimeError("no claimed record available for duplicate-owner negative case")


def _records_with_stale_waiver(records: dict[str, Any]) -> dict[str, Any]:
    mutated = json.loads(json.dumps(records))
    for record in mutated["records"]:
        if record.get("status") in {"BLOCKED", "OUT_OF_SCOPE"}:
            record["expires"] = "2026-01-01"
            return mutated
    raise RuntimeError("no waiver record available for stale-waiver negative case")


def _report_map_without_one(root: Path) -> dict[str, dict[str, Any]]:
    from tau_coding.source_feature_inventory import _report_map

    reports = _report_map(root)
    reports.pop("evals/tau_terminal_dag_watch_agentic_eval.json", None)
    return reports


def _manifest_records(root: Path) -> list[dict[str, Any]]:
    from tau_coding.source_feature_inventory import _manifest_records

    return _manifest_records(root)


def _expected_code(mode: str) -> str:
    return {
        "missing-capability-claim": "missing_capability_claims",
        "unmanifested-cli-command": "uncovered_source_feature",
        "orphan-eval-manifest": "orphan_eval_manifest",
        "duplicate-feature-owner": "duplicate_feature_owner",
        "missing-retained-report": "missing_retained_report",
        "stale-waiver": "stale_waiver",
    }[mode]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--mode",
        choices=[
            "positive",
            "missing-capability-claim",
            "unmanifested-cli-command",
            "orphan-eval-manifest",
            "duplicate-feature-owner",
            "missing-retained-report",
            "stale-waiver",
        ],
        required=True,
    )
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    if args.mode == "positive":
        return _positive(args.out)
    return _adversarial(args.out, args.mode)


if __name__ == "__main__":
    raise SystemExit(main())
