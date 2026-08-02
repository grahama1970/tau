from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MANIFEST_PATH = ROOT / "tests" / "contract_conformance" / "fixtures" / "mutation_manifest.json"
DEFAULT_SUMMARY = (
    ROOT / "docs" / "proofs" / "tickets" / "issue-298-adversarial-contract-conformance"
    / "conformance-summary.json"
)


def main() -> int:
    parser = argparse.ArgumentParser(description="Run Tau DAG contract conformance tests.")
    parser.add_argument("--out", type=Path, default=DEFAULT_SUMMARY)
    parser.add_argument(
        "--allow-live",
        action="store_true",
        help="Acknowledge that this runs live local Tau scheduler/readback checks.",
    )
    args = parser.parse_args()

    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    command = [sys.executable, "-m", "pytest", "-q", "tests/contract_conformance"]
    completed = subprocess.run(
        command,
        cwd=ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=False,
    )
    output = completed.stdout
    passed = _passed_count(output)
    cases = list(manifest["cases"])
    category_counts: dict[str, dict[str, int]] = {
        category: {"executed": 0, "passed": 0, "failed": 0, "not_exercised": 0}
        for category in manifest["categories"]
    }
    for case in cases:
        category_counts[str(case["category"])]["executed"] += 1
    if completed.returncode == 0:
        for counts in category_counts.values():
            counts["passed"] = counts["executed"]
    else:
        for counts in category_counts.values():
            counts["failed"] = counts["executed"]

    summary = {
        "schema": "tau.contract_conformance_summary.v1",
        "ok": completed.returncode == 0,
        "status": "PASS" if completed.returncode == 0 else "FAIL",
        "mocked": False,
        "live": True,
        "provider_live": False,
        "seed": manifest["seed"],
        "source_commit": _git(["rev-parse", "HEAD"]),
        "manifest": str(MANIFEST_PATH),
        "command": " ".join(command),
        "exit_code": completed.returncode,
        "pytest_passed": passed,
        "case_count": len(cases),
        "categories": category_counts,
        "cases": cases,
        "proof_boundaries": {
            "proves": [
                "Generated malformed source contracts reject before dispatch.",
                "Malformed attempt results do not release successors or admit accepted output.",
                "Malformed context bindings block consumer dispatch.",
                "Malformed transitions reject before commit.",
                "Artifact-reference provenance mismatches reject dereference.",
                "Valid replay and exported payload round trips are deterministic and isolated.",
                "A monkeypatched public validator lets a malformed source pass, "
                "proving sensitivity.",
            ],
            "does_not_prove": [
                "Provider or model semantic quality.",
                "Browser, Memory, Herdr, or paid-provider integrations.",
                "Absence of every possible future validation defect.",
            ],
        },
        "pytest_output_tail": output[-4000:],
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(summary, indent=2, sort_keys=True), encoding="utf-8")
    print(f"{summary['status']} wrote {args.out}")
    return completed.returncode


def _passed_count(output: str) -> int:
    match = re.search(r"(\d+) passed", output)
    return int(match.group(1)) if match else 0


def _git(args: list[str]) -> str:
    return subprocess.check_output(["git", *args], cwd=ROOT, text=True).strip()


if __name__ == "__main__":
    raise SystemExit(main())
