"""Live Herdr provider-pane proof of concept for Tau."""

from __future__ import annotations

import json
import os
import subprocess
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from tau_coding.provider_lifecycle import (
    build_provider_session_state,
    compact_provider_session_state,
    load_provider_session_states,
)

PROVIDER_PANE_RUN_SCHEMA = "tau.provider_pane_run_receipt.v1"
PROVIDER_PANE_MANIFEST_SCHEMA = "tau.provider_pane_runtime_manifest.v1"
PROVIDER_READINESS_RUN_SCHEMA = "tau.provider_readiness_run_receipt.v1"
PROVIDER_READINESS_MANIFEST_SCHEMA = "tau.provider_readiness_runtime_manifest.v1"
PROVIDER_READINESS_SCHEMA = "tau.provider_readiness.v1"


@dataclass(frozen=True, slots=True)
class ProviderPane:
    """One provider session Tau expects Herdr to launch."""

    provider_id: str
    role: str
    command: tuple[str, ...]
    split: str | None = None


def run_provider_pane_poc(
    *,
    repo: Path,
    run_root: Path,
    label: str = "tau-provider-pane-poc",
    herdr_workstation: Path | None = None,
    herdr_bin: str = "herdr",
    session: str | None = None,
    install_integrations: bool = True,
) -> dict[str, Any]:
    """Launch real Codex and OpenCode sessions in visible Herdr panes."""

    resolved_repo = repo.expanduser().resolve()
    if not resolved_repo.exists():
        raise RuntimeError(f"repo does not exist: {resolved_repo}")
    skill_root = _resolve_herdr_workstation(herdr_workstation)
    resolved_run_root = run_root.expanduser().resolve()
    resolved_run_root.mkdir(parents=True, exist_ok=True)
    run_id = f"{_compact_stamp()}-{_slug(label)}"
    run_dir = resolved_run_root / run_id
    work_order_dir = run_dir / "work-orders"
    logs_dir = run_dir / "logs"
    for path in (work_order_dir, logs_dir):
        path.mkdir(parents=True, exist_ok=True)
    events_path = run_dir / "events.jsonl"

    providers = (
        ProviderPane(
            provider_id="codex",
            role="codex",
            command=("codex", "--cd", str(resolved_repo)),
        ),
        ProviderPane(
            provider_id="opencode",
            role="opencode",
            command=("opencode", str(resolved_repo)),
            split="right",
        ),
    )
    _write_json(run_dir / "provider-pane-spec.json", _provider_spec(run_id, label, providers))
    _append_event(events_path, "provider_spec_created", {"run_id": run_id})

    command_results: list[dict[str, Any]] = []
    for executable in ("codex", "opencode"):
        version = _run_command([executable, "--version"], cwd=resolved_repo)
        command_results.append(_command_result_dict(version))
        if version.returncode != 0:
            receipt = _blocked_run_receipt(
                run_id=run_id,
                run_dir=run_dir,
                events_path=events_path,
                errors=[f"{executable} --version failed"],
                command_results=command_results,
            )
            _write_json(run_dir / "run-receipt.json", receipt)
            return receipt

    doctor = _run_skill(
        skill_root,
        ["doctor", "--json", "--herdr-bin", herdr_bin, *_session_args(session)],
        cwd=resolved_repo,
    )
    command_results.append(_command_result_dict(doctor))
    doctor_payload = _parse_json_stdout(doctor.stdout, label="herdr-workstation doctor")
    if doctor.returncode != 0 or doctor_payload.get("ok") is not True:
        receipt = _blocked_run_receipt(
            run_id=run_id,
            run_dir=run_dir,
            events_path=events_path,
            errors=["herdr-workstation doctor failed"],
            command_results=command_results,
        )
        _write_json(run_dir / "run-receipt.json", receipt)
        return receipt

    if install_integrations:
        integration = _run_skill(
            skill_root,
            ["install-integrations", "codex", "opencode", "--json"],
            cwd=resolved_repo,
        )
        command_results.append(_command_result_dict(integration))
        if integration.returncode != 0:
            receipt = _blocked_run_receipt(
                run_id=run_id,
                run_dir=run_dir,
                events_path=events_path,
                errors=["herdr integration install failed"],
                command_results=command_results,
            )
            _write_json(run_dir / "run-receipt.json", receipt)
            return receipt

    workstation = _run_skill(
        skill_root,
        [
            "workstation",
            "create",
            "--repo",
            str(resolved_repo),
            "--label",
            label,
            "--run-root",
            str(run_dir / "herdr-workstations"),
            "--herdr-bin",
            herdr_bin,
            *_session_args(session),
            "--tab",
            "providers",
            "--tab",
            "logs",
            "--tab",
            "receipts",
            "--json",
        ],
        cwd=resolved_repo,
    )
    command_results.append(_command_result_dict(workstation))
    if workstation.returncode != 0:
        receipt = _blocked_run_receipt(
            run_id=run_id,
            run_dir=run_dir,
            events_path=events_path,
            errors=["workstation create failed"],
            command_results=command_results,
        )
        _write_json(run_dir / "run-receipt.json", receipt)
        return receipt
    workstation_manifest = _parse_json_stdout(
        workstation.stdout,
        label="herdr-workstation manifest",
    )
    workstation_manifest_path = Path(str(workstation_manifest["run_dir"])) / "workstation.json"

    pane_records: list[dict[str, Any]] = []
    for provider in providers:
        agent_name = f"{run_id}-{provider.provider_id}"
        work_order_path = work_order_dir / f"{provider.provider_id}.json"
        _write_json(work_order_path, _work_order(run_id, provider, work_order_path))
        _append_event(
            events_path,
            "work_order_written",
            {
                "run_id": run_id,
                "provider_id": provider.provider_id,
                "work_order_path": str(work_order_path),
            },
        )
        start_args = [
            "agent",
            "start",
            str(workstation_manifest_path),
            "--name",
            agent_name,
            "--role",
            provider.role,
            "--command",
            " ".join(_shell_quote(part) for part in provider.command),
            "--tab",
            "providers",
            "--work-order",
            str(work_order_path),
            "--env",
            f"TAU_PROVIDER_PANE_RUN_ID={run_id}",
            "--env",
            f"TAU_PROVIDER_PANE_PROVIDER={provider.provider_id}",
            "--herdr-bin",
            herdr_bin,
            *_session_args(session),
        ]
        if provider.split is not None:
            start_args.extend(["--split", provider.split])
        start_args.append("--json")
        start = _run_skill(skill_root, start_args, cwd=resolved_repo)
        command_results.append(_command_result_dict(start))
        if start.returncode != 0:
            receipt = _blocked_run_receipt(
                run_id=run_id,
                run_dir=run_dir,
                events_path=events_path,
                errors=[f"provider pane start failed for {provider.provider_id}"],
                command_results=command_results,
                pane_records=pane_records,
            )
            _write_json(run_dir / "run-receipt.json", receipt)
            return receipt
        start_payload = _parse_json_stdout(start.stdout, label=f"{provider.provider_id} start")
        pane_record = _pane_record(provider, work_order_path, agent_name, start_payload)
        pane_records.append(pane_record)
        _append_event(
            events_path,
            "provider_pane_started",
            {
                "run_id": run_id,
                "provider_id": provider.provider_id,
                "pane_id": pane_record.get("pane_id"),
                "terminal_id": pane_record.get("terminal_id"),
            },
        )

    for pane in pane_records:
        readiness = _settle_provider_pane(
            provider_id=str(pane["provider_id"]),
            pane_id=str(pane["pane_id"]),
            herdr_bin=herdr_bin,
            cwd=resolved_repo,
        )
        pane["readiness_actions"] = readiness["actions"]
        pane["ready_prompt_observed"] = readiness["ready_prompt_observed"]
        command_results.extend(readiness["command_results"])
        _append_event(
            events_path,
            "provider_pane_settled",
            {
                "run_id": run_id,
                "provider_id": pane["provider_id"],
                "pane_id": pane["pane_id"],
                "ready_prompt_observed": readiness["ready_prompt_observed"],
                "actions": readiness["actions"],
            },
        )

    inspect = _run_skill(
        skill_root,
        [
            "workstation",
            "inspect",
            str(workstation_manifest_path),
            "--herdr-bin",
            herdr_bin,
            *_session_args(session),
        ],
        cwd=resolved_repo,
    )
    command_results.append(_command_result_dict(inspect))
    inspect_path = run_dir / "inspect.json"
    inspect_payload = _parse_json_stdout(inspect.stdout, label="workstation inspect")
    _write_json(inspect_path, inspect_payload)

    for pane in pane_records:
        pane_id = str(pane.get("pane_id") or "")
        if pane_id:
            read = _run_command(
                [herdr_bin, "pane", "read", pane_id, "--source", "visible", "--lines", "80"],
                cwd=resolved_repo,
            )
            command_results.append(_command_result_dict(read))
            log_path = logs_dir / f"{pane['provider_id']}.visible.txt"
            log_path.write_text(read.stdout, encoding="utf-8")
            pane["visible_log"] = str(log_path)
            pane["read_returncode"] = read.returncode

    runtime_manifest = {
        "schema": PROVIDER_PANE_MANIFEST_SCHEMA,
        "run_id": run_id,
        "label": label,
        "repo": str(resolved_repo),
        "run_dir": str(run_dir),
        "events_jsonl": str(events_path),
        "provider_spec_path": str(run_dir / "provider-pane-spec.json"),
        "workstation_manifest": str(workstation_manifest_path),
        "inspect_path": str(inspect_path),
        "providers": pane_records,
    }
    _write_json(run_dir / "runtime-manifest.json", runtime_manifest)
    all_provider_prompts_observed = all(
        provider.get("ready_prompt_observed") is True for provider in pane_records
    )
    final_receipt = {
        "schema": PROVIDER_PANE_RUN_SCHEMA,
        "ok": all_provider_prompts_observed,
        "status": "PASS" if all_provider_prompts_observed else "BLOCKED",
        "mocked": False,
        "live": True,
        "proof_scope": {
            "proves": [
                "Tau found real codex and opencode executables",
                "Tau verified Herdr is reachable through herdr-workstation doctor",
                "Tau installed or refreshed Herdr Codex/OpenCode integrations",
                "Tau created a visible Herdr workstation for provider sessions",
                "Tau sequentially launched real Codex and OpenCode provider panes",
                "Tau settled known Codex launch interstitials when present",
                "Tau captured workstation inspect output, pane ids, terminal ids, "
                "work orders, event log, and visible pane text",
            ],
            "does_not_prove": [
                "Codex/OpenCode semantic task completion",
                "provider authentication beyond process/session launch",
                "remote Tailscale monitoring from another machine",
                "GitHub ticket closure workflow",
            ],
        },
        "run_id": run_id,
        "run_dir": str(run_dir),
        "runtime_manifest": str(run_dir / "runtime-manifest.json"),
        "events_jsonl": str(events_path),
        "workstation_manifest": str(workstation_manifest_path),
        "inspect_path": str(inspect_path),
        "providers": pane_records,
        "all_provider_prompts_observed": all_provider_prompts_observed,
        "command_results": command_results,
        "timestamp": _utc_stamp(),
    }
    _write_json(run_dir / "run-receipt.json", final_receipt)
    return final_receipt


def inspect_provider_pane_run(run_dir: Path) -> dict[str, Any]:
    """Return a compact summary for a provider-pane POC run directory."""

    resolved = run_dir.expanduser().resolve()
    manifest = _read_json_object(resolved / "runtime-manifest.json", label="runtime manifest")
    receipt = _read_json_object(resolved / "run-receipt.json", label="run receipt")
    events_path = Path(str(manifest["events_jsonl"]))
    events = [
        json.loads(line)
        for line in events_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    return {
        "schema": "tau.provider_pane_inspect.v1",
        "ok": receipt.get("ok") is True,
        "run_id": manifest.get("run_id"),
        "status": receipt.get("status"),
        "mocked": receipt.get("mocked"),
        "live": receipt.get("live"),
        "run_dir": str(resolved),
        "workstation_manifest": manifest.get("workstation_manifest"),
        "inspect_path": manifest.get("inspect_path"),
        "events_count": len(events),
        "providers": [
            {
                "provider_id": provider.get("provider_id"),
                "role": provider.get("role"),
                "pane_id": provider.get("pane_id"),
                "terminal_id": provider.get("terminal_id"),
                "work_order_path": provider.get("work_order_path"),
                "ready_prompt_observed": provider.get("ready_prompt_observed"),
                "readiness_actions": provider.get("readiness_actions"),
                "visible_log": provider.get("visible_log"),
                "read_returncode": provider.get("read_returncode"),
            }
            for provider in manifest.get("providers", [])
            if isinstance(provider, dict)
        ],
        "proof_scope": receipt.get("proof_scope"),
    }


def run_provider_readiness_poc(
    *,
    repo: Path,
    run_root: Path,
    label: str = "tau-provider-readiness-poc",
    herdr_workstation: Path | None = None,
    herdr_bin: str = "herdr",
    session: str | None = None,
    install_integrations: bool = True,
) -> dict[str, Any]:
    """Launch provider panes and gate PASS on structured Herdr readiness state."""

    resolved_repo = repo.expanduser().resolve()
    if not resolved_repo.exists():
        raise RuntimeError(f"repo does not exist: {resolved_repo}")
    skill_root = _resolve_herdr_workstation(herdr_workstation)
    resolved_run_root = run_root.expanduser().resolve()
    resolved_run_root.mkdir(parents=True, exist_ok=True)
    run_id = f"{_compact_stamp()}-{_slug(label)}"
    run_dir = resolved_run_root / run_id
    work_order_dir = run_dir / "work-orders"
    readiness_dir = run_dir / "readiness"
    logs_dir = run_dir / "logs"
    for path in (work_order_dir, readiness_dir, logs_dir):
        path.mkdir(parents=True, exist_ok=True)
    events_path = run_dir / "events.jsonl"

    providers = _default_provider_panes(resolved_repo)
    _write_json(run_dir / "provider-readiness-spec.json", _provider_spec(run_id, label, providers))
    _append_event(events_path, "provider_readiness_spec_created", {"run_id": run_id})

    command_results: list[dict[str, Any]] = []
    for executable in ("codex", "opencode"):
        version = _run_command([executable, "--version"], cwd=resolved_repo)
        command_results.append(_command_result_dict(version))
        if version.returncode != 0:
            receipt = _blocked_readiness_receipt(
                run_id=run_id,
                run_dir=run_dir,
                events_path=events_path,
                errors=[f"{executable} --version failed"],
                command_results=command_results,
            )
            _write_json(run_dir / "run-receipt.json", receipt)
            return receipt

    doctor = _run_skill(
        skill_root,
        ["doctor", "--json", "--herdr-bin", herdr_bin, *_session_args(session)],
        cwd=resolved_repo,
    )
    command_results.append(_command_result_dict(doctor))
    doctor_payload = _parse_json_stdout(doctor.stdout, label="herdr-workstation doctor")
    if doctor.returncode != 0 or doctor_payload.get("ok") is not True:
        receipt = _blocked_readiness_receipt(
            run_id=run_id,
            run_dir=run_dir,
            events_path=events_path,
            errors=["herdr-workstation doctor failed"],
            command_results=command_results,
        )
        _write_json(run_dir / "run-receipt.json", receipt)
        return receipt

    if install_integrations:
        integration = _run_skill(
            skill_root,
            ["install-integrations", "codex", "opencode", "--json"],
            cwd=resolved_repo,
        )
        command_results.append(_command_result_dict(integration))
        if integration.returncode != 0:
            receipt = _blocked_readiness_receipt(
                run_id=run_id,
                run_dir=run_dir,
                events_path=events_path,
                errors=["herdr integration install failed"],
                command_results=command_results,
            )
            _write_json(run_dir / "run-receipt.json", receipt)
            return receipt

    workstation = _run_skill(
        skill_root,
        [
            "workstation",
            "create",
            "--repo",
            str(resolved_repo),
            "--label",
            label,
            "--run-root",
            str(run_dir / "herdr-workstations"),
            "--herdr-bin",
            herdr_bin,
            *_session_args(session),
            "--tab",
            "providers",
            "--tab",
            "logs",
            "--tab",
            "receipts",
            "--json",
        ],
        cwd=resolved_repo,
    )
    command_results.append(_command_result_dict(workstation))
    if workstation.returncode != 0:
        receipt = _blocked_readiness_receipt(
            run_id=run_id,
            run_dir=run_dir,
            events_path=events_path,
            errors=["workstation create failed"],
            command_results=command_results,
        )
        _write_json(run_dir / "run-receipt.json", receipt)
        return receipt
    workstation_manifest = _parse_json_stdout(
        workstation.stdout,
        label="herdr-workstation manifest",
    )
    workstation_manifest_path = Path(str(workstation_manifest["run_dir"])) / "workstation.json"

    pane_records: list[dict[str, Any]] = []
    readiness_records: list[dict[str, Any]] = []
    session_state_records: list[dict[str, Any]] = []
    for provider in providers:
        agent_name = f"{run_id}-{provider.provider_id}"
        work_order_path = work_order_dir / f"{provider.provider_id}.json"
        _write_json(work_order_path, _work_order(run_id, provider, work_order_path))
        start_args = [
            "agent",
            "start",
            str(workstation_manifest_path),
            "--name",
            agent_name,
            "--role",
            provider.role,
            "--command",
            " ".join(_shell_quote(part) for part in provider.command),
            "--tab",
            "providers",
            "--work-order",
            str(work_order_path),
            "--env",
            f"TAU_PROVIDER_READINESS_RUN_ID={run_id}",
            "--env",
            f"TAU_PROVIDER_READINESS_PROVIDER={provider.provider_id}",
            "--herdr-bin",
            herdr_bin,
            *_session_args(session),
        ]
        if provider.split is not None:
            start_args.extend(["--split", provider.split])
        start_args.append("--json")
        start = _run_skill(skill_root, start_args, cwd=resolved_repo)
        command_results.append(_command_result_dict(start))
        if start.returncode != 0:
            receipt = _blocked_readiness_receipt(
                run_id=run_id,
                run_dir=run_dir,
                events_path=events_path,
                errors=[f"provider pane start failed for {provider.provider_id}"],
                command_results=command_results,
                readiness_records=readiness_records,
            )
            _write_json(run_dir / "run-receipt.json", receipt)
            return receipt
        start_payload = _parse_json_stdout(start.stdout, label=f"{provider.provider_id} start")
        pane_record = _pane_record(provider, work_order_path, agent_name, start_payload)
        pane_records.append(pane_record)
        _append_event(
            events_path,
            "provider_pane_started",
            {
                "run_id": run_id,
                "provider_id": provider.provider_id,
                "pane_id": pane_record.get("pane_id"),
                "terminal_id": pane_record.get("terminal_id"),
            },
        )

    for pane in pane_records:
        pane_id = str(pane["pane_id"])
        provider_id = str(pane["provider_id"])
        settle = _settle_provider_pane(
            provider_id=provider_id,
            pane_id=pane_id,
            herdr_bin=herdr_bin,
            cwd=resolved_repo,
        )
        command_results.extend(settle["command_results"])
        visible_log_path = logs_dir / f"{provider_id}.visible.txt"
        visible_text = _read_visible_pane_text(
            pane_id=pane_id,
            herdr_bin=herdr_bin,
            cwd=resolved_repo,
            log_path=visible_log_path,
            command_results=command_results,
        )
        readiness = _probe_provider_readiness(
            run_id=run_id,
            provider_id=provider_id,
            expected_command=_expected_provider_command(provider_id),
            pane_id=pane_id,
            terminal_id=str(pane["terminal_id"]),
            workspace_id=str(pane["workspace_id"]),
            visible_log_path=visible_log_path,
            visible_text=visible_text,
            readiness_actions=settle["actions"],
            herdr_bin=herdr_bin,
            cwd=resolved_repo,
            command_results=command_results,
        )
        readiness_path = readiness_dir / f"{provider_id}.readiness.json"
        session_state_path = readiness_dir / f"{provider_id}.session-state.json"
        readiness["evidence"]["provider_readiness_path"] = str(readiness_path)
        session_state = build_provider_session_state(readiness, visible_text=visible_text)
        session_state["evidence"]["provider_readiness_path"] = str(readiness_path)
        readiness["evidence"]["provider_session_state_path"] = str(session_state_path)
        readiness["provider_session_state"] = compact_provider_session_state(session_state)
        _write_json(readiness_path, readiness)
        _write_json(session_state_path, session_state)
        readiness_records.append(readiness)
        session_state_records.append(session_state)
        _append_event(
            events_path,
            "provider_structured_readiness_observed",
            {
                "run_id": run_id,
                "provider_id": provider_id,
                "pane_id": pane_id,
                "state": readiness["state"],
                "ready": readiness["ready"],
                "source": readiness["source"],
            },
        )
        _append_event(
            events_path,
            "provider_session_state_observed",
            {
                "run_id": run_id,
                "provider_id": provider_id,
                "pane_id": pane_id,
                "state": session_state["state"],
                "ready": session_state["ready"],
                "source": session_state["source"],
            },
        )

    inspect = _run_skill(
        skill_root,
        [
            "workstation",
            "inspect",
            str(workstation_manifest_path),
            "--herdr-bin",
            herdr_bin,
            *_session_args(session),
        ],
        cwd=resolved_repo,
    )
    command_results.append(_command_result_dict(inspect))
    inspect_path = run_dir / "inspect.json"
    inspect_payload = _parse_json_stdout(inspect.stdout, label="workstation inspect")
    _write_json(inspect_path, inspect_payload)

    runtime_manifest = {
        "schema": PROVIDER_READINESS_MANIFEST_SCHEMA,
        "run_id": run_id,
        "label": label,
        "repo": str(resolved_repo),
        "run_dir": str(run_dir),
        "events_jsonl": str(events_path),
        "provider_spec_path": str(run_dir / "provider-readiness-spec.json"),
        "workstation_manifest": str(workstation_manifest_path),
        "inspect_path": str(inspect_path),
        "readiness_records": [
            str(readiness_dir / f"{record['provider_id']}.readiness.json")
            for record in readiness_records
        ],
        "provider_session_states": [
            str(readiness_dir / f"{record['provider_id']}.session-state.json")
            for record in readiness_records
        ],
    }
    _write_json(run_dir / "runtime-manifest.json", runtime_manifest)
    all_ready = all(record.get("ready") is True for record in readiness_records)
    final_receipt = {
        "schema": PROVIDER_READINESS_RUN_SCHEMA,
        "ok": all_ready,
        "status": "PASS" if all_ready else "BLOCKED",
        "mocked": False,
        "live": True,
        "run_id": run_id,
        "run_dir": str(run_dir),
        "runtime_manifest": str(run_dir / "runtime-manifest.json"),
        "events_jsonl": str(events_path),
        "workstation_manifest": str(workstation_manifest_path),
        "inspect_path": str(inspect_path),
        "all_provider_structured_ready": all_ready,
        "readiness_records": readiness_records,
        "provider_session_states": [
            compact_provider_session_state(record) for record in session_state_records
        ],
        "command_results": command_results,
        "proof_scope": {
            "proves": [
                "Tau can allocate real Codex and OpenCode provider panes through Herdr",
                "Tau can capture structured Herdr pane/process state per provider",
                "Tau can emit tau.provider_readiness.v1 records per provider",
                "Tau can emit tau.provider_session_state.v1 lifecycle records per provider",
                "Tau can gate PASS on structured state instead of visible TUI prompt text",
                "Tau captures visible TUI text only as diagnostic evidence",
            ],
            "does_not_prove": [
                "provider-native session.ready events",
                "semantic Codex/OpenCode task completion",
                "coder -> reviewer dependency execution",
                "real repository mutation",
                "remote Tailscale monitoring",
                "GitHub ticket closure workflow",
            ],
        },
        "timestamp": _utc_stamp(),
    }
    _write_json(run_dir / "run-receipt.json", final_receipt)
    return final_receipt


def inspect_provider_readiness_run(run_dir: Path) -> dict[str, Any]:
    """Return a compact summary for a provider-readiness POC run directory."""

    resolved = run_dir.expanduser().resolve()
    manifest = _read_json_object(resolved / "runtime-manifest.json", label="runtime manifest")
    receipt = _read_json_object(resolved / "run-receipt.json", label="run receipt")
    events_path = Path(str(manifest["events_jsonl"]))
    events = [
        json.loads(line)
        for line in events_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    readiness_summaries = []
    for path_text in manifest.get("readiness_records", []):
        readiness = _read_json_object(Path(str(path_text)), label=f"readiness {path_text}")
        readiness_summaries.append(
            {
                "provider_id": readiness.get("provider_id"),
                "state": readiness.get("state"),
                "ready": readiness.get("ready"),
                "source": readiness.get("source"),
                "pane_id": readiness.get("pane_id"),
                "terminal_id": readiness.get("terminal_id"),
                "visible_prompt_observed": readiness.get("diagnostics", {}).get(
                    "visible_prompt_observed"
                ),
                "visible_prompt_is_gate": readiness.get("diagnostics", {}).get(
                    "visible_prompt_is_gate"
                ),
                "provider_readiness_path": readiness.get("evidence", {}).get(
                    "provider_readiness_path"
                ),
                "provider_session_state_path": readiness.get("evidence", {}).get(
                    "provider_session_state_path"
                ),
                "provider_session_state": readiness.get("provider_session_state"),
            }
        )
    session_states = [
        compact_provider_session_state(state)
        for state in load_provider_session_states(manifest.get("provider_session_states", []))
    ]
    return {
        "schema": "tau.provider_readiness_inspect.v1",
        "ok": receipt.get("ok") is True,
        "run_id": manifest.get("run_id"),
        "status": receipt.get("status"),
        "mocked": receipt.get("mocked"),
        "live": receipt.get("live"),
        "run_dir": str(resolved),
        "workstation_manifest": manifest.get("workstation_manifest"),
        "inspect_path": manifest.get("inspect_path"),
        "events_count": len(events),
        "all_provider_structured_ready": receipt.get("all_provider_structured_ready"),
        "readiness": readiness_summaries,
        "provider_session_states": session_states,
        "proof_scope": receipt.get("proof_scope"),
    }


def _provider_spec(
    run_id: str,
    label: str,
    providers: tuple[ProviderPane, ...],
) -> dict[str, Any]:
    return {
        "schema": "tau.provider_pane_spec.v1",
        "run_id": run_id,
        "label": label,
        "providers": [
            {
                "provider_id": provider.provider_id,
                "role": provider.role,
                "command": list(provider.command),
                "receipt_required": False,
                "stop_conditions": ["pane_started", "blocked_with_reason"],
            }
            for provider in providers
        ],
        "receipt_policy": {
            "final_receipt_required": True,
            "provider_task_receipt_required": False,
        },
    }


def _work_order(run_id: str, provider: ProviderPane, work_order_path: Path) -> dict[str, Any]:
    return {
        "schema": "tau.provider_pane_work_order.v1",
        "run_id": run_id,
        "provider_id": provider.provider_id,
        "role": provider.role,
        "summary": (
            "Provider pane launch smoke only. Do not modify files or claim task completion "
            "from this work order."
        ),
        "command": list(provider.command),
        "work_order_path": str(work_order_path),
        "owns": ["launch an interactive provider session in a visible Herdr pane"],
        "does_not_own": [
            "semantic task execution",
            "GitHub ticket closure",
            "remote Tailscale monitoring proof",
        ],
        "status_reporting": {
            "required": False,
            "reason": "This POC proves provider session launch, not provider task execution.",
        },
    }


def _pane_record(
    provider: ProviderPane,
    work_order_path: Path,
    agent_name: str,
    start_payload: dict[str, Any],
) -> dict[str, Any]:
    agents = start_payload.get("agents")
    if not isinstance(agents, dict):
        agents = {}
    wrapper_agent = agents.get(agent_name)
    if not isinstance(wrapper_agent, dict):
        wrapper_agent = {}
    last_start_result = wrapper_agent.get("last_start_result")
    if not isinstance(last_start_result, dict):
        last_start_result = {}
    parsed = last_start_result.get("parsed")
    if not isinstance(parsed, dict):
        parsed = {}
    result = parsed.get("result")
    if not isinstance(result, dict):
        result = {}
    agent = result.get("agent")
    if not isinstance(agent, dict):
        agent = {}
    return {
        "provider_id": provider.provider_id,
        "role": provider.role,
        "work_order_path": str(work_order_path),
        "pane_id": agent.get("pane_id"),
        "terminal_id": agent.get("terminal_id"),
        "workspace_id": agent.get("workspace_id"),
        "command": {
            "argv": result.get("argv"),
            "type": result.get("type"),
            "returncode": last_start_result.get("returncode"),
        },
        "start_payload": start_payload,
    }


def _settle_provider_pane(
    *,
    provider_id: str,
    pane_id: str,
    herdr_bin: str,
    cwd: Path,
) -> dict[str, Any]:
    actions: list[str] = []
    command_results: list[dict[str, Any]] = []
    ready_prompt_observed = False
    for _ in range(60):
        read = _run_command(
            [herdr_bin, "pane", "read", pane_id, "--source", "visible", "--lines", "80"],
            cwd=cwd,
        )
        command_results.append(_command_result_dict(read))
        text = read.stdout
        ready_prompt_observed = _ready_prompt_observed(provider_id, text)
        if ready_prompt_observed:
            break
        if provider_id == "codex" and "Update available" in text and "Skip" in text:
            send = _run_command([herdr_bin, "pane", "send-text", pane_id, "2\n"], cwd=cwd)
            command_results.append(_command_result_dict(send))
            actions.append("codex_update_prompt_skipped")
            time.sleep(0.5)
            continue
        if (
            provider_id == "codex"
            and "Hooks need review" in text
            and "Trust all and continue" in text
        ):
            send = _run_command([herdr_bin, "pane", "send-text", pane_id, "2\n"], cwd=cwd)
            command_results.append(_command_result_dict(send))
            actions.append("codex_herdr_hooks_trusted")
            time.sleep(0.5)
            continue
        time.sleep(0.5)
    return {
        "actions": actions,
        "ready_prompt_observed": ready_prompt_observed,
        "command_results": command_results,
    }


def _ready_prompt_observed(provider_id: str, text: str) -> bool:
    if provider_id == "codex":
        return "OpenAI Codex" in text and "\n›" in text
    if provider_id == "opencode":
        return "Ask" in text and "anything" in text
    return False


def _default_provider_panes(repo: Path) -> tuple[ProviderPane, ...]:
    return (
        ProviderPane(
            provider_id="codex",
            role="codex",
            command=("codex", "--cd", str(repo)),
        ),
        ProviderPane(
            provider_id="opencode",
            role="opencode",
            command=("opencode", str(repo)),
            split="right",
        ),
    )


def _expected_provider_command(provider_id: str) -> str:
    if provider_id == "codex":
        return "codex"
    if provider_id == "opencode":
        return "opencode"
    return provider_id


def _read_visible_pane_text(
    *,
    pane_id: str,
    herdr_bin: str,
    cwd: Path,
    log_path: Path,
    command_results: list[dict[str, Any]],
) -> str:
    read = _run_command(
        [herdr_bin, "pane", "read", pane_id, "--source", "visible", "--lines", "80"],
        cwd=cwd,
    )
    command_results.append(_command_result_dict(read))
    log_path.write_text(read.stdout, encoding="utf-8")
    return read.stdout


def _probe_provider_readiness(
    *,
    run_id: str,
    provider_id: str,
    expected_command: str,
    pane_id: str,
    terminal_id: str,
    workspace_id: str,
    visible_log_path: Path,
    visible_text: str,
    readiness_actions: list[str],
    herdr_bin: str,
    cwd: Path,
    command_results: list[dict[str, Any]],
) -> dict[str, Any]:
    samples: list[dict[str, Any]] = []
    best_sample: dict[str, Any] = {}
    for probe_attempt in range(1, _readiness_probe_attempts(provider_id) + 1):
        sample = _sample_provider_readiness(
            provider_id=provider_id,
            expected_command=expected_command,
            pane_id=pane_id,
            herdr_bin=herdr_bin,
            cwd=cwd,
        )
        sample["attempt"] = probe_attempt
        samples.append(sample)
        command_results.extend(sample["command_results"])
        best_sample = sample
        if sample["ready"]:
            break
        if probe_attempt < _readiness_probe_attempts(provider_id):
            time.sleep(1.0)
    pane = best_sample.get("pane") if isinstance(best_sample.get("pane"), dict) else {}
    foreground_process = (
        best_sample.get("foreground_process")
        if isinstance(best_sample.get("foreground_process"), dict)
        else {}
    )
    argv = foreground_process.get("argv")
    if not isinstance(argv, list):
        argv = []
    process_alive = best_sample.get("process_alive") is True
    foreground_command = str(argv[0]) if argv else str(foreground_process.get("name") or "")
    state = str(best_sample.get("state") or "unknown")
    ready = state == "ready"
    visible_prompt_observed = _ready_prompt_observed(provider_id, visible_text)
    interstitial_visible = _known_interstitial_visible(provider_id, visible_text)
    return {
        "schema": PROVIDER_READINESS_SCHEMA,
        "run_id": run_id,
        "provider_id": provider_id,
        "workspace_id": workspace_id,
        "pane_id": pane_id,
        "terminal_id": terminal_id,
        "state": state,
        "ready": ready,
        "source": "herdr_process_info",
        "observed_at": _utc_stamp(),
        "evidence": {
            "process_alive": process_alive,
            "foreground_command": foreground_command,
            "foreground_argv": argv,
            "foreground_pid": foreground_process.get("pid"),
            "foreground_cwd": foreground_process.get("cwd"),
            "pane_agent_status": pane.get("agent_status"),
            "pane_cwd": pane.get("cwd"),
            "pane_label": pane.get("label"),
            "readiness_probe_attempt_count": len(samples),
            "readiness_probe_samples": _compact_readiness_samples(samples),
            "visible_log_path": str(visible_log_path),
            "provider_event_log_path": None,
        },
        "diagnostics": {
            "visible_prompt_observed": visible_prompt_observed,
            "visible_prompt_is_gate": False,
            "interstitial_visible": interstitial_visible,
            "readiness_actions": readiness_actions,
        },
    }


def _sample_provider_readiness(
    *,
    provider_id: str,
    expected_command: str,
    pane_id: str,
    herdr_bin: str,
    cwd: Path,
) -> dict[str, Any]:
    pane_get = _run_command([herdr_bin, "pane", "get", pane_id], cwd=cwd)
    process_info = _run_command([herdr_bin, "pane", "process-info", "--pane", pane_id], cwd=cwd)
    pane_payload = _parse_optional_json_stdout(pane_get.stdout)
    process_payload = _parse_optional_json_stdout(process_info.stdout)
    pane = _nested_dict(pane_payload, "result", "pane")
    process = _nested_dict(process_payload, "result", "process_info")
    foreground_processes = process.get("foreground_processes")
    if not isinstance(foreground_processes, list):
        foreground_processes = []
    foreground_process = _matching_foreground_process(foreground_processes, expected_command)
    process_alive = process_info.returncode == 0 and bool(foreground_process)
    command_matches = _process_matches_expected_command(foreground_process, expected_command)
    if process_info.returncode != 0 or pane_get.returncode != 0:
        state = "unknown"
    elif not process_alive:
        state = "unknown"
    elif not command_matches:
        state = "blocked"
    else:
        state = "ready"
    return {
        "provider_id": provider_id,
        "state": state,
        "ready": state == "ready",
        "process_alive": process_alive,
        "command_matches": command_matches,
        "pane": pane,
        "foreground_process": foreground_process,
        "pane_get_returncode": pane_get.returncode,
        "process_info_returncode": process_info.returncode,
        "command_results": [_command_result_dict(pane_get), _command_result_dict(process_info)],
    }


def _readiness_probe_attempts(provider_id: str) -> int:
    if provider_id == "opencode":
        return 6
    return 3


def _compact_readiness_samples(samples: list[dict[str, Any]]) -> list[dict[str, Any]]:
    compact = []
    for sample in samples:
        process = sample.get("foreground_process")
        argv = process.get("argv") if isinstance(process, dict) else []
        if not isinstance(argv, list):
            argv = []
        compact.append(
            {
                "attempt": sample.get("attempt"),
                "state": sample.get("state"),
                "ready": sample.get("ready"),
                "process_alive": sample.get("process_alive"),
                "command_matches": sample.get("command_matches"),
                "foreground_command": str(argv[0]) if argv else "",
                "pane_get_returncode": sample.get("pane_get_returncode"),
                "process_info_returncode": sample.get("process_info_returncode"),
            }
        )
    return compact


def _known_interstitial_visible(provider_id: str, text: str) -> bool:
    if provider_id == "codex":
        return "Update available" in text or "Hooks need review" in text
    return False


def _matching_foreground_process(
    foreground_processes: list[Any],
    expected_command: str,
) -> dict[str, Any]:
    fallback: dict[str, Any] = {}
    argv_match: dict[str, Any] = {}
    for item in foreground_processes:
        if not isinstance(item, dict):
            continue
        if not fallback:
            fallback = item
        if Path(str(item.get("name") or "")).name == expected_command:
            return item
        if not argv_match and _process_matches_expected_command(item, expected_command):
            argv_match = item
    return argv_match or fallback


def _process_matches_expected_command(process: dict[str, Any], expected_command: str) -> bool:
    name = Path(str(process.get("name") or "")).name
    if name == expected_command:
        return True
    argv = process.get("argv")
    if not isinstance(argv, list):
        return False
    return any(Path(str(part)).name == expected_command for part in argv)


def _nested_dict(payload: dict[str, Any], *keys: str) -> dict[str, Any]:
    current: Any = payload
    for key in keys:
        if not isinstance(current, dict):
            return {}
        current = current.get(key)
    return current if isinstance(current, dict) else {}


def _parse_optional_json_stdout(stdout: str) -> dict[str, Any]:
    try:
        payload = json.loads(stdout.strip())
    except json.JSONDecodeError:
        return {}
    return payload if isinstance(payload, dict) else {}


def _resolve_herdr_workstation(path: Path | None) -> Path:
    if path is not None:
        resolved = path.expanduser().resolve()
    else:
        resolved = Path("/home/graham/workspace/experiments/agent-skills/skills/herdr-workstation")
    run_sh = resolved / "run.sh"
    if not run_sh.exists():
        raise RuntimeError(f"herdr-workstation run.sh not found: {run_sh}")
    return resolved


def _run_skill(skill_root: Path, args: list[str], *, cwd: Path) -> subprocess.CompletedProcess[str]:
    return _run_command([str(skill_root / "run.sh"), *args], cwd=cwd)


def _run_command(args: list[str], *, cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        args,
        cwd=str(cwd),
        text=True,
        capture_output=True,
        env=os.environ.copy(),
    )


def _blocked_run_receipt(
    *,
    run_id: str,
    run_dir: Path,
    events_path: Path,
    errors: list[str],
    command_results: list[dict[str, Any]],
    pane_records: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    return {
        "schema": PROVIDER_PANE_RUN_SCHEMA,
        "ok": False,
        "status": "BLOCKED",
        "mocked": False,
        "live": True,
        "run_id": run_id,
        "run_dir": str(run_dir),
        "events_jsonl": str(events_path),
        "providers": pane_records or [],
        "command_results": command_results,
        "errors": errors,
        "timestamp": _utc_stamp(),
    }


def _blocked_readiness_receipt(
    *,
    run_id: str,
    run_dir: Path,
    events_path: Path,
    errors: list[str],
    command_results: list[dict[str, Any]],
    readiness_records: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    return {
        "schema": PROVIDER_READINESS_RUN_SCHEMA,
        "ok": False,
        "status": "BLOCKED",
        "mocked": False,
        "live": True,
        "run_id": run_id,
        "run_dir": str(run_dir),
        "events_jsonl": str(events_path),
        "all_provider_structured_ready": False,
        "readiness_records": readiness_records or [],
        "command_results": command_results,
        "errors": errors,
        "timestamp": _utc_stamp(),
    }


def _command_result_dict(result: subprocess.CompletedProcess[str]) -> dict[str, Any]:
    return {
        "argv": result.args,
        "returncode": result.returncode,
        "stdout": result.stdout,
        "stderr": result.stderr,
    }


def _parse_json_stdout(stdout: str, *, label: str) -> dict[str, Any]:
    stripped = stdout.strip()
    try:
        payload = json.loads(stripped)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"{label} did not emit JSON: {exc}: {stripped[:500]}") from exc
    if not isinstance(payload, dict):
        raise RuntimeError(f"{label} JSON root must be an object")
    return payload


def _read_json_object(path: Path, *, label: str) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"{label} is unreadable: {path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise RuntimeError(f"{label} root must be a JSON object: {path}")
    return payload


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _append_event(path: Path, kind: str, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    event = {
        "schema": "tau.provider_pane_event.v1",
        "kind": kind,
        "timestamp": _utc_stamp(),
        **payload,
    }
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(event, sort_keys=True) + "\n")


def _session_args(session: str | None) -> list[str]:
    return [] if session is None else ["--session", session]


def _compact_stamp() -> str:
    return datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")


def _utc_stamp() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _slug(value: str) -> str:
    chars = [char.lower() if char.isalnum() else "-" for char in value.strip()]
    return "-".join(part for part in "".join(chars).split("-") if part)[:80] or "provider-pane"


def _shell_quote(value: str) -> str:
    return "'" + value.replace("'", "'\"'\"'") + "'"
