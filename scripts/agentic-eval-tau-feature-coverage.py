"""Agentic eval coverage guard for Tau feature claims.

The guard is intentionally repository-local: Tau feature proof is represented by
version-2 agentic-eval manifests under evals/, with one or more
capability_claims and retained READY reports under local/agentic-evals/.
"""

from __future__ import annotations

import argparse
import json
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

SELF_MANIFEST = "evals/tau_feature_coverage_agentic_eval.json"
REPORT_GLOBS = (
    "local/agentic-evals/*agentic-evals-report.json",
    "local/issue-327-ledger-proof/evals/agentic-evals-report.json",
)


@dataclass
class Finding:
    severity: str
    manifest: str
    message: str


@dataclass
class CoverageResult:
    manifests: list[dict[str, Any]] = field(default_factory=list)
    reports: dict[str, dict[str, Any]] = field(default_factory=dict)
    findings: list[Finding] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not any(finding.severity == "ERROR" for finding in self.findings)


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _manifest_paths(root: Path) -> list[Path]:
    return [
        path
        for path in sorted((root / "evals").glob("*agentic_eval.json"))
        if path.relative_to(root).as_posix() != SELF_MANIFEST
    ]


def _report_map(root: Path) -> dict[str, dict[str, Any]]:
    reports: dict[str, dict[str, Any]] = {}
    for pattern in REPORT_GLOBS:
        for path in sorted(root.glob(pattern)):
            data = _load_json(path)
            source = data.get("source")
            if isinstance(source, str) and source.startswith("evals/"):
                reports[source] = {"path": path.relative_to(root).as_posix(), "data": data}
    return reports


def inspect_coverage(root: Path, *, require_reports: bool = True) -> CoverageResult:
    result = CoverageResult(reports=_report_map(root))
    for path in _manifest_paths(root):
        rel = path.relative_to(root).as_posix()
        manifest = _load_json(path)
        claims = manifest.get("capability_claims")
        cases = manifest.get("cases") if isinstance(manifest.get("cases"), list) else []
        manifest_record = {
            "path": rel,
            "skill": manifest.get("skill"),
            "claim_ids": [],
            "case_count": len(cases),
            "report": result.reports.get(rel, {}).get("path"),
        }
        if not isinstance(claims, list) or not claims:
            result.findings.append(Finding("ERROR", rel, "missing capability_claims"))
            result.manifests.append(manifest_record)
            continue
        if not any(case.get("type") in {"negative", "adversarial"} for case in cases):
            result.findings.append(Finding("ERROR", rel, "missing negative/adversarial case"))
        if not any(case.get("real_world") is True for case in cases):
            result.findings.append(Finding("ERROR", rel, "missing real_world case"))
        for claim in claims:
            if not isinstance(claim, dict):
                result.findings.append(
                    Finding("ERROR", rel, "capability_claim entry is not an object")
                )
                continue
            claim_id = claim.get("id")
            manifest_record["claim_ids"].append(claim_id)
            evidence_required = claim.get("evidence_required")
            if not isinstance(claim_id, str) or not claim_id:
                result.findings.append(Finding("ERROR", rel, "capability_claim missing id"))
                continue
            if not isinstance(evidence_required, dict) or not evidence_required:
                result.findings.append(
                    Finding("ERROR", rel, f"claim {claim_id} missing evidence_required")
                )
                continue
            for evidence_class, required in sorted(evidence_required.items()):
                if required is not True:
                    continue
                supporting = [
                    case
                    for case in cases
                    if claim_id in case.get("supports_claims", [])
                    and case.get("evidence_class") == evidence_class
                ]
                if not supporting:
                    result.findings.append(
                        Finding(
                            "ERROR",
                            rel,
                            f"claim {claim_id} missing supporting case for {evidence_class}",
                        )
                    )
                for case in supporting:
                    if evidence_class == "live_e2e" and not _has_readback(case):
                        result.findings.append(
                            Finding(
                                "ERROR",
                                rel,
                                (
                                    f"live claim {claim_id} case {case.get('name')} "
                                    "lacks readback oracle"
                                ),
                            )
                        )
        if require_reports:
            report = result.reports.get(rel, {}).get("data")
            if not isinstance(report, dict):
                result.findings.append(
                    Finding("ERROR", rel, "missing retained agentic-evals report")
                )
            elif report.get("readiness") != "READY":
                result.findings.append(
                    Finding("ERROR", rel, f"retained report not READY: {report.get('readiness')}")
                )
            elif report.get("mocked") is not False or report.get("live") is not True:
                result.findings.append(
                    Finding(
                        "ERROR",
                        rel,
                        (
                            "retained report proof boundary invalid: "
                            f"mocked={report.get('mocked')} live={report.get('live')}"
                        ),
                    )
                )
        result.manifests.append(manifest_record)
    return result


def _has_readback(case: dict[str, Any]) -> bool:
    expected = case.get("expected") if isinstance(case.get("expected"), dict) else {}
    artifacts = expected.get("artifacts") if isinstance(expected.get("artifacts"), list) else []
    return case.get("readback") is True or bool(artifacts)


def _write_receipt(path: Path, result: CoverageResult, *, mode: str) -> dict[str, Any]:
    payload = {
        "schema": "tau.feature_agentic_eval_coverage_receipt.v1",
        "mode": mode,
        "ok": result.ok,
        "mocked": False,
        "live": True,
        "manifest_count": len(result.manifests),
        "claim_count": sum(len(item.get("claim_ids", [])) for item in result.manifests),
        "manifests": result.manifests,
        "findings": [finding.__dict__ for finding in result.findings],
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return payload


def _positive(out: Path) -> int:
    result = inspect_coverage(_repo_root(), require_reports=True)
    payload = _write_receipt(out, result, mode="positive")
    print(
        json.dumps(
            {"status": "PASS" if result.ok else "FAIL", "proof": str(out)},
            sort_keys=True,
        )
    )
    return 0 if payload["ok"] else 1


def _adversarial(out: Path) -> int:
    root = _repo_root()
    with tempfile_copy(root / "evals") as temp_root:
        target = temp_root / "evals" / "tau_terminal_dag_watch_agentic_eval.json"
        manifest = _load_json(target)
        manifest.pop("capability_claims", None)
        target.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
        result = inspect_coverage(temp_root, require_reports=False)
    detected = any(
        finding.manifest == "evals/tau_terminal_dag_watch_agentic_eval.json"
        and "missing capability_claims" in finding.message
        for finding in result.findings
    )
    if not detected:
        result.findings.append(
            Finding(
                "ERROR",
                "evals/tau_terminal_dag_watch_agentic_eval.json",
                "adversarial deletion was not detected",
            )
        )
    result.findings = [] if detected else result.findings
    payload = _write_receipt(out, result, mode="adversarial-missing-claim")
    payload["adversarial_detection"] = detected
    out.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(
        json.dumps(
            {"status": "PASS" if detected else "FAIL", "proof": str(out), "detected": detected},
            sort_keys=True,
        )
    )
    return 0 if detected else 1


class tempfile_copy:
    def __init__(self, evals_dir: Path) -> None:
        import tempfile

        self._tmp = tempfile.TemporaryDirectory(prefix="tau-feature-coverage-")
        self.root = Path(self._tmp.name)
        self.evals_dir = evals_dir

    def __enter__(self) -> Path:
        shutil.copytree(self.evals_dir, self.root / "evals")
        return self.root

    def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
        self._tmp.cleanup()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=["positive", "adversarial"], required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    if args.mode == "positive":
        return _positive(args.out)
    return _adversarial(args.out)


if __name__ == "__main__":
    raise SystemExit(main())
