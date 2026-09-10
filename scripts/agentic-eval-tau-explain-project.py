"""Retained eval for Tau's source-bound explain-project package."""

from __future__ import annotations

import argparse
import ast
import json
import subprocess
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[1]
EXPLAIN = Path.home() / ".pi/agent/skills/explain-project/run.sh"
EXCALIDRAW = Path.home() / ".pi/agent/skills/ops-excalidraw/run.sh"
EXPLAINERS = REPO / "docs/explain/explainers.jsonl"
BOARDS = REPO / "docs/explain/boards"

EXPECTED_RECORDS = 14

REQUIRED_DOC_LINKS = {
    "src/tau_agent/loop.py": "tau.agent-loop",
    "src/tau_ai/provider.py": "tau.provider-boundary",
    "src/tau_coding/dag_runtime/scheduler.py": "tau.dag-runtime",
    "src/tau_coding/dag_runtime/attempt_result.py": "tau.attempt-result-boundary",
    "src/tau_coding/dag_runtime/native_agent_dispatch.py": "tau.native-agent-node",
    "src/tau_coding/dag_runtime/replay.py": "tau.viewer-replay",
    "src/tau_coding/dag_viewer/project_receipt_projection.py": "tau.viewer-replay",
    "src/tau_coding/dag_runtime/compiler.py": "tau.dag-compilation",
    "src/tau_coding/dag_runtime/transition.py": "tau.scheduler-authority",
    "src/tau_coding/dag_runtime/run_store.py": "tau.journal-recovery",
    "src/tau_coding/dag_runtime/artifact_reference.py": "tau.node-input-binding",
    "src/tau_coding/runtime_backends/contracts.py": "tau.runtime-backend-boundary",
    "src/tau_coding/dag_runtime/worker_assignment.py": "tau.worker-dispatch",
    "src/tau_coding/dag_runtime/effects.py": "tau.effect-settlement",
    "src/tau_coding/dag_runtime/triage_error_bridge.py": "tau.failure-repair",
}

QUESTIONS = {
    "How does Tau execute a public DAG contract durably?": "tau.dag_runtime",
    "Why can a DAG node not smuggle arbitrary fields?": "tau.attempt_result_boundary",
    "How does a Tau DAG node run the native agent loop safely?": "tau.native_agent_node",
    "How does Tau turn a public DAG contract into the canonical DagPlan?": "tau.dag_compilation",
    "Who decides that a Tau DAG node is ready or blocked?": "tau.scheduler_authority",
    "How does Tau decide exactly what context a DAG node may consume?": "tau.node_input_binding",
    "What can a Tau runtime backend do, and what decisions is it forbidden from making?": "tau.runtime_backend_boundary",
    "How does Tau prevent retries from duplicating side effects?": "tau.effect_settlement",
    "When execution goes wrong, how does Tau classify the failure?": "tau.failure_repair",
}


def run(command: list[str], *, cwd: Path = REPO) -> dict[str, Any]:
    completed = subprocess.run(command, cwd=cwd, text=True, capture_output=True, timeout=180)
    return {
        "command": command,
        "cwd": str(cwd),
        "exit_code": completed.returncode,
        "stdout": completed.stdout,
        "stderr": completed.stderr,
    }


def json_stdout(result: dict[str, Any]) -> dict[str, Any]:
    if result["exit_code"] != 0:
        return {}
    return json.loads(result["stdout"])


def docstring_links_present() -> bool:
    for rel, diagram_id in REQUIRED_DOC_LINKS.items():
        path = REPO / rel
        doc = ast.get_docstring(ast.parse(path.read_text(encoding="utf-8"))) or ""
        if f"Diagram ID: {diagram_id}" not in doc:
            return False
        if "Excalidraw source: docs/explain/boards/" not in doc:
            return False
    return True


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", required=True)
    args = parser.parse_args()

    validate = run([str(EXPLAIN), "validate", str(EXPLAINERS)])
    listing = run([str(EXPLAIN), "list", str(EXPLAINERS)])
    ask_results = {
        question: run([str(EXPLAIN), "ask", str(EXPLAINERS), "--question", question])
        for question in QUESTIONS
    }
    board_results = {
        board.name: run([str(EXCALIDRAW), "validate", str(board), "--profile", "fanout"])
        for board in sorted(BOARDS.glob("*.excalidraw"))
    }

    validate_payload = json_stdout(validate)
    list_payload = json_stdout(listing)
    ask_payloads = {question: json_stdout(result) for question, result in ask_results.items()}

    checks = {
        "explainer_validation_passed": validate_payload.get("status") == "PASS"
        and validate_payload.get("records") == EXPECTED_RECORDS,
        "explainer_list_has_expected_records": len(list_payload.get("records", [])) == EXPECTED_RECORDS,
        "questions_route_to_expected_features": all(
            ask_payloads[question].get("matched_feature") == feature
            and ask_payloads[question].get("debugger_stops")
            and ask_payloads[question].get("teleprompter_points")
            and ask_payloads[question].get("diagram", {}).get("source_kind") == "excalidraw"
            for question, feature in QUESTIONS.items()
        ),
        "boards_validate": len(board_results) == EXPECTED_RECORDS
        and all(json_stdout(result).get("status") == "PASS" for result in board_results.values()),
        "module_docstrings_link_diagrams": docstring_links_present(),
    }
    proof = {
        "schema": "tau.explain_project_proof.v1",
        "ok": all(checks.values()),
        "status": "PASS" if all(checks.values()) else "FAIL",
        "mocked": False,
        "live": True,
        "provider_live": False,
        "github_live": False,
        "repo": str(REPO),
        "proof_boundary": {
            "proves": (
                "Tau has a valid source-bound explain-project catalog with editable "
                "Excalidraw boards, debugger stops, cockpit bullet points, and "
                "module docstring links for the first core and P1 authority-boundary architecture slices."
            ),
            "does_not_prove": (
                "Live debugger execution, provider availability, rendered SVG approval, "
                "or complete diagram coverage for every Tau module."
            ),
        },
        "checks": checks,
        "commands": {
            "validate": validate,
            "list": listing,
            "ask": ask_results,
            "boards": board_results,
        },
    }
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(proof, indent=2, sort_keys=True), encoding="utf-8")
    print(json.dumps(proof, indent=2, sort_keys=True))
    return 0 if proof["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
