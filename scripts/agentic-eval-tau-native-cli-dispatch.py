#!/usr/bin/env python3
"""Live CLI proof for tau#340: `tau run` dispatches tau_agent nodes over SciLLM.

Writes a fresh random nonce the model has never seen, runs the documented
``tau run`` entrypoint (subprocess, not an in-process harness) with a real
configured SciLLM profile, then independently reads back the nonce from the
final text, the tool-effect payload in SQLite, the settlement, and the node
receipt. Also runs four preflight-rejection specs through the same CLI and
proves ``provider_invoked`` stays false for each.
"""

# ruff: noqa: E501  (live proof harness; long report lines are intentional)

from __future__ import annotations

import argparse
import copy
import json
import secrets
import subprocess
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC = REPO_ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from tau_coding.dag_runtime.admission import write_durable_json  # noqa: E402
from tau_coding.dag_runtime.agent_events import load_agent_events  # noqa: E402
from tau_coding.dag_runtime.run_store import SqliteDagRunStore  # noqa: E402

NEGATIVE_CASES: dict[str, dict[str, Any]] = {
    "disallowed-path": {"allowed_paths": ["../**"]},
    "unsupported-tool": {"allowed_tools": ["bash"]},
    "missing-profile": {"agent_requirement": {"profile_preferences": ["no-such-profile"]}},
    "malformed-node": {"max_turns": "many"},
}
EXPECTED_VERDICTS = {
    "disallowed-path": "NATIVE_PATH_POLICY_INVALID",
    "unsupported-tool": "NATIVE_TOOL_UNSUPPORTED",
    "missing-profile": "TRANSPORT_PROFILE_UNAVAILABLE",
    "malformed-node": "NATIVE_NODE_INVALID",
}


def _spec(work: Path, *, profiles: list[str]) -> dict[str, Any]:
    return {
        "schema": "tau.generic_dag_spec.v1",
        "run_id": "tau340-live-reviewer",
        "run_dir": str(work / "run"),
        "nodes": [
            {
                "node_id": "reviewer",
                "role": "review",
                "tau_agent": {
                    "prompt": (
                        "You are a read-only reviewer. Use the read tool to read the file "
                        "notes/nonce.txt (path exactly notes/nonce.txt). Then reply with one "
                        "line: NONCE=<the exact contents of that file, trimmed>. Do not guess; "
                        "if the tool fails, say TOOL_FAILED."
                    ),
                    "role": "review",
                    "agent_requirement": {
                        "schema": "tau.agent_requirement.v1",
                        "role": "review",
                        "harness": "tau_native_agent_loop",
                        "profile_preferences": profiles,
                        "required_transport_capabilities": ["tool_calling", "structured_events"],
                        "workspace": {"mode": "read_only", "allowed_paths": ["notes/**"]},
                        "required_evidence": ["tool_effect_receipt"],
                        "fallback_policy": {"allowed": True, "prohibit_capability_downgrade": True},
                    },
                    "allowed_tools": ["read"],
                    "allowed_paths": ["notes/**"],
                    "cwd": str(work / "workspace"),
                    "max_turns": 4,
                    "max_tool_calls": 2,
                    "required_evidence": ["tool_effect_receipt"],
                },
                "depends_on": [],
                "accepted_context_from": [],
                "receipt_path": str(work / "reviewer.json"),
                "timeout_seconds": 300,
                "max_attempts": 1,
            }
        ],
    }


def _tau_run(spec_path: Path, out_dir: Path) -> tuple[int, dict[str, Any]]:
    proc = subprocess.run(
        ["uv", "run", "tau", "run", str(spec_path)],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
        timeout=600,
        check=False,
    )
    (out_dir / "cli-stdout.json").write_text(proc.stdout, encoding="utf-8")
    (out_dir / "cli-stderr.txt").write_text(proc.stderr, encoding="utf-8")
    try:
        payload = json.loads(proc.stdout)
    except json.JSONDecodeError:
        payload = {}
    return proc.returncode, payload


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", required=True)
    parser.add_argument("--work", required=True)
    parser.add_argument("--profile", action="append", default=None)
    args = parser.parse_args()
    work = Path(args.work).expanduser().resolve()
    (work / "workspace" / "notes").mkdir(parents=True, exist_ok=True)
    nonce = f"nonce-{secrets.token_hex(8)}"
    (work / "workspace" / "notes" / "nonce.txt").write_text(nonce + "\n", encoding="utf-8")
    (work / "nonce-expected.txt").write_text(nonce + "\n", encoding="utf-8")
    profiles = args.profile or ["claude-model-turn", "codex-model-turn"]
    spec = _spec(work, profiles=profiles)
    spec_path = work / "spec.json"
    spec_path.write_text(json.dumps(spec, indent=2), encoding="utf-8")
    errors: list[str] = []

    exit_code, cli = _tau_run(spec_path, work)
    node = next((n for n in cli.get("nodes", []) if n.get("node_id") == "reviewer"), {})
    accepted = node.get("accepted_output") or {}
    settlement = accepted.get("settlement") or {}
    final_text = str(accepted.get("final_text") or "")
    receipt = json.loads((work / "reviewer.json").read_text(encoding="utf-8")) if (work / "reviewer.json").exists() else {}
    events: list[dict[str, Any]] = []
    store_path = work / "run" / "dag-run.sqlite3"
    if store_path.exists() and cli.get("scheduler_run_id"):
        store = SqliteDagRunStore(store_path)
        try:
            events = [e["agent_event"] for e in load_agent_events(store, cli["scheduler_run_id"], node_id="reviewer")]
        finally:
            store.close()
    effects = [e["payload"] for e in events if e["event_type"] == "tool_effect_recorded"]
    live = {
        "exit_code": exit_code,
        "cli_ok": cli.get("ok"),
        "node_status": node.get("status"),
        "node_verdict": node.get("verdict"),
        "provider_invoked": node.get("provider_invoked"),
        "transport_profile": node.get("transport_profile"),
        "transport_ids": sorted({str(t.get("transport_id")) for t in receipt.get("transport_turn_results") or []}),
        "nonce": nonce,
        "final_text": final_text.strip(),
        "nonce_in_final_text": nonce in final_text,
        "nonce_in_tool_effect_payload": any(nonce in json.dumps(e) for e in effects),
        "settlement_state": settlement.get("state"),
        "settlement_turns": settlement.get("turns"),
        "tool_effect_receipt_sha256s": settlement.get("tool_effect_receipt_sha256s"),
        "receipt_settlement_sha256_matches": (receipt.get("settlement") or {}).get("sha256") == settlement.get("sha256"),
        "sqlite_event_types": [e["event_type"] for e in events],
    }
    if exit_code != 0 or node.get("status") != "PASS" or node.get("verdict") != "PASS":
        errors.append("live_cli_node_not_pass")
    if not live["nonce_in_final_text"] or not live["nonce_in_tool_effect_payload"]:
        errors.append("nonce_not_read_back")
    if settlement.get("state") != "completed" or not settlement.get("tool_effect_receipt_sha256s"):
        errors.append("settlement_missing_tool_effect")
    if not live["receipt_settlement_sha256_matches"]:
        errors.append("node_receipt_settlement_mismatch")
    if "agent_node_settled" not in live["sqlite_event_types"]:
        errors.append("sqlite_events_missing")
    if not node.get("provider_invoked") or (node.get("transport_profile") or {}).get("profile_id") not in profiles:
        errors.append("provider_not_invoked_via_selected_profile")

    negative: dict[str, Any] = {}
    for name, override in NEGATIVE_CASES.items():
        case_dir = work / f"neg-{name}"
        case_dir.mkdir(exist_ok=True)
        case = copy.deepcopy(spec)
        case["run_id"] = f"tau340-neg-{name}"
        case["run_dir"] = str(case_dir / "run")
        case["nodes"][0]["receipt_path"] = str(case_dir / "reviewer.json")
        agent = case["nodes"][0]["tau_agent"]
        for key, value in override.items():
            if key == "agent_requirement":
                agent[key] = {**agent[key], **value}
            else:
                agent[key] = value
        case_spec = case_dir / "spec.json"
        case_spec.write_text(json.dumps(case, indent=2), encoding="utf-8")
        code, payload = _tau_run(case_spec, case_dir)
        case_node = next((n for n in payload.get("nodes", []) if n.get("node_id") == "reviewer"), {})
        negative[name] = {
            "exit_code": code,
            "status": case_node.get("status"),
            "verdict": case_node.get("verdict"),
            "provider_invoked": case_node.get("provider_invoked"),
            "errors": case_node.get("errors"),
        }
        if code == 0 or case_node.get("status") != "BLOCKED" or case_node.get("verdict") != EXPECTED_VERDICTS[name] or case_node.get("provider_invoked") is not False:
            errors.append(f"negative_case_failed:{name}")

    payload = {
        "schema": "tau.native_cli_dispatch_proof.v1",
        "status": "PASS" if not errors else "FAIL",
        "mocked": False,
        "live": True,
        "provider_live": bool(node.get("provider_invoked")),
        "entrypoint": "uv run tau run <spec.json> (subprocess)",
        "work": str(work),
        "live_reviewer": live,
        "negative_cases": negative,
        "proof_boundary": {
            "proves": (
                "The documented `tau run` CLI dispatches a tau_agent node through the tau#310 adapter over a live "
                "SciLLM transport profile; a read-only reviewer read a fresh random nonce with Tau's native read tool "
                "and the nonce was read back from final text, the SQLite tool-effect payload, the settlement, and the "
                "node receipt; four malformed/unsupported/unavailable specs were blocked by preflight through the same "
                "CLI with provider_invoked=false."
            ),
            "does_not_prove": "provider semantic quality beyond nonce recall, write-capable workers, Herdr hosting, GOAL.md completion",
        },
        "errors": errors,
    }
    write_durable_json(Path(args.out).expanduser().resolve(), payload)
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0 if not errors else 2


if __name__ == "__main__":
    raise SystemExit(main())
