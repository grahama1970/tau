#!/usr/bin/env bash
set -euo pipefail

OUT_DIR="${1:-/tmp/tau-omp-worker-example}"
REPO_DIR="${OUT_DIR}/repo"
WORK_ORDER="${OUT_DIR}/work-order.json"
RESULT="${OUT_DIR}/omp-result.json"
RECEIPT="${OUT_DIR}/omp-worker-receipt.json"
LAUNCH_RECEIPT="${OUT_DIR}/omp-worker-launch-receipt.json"
APPLY_LAUNCH_RECEIPT="${OUT_DIR}/omp-worker-launch-apply-receipt.json"
DEMO_RECEIPT="${OUT_DIR}/demo-receipt.json"
SANDBOX_RECEIPT="${OUT_DIR}/sandbox-run-receipt.json"
FAKE_OMP="${OUT_DIR}/fake-omp"

mkdir -p "${REPO_DIR}/src" "${REPO_DIR}/tests" "${REPO_DIR}/logs"
printf 'def answer():\n    return 42\n' > "${REPO_DIR}/src/example.py"
printf 'fixture pytest log\n' > "${REPO_DIR}/logs/pytest.log"
cat > "${FAKE_OMP}" <<'PY'
#!/usr/bin/env python3
import json
import sys

payload = json.loads(sys.stdin.readline())
print(json.dumps({
    "schema": "fake.omp.rpc.response",
    "received_type": payload.get("type"),
    "metadata": payload.get("metadata"),
}, sort_keys=True))
PY
chmod +x "${FAKE_OMP}"

cat > "${SANDBOX_RECEIPT}" <<JSON
{
  "schema": "tau.sandbox_run_receipt.v1",
  "ok": true,
  "status": "PASS",
  "mocked": false,
  "live": true,
  "provider_live": false,
  "command_executed": true,
  "network_egress": "denied"
}
JSON

cat > "${WORK_ORDER}" <<JSON
{
  "schema": "tau.executor.omp.v1",
  "dag_id": "omp-worker-example",
  "node_id": "coder",
  "agent": "coder",
  "goal_hash": "sha256:omp-worker-example-goal",
  "attempt": 1,
  "repo": "${REPO_DIR}",
  "allowed_paths": ["src/**", "tests/**"],
  "forbidden_paths": ["secrets/**", ".env", ".github/**"],
  "task": "Make a bounded coding change and return a structured worker result.",
  "required_artifacts": ["logs/pytest.log"],
  "result_path": "${RESULT}",
  "receipt_path": "${RECEIPT}",
  "high_stakes": true,
  "zero_trust": true,
  "execution_substrate": "docker-sandbox",
  "sandbox_receipt_path": "${SANDBOX_RECEIPT}",
  "policy_profile": {
    "schema": "tau.policy_profile.v1",
    "profile_id": "omp-worker-example-zero-trust",
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
    "surface": "omp_rpc"
  }
}
JSON

WORKER_RESULT_SOURCE="fixture"
if [[ -n "${OMP_WORKER_RESULT:-}" ]]; then
  cp "${OMP_WORKER_RESULT}" "${RESULT}"
  WORKER_RESULT_SOURCE="external"
else
  cat > "${RESULT}" <<JSON
{
  "schema": "tau.omp_worker_result.v1",
  "status": "NEEDS_REVIEW",
  "goal_hash": "sha256:omp-worker-example-goal",
  "changed_files": ["src/example.py"],
  "artifacts": ["logs/pytest.log"],
  "tests_run": [
    {
      "name": "pytest",
      "status": "PASS",
      "log_path": "${REPO_DIR}/logs/pytest.log"
    }
  ],
  "findings": [],
  "next_recommended_route": "reviewer"
}
JSON
fi

uv run tau omp-worker-launch \
  --work-order "${WORK_ORDER}" \
  --out "${LAUNCH_RECEIPT}" >/tmp/tau-omp-worker-launch.stdout.json

uv run tau omp-worker-launch \
  --work-order "${WORK_ORDER}" \
  --out "${APPLY_LAUNCH_RECEIPT}" \
  --apply \
  --omp-bin "${FAKE_OMP}" \
  --timeout-s 5 >/tmp/tau-omp-worker-launch-apply.stdout.json

uv run tau omp-worker-validate \
  --work-order "${WORK_ORDER}" \
  --result "${RESULT}" \
  --out "${RECEIPT}" >/tmp/tau-omp-worker-validate.stdout.json

python3 - "${DEMO_RECEIPT}" "${RECEIPT}" "${LAUNCH_RECEIPT}" "${APPLY_LAUNCH_RECEIPT}" "${WORKER_RESULT_SOURCE}" <<'PY'
import json
import sys
from pathlib import Path

demo_path = Path(sys.argv[1])
receipt_path = Path(sys.argv[2])
launch_receipt_path = Path(sys.argv[3])
apply_launch_receipt_path = Path(sys.argv[4])
worker_result_source = sys.argv[5]
receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
launch_receipt = json.loads(launch_receipt_path.read_text(encoding="utf-8"))
apply_launch_receipt = json.loads(apply_launch_receipt_path.read_text(encoding="utf-8"))
ok = (
    receipt.get("ok") is True
    and launch_receipt.get("ok") is True
    and apply_launch_receipt.get("ok") is True
)
payload = {
    "schema": "tau.omp_worker_example_receipt.v1",
    "ok": ok,
    "status": "PASS" if ok else "BLOCKED",
    "mocked": worker_result_source == "fixture",
    "live": "mixed",
    "provider_live": False,
    "worker_result_source": worker_result_source,
    "worker_receipt_path": str(receipt_path),
    "worker_receipt_schema": receipt.get("schema"),
    "worker_receipt_status": receipt.get("status"),
    "worker_receipt_alert_codes": receipt.get("alert_codes", []),
    "worker_receipt_work_order_sha256": receipt.get("work_order_sha256"),
    "worker_receipt_result_sha256": receipt.get("result_sha256"),
    "worker_receipt_validated_artifacts": receipt.get("validated_artifacts", []),
    "launch_receipt_path": str(launch_receipt_path),
    "launch_receipt_schema": launch_receipt.get("schema"),
    "launch_receipt_status": launch_receipt.get("status"),
    "launch_receipt_alert_codes": launch_receipt.get("alert_codes", []),
    "launch_command": launch_receipt.get("command"),
    "apply_launch_receipt_path": str(apply_launch_receipt_path),
    "apply_launch_receipt_schema": apply_launch_receipt.get("schema"),
    "apply_launch_receipt_status": apply_launch_receipt.get("status"),
    "apply_launch_receipt_alert_codes": apply_launch_receipt.get("alert_codes", []),
    "apply_launch_process_executed": apply_launch_receipt.get("process_executed"),
    "apply_launch_exit_code": apply_launch_receipt.get("exit_code"),
    "apply_launch_stdout_path": apply_launch_receipt.get("stdout_path"),
    "apply_launch_stdout_sha256": apply_launch_receipt.get("stdout_sha256"),
    "apply_launch_stderr_sha256": apply_launch_receipt.get("stderr_sha256"),
    "apply_launch_log_artifacts": apply_launch_receipt.get("log_artifacts", []),
    "proof_scope": {
        "proves": [
            "Tau built a dry-run OMP RPC launch request from a bounded work order.",
            "Tau invoked a deterministic local OMP-compatible process and captured stdout/stderr.",
            "Tau validated an OMP-shaped worker result against a bounded work order.",
            "Tau checked goal hash, changed paths, required artifacts, test logs, and substrate metadata."
        ],
        "does_not_prove": [
            "Tau launched a real oh-my-pi binary.",
            "OMP accepted or ran the request.",
            "OMP performed live coding work.",
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
