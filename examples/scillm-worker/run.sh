#!/usr/bin/env bash
set -euo pipefail

OUT_DIR="${1:-/tmp/tau-scillm-worker-example}"
REPO_DIR="${OUT_DIR}/repo"
WORK_ORDER="${OUT_DIR}/work-order.json"
RESULT="${OUT_DIR}/scillm-result.json"
RECEIPT="${OUT_DIR}/scillm-worker-receipt.json"
LAUNCH_RECEIPT="${OUT_DIR}/scillm-worker-launch-receipt.json"
APPLY_LAUNCH_RECEIPT="${OUT_DIR}/scillm-worker-launch-apply-receipt.json"
DEMO_RECEIPT="${OUT_DIR}/demo-receipt.json"
SANDBOX_RECEIPT="${OUT_DIR}/sandbox-run-receipt.json"

mkdir -p "${REPO_DIR}/src" "${REPO_DIR}/tests" "${REPO_DIR}/logs"
printf 'def answer():\n    return 42\n' > "${REPO_DIR}/src/example.py"
printf 'fixture pytest log\n' > "${REPO_DIR}/logs/pytest.log"

cat > "${SANDBOX_RECEIPT}" <<JSON
{
  "schema": "tau.sandbox_run_receipt.v1",
  "ok": true,
  "status": "PASS",
  "mocked": false,
  "live": true,
  "provider_live": false,
  "goal_hash": "sha256:scillm-worker-example-goal",
  "command_executed": true,
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
  "allowed_paths": ["src/**", "tests/**", "logs/**"],
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
    "default_decision": "deny",
    "requires_data_boundary": true,
    "network": {
      "default": "deny",
      "allowed_domains": []
    },
    "providers": {
      "cloud_llm": "deny",
      "local_model": "allow_with_approval"
    },
    "research": {
      "external_search": "deny",
      "manual_sanitized_receipt": "allow_with_review"
    },
    "memory": {
      "read": "allow",
      "write": "approval_required"
    },
    "github": {
      "public_mutation": "deny",
      "dry_run_projection": "allow"
    },
    "filesystem": {
      "write_allowlist": ["src/**", "tests/**", "logs/**"],
      "read_denylist": []
    }
  },
  "data_boundary": {
    "schema": "tau.data_boundary.v1",
    "classification": "public",
    "export_controlled": false,
    "itar": false,
    "technical_data": false,
    "foreign_person_access": "allowed",
    "external_provider_allowed": false,
    "external_research_allowed": false,
    "public_repo_allowed": false,
    "notes": []
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
      "log_path": "${REPO_DIR}/logs/pytest.log"
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

python3 - "${WORK_ORDER}" "${APPLY_LAUNCH_RECEIPT}" <<'PY'
import json
import subprocess
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread

work_order = Path(sys.argv[1])
out = Path(sys.argv[2])
requests = []


class Handler(BaseHTTPRequestHandler):
    def do_POST(self):
        length = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(length).decode("utf-8")
        requests.append(
            {
                "path": self.path,
                "authorization": self.headers.get("Authorization"),
                "caller_skill": self.headers.get("X-Caller-Skill"),
                "payload": json.loads(body),
            }
        )
        response = {
            "schema": "scillm.opencode_serve.run.v1",
            "run_id": "example-run",
            "session_id": "example-session",
            "status": "completed",
            "assistant_text": "fixture OpenCode serve response",
            "artifacts": ["events.jsonl"],
        }
        encoded = json.dumps(response, sort_keys=True).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def log_message(self, format, *args):
        return


server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
thread = Thread(target=server.serve_forever, daemon=True)
thread.start()
host, port = server.server_address
try:
    result = subprocess.run(
        [
            "uv",
            "run",
            "tau",
            "scillm-worker-launch",
            "--work-order",
            str(work_order),
            "--out",
            str(out),
            "--scillm-base-url",
            f"http://{host}:{port}",
            "--caller-skill",
            "tau-example",
            "--apply",
            "--auth-token",
            "example-token",
            "--request-timeout-s",
            "5",
        ],
        text=True,
        capture_output=True,
        check=False,
    )
finally:
    server.shutdown()

(out.parent / "scillm-worker-launch-apply.stdout.json").write_text(
    result.stdout,
    encoding="utf-8",
)
(out.parent / "scillm-worker-launch-apply.stderr.txt").write_text(
    result.stderr,
    encoding="utf-8",
)
if result.returncode != 0:
    raise SystemExit(result.returncode)
receipt = json.loads(out.read_text(encoding="utf-8"))
if "example-token" in json.dumps(receipt):
    raise SystemExit("auth token leaked into receipt")
if not requests:
    raise SystemExit("fake SciLLM server received no request")
PY

uv run tau scillm-worker-validate \
  --work-order "${WORK_ORDER}" \
  --result "${RESULT}" \
  --out "${RECEIPT}" >/tmp/tau-scillm-worker-validate.stdout.json

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
route = receipt.get("model_provider_route", {})
ok = (
    receipt.get("ok") is True
    and launch_receipt.get("ok") is True
    and apply_launch_receipt.get("ok") is True
)
payload = {
    "schema": "tau.scillm_worker_example_receipt.v1",
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
    "launch_url": launch_receipt.get("url"),
    "apply_launch_receipt_path": str(apply_launch_receipt_path),
    "apply_launch_receipt_schema": apply_launch_receipt.get("schema"),
    "apply_launch_receipt_status": apply_launch_receipt.get("status"),
    "apply_launch_receipt_alert_codes": apply_launch_receipt.get("alert_codes", []),
    "apply_launch_http_executed": apply_launch_receipt.get("http_executed"),
    "apply_launch_http_status": apply_launch_receipt.get("http_status"),
    "apply_launch_response_path": apply_launch_receipt.get("response_path"),
    "apply_launch_response_sha256": apply_launch_receipt.get("response_sha256"),
    "apply_launch_http_artifacts": apply_launch_receipt.get("http_artifacts", []),
    "apply_launch_run_id": apply_launch_receipt.get("run_id"),
    "model_provider_route": route,
    "proof_scope": {
        "proves": [
            "Tau built a dry-run SciLLM OpenCode-serve launch request from a bounded work order.",
            "Tau posted a bounded request to a deterministic local SciLLM-compatible server and captured the response.",
            "Tau validated a SciLLM-shaped worker result against a bounded work order.",
            "Tau checked goal hash, changed paths, required artifacts, test logs, substrate evidence, and route metadata."
        ],
        "does_not_prove": [
            "Tau called a live SciLLM service.",
            "The OpenCode worker result is truthful or sufficient for closure.",
            "OpenCode serve performed live coding work against a real SciLLM service.",
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
