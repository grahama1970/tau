#!/usr/bin/env bash
set -euo pipefail

interval="${TAU_ORCHESTRATOR_INTERVAL_SECONDS:-300}"
start="${TAU_ORCHESTRATOR_START:-}"
agents_root="${TAU_AGENTS_ROOT:-/opt/tau/experiments/goal-locked-subagents/agent-command-specs}"
command_spec_root="${TAU_COMMAND_SPEC_ROOT:-/workspace/experiments/goal-locked-subagents/agent-command-specs}"
receipt_root="${TAU_RECEIPT_DIR:-/data/receipts}"
max_steps="${TAU_ORCHESTRATOR_MAX_STEPS:-1}"
active_goal_hash="${TAU_ACTIVE_GOAL_HASH:-}"
ticket_source="${TAU_GOAL_GUARDIAN_TICKET_SOURCE:-}"

if [[ -z "${start}" ]]; then
  echo "TAU_ORCHESTRATOR_START is required for tau-cron." >&2
  echo "Set it to a mounted tau.agent_handoff.v1 JSON file, for example /data/start-handoff.json." >&2
  exit 64
fi

mkdir -p "${receipt_root}"

while true; do
  run_id="$(date -u +%Y%m%dT%H%M%SZ)"
  receipt_dir="${receipt_root}/${run_id}"
  mkdir -p "${receipt_dir}"

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

  echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] ${cmd[*]}"
  if ! "${cmd[@]}"; then
    echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] Tau bounded loop exited non-zero; next tick remains scheduled." >&2
  fi

  sleep "${interval}"
done
