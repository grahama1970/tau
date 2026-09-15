#!/usr/bin/env python3
"""Retained issue #350 proof for the canonical five-workflow catalog surface.

Asserts, through the public CLI only:
1. `tau workflows list --json` exposes exactly the five canonical workflows in
   ladder order, matching `tau_coding.workflows.catalog`.
2. `tau workflows describe <id> --json` matches the authoritative registry
   descriptors in `src/tau_coding/workflows/definitions/<id>.json`.
3. A missing required launch input fails with an actionable option error.
4. The retained clean-checkout trace (issue-350 proof bundle) carries five
   resolved launch/deep-link records with explicit mocked/live boundaries.

Writes a `tau.workflow_catalog_eval.v1` JSON receipt. Exit non-zero on any
assertion failure.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

SCHEMA = "tau.workflow_catalog_eval.v1"

EXPECTED_IDS = [
    "repository-readiness",
    "tau-operator-reference",
    "repository-evidence-map",
    "approved-release-bundle",
    "durable-repository-qualification",
]
IDENTITY_FIELDS = (
    "workflow_id",
    "workflow_version",
    "rung",
    "title",
    "topology",
    "result_node_id",
    "input_schema",
    "result_schema",
)


def run(argv: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
    return subprocess.run(argv, capture_output=True, text=True, **kwargs)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, default=Path.cwd())
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument(
        "--trace",
        type=Path,
        default=Path("docs/proofs/tickets/issue-350-workflow-catalog-launch-progress-20260915/trace.json"),
    )
    args = parser.parse_args()

    repo = Path(run(["git", "-C", str(args.repo), "rev-parse", "--show-toplevel"]).stdout.strip())
    checks: list[dict[str, Any]] = []
    ok = True

    def record(name: str, passed: bool, detail: dict[str, Any]) -> None:
        nonlocal ok
        checks.append({"check": name, "passed": passed, **detail})
        ok = ok and passed

    # 1. list: exactly five ordered rungs from the one registry.
    cli_list = run(["uv", "run", "tau", "workflows", "list", "--json"], cwd=repo)
    catalog_payload = json.loads(cli_list.stdout)
    ids = [w["workflow_id"] for w in catalog_payload["workflows"]]
    rungs = [w["rung"] for w in catalog_payload["workflows"]]
    registry = run(
        ["uv", "run", "python", "-c",
         "from tau_coding.workflows.catalog import workflow_catalog_payload;"
         "import json,sys;"
         "sys.stdout.write(json.dumps([w['workflow_id'] for w in workflow_catalog_payload()['workflows']]))"],
        cwd=repo,
    )
    record(
        "list_exposes_five_ordered_rungs_from_one_registry",
        ids == EXPECTED_IDS and rungs == [1, 2, 3, 4, 5] and registry.stdout.strip() == json.dumps(EXPECTED_IDS),
        {"ids": ids, "rungs": rungs, "registry_ids_match": registry.stdout.strip() == json.dumps(EXPECTED_IDS)},
    )

    # 2. describe matches the authoritative registry descriptors.
    describe_detail: dict[str, Any] = {}
    describe_ok = True
    for wid in EXPECTED_IDS:
        desc = json.loads(
            run(["uv", "run", "tau", "workflows", "describe", wid, "--json"], cwd=repo).stdout
        )
        reg = json.loads(
            (repo / "src/tau_coding/workflows/definitions" / f"{wid}.json").read_text()
        )
        mismatches = [f for f in IDENTITY_FIELDS if desc[f] != reg[f]]
        describe_detail[wid] = mismatches or "match"
        describe_ok = describe_ok and not mismatches
    record("describe_matches_registry_for_all_five", describe_ok, describe_detail)

    # 3. missing --goal is an actionable blocker, not a traceback.
    with tempfile.TemporaryDirectory(prefix="tau350-catalog-neg-") as tmp:
        neg = run(
            ["uv", "run", "tau", "workflows", "run", "repository-readiness",
             "--repo", str(repo), "--run-dir", str(Path(tmp) / "rd")],
            cwd=repo,
        )
    record(
        "missing_goal_is_actionable_blocker",
        neg.returncode == 2 and "missing required option: --goal" in (neg.stderr + neg.stdout)
        and "Traceback" not in (neg.stderr + neg.stdout),
        {"exit_code": neg.returncode, "stderr_tail": neg.stderr.strip().splitlines()[-1:]},
    )

    # 4. retained clean-checkout trace integrity.
    trace_path = args.trace if args.trace.is_absolute() else repo / args.trace
    trace = json.loads(trace_path.read_text())
    launches = trace["step3_launch"]["launches"]
    boundaries = trace["boundaries"]
    record(
        "retained_trace_five_resolved_launches_with_boundaries",
        trace.get("schema") == "tau.issue350.workflow_launch_trace.v1"
        and trace.get("verdict") == "PASS"
        and len(launches) == 5
        and [l["rung"] for l in launches] == [1, 2, 3, 4, 5]
        and all(l["open_progress"]["resolved"] is True for l in launches)
        and boundaries["mocked"] is False
        and boundaries["live"] is True
        and boundaries["provider_live"] is False,
        {
            "trace": str(trace_path.relative_to(repo)),
            "launch_count": len(launches),
            "boundaries": {k: boundaries[k] for k in ("mocked", "live", "provider_live")},
        },
    )

    out = args.out.expanduser().resolve()
    out.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema": SCHEMA,
        "status": "PASS" if ok else "FAIL",
        "ok": ok,
        "mocked": False,
        "live": True,
        "provider_live": False,
        "checked_at": datetime.now(UTC).isoformat(),
        "repo": str(repo),
        "checks": checks,
    }
    out.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"status": payload["status"], "out": str(out)}))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
