#!/usr/bin/env bash
set -euo pipefail

OUT_DIR="${1:-/tmp/tau-scillm-worker-example}"
REPO_DIR="${OUT_DIR}/repo"
WORK_ORDER="${OUT_DIR}/work-order.json"
RESULT="${OUT_DIR}/scillm-result.json"
RECEIPT="${OUT_DIR}/scillm-worker-receipt.json"
LAUNCH_RECEIPT="${OUT_DIR}/scillm-worker-launch-receipt.json"
DEMO_RECEIPT="${OUT_DIR}/demo-receipt.json"
SANDBOX_RECEIPT="${OUT_DIR}/sandbox-run-receipt.json"

mkdir -p "${REPO_DIR}/src" "${REPO_DIR}/tests" "${OUT_DIR}/logs"
printf 'def answer():\n    return 42\n' > "${REPO_DIR}/src/example.py"
printf 'fixture pytest log\n' > "${OUT_DIR}/logs/pytest.log"

cat > "${SANDBOX_RECEIPT}" <<JSON
{
  "schema": "tau.sandbox_run_receipt.v1",
  "ok": true,
  "status": "PASS",
  "mocked": true,
  "live": false,
  "provider_live": false,
  "command_executed": false,
  "network_egress": "denied"
}
JSON

cat > "${WORK_ORDER}" <<JSON
{
  "schema": "tau.executor.scillm_worker.v1",
  "dag_id": "scillm-worker-example",
  "node_id": "coder",
  "agent": "coder",
  "goal_hash": "sha256:scillm-worker-example-goal",
  "attempt": 1,
  "repo": "${REPO_DIR}",
  "allowed_paths": ["src/**", "tests/**"],
  "forbidden_paths": ["secrets/**", ".env", ".github/**"],
  "task": "Use SciLLM OpenCode serve as a bounded coding delegate and return structured evidence.",
  "required_artifacts": ["logs/pytest.log"],
  "result_path": "${RESULT}",
  "receipt_path": "${RECEIPT}",
  "high_stakes": true,
  "zero_trust": true,
  "execution_substrate": "docker-sandbox",
  "sandbox_receipt_path": "${SANDBOX_RECEIPT}",
  "policy_profile": {
    "schema": "tau.policy_profile.v1",
    "profile_id": "scillm-worker-example-zero-trust",
    "default_decision": "deny"
  },
  "data_boundary": {
    "schema": "tau.data_boundary.v1",
    "classification": "public",
    "export_controlled": false,
    "external_provider_allowed": false,
    "external_research_allowed": false,
    "public_repo_allowed": false
  },
  "model_provider_route": {
    "surface": "opencode_serve",
    "endpoint": "/v1/scillm/opencode/runs",
    "agent": "build",
    "skills": ["memory", "debugger", "scillm"]
  }
}
JSON

WORKER_RESULT_SOURCE="fixture"
if [[ -n "${SCILLM_WORKER_RESULT:-}" ]]; then
  cp "${SCILLM_WORKER_RESULT}" "${RESULT}"
  WORKER_RESULT_SOURCE="external"
else
  cat > "${RESULT}" <<JSON
{
  "schema": "tau.scillm_worker_result.v1",
  "status": "NEEDS_REVIEW",
  "goal_hash": "sha256:scillm-worker-example-goal",
  "changed_files": ["src/example.py"],
  "artifacts": ["logs/pytest.log"],
  "tests_run": [
    {
      "name": "pytest",
      "status": "PASS",
      "log_path": "${OUT_DIR}/logs/pytest.log"
    }
  ],
  "findings": [],
  "next_recommended_route": "reviewer",
  "model_provider_route": {
    "surface": "opencode_serve",
    "endpoint": "/v1/scillm/opencode/runs",
    "agent": "build"
  }
}
JSON
fi

uv run tau scillm-worker-launch \
  --work-order "${WORK_ORDER}" \
  --out "${LAUNCH_RECEIPT}" >/tmp/tau-scillm-worker-launch.stdout.json

uv run tau scillm-worker-validate \
  --work-order "${WORK_ORDER}" \
  --result "${RESULT}" \
  --out "${RECEIPT}" >/tmp/tau-scillm-worker-validate.stdout.json

python3 - "${DEMO_RECEIPT}" "${RECEIPT}" "${LAUNCH_RECEIPT}" "${WORKER_RESULT_SOURCE}" <<'PY'
import json
import sys
from pathlib import Path

demo_path = Path(sys.argv[1])
receipt_path = Path(sys.argv[2])
launch_receipt_path = Path(sys.argv[3])
worker_result_source = sys.argv[4]
receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
launch_receipt = json.loads(launch_receipt_path.read_text(encoding="utf-8"))
route = receipt.get("model_provider_route", {})
payload = {
    "schema": "tau.scillm_worker_example_receipt.v1",
    "ok": receipt.get("ok") is True and launch_receipt.get("ok") is True,
    "status": (
        "PASS" if receipt.get("ok") is True and launch_receipt.get("ok") is True else "BLOCKED"
    ),
    "mocked": worker_result_source == "fixture",
    "live": worker_result_source != "fixture",
    "provider_live": False,
    "worker_result_source": worker_result_source,
    "worker_receipt_path": str(receipt_path),
    "worker_receipt_schema": receipt.get("schema"),
    "worker_receipt_status": receipt.get("status"),
    "worker_receipt_alert_codes": receipt.get("alert_codes", []),
    "launch_receipt_path": str(launch_receipt_path),
    "launch_receipt_schema": launch_receipt.get("schema"),
    "launch_receipt_status": launch_receipt.get("status"),
    "launch_receipt_alert_codes": launch_receipt.get("alert_codes", []),
    "launch_url": launch_receipt.get("url"),
    "model_provider_route": route,
    "proof_scope": {
        "proves": [
            "Tau built a dry-run SciLLM OpenCode-serve launch request from a bounded work order.",
            "Tau validated a SciLLM-shaped worker result against a bounded work order.",
            "Tau checked goal hash, changed paths, required artifacts, test logs, substrate evidence, and route metadata."
        ],
        "does_not_prove": [
            "Tau called SciLLM.",
            "OpenCode serve accepted or ran the request.",
            "OpenCode serve performed live coding work.",
            "The code is semantically correct.",
            "Provider/model semantic quality."
        ],
    },
}
demo_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
if payload["status"] != "PASS":
    raise SystemExit(1)
PY

cat "${DEMO_RECEIPT}"
