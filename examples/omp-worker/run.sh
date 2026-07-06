#!/usr/bin/env bash
set -euo pipefail

OUT_DIR="${1:-/tmp/tau-omp-worker-example}"
REPO_DIR="${OUT_DIR}/repo"
WORK_ORDER="${OUT_DIR}/work-order.json"
RESULT="${OUT_DIR}/omp-result.json"
RECEIPT="${OUT_DIR}/omp-worker-receipt.json"
DEMO_RECEIPT="${OUT_DIR}/demo-receipt.json"

mkdir -p "${REPO_DIR}/src" "${REPO_DIR}/tests" "${OUT_DIR}/logs"
printf 'def answer():\n    return 42\n' > "${REPO_DIR}/src/example.py"
printf 'fixture pytest log\n' > "${OUT_DIR}/logs/pytest.log"

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
  "execution_substrate": "docker-sandbox"
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
      "log_path": "${OUT_DIR}/logs/pytest.log"
    }
  ],
  "findings": [],
  "next_recommended_route": "reviewer"
}
JSON
fi

uv run tau omp-worker-validate \
  --work-order "${WORK_ORDER}" \
  --result "${RESULT}" \
  --out "${RECEIPT}" >/tmp/tau-omp-worker-validate.stdout.json

python3 - "${DEMO_RECEIPT}" "${RECEIPT}" "${WORKER_RESULT_SOURCE}" <<'PY'
import json
import sys
from pathlib import Path

demo_path = Path(sys.argv[1])
receipt_path = Path(sys.argv[2])
worker_result_source = sys.argv[3]
receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
payload = {
    "schema": "tau.omp_worker_example_receipt.v1",
    "ok": receipt.get("ok") is True,
    "status": "PASS" if receipt.get("ok") is True else "BLOCKED",
    "mocked": worker_result_source == "fixture",
    "live": worker_result_source != "fixture",
    "provider_live": False,
    "worker_result_source": worker_result_source,
    "worker_receipt_path": str(receipt_path),
    "worker_receipt_schema": receipt.get("schema"),
    "worker_receipt_status": receipt.get("status"),
    "worker_receipt_alert_codes": receipt.get("alert_codes", []),
    "proof_scope": {
        "proves": [
            "Tau validated an OMP-shaped worker result against a bounded work order.",
            "Tau checked goal hash, changed paths, required artifacts, test logs, and substrate metadata."
        ],
        "does_not_prove": [
            "Tau launched OMP.",
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
