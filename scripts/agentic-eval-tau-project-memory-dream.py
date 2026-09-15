"""Agentic eval proof for the packaged project-memory-dream workflow (issue #320).

Runs the packaged workflow end-to-end over deterministic local fixture
workspaces through the canonical Tau scheduler, then reads back the produced
terminal receipts and validates each one under ``tau.project_dream_receipt.v1``
(issue #318). Model semantic quality is not a pass criterion; the fake
deterministic adapters satisfy the determinism requirement.

Output: one proof JSON with the required proof matrix read back from disk.
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
EXAMPLE_DIR = REPO_ROOT / "examples" / "project-memory-dream"
sys.path.insert(0, str(REPO_ROOT / "src"))
sys.path.insert(0, str(EXAMPLE_DIR))

import fixture_workspace as fw  # noqa: E402

from tau_coding.project_dream_receipt import (  # noqa: E402
    validate_project_dream_receipt_path,
)


def _sha256(path: Path) -> str:
    return f"sha256:{hashlib.sha256(path.read_bytes()).hexdigest()}"


def _run(config: Path, run_dir: Path) -> dict[str, Any]:
    completed = subprocess.run(
        [
            sys.executable,
            str(EXAMPLE_DIR / "workflow.py"),
            "run",
            "--config",
            str(config),
            "--run-dir",
            str(run_dir),
        ],
        capture_output=True,
        text=True,
        timeout=280,
        check=False,
    )
    payload = json.loads(completed.stdout[completed.stdout.index("{") :])
    return payload


def _summary(run_dir: Path) -> dict[str, Any]:
    return json.loads((run_dir / "run-summary.json").read_text(encoding="utf-8"))


def _rows(run_dir: Path) -> dict[str, dict[str, Any]]:
    return {row["project_id"]: row for row in _summary(run_dir)["terminal_rows"]}


def _events(run_dir: Path) -> list[tuple[str, str]]:
    lines = (run_dir / "events.jsonl").read_text(encoding="utf-8").splitlines()
    parsed = [json.loads(line[line.index("{") :]) for line in lines if line.strip()]
    return [(event.get("kind") or "", event.get("node_id") or "") for event in parsed]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", required=True, help="proof JSON output path")
    args = parser.parse_args()
    out_path = Path(args.out).expanduser().resolve()
    out_path.parent.mkdir(parents=True, exist_ok=True)

    proofs: dict[str, Any] = {}
    work_root = out_path.parent / "project-dream-work"
    shutil.rmtree(work_root, ignore_errors=True)
    work_root.mkdir(parents=True, exist_ok=True)
    root = work_root

        # -- default scenario: two eligible projects + unchanged skip + shadow ----
    ws = root / "default"
    pids = fw.default_workspace(ws)
    config = fw.write_config(ws, projects=pids)
    run_dir = root / "run-default"
    receipt = _run(config, run_dir)
    rows = _rows(run_dir)
    summary = _summary(run_dir)
    alpha_row, beta_row, gamma_row = rows["alpha"], rows["beta"], rows["gamma"]

    proofs["two_eligible_projects_independent_terminal_rows"] = {
        "ok": (
            receipt["status"] == "PASS"
            and alpha_row["terminal_status"] == "SHADOW_STAGED"
            and beta_row["terminal_status"] == "SHADOW_STAGED"
            and alpha_row["receipt_path"] != beta_row["receipt_path"]
        ),
        "evidence": [alpha_row["receipt_path"], beta_row["receipt_path"]],
    }
    proofs["unchanged_project_skipped_with_typed_reason"] = {
        "ok": (
            gamma_row["terminal_status"] == "NO_CHANGE"
            and gamma_row["skip_reason"] == "unchanged_project"
        ),
        "evidence": [
            str(run_dir / "artifacts" / "gamma" / "delta" / "dispatch-decision.json")
        ],
    }
    kinds = _events(run_dir)
    join_ok = True
    for pid in ("alpha", "beta"):
        join_settled = max(
            index
            for index, (kind, node) in enumerate(kinds)
            if node == f"join.{pid}" and kind == "node_receipt_validated"
        )
        packet_dispatched = min(
            index
            for index, (kind, node) in enumerate(kinds)
            if node == f"packet.{pid}" and kind == "node_dispatch"
        )
        join_ok = join_ok and join_settled < packet_dispatched
    proofs["join_before_packet"] = {
        "ok": join_ok,
        "evidence": [str(run_dir / "events.jsonl")],
    }
    heads_unchanged = all(
        json.loads((ws / "memory" / pid / "head.json").read_text(encoding="utf-8"))[
            "generation"
        ]
        == 1
        for pid in pids
    )
    zero_effects = all(
        json.loads(Path(row["receipt_path"]).read_text(encoding="utf-8"))[
            "accepted_effect_count"
        ]
        == 0
        for row in (alpha_row, beta_row)
    )
    proofs["shadow_staging_head_unchanged"] = {
        "ok": heads_unchanged and zero_effects,
        "evidence": [str(ws / "memory" / pid / "head.json") for pid in pids]
        + [alpha_row["receipt_path"], beta_row["receipt_path"]],
    }

    # -- restart after stage: byte-identical stage receipts, no duplicates ----
    def stage_snapshot() -> dict[str, str]:
        return {
            str(path.relative_to(run_dir)): _sha256(path)
            for path in sorted((run_dir / "artifacts").glob("*/stage/*/*"))
        }

    before = stage_snapshot()
    _run(config, run_dir)
    after = stage_snapshot()
    resumed_digests = [
        row["candidate_digest"] for row in _rows(run_dir).values() if row["candidate_digest"]
    ]
    proofs["restart_after_stage_no_duplicate"] = {
        "ok": before == after and len(resumed_digests) == len(set(resumed_digests)) == 2,
        "evidence": sorted(after),
    }

    # -- malformed transcript blocks only its project; sibling survives -------
    ws_mal = root / "malformed"
    fw.build_project(ws_mal, "alpha")
    fw.build_project(ws_mal, "beta")
    fw.corrupt_transcripts(ws_mal, "beta")
    notes = ws_mal / "projects" / "alpha" / "worktree" / "notes.md"
    notes.write_text(notes.read_text(encoding="utf-8") + "\nchanged\n", encoding="utf-8")
    cfg_mal = fw.write_config(ws_mal, projects=["alpha", "beta"])
    run_mal = root / "run-malformed"
    receipt_mal = _run(cfg_mal, run_mal)
    rows_mal = _rows(run_mal)
    no_model_output = not (run_mal / "artifacts" / "beta" / "synthesize").exists()
    proofs["malformed_transcript_blocks_only_its_project"] = {
        "ok": (
            receipt_mal["status"] == "PASS"
            and rows_mal["beta"]["terminal_status"] == "BLOCKED_INPUT"
            and rows_mal["beta"]["first_failed_gate"] == "transcript_malformed"
            and rows_mal["alpha"]["terminal_status"] == "SHADOW_STAGED"
            and no_model_output
        ),
        "evidence": [str(run_mal / "run-summary.json")],
    }
    proofs["one_project_failure_does_not_suppress_others"] = {
        "ok": (
            receipt_mal["status"] == "PASS"
            and rows_mal["alpha"]["terminal_status"] == "SHADOW_STAGED"
            and len(rows_mal) == 2
        ),
        "evidence": [str(run_mal / "run-summary.json")],
    }

    # -- distinct state/ingest failures --------------------------------------
    ws_distinct = root / "distinct"
    fw.build_project(ws_distinct, "alpha", faults={"state": "fail"})
    fw.build_project(ws_distinct, "beta", faults={"ingest": "fail"})
    for pid in ("alpha", "beta"):
        notes = ws_distinct / "projects" / pid / "worktree" / "notes.md"
        notes.write_text(notes.read_text(encoding="utf-8") + "\nchanged\n", encoding="utf-8")
    cfg_distinct = fw.write_config(ws_distinct, projects=["alpha", "beta"])
    run_distinct = root / "run-distinct"
    receipt_distinct = _run(cfg_distinct, run_distinct)
    rows_distinct = _rows(run_distinct)
    proofs["state_and_ingest_failures_distinct"] = {
        "ok": (
            receipt_distinct["status"] == "PASS"
            and rows_distinct["alpha"]["first_failed_gate"] == "project_state_unavailable"
            and rows_distinct["beta"]["first_failed_gate"] == "code_ingest_unavailable"
            and rows_distinct["alpha"]["first_failed_gate"]
            != rows_distinct["beta"]["first_failed_gate"]
        ),
        "evidence": [str(run_distinct / "run-summary.json")],
    }

    # -- undeclared capability fails closed before dispatch -------------------
    ws_cap = root / "undeclared"
    pids_cap = fw.default_workspace(ws_cap)
    cfg_cap = fw.write_config(
        ws_cap,
        projects=pids_cap,
        extra={"synthesis_uses_capabilities_extra": ["arangodb.write"]},
    )
    run_cap = root / "run-undeclared"
    receipt_cap = _run(cfg_cap, run_cap)
    preflight = json.loads(
        (run_cap / "node-receipts" / "preflight.json").read_text(encoding="utf-8")
    )
    proofs["undeclared_capability_rejected_before_dispatch"] = {
        "ok": (
            receipt_cap["status"] == "BLOCKED"
            and preflight["verdict"] == "BLOCKED"
            and any("undeclared_capabilities" in e for e in preflight["errors"])
            and not list((run_cap / "node-receipts").glob("synthesize.*"))
            and not list((run_cap / "artifacts").glob("*/synthesize/raw-model-output.json"))
        ),
        "evidence": [str(run_cap / "node-receipts" / "preflight.json")],
    }

    # -- #318 receipt validation readback (independent of node claims) --------
    validations: dict[str, Any] = {}
    all_ok = True
    for run in (run_dir, run_mal):
        for row in _rows(run).values():
            if not row.get("receipt_path"):
                continue
            validation = validate_project_dream_receipt_path(Path(row["receipt_path"]))
            validations[f"{run.name}:{row['project_id']}"] = {
                "receipt_path": row["receipt_path"],
                "ok": validation["ok"],
                "errors": validation["errors"],
            }
            all_ok = all_ok and validation["ok"]
    final_receipt = run_dir / "receipts" / "aggregate" / "project-dream-receipt.json"
    final_validation = validate_project_dream_receipt_path(final_receipt)
    validations["aggregate"] = {
        "receipt_path": str(final_receipt),
        "ok": final_validation["ok"],
        "errors": final_validation["errors"],
    }
    all_ok = all_ok and final_validation["ok"]
    proofs["terminal_receipts_validate_under_issue_318"] = {
        "ok": all_ok,
        "evidence": [entry["receipt_path"] for entry in validations.values()],
        "validations": validations,
    }

    ok = all(proof["ok"] for proof in proofs.values())
    payload = {
    "schema": "tau.project_memory_dream_agentic_eval_proof.v1",
    "status": "PASS" if ok else "FAIL",
    "mocked": False,
    "live": True,
    "provider_live": False,
    "checked_at": dt.datetime.now(dt.UTC).isoformat().replace("+00:00", "Z"),
    "workflow": "project-memory-dream",
    "scheduler": "tau_coding.generic_dag.run_generic_dag (canonical)",
    "receipt_contract": "tau.project_dream_receipt.v1 (issue #318)",
    "proofs": proofs,
    "proof_scope": {
        "proves": [
            "The packaged project-memory-dream workflow compiles to tau.generic_dag_spec.v1 "
            "and executes on the canonical Tau scheduler with deterministic local adapters.",
            "Terminal receipts validate under tau.project_dream_receipt.v1 with typed "
            "isolation, shadow default, and replay idempotency proofs read back from disk.",
        ],
        "does_not_prove": [
            "Semantic truth of synthesized project knowledge.",
            "Provider/model quality (fixture backend; opencode canary is separate).",
            "Production Graph Memory persistence beyond the local fixture head store.",
        ],
    },
    "errors": [] if ok else [name for name, proof in proofs.items() if not proof["ok"]],
    }
    out_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"status": payload["status"], "out": str(out_path)}, indent=2))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
