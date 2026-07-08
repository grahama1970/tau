#!/usr/bin/env python3
"""Focused sanity runner for Tau external-review artifacts."""

from __future__ import annotations

import argparse
import json
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

SCHEMA = "tau.external_review_sanity_receipt.v1"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", required=True)
    args = parser.parse_args()
    out = Path(args.out).expanduser().resolve()
    out.mkdir(parents=True, exist_ok=True)
    receipt_path = out / "external-review-sanity-receipt.json"
    checks: list[dict[str, Any]] = []

    checks.append(
        _run(
            [
                "uv",
                "run",
                "ruff",
                "check",
                "src/tau_coding/init_project.py",
                "src/tau_coding/local_provider_readiness.py",
                "src/tau_coding/airgap_no_egress.py",
                "src/tau_coding/itar_contract.py",
                "src/tau_coding/sparta_posture.py",
                "src/tau_coding/demo_airgap_itar.py",
                "src/tau_coding/embry_sparta_demo.py",
                "tests/test_init_project.py",
                "tests/test_local_provider_readiness.py",
                "tests/test_airgap_no_egress.py",
                "tests/test_itar_contract.py",
                "tests/test_sparta_posture.py",
                "tests/test_demo_airgap_itar.py",
                "tests/test_embry_sparta_demo.py",
            ],
            cwd=Path.cwd(),
        )
    )
    checks.append(
        _run(
            [
                "uv",
                "run",
                "pytest",
                "tests/test_init_project.py",
                "tests/test_local_provider_readiness.py",
                "tests/test_airgap_no_egress.py",
                "tests/test_itar_contract.py",
                "tests/test_sparta_posture.py",
                "tests/test_demo_airgap_itar.py",
                "tests/test_embry_sparta_demo.py",
                "-q",
            ],
            cwd=Path.cwd(),
        )
    )
    demo_dir = out / "tau-review-demo"
    checks.append(
        _run(
            ["uv", "run", "tau", "demo", "airgap-itar-basic", "--out", str(demo_dir)],
            cwd=Path.cwd(),
        )
    )
    checks.append(_run(["uv", "run", "tau", "run-status", str(demo_dir)], cwd=Path.cwd()))
    checks.append(
        _run(
            [
                "uv",
                "run",
                "tau",
                "proof-index",
                "build",
                str(demo_dir),
                "--out",
                str(demo_dir / "proof-index.jsonl"),
            ],
            cwd=Path.cwd(),
        )
    )
    expected_files = [
        demo_dir / "policy-profile.json",
        demo_dir / "data-boundary.json",
        demo_dir / "local-provider-readiness-receipt.json",
        demo_dir / "airgap-no-egress-receipt.json",
        demo_dir / "itar-contract-receipt.json",
        demo_dir / "sparta-posture-contract.json",
        demo_dir / "proof-index.jsonl",
    ]
    missing = [str(path) for path in expected_files if not path.exists()]
    checks.append(
        {
            "command": "expected-file-check",
            "returncode": 0 if not missing else 1,
            "ok": not missing,
            "stdout": json.dumps({"missing": missing}, sort_keys=True),
            "stderr": "",
        }
    )
    posture = _read_json(demo_dir / "sparta-posture-contract.json")
    demo_verdict = posture.get("readiness", {}).get("status")
    blockers = posture.get("top_blockers")
    top_blocker = blockers[0].get("code") if isinstance(blockers, list) and blockers else None
    posture_ok = (
        demo_verdict == "NOT_SIGNOFF_READY"
        and top_blocker == "human_export_control_review_required"
    )
    checks.append(
        {
            "command": "posture-verdict-check",
            "returncode": 0 if posture_ok else 1,
            "ok": posture_ok,
            "stdout": json.dumps(
                {"demo_verdict": demo_verdict, "top_blocker": top_blocker},
                sort_keys=True,
            ),
            "stderr": "",
        }
    )

    failed = [check for check in checks if check["ok"] is not True]
    receipt = {
        "schema": SCHEMA,
        "ok": not failed,
        "status": "PASS" if not failed else "BLOCKED",
        "mocked": False,
        "live": True,
        "provider_live": False,
        "check_count": len(checks),
        "failed_check_count": len(failed),
        "demo_verdict": demo_verdict,
        "top_blocker": top_blocker,
        "demo_dir": str(demo_dir),
        "checks": checks,
        "receipt_path": str(receipt_path),
        "non_claims": [
            "Synthetic data only.",
            "Does not prove ITAR compliance.",
            "Does not prove model semantic correctness.",
            "Does not prove airgap certification.",
        ],
        "created_at": datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z"),
    }
    receipt_path.write_text(json.dumps(receipt, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(receipt, indent=2, sort_keys=True))
    return 0 if receipt["ok"] else 1


def _run(command: list[str], *, cwd: Path) -> dict[str, Any]:
    result = subprocess.run(command, cwd=cwd, capture_output=True, text=True, check=False)
    return {
        "command": " ".join(command),
        "returncode": result.returncode,
        "ok": result.returncode == 0,
        "stdout": result.stdout[-4000:],
        "stderr": result.stderr[-4000:],
    }


def _read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    payload = json.loads(path.read_text(encoding="utf-8"))
    return payload if isinstance(payload, dict) else {}


if __name__ == "__main__":
    raise SystemExit(main())
