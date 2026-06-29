#!/usr/bin/env bash
set -euo pipefail

interval="${TAU_ORCHESTRATOR_INTERVAL_SECONDS:-300}"
mode="${TAU_CRON_MODE:-handoff}"
start="${TAU_ORCHESTRATOR_START:-}"
agents_root="${TAU_AGENTS_ROOT:-/opt/tau/experiments/goal-locked-subagents/agent-command-specs}"
command_spec_root="${TAU_COMMAND_SPEC_ROOT:-/workspace/experiments/goal-locked-subagents/agent-command-specs}"
receipt_root="${TAU_RECEIPT_DIR:-/data/receipts}"
max_steps="${TAU_ORCHESTRATOR_MAX_STEPS:-1}"
active_goal_hash="${TAU_ACTIVE_GOAL_HASH:-}"
ticket_source="${TAU_GOAL_GUARDIAN_TICKET_SOURCE:-}"
run_once="${TAU_ORCHESTRATOR_ONCE:-0}"
self_fix_repo="${TAU_SELF_FIX_REPO:-}"
self_fix_issue_limit="${TAU_SELF_FIX_ISSUE_LIMIT:-30}"
self_fix_repair="${TAU_SELF_FIX_REPAIR:-0}"
self_fix_apply_github="${TAU_SELF_FIX_APPLY_GITHUB:-0}"
self_fix_memory_base_url="${TAU_MEMORY_BASE_URL:-http://127.0.0.1:8601}"
self_fix_scillm_base_url="${TAU_SCILLM_BASE_URL:-http://127.0.0.1:4001}"
self_fix_model="${TAU_SELF_FIX_MODEL:-gpt-5.5}"
self_fix_repo_root="${TAU_SELF_FIX_REPO_ROOT:-}"

write_preflight_failure() {
  local reason="$1"
  local detail="$2"
  local receipt_path
  mkdir -p "${receipt_root}"
  receipt_path="${receipt_root}/tau-cron-preflight-$(date -u +%Y%m%dT%H%M%SZ).json"
  python - "${receipt_path}" "${reason}" "${detail}" "${start}" "${receipt_root}" <<'PY'
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

receipt_path, reason, detail, start, receipt_root = sys.argv[1:]
payload = {
    "schema": "tau.cron_preflight_receipt.v1",
    "ok": False,
    "status": "BLOCKED",
    "reason": reason,
    "detail": detail,
    "start": start,
    "receipt_root": receipt_root,
    "mocked": False,
    "live": True,
    "command_executed": False,
    "captured_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
}
Path(receipt_path).write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
print(receipt_path)
PY
}

if [[ "${mode}" != "handoff" && "${mode}" != "self-fix" ]]; then
  echo "TAU_CRON_MODE must be handoff or self-fix; got ${mode}" >&2
  write_preflight_failure "invalid_mode" "TAU_CRON_MODE must be handoff or self-fix."
  exit 64
fi

if [[ "${mode}" == "handoff" && -z "${start}" ]]; then
  echo "TAU_ORCHESTRATOR_START is required for tau-cron." >&2
  echo "Set it to a mounted tau.agent_handoff.v1 JSON file, for example /data/start-handoff.json." >&2
  write_preflight_failure "missing_start" "TAU_ORCHESTRATOR_START is required for tau-cron."
  exit 64
fi

if [[ "${mode}" == "self-fix" && -z "${self_fix_repo}" ]]; then
  echo "TAU_SELF_FIX_REPO is required when TAU_CRON_MODE=self-fix." >&2
  write_preflight_failure "missing_self_fix_repo" "TAU_SELF_FIX_REPO is required when TAU_CRON_MODE=self-fix."
  exit 64
fi

mkdir -p "${receipt_root}"

if [[ "${mode}" == "handoff" && ! -f "${start}" ]]; then
  echo "TAU_ORCHESTRATOR_START does not point to a readable file: ${start}" >&2
  write_preflight_failure "start_not_file" "TAU_ORCHESTRATOR_START must point to a mounted tau.agent_handoff.v1 JSON file."
  exit 66
fi

if [[ "${mode}" == "handoff" && ! -r "${start}" ]]; then
  echo "TAU_ORCHESTRATOR_START is not readable: ${start}" >&2
  write_preflight_failure "start_not_readable" "TAU_ORCHESTRATOR_START is not readable by tau-cron."
  exit 66
fi

while true; do
  run_id="$(date -u +%Y%m%dT%H%M%SZ)"
  receipt_dir="${receipt_root}/${run_id}"
  mkdir -p "${receipt_dir}"

  if [[ "${mode}" == "self-fix" ]]; then
    cmd=(
      tau self-fix poll
      --repo "${self_fix_repo}"
      --receipt-dir "${receipt_dir}"
      --dispatch
      --issue-limit "${self_fix_issue_limit}"
      --memory-base-url "${self_fix_memory_base_url}"
      --scillm-base-url "${self_fix_scillm_base_url}"
      --model "${self_fix_model}"
      --max-steps "${max_steps}"
    )
    if [[ -n "${active_goal_hash}" ]]; then
      cmd+=(--active-goal-hash "${active_goal_hash}")
    fi
    if [[ "${self_fix_repair}" == "1" || "${self_fix_repair}" == "true" || "${self_fix_repair}" == "TRUE" ]]; then
      cmd+=(--repair)
    fi
    if [[ "${self_fix_apply_github}" == "1" || "${self_fix_apply_github}" == "true" || "${self_fix_apply_github}" == "TRUE" ]]; then
      cmd+=(--apply-github)
    fi
    if [[ -n "${self_fix_repo_root}" ]]; then
      cmd+=(--repo-root "${self_fix_repo_root}")
    fi
  else
    cmd=(
      tau handoff-command-loop
      --start "${start}"
      --receipt-dir "${receipt_dir}"
      --agents-root "${agents_root}"
      --command-spec-root "${command_spec_root}"
      --max-steps "${max_steps}"
    )

    if [[ -n "${active_goal_hash}" ]]; then
      cmd+=(--active-goal-hash "${active_goal_hash}")
    fi
    if [[ -n "${ticket_source}" ]]; then
      cmd+=(--goal-guardian-ticket-source "${ticket_source}")
    fi
  fi

  echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] ${cmd[*]}"
  if ! "${cmd[@]}"; then
    echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] Tau bounded loop exited non-zero; next tick remains scheduled." >&2
  fi

  if [[ "${run_once}" == "1" || "${run_once}" == "true" || "${run_once}" == "TRUE" ]]; then
    echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] TAU_ORCHESTRATOR_ONCE=${run_once}; exiting after one bounded tick."
    exit 0
  fi

  sleep "${interval}"
done
