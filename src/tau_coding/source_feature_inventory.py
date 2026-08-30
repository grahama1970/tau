"""Source-derived feature inventory for Tau coverage reconciliation."""

from __future__ import annotations

import ast
import hashlib
import json
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from tau_coding.dag_viewer.contracts import viewer_capabilities
from tau_coding.skill_capability_registry import DEFAULT_SKILL_CAPABILITY_REGISTRY

INVENTORY_SCHEMA = "tau.source_feature_inventory.v1"
COVERAGE_RECORDS_SCHEMA = "tau.source_feature_coverage_records.v1"
RECONCILIATION_SCHEMA = "tau.source_feature_reconciliation.v1"
SELF_MANIFEST = "evals/tau_feature_coverage_agentic_eval.json"
SOURCE_COVERAGE_RECORDS_PATH = "evals/tau_source_feature_coverage_records.json"
REPORT_GLOBS = (
    "local/agentic-evals/*agentic-evals-report.json",
    "local/issue-327-ledger-proof/evals/agentic-evals-report.json",
)
VALID_NON_CLAIM_STATUSES = {"BLOCKED", "OUT_OF_SCOPE"}
CAPABILITY_MODULE_PATHS = (
    "src/tau_coding/acceptance_attestation.py",
    "src/tau_coding/child_agent_requests.py",
    "src/tau_coding/codebase_ingest.py",
    "src/tau_coding/dag_runtime/memory_projection.py",
    "src/tau_coding/dag_runtime/run_store.py",
    "src/tau_coding/dag_runtime/scheduler.py",
    "src/tau_coding/runtime_backends/kernel.py",
    "src/tau_coding/runtime_backends/kernel_host_bridge.py",
    "src/tau_coding/runtime_backends/python_workspace.py",
    "src/tau_coding/runtime_backends/python_workspace_worker.py",
)


@dataclass(frozen=True, slots=True)
class SourceFeature:
    feature_id: str
    kind: str
    name: str
    source_path: str
    source_line: int | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_payload(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class ReconciliationFinding:
    severity: str
    code: str
    feature_id: str | None
    message: str
    source: str | None = None

    def to_payload(self) -> dict[str, Any]:
        return asdict(self)


def build_source_inventory(
    root: Path,
    *,
    cli_source_text: str | None = None,
) -> dict[str, Any]:
    """Derive a stable inventory from Tau source files."""

    resolved = root.expanduser().resolve()
    features: dict[str, SourceFeature] = {}
    _add_cli_features(resolved, features, cli_source_text=cli_source_text)
    _add_workflow_features(resolved, features)
    _add_capability_module_features(resolved, features)
    _add_skill_capability_features(features)
    _add_viewer_surface_features(features)
    _add_ledger_path_features(resolved, features)
    _add_evaluator_script_features(resolved, features)
    ordered = sorted(features.values(), key=lambda item: (item.kind, item.feature_id))
    payload_features = [feature.to_payload() for feature in ordered]
    return {
        "schema": INVENTORY_SCHEMA,
        "generated_at": _utc_stamp(),
        "mocked": False,
        "live": True,
        "source_root": str(resolved),
        "source_sha256": _inventory_source_hash(resolved, cli_source_text=cli_source_text),
        "feature_count": len(payload_features),
        "features": payload_features,
        "counts_by_kind": _counts_by_kind(payload_features),
    }


def load_source_coverage_records(root: Path) -> dict[str, Any]:
    path = root / SOURCE_COVERAGE_RECORDS_PATH
    payload = _load_json(path)
    if payload.get("schema") != COVERAGE_RECORDS_SCHEMA:
        raise ValueError(f"{SOURCE_COVERAGE_RECORDS_PATH} schema must be {COVERAGE_RECORDS_SCHEMA}")
    return payload


def reconcile_source_inventory(
    root: Path,
    inventory: dict[str, Any],
    coverage_records: dict[str, Any],
    *,
    today: str | None = None,
    require_reports: bool = True,
    reports: dict[str, dict[str, Any]] | None = None,
    manifests: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Reconcile source-visible features against eval claims and retained reports."""

    resolved = root.expanduser().resolve()
    today_value = today or datetime.now(UTC).date().isoformat()
    feature_ids = {
        feature["feature_id"]
        for feature in inventory.get("features", [])
        if isinstance(feature, dict) and isinstance(feature.get("feature_id"), str)
    }
    manifest_records = manifests if manifests is not None else _manifest_records(resolved)
    report_map = reports if reports is not None else _report_map(resolved)
    claim_to_manifest = _claim_to_manifest(manifest_records)
    records = _coverage_records(coverage_records)
    merge_records = _merge_records(coverage_records)
    findings: list[ReconciliationFinding] = []
    coverage_by_feature: dict[str, list[dict[str, Any]]] = {}
    for record in records:
        feature_id = record.get("feature_id")
        if isinstance(feature_id, str):
            coverage_by_feature.setdefault(feature_id, []).append(record)
    covered_claims: set[str] = set()
    resolved_features: list[dict[str, Any]] = []
    for feature in inventory.get("features", []):
        if not isinstance(feature, dict):
            continue
        feature_id = str(feature.get("feature_id", ""))
        matching = coverage_by_feature.get(feature_id, [])
        if not matching:
            findings.append(
                ReconciliationFinding(
                    "ERROR",
                    "uncovered_source_feature",
                    feature_id,
                    "source feature has no capability claim or explicit non-coverage record",
                    feature.get("source_path") if isinstance(feature.get("source_path"), str) else None,
                )
            )
            resolved_features.append({**feature, "coverage_status": "MISSING", "coverage": []})
            continue
        resolved_records: list[dict[str, Any]] = []
        claim_owners: list[str] = []
        for record in matching:
            status = record.get("status")
            if status == "CLAIMED":
                claim_id = record.get("claim_id")
                if not isinstance(claim_id, str) or claim_id not in claim_to_manifest:
                    findings.append(
                        ReconciliationFinding(
                            "ERROR",
                            "unknown_claim_owner",
                            feature_id,
                            f"feature references missing capability claim {claim_id!r}",
                        )
                    )
                else:
                    claim_owners.append(claim_id)
                    covered_claims.add(claim_id)
                resolved_records.append(record)
            elif status in VALID_NON_CLAIM_STATUSES:
                _validate_waiver_record(record, feature_id, today_value, findings)
                resolved_records.append(record)
            else:
                findings.append(
                    ReconciliationFinding(
                        "ERROR",
                        "invalid_coverage_status",
                        feature_id,
                        f"coverage record has invalid status {status!r}",
                    )
                )
        if len(set(claim_owners)) > 1 and feature_id not in merge_records:
            findings.append(
                ReconciliationFinding(
                    "ERROR",
                    "duplicate_feature_owner",
                    feature_id,
                    "multiple capability claims own the same source feature without a merge record",
                )
            )
        resolved_features.append(
            {
                **feature,
                "coverage_status": _feature_coverage_status(resolved_records),
                "coverage": resolved_records,
            }
        )
    for record in records:
        feature_id = record.get("feature_id")
        if isinstance(feature_id, str) and feature_id not in feature_ids:
            findings.append(
                ReconciliationFinding(
                    "ERROR",
                    "stale_coverage_record",
                    feature_id,
                    "coverage record references a feature absent from the source inventory",
                )
            )
    for manifest in manifest_records:
        path = manifest["path"]
        if path == SELF_MANIFEST:
            continue
        for claim_id in manifest.get("claim_ids", []):
            if claim_id not in covered_claims:
                findings.append(
                    ReconciliationFinding(
                        "ERROR",
                        "orphan_eval_manifest",
                        None,
                        f"capability claim {claim_id} is not mapped to any source feature",
                        path,
                    )
                )
        _validate_manifest_claim_shape(manifest, findings)
        if require_reports:
            _validate_retained_report(manifest, report_map, findings)
    self_claim_ids = {
        claim_id
        for manifest in manifest_records
        if manifest["path"] == SELF_MANIFEST
        for claim_id in manifest.get("claim_ids", [])
        if isinstance(claim_id, str)
    }
    non_self_claims = covered_claims - self_claim_ids
    ok = not any(finding.severity == "ERROR" for finding in findings)
    return {
        "schema": RECONCILIATION_SCHEMA,
        "generated_at": _utc_stamp(),
        "ok": ok,
        "status": "PASS" if ok else "FAIL",
        "mocked": False,
        "live": True,
        "source_inventory_sha256": "sha256:" + _json_hash(inventory),
        "coverage_records_sha256": "sha256:" + _json_hash(coverage_records),
        "inventory_feature_count": len(feature_ids),
        "manifest_count": len([item for item in manifest_records if item["path"] != SELF_MANIFEST]),
        "claim_count": sum(
            len(item.get("claim_ids", []))
            for item in manifest_records
            if item["path"] != SELF_MANIFEST
        ),
        "covered_claim_count": len(non_self_claims),
        "self_covered_claim_count": len(covered_claims & self_claim_ids),
        "features": resolved_features,
        "manifests": manifest_records,
        "reports": {key: {"path": value["path"]} for key, value in sorted(report_map.items())},
        "findings": [finding.to_payload() for finding in findings],
    }


def write_json(path: Path, payload: dict[str, Any]) -> dict[str, Any]:
    resolved = path.expanduser()
    resolved.parent.mkdir(parents=True, exist_ok=True)
    resolved.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return payload


def _add_feature(features: dict[str, SourceFeature], feature: SourceFeature) -> None:
    existing = features.get(feature.feature_id)
    if existing is None or (
        feature.source_line is not None
        and (existing.source_line is None or feature.source_line < existing.source_line)
    ):
        features[feature.feature_id] = feature


def _add_cli_features(
    root: Path,
    features: dict[str, SourceFeature],
    *,
    cli_source_text: str | None,
) -> None:
    rel = "src/tau_coding/cli.py"
    path = root / rel
    source = cli_source_text if cli_source_text is not None else path.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=rel)
    typer_apps: dict[str, str] = {"app": ""}
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Assign)
            and isinstance(node.value, ast.Call)
            and _call_name(node.value.func) == "typer.Typer"
        ):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    typer_apps[target.id] = ""
        if (
            isinstance(node, ast.Expr)
            and isinstance(node.value, ast.Call)
            and isinstance(node.value.func, ast.Attribute)
            and node.value.func.attr == "add_typer"
            and isinstance(node.value.func.value, ast.Name)
        ):
            child = node.value.args[0].id if node.value.args and isinstance(node.value.args[0], ast.Name) else None
            name = _keyword_constant(node.value, "name")
            if child and isinstance(name, str):
                typer_apps[child] = name
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for decorator in node.decorator_list:
                app_name, command_name = _decorated_command(decorator)
                if app_name and command_name:
                    prefix = typer_apps.get(app_name, "")
                    public = " ".join(part for part in (prefix, command_name) if part)
                    _add_cli_command(features, public, rel, node.lineno, "typer")
            subcommand_prefix = _subcommand_prefix_for_function(node.name)
            if subcommand_prefix:
                for subcommand, line in _subcommand_values_from_node(node):
                    _add_cli_command(
                        features,
                        f"{subcommand_prefix} {subcommand}",
                        rel,
                        line,
                        "subcommand",
                    )
            if node.name == "main":
                for child in ast.walk(node):
                    for value, line in _command_values_from_node(child):
                        _add_cli_command(features, value, rel, line, "manual-branch")


def _add_cli_command(
    features: dict[str, SourceFeature],
    public_command: str,
    source_path: str,
    source_line: int,
    detector: str,
) -> None:
    public = " ".join(public_command.split())
    if not public:
        return
    feature_id = "cli-command:" + public.replace(" ", "/")
    _add_feature(
        features,
        SourceFeature(
            feature_id=feature_id,
            kind="cli-command",
            name=f"tau {public}",
            source_path=source_path,
            source_line=source_line,
            metadata={"detector": detector},
        ),
    )


def _add_workflow_features(root: Path, features: dict[str, SourceFeature]) -> None:
    for path in sorted((root / "src/tau_coding/workflows/definitions").glob("*.json")):
        rel = path.relative_to(root).as_posix()
        payload = _load_json(path)
        workflow_id = str(payload.get("workflow_id") or path.stem)
        _add_feature(
            features,
            SourceFeature(
                feature_id=f"workflow-definition:{workflow_id}",
                kind="workflow-definition",
                name=workflow_id,
                source_path=rel,
                metadata={
                    "schema": payload.get("schema"),
                    "topology": payload.get("topology"),
                    "rung": payload.get("rung"),
                },
            ),
        )


def _add_capability_module_features(root: Path, features: dict[str, SourceFeature]) -> None:
    for rel in CAPABILITY_MODULE_PATHS:
        path = root / rel
        if not path.exists():
            continue
        _add_feature(
            features,
            SourceFeature(
                feature_id=f"capability-module:{rel}",
                kind="capability-module",
                name=Path(rel).stem,
                source_path=rel,
                metadata={"sha256": "sha256:" + _sha256(path)},
            ),
        )


def _add_skill_capability_features(features: dict[str, SourceFeature]) -> None:
    capabilities = DEFAULT_SKILL_CAPABILITY_REGISTRY.get("capabilities", {})
    if not isinstance(capabilities, dict):
        return
    for capability, entry in sorted(capabilities.items()):
        if not isinstance(entry, dict):
            continue
        _add_feature(
            features,
            SourceFeature(
                feature_id=f"skill-capability:{capability}",
                kind="skill-capability",
                name=str(capability),
                source_path="src/tau_coding/skill_capability_registry.py",
                metadata={
                    "skill": entry.get("skill"),
                    "tau_receipt_schema": entry.get("tau_receipt_schema"),
                },
            ),
        )


def _add_viewer_surface_features(features: dict[str, SourceFeature]) -> None:
    payload = viewer_capabilities()
    for key, value in sorted(payload.items()):
        if key.startswith("supports_") and value:
            _add_feature(
                features,
                SourceFeature(
                    feature_id=f"viewer-surface:{key}",
                    kind="viewer-surface",
                    name=key,
                    source_path="src/tau_coding/dag_viewer/contracts.py",
                    metadata={"value": value},
                ),
            )
    for key in ("manifest_schema", "snapshot_schema", "event_schema"):
        value = payload.get(key)
        if isinstance(value, str) and value:
            _add_feature(
                features,
                SourceFeature(
                    feature_id=f"viewer-surface:{key}:{value}",
                    kind="viewer-surface",
                    name=value,
                    source_path="src/tau_coding/dag_viewer/contracts.py",
                    metadata={"field": key},
                ),
            )


def _add_ledger_path_features(root: Path, features: dict[str, SourceFeature]) -> None:
    for path in sorted((root / "src/tau_coding").rglob("*.py")):
        rel = path.relative_to(root).as_posix()
        if "__pycache__" in path.parts:
            continue
        source = path.read_text(encoding="utf-8")
        if "ledger" not in source.lower():
            continue
        tree = ast.parse(source, filename=rel)
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            name = node.name
            lowered = name.lower()
            if "ledger" not in lowered:
                continue
            if not lowered.startswith(("build", "write", "publish", "verify", "read", "_run")):
                continue
            _add_feature(
                features,
                SourceFeature(
                    feature_id=f"ledger-path:{rel}:{name}",
                    kind="ledger-path",
                    name=name,
                    source_path=rel,
                    source_line=node.lineno,
                ),
            )


def _add_evaluator_script_features(root: Path, features: dict[str, SourceFeature]) -> None:
    for path in sorted((root / "scripts").glob("agentic-eval-*.py")):
        rel = path.relative_to(root).as_posix()
        _add_feature(
            features,
            SourceFeature(
                feature_id=f"evaluator-script:{path.stem}",
                kind="evaluator-script",
                name=path.stem,
                source_path=rel,
            ),
        )


def _decorated_command(decorator: ast.expr) -> tuple[str | None, str | None]:
    if not (
        isinstance(decorator, ast.Call)
        and isinstance(decorator.func, ast.Attribute)
        and decorator.func.attr == "command"
        and isinstance(decorator.func.value, ast.Name)
    ):
        return None, None
    name: str | None = None
    if decorator.args and isinstance(decorator.args[0], ast.Constant):
        name = str(decorator.args[0].value)
    if name is None:
        name = _keyword_constant(decorator, "name")
    return decorator.func.value.id, name


def _command_values_from_node(node: ast.AST) -> list[tuple[str, int]]:
    values: list[tuple[str, int]] = []
    if not isinstance(node, ast.If):
        return values
    for compare in ast.walk(node.test):
        if not isinstance(compare, ast.Compare):
            continue
        if _is_name(compare.left, "command"):
            for op, comparator in zip(compare.ops, compare.comparators, strict=False):
                if isinstance(op, ast.Eq) and isinstance(comparator, ast.Constant):
                    values.append((str(comparator.value), node.lineno))
                elif isinstance(op, ast.In):
                    values.extend((value, node.lineno) for value in _string_constants(comparator))
    return values


def _subcommand_values_from_node(node: ast.AST) -> list[tuple[str, int]]:
    values: list[tuple[str, int]] = []
    if not isinstance(node, ast.If):
        return values
    for compare in ast.walk(node.test):
        if not isinstance(compare, ast.Compare) or not _is_name(compare.left, "subcommand"):
            continue
        for op, comparator in zip(compare.ops, compare.comparators, strict=False):
            if isinstance(op, ast.Eq) and isinstance(comparator, ast.Constant):
                values.append((str(comparator.value), node.lineno))
            elif isinstance(op, ast.In):
                values.extend((value, node.lineno) for value in _string_constants(comparator))
    return values


def _subcommand_prefix_for_function(name: str) -> str | None:
    return {
        "_run_ledger_cli": "ledger",
        "_dispatch_workflows_cli": "workflows",
    }.get(name)


def _manifest_records(root: Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for path in sorted((root / "evals").glob("*agentic_eval.json")):
        rel = path.relative_to(root).as_posix()
        payload = _load_json(path)
        cases = payload.get("cases") if isinstance(payload.get("cases"), list) else []
        claims = payload.get("capability_claims")
        claim_records = claims if isinstance(claims, list) else []
        records.append(
            {
                "path": rel,
                "skill": payload.get("skill"),
                "claim_ids": [
                    claim.get("id")
                    for claim in claim_records
                    if isinstance(claim, dict) and isinstance(claim.get("id"), str)
                ],
                "claims": claim_records,
                "case_count": len(cases),
                "has_negative_or_adversarial": any(
                    isinstance(case, dict) and case.get("type") in {"negative", "adversarial"}
                    for case in cases
                ),
                "has_real_world_case": any(
                    isinstance(case, dict) and case.get("real_world") is True for case in cases
                ),
            }
        )
    return records


def _report_map(root: Path) -> dict[str, dict[str, Any]]:
    reports: dict[str, dict[str, Any]] = {}
    for pattern in REPORT_GLOBS:
        for path in sorted(root.glob(pattern)):
            payload = _load_json(path)
            source = payload.get("source")
            if isinstance(source, str) and source.startswith("evals/"):
                reports[source] = {"path": path.relative_to(root).as_posix(), "data": payload}
    return reports


def _claim_to_manifest(manifests: list[dict[str, Any]]) -> dict[str, str]:
    mapping: dict[str, str] = {}
    for manifest in manifests:
        for claim_id in manifest.get("claim_ids", []):
            if isinstance(claim_id, str):
                mapping[claim_id] = str(manifest["path"])
    return mapping


def _coverage_records(payload: dict[str, Any]) -> list[dict[str, Any]]:
    records = payload.get("records")
    if not isinstance(records, list):
        raise ValueError("source coverage records must contain records[]")
    return [record for record in records if isinstance(record, dict)]


def _merge_records(payload: dict[str, Any]) -> set[str]:
    records = payload.get("merge_records", [])
    if not isinstance(records, list):
        return set()
    return {
        str(record.get("feature_id"))
        for record in records
        if isinstance(record, dict) and isinstance(record.get("feature_id"), str)
    }


def _validate_waiver_record(
    record: dict[str, Any],
    feature_id: str,
    today: str,
    findings: list[ReconciliationFinding],
) -> None:
    for key in ("owner", "reason", "expires"):
        if not isinstance(record.get(key), str) or not str(record.get(key)).strip():
            findings.append(
                ReconciliationFinding(
                    "ERROR",
                    "incomplete_non_claim_record",
                    feature_id,
                    f"{record.get('status')} record is missing {key}",
                )
            )
    expires = record.get("expires")
    if isinstance(expires, str) and expires < today:
        findings.append(
            ReconciliationFinding(
                "ERROR",
                "stale_waiver",
                feature_id,
                f"{record.get('status')} record expired on {expires}",
            )
        )


def _validate_manifest_claim_shape(
    manifest: dict[str, Any],
    findings: list[ReconciliationFinding],
) -> None:
    path = str(manifest["path"])
    claims = manifest.get("claims")
    if not isinstance(claims, list) or not claims:
        findings.append(
            ReconciliationFinding("ERROR", "missing_capability_claims", None, path, path)
        )
        return
    if not manifest.get("has_negative_or_adversarial"):
        findings.append(
            ReconciliationFinding("ERROR", "missing_negative_case", None, path, path)
        )
    if not manifest.get("has_real_world_case"):
        findings.append(
            ReconciliationFinding("ERROR", "missing_real_world_case", None, path, path)
        )
    for claim in claims:
        if not isinstance(claim, dict):
            findings.append(ReconciliationFinding("ERROR", "invalid_claim", None, path, path))
            continue
        claim_id = claim.get("id")
        evidence_required = claim.get("evidence_required")
        if not isinstance(claim_id, str) or not claim_id:
            findings.append(
                ReconciliationFinding("ERROR", "claim_missing_id", None, path, path)
            )
        if not isinstance(evidence_required, dict) or not evidence_required:
            findings.append(
                ReconciliationFinding(
                    "ERROR",
                    "claim_missing_evidence_required",
                    None,
                    f"{path} claim {claim_id} missing evidence_required",
                    path,
                )
            )


def _validate_retained_report(
    manifest: dict[str, Any],
    reports: dict[str, dict[str, Any]],
    findings: list[ReconciliationFinding],
) -> None:
    path = str(manifest["path"])
    report = reports.get(path, {}).get("data")
    if not isinstance(report, dict):
        findings.append(
            ReconciliationFinding(
                "ERROR",
                "missing_retained_report",
                None,
                "missing retained agentic-evals report",
                path,
            )
        )
        return
    if report.get("readiness") != "READY":
        findings.append(
            ReconciliationFinding(
                "ERROR",
                "retained_report_not_ready",
                None,
                f"retained report not READY: {report.get('readiness')}",
                path,
            )
        )
    if report.get("mocked") is not False or report.get("live") is not True:
        findings.append(
            ReconciliationFinding(
                "ERROR",
                "retained_report_boundary_invalid",
                None,
                f"retained report boundary mocked={report.get('mocked')} live={report.get('live')}",
                path,
            )
        )


def _feature_coverage_status(records: list[dict[str, Any]]) -> str:
    statuses = {str(record.get("status")) for record in records}
    if "CLAIMED" in statuses:
        return "CLAIMED"
    if "BLOCKED" in statuses:
        return "BLOCKED"
    if "OUT_OF_SCOPE" in statuses:
        return "OUT_OF_SCOPE"
    return "INVALID"


def _counts_by_kind(features: list[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for feature in features:
        kind = str(feature.get("kind"))
        counts[kind] = counts.get(kind, 0) + 1
    return dict(sorted(counts.items()))


def _inventory_source_hash(root: Path, *, cli_source_text: str | None) -> str:
    hasher = hashlib.sha256()
    paths = [
        root / "src/tau_coding/cli.py",
        root / "src/tau_coding/dag_viewer/contracts.py",
        root / "src/tau_coding/skill_capability_registry.py",
        root / "src/tau_coding/source_feature_inventory.py",
    ]
    paths.extend(root / rel for rel in CAPABILITY_MODULE_PATHS if (root / rel).exists())
    paths.extend(sorted((root / "src/tau_coding/workflows/definitions").glob("*.json")))
    paths.extend(sorted((root / "scripts").glob("agentic-eval-*.py")))
    for path in paths:
        rel = path.relative_to(root).as_posix()
        hasher.update(rel.encode("utf-8"))
        if cli_source_text is not None and rel == "src/tau_coding/cli.py":
            hasher.update(cli_source_text.encode("utf-8"))
        else:
            hasher.update(path.read_bytes())
    return "sha256:" + hasher.hexdigest()


def _json_hash(payload: dict[str, Any]) -> str:
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _load_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return payload


def _call_name(node: ast.AST) -> str:
    if isinstance(node, ast.Attribute):
        prefix = _call_name(node.value)
        return f"{prefix}.{node.attr}" if prefix else node.attr
    if isinstance(node, ast.Name):
        return node.id
    return ""


def _keyword_constant(call: ast.Call, key: str) -> str | None:
    for keyword in call.keywords:
        if keyword.arg == key and isinstance(keyword.value, ast.Constant):
            return str(keyword.value.value)
    return None


def _string_constants(node: ast.AST) -> list[str]:
    if isinstance(node, (ast.Set, ast.Tuple, ast.List)):
        return [str(item.value) for item in node.elts if isinstance(item, ast.Constant)]
    return []


def _is_name(node: ast.AST, name: str) -> bool:
    return isinstance(node, ast.Name) and node.id == name


def _utc_stamp() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")
