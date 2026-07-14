#!/usr/bin/env python3
"""Run a live tmux runtime smoke with an induced ambiguous submit result."""

from __future__ import annotations

import argparse
import json
import shlex
import shutil
import subprocess
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from tau_coding.dag_runtime.model import FrozenJson, canonical_sha256
from tau_coding.runtime_backends import (
    TmuxRuntimeBackend,
    tmux_cleanup_authorization,
    tmux_runtime_scope_request,
    tmux_runtime_spawn_request,
    tmux_runtime_work_order,
)


class _AmbiguousPasteRunner:
    """Execute one real paste, then hide its acknowledgement from the adapter."""

    def __init__(self) -> None:
        self.paste_attempt_count = 0
        self.induced_ambiguity_count = 0

    def __call__(self, argv: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        completed = subprocess.run(argv, **kwargs)
        if any("paste-buffer" in argument for argument in argv):
            self.paste_attempt_count += 1
            if self.induced_ambiguity_count == 0 and completed.returncode == 0:
                self.induced_ambiguity_count += 1
                timeout = kwargs.get("timeout")
                raise subprocess.TimeoutExpired(
                    cmd=argv,
                    timeout=float(timeout) if isinstance(timeout, int | float) else 0.0,
                    output=completed.stdout,
                    stderr=completed.stderr,
                )
        return completed


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out-dir", type=Path, required=True)
    parser.add_argument("--tmux-bin", default="tmux")
    parser.add_argument("--timeout-seconds", type=float, default=10.0)
    args = parser.parse_args()
    receipt = run_smoke(
        out_dir=args.out_dir,
        tmux_bin=args.tmux_bin,
        timeout_seconds=args.timeout_seconds,
    )
    print(json.dumps(receipt, indent=2, sort_keys=True))
    return 0 if receipt["status"] == "PASS" else 1


def run_smoke(*, out_dir: Path, tmux_bin: str, timeout_seconds: float) -> dict[str, Any]:
    resolved_out = out_dir.expanduser().resolve()
    resolved_out.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    run_id = f"tau-tmux-runtime-smoke-{stamp}"
    server_name = f"tau-smoke-{stamp.lower()}"
    owner = "tau-runtime-smoke"
    resolved_tmux = shutil.which(tmux_bin)
    tmux_surface = _classify_tmux_surface(tmux_bin)
    runner = _AmbiguousPasteRunner()
    backend = TmuxRuntimeBackend(
        server_name=server_name,
        tmux_bin=tmux_bin,
        command_runner=runner,
        poll_interval_seconds=0.05,
        command_timeout_seconds=timeout_seconds,
    )
    scope_records: list[dict[str, Any]] = []
    lease = None
    submit = None
    submit_replay = None
    capture: dict[str, Any] | None = None
    observation = None
    cleanup: dict[str, Any] | None = None
    wrong_server: dict[str, Any] | None = None
    server_cleanup: dict[str, Any] | None = None
    unauthorized_cleanup_blocked = False
    side_effect_path = resolved_out / "submit-side-effect.txt"
    side_effect_path.unlink(missing_ok=True)
    marker = f"TAU_TMUX_RUNTIME_SMOKE_{stamp}"
    work_order_text = (
        f"printf x >> {shlex.quote(str(side_effect_path))}; "
        f"printf '{marker}\\n'"
    )
    errors: list[str] = []
    try:
        if tmux_surface != "real":
            raise RuntimeError("tmux_binary_provenance_unverified")
        for suffix in ("primary", "collision"):
            scope = backend.ensure_scope(
                tmux_runtime_scope_request(
                    run_id=f"{run_id}-{suffix}",
                    owner=owner,
                    cwd=resolved_out,
                    label="tau-tmux-runtime-smoke",
                )
            ).to_value()
            scope_records.append(scope)
        if scope_records[0]["session_id"] == scope_records[1]["session_id"]:
            raise RuntimeError("duplicate labels resolved to the same tmux session id")
        work_order_sha256 = canonical_sha256({"text": work_order_text})
        lease = backend.spawn(
            tmux_runtime_spawn_request(
                run_id=f"{run_id}-primary",
                plan_revision=canonical_sha256({"plan": run_id}),
                dag_id="tau-tmux-runtime-smoke",
                node_id="shell-worker",
                attempt_id="attempt-1",
                attempt_number=1,
                execution_token=canonical_sha256({"execution": run_id}),
                scope_id=str(scope_records[0]["scope_id"]),
                command=("bash", "--noprofile", "--norc"),
                cwd=resolved_out,
                work_order_sha256=work_order_sha256,
                goal_hash=canonical_sha256({"goal": "exercise tmux adapter"}),
                owner=owner,
                label="shell-worker",
                lease_seconds=max(timeout_seconds + 60.0, 120.0),
            )
        )
        _write_json(resolved_out / "runtime-endpoint-lease.json", lease.to_payload())
        wrong_server = _probe_wrong_server(
            tmux_bin=tmux_bin,
            server_name=f"{server_name}-wrong",
            pane_id=lease.endpoint_id,
            timeout_seconds=timeout_seconds,
        )
        if wrong_server["returncode"] == 0 or wrong_server["timed_out"] is True:
            raise RuntimeError("wrong tmux server endpoint lookup was not conclusively blocked")
        bad_authorization = tmux_cleanup_authorization(lease).to_value()
        bad_authorization["owner"] = "not-the-owner"
        try:
            backend.terminate(lease, FrozenJson.from_value(bad_authorization))
        except RuntimeError as exc:
            unauthorized_cleanup_blocked = "cleanup_unauthorized" in str(exc)
        if not unauthorized_cleanup_blocked:
            raise RuntimeError("unowned tmux endpoint cleanup was not blocked")
        backend.observe(lease)
        work_order = tmux_runtime_work_order(
            work_order_sha256=work_order_sha256,
            text=work_order_text,
        )
        submit = backend.submit(lease, work_order)
        submit_replay = backend.submit(lease, work_order)
        deadline = time.monotonic() + timeout_seconds
        while time.monotonic() < deadline:
            capture = backend.capture(lease, 80).to_value()
            if side_effect_path.exists() and marker in str(capture.get("text") or ""):
                break
            time.sleep(0.1)
        observation = backend.observe(lease)
        side_effect = side_effect_path.read_text() if side_effect_path.exists() else ""
        if side_effect != "x":
            raise RuntimeError(f"work order side effect count was {len(side_effect)}, expected 1")
        if capture is None or marker not in str(capture.get("text") or ""):
            raise RuntimeError("submitted marker was not visible before timeout")
        if submit != submit_replay or runner.paste_attempt_count != 1:
            raise RuntimeError("uncertain submit was retried")
        owned = backend.list_owned(f"{run_id}-primary")
        if owned != [lease]:
            raise RuntimeError("owned tmux inventory did not return the exact endpoint lease")
    except Exception as exc:
        errors.append(f"{type(exc).__name__}: {exc}")
    finally:
        if lease is not None:
            try:
                cleanup = backend.terminate(
                    lease, tmux_cleanup_authorization(lease)
                ).to_value()
            except Exception as exc:
                errors.append(f"endpoint_cleanup_failed: {type(exc).__name__}: {exc}")
        if tmux_surface == "real" and scope_records:
            server_cleanup = _cleanup_server(
                tmux_bin=tmux_bin,
                server_name=server_name,
                timeout_seconds=timeout_seconds,
                expected_incarnation=scope_records[0],
            )
            if not server_cleanup["post_verified_absent"]:
                errors.append("tmux_server_cleanup_not_verified")

    side_effect = side_effect_path.read_text() if side_effect_path.exists() else ""
    passed = all(
        (
            not errors,
            tmux_surface == "real",
            lease is not None,
            submit is not None and submit.delivery_status == "INDETERMINATE",
            submit_replay == submit,
            runner.paste_attempt_count == 1,
            runner.induced_ambiguity_count == 1,
            side_effect == "x",
            capture is not None and marker in str(capture.get("text") or ""),
            observation is not None and observation.liveness == "ALIVE",
            wrong_server is not None and wrong_server["blocked"] is True,
            unauthorized_cleanup_blocked,
            cleanup is not None and cleanup.get("post_verified_absent") is True,
            server_cleanup is not None and server_cleanup["post_verified_absent"] is True,
        )
    )
    receipt = {
        "schema": "tau.tmux_runtime_smoke_receipt.v1",
        "ok": passed,
        "status": "PASS" if passed else "BLOCKED",
        "mocked": tmux_surface != "real",
        "live": tmux_surface == "real",
        "provider_live": False,
        "tmux_surface": tmux_surface,
        "tmux_bin": resolved_tmux,
        "tmux_server": server_name,
        "run_id": run_id,
        "duplicate_label_control": {
            "session_ids": [scope["session_id"] for scope in scope_records],
            "distinct_exact_ids": len({scope["session_id"] for scope in scope_records})
            == len(scope_records),
        },
        "wrong_server_control": wrong_server,
        "unowned_cleanup_blocked": unauthorized_cleanup_blocked,
        "endpoint_lease": lease.to_payload() if lease is not None else None,
        "submit_receipt": submit.to_payload() if submit is not None else None,
        "submit_replay_same_receipt": submit_replay == submit and submit is not None,
        "paste_attempt_count": runner.paste_attempt_count,
        "induced_ambiguity_count": runner.induced_ambiguity_count,
        "side_effect_count": len(side_effect),
        "capture": capture,
        "runtime_event": observation.to_payload() if observation is not None else None,
        "endpoint_cleanup": cleanup,
        "server_cleanup": server_cleanup,
        "errors": errors,
        "proof_scope": {
            "proves": [
                "Tau used a real tmux binary with an explicit isolated server name.",
                "Duplicate labels produced distinct recorded session IDs.",
                "Tau exercised one exact pane through spawn, submit, capture, "
                "observation, inventory, and termination.",
                "A real paste with a hidden acknowledgement was not retried; "
                "its side effect occurred exactly once.",
                "Wrong-server lookup and unowned endpoint cleanup were blocked.",
                "The endpoint and dedicated tmux server were post-verified absent.",
            ],
            "does_not_prove": [
                "Pane text or process liveness completed a Tau DAG node.",
                "Provider or model semantic quality.",
                "Crash-safe restart reconciliation.",
                "Secure sandbox isolation or production readiness.",
            ],
        },
    }
    _write_json(resolved_out / "tmux-runtime-smoke-receipt.json", receipt)
    return receipt


def _probe_wrong_server(
    *, tmux_bin: str, server_name: str, pane_id: str, timeout_seconds: float
) -> dict[str, Any]:
    argv = [
        tmux_bin,
        "-L",
        server_name,
        "-N",
        "display-message",
        "-p",
        "-t",
        pane_id,
        "#{pane_id}",
    ]
    try:
        completed = subprocess.run(
            argv, text=True, capture_output=True, check=False, timeout=timeout_seconds
        )
        return {
            "returncode": completed.returncode,
            "stdout": completed.stdout,
            "stderr": completed.stderr,
            "timed_out": False,
            "blocked": completed.returncode != 0,
        }
    except subprocess.TimeoutExpired as exc:
        return {
            "returncode": 124,
            "stdout": str(exc.stdout or ""),
            "stderr": str(exc.stderr or ""),
            "timed_out": True,
            "blocked": False,
        }


def _cleanup_server(
    *,
    tmux_bin: str,
    server_name: str,
    timeout_seconds: float,
    expected_incarnation: dict[str, Any],
) -> dict[str, Any]:
    incarnation_argv = [
        tmux_bin,
        "-L",
        server_name,
        "-N",
        "list-sessions",
        "-F",
        "#{socket_path}\t#{pid}\t#{start_time}\t#{version}",
    ]
    precheck = _cleanup_command(incarnation_argv, timeout_seconds)
    incarnation_matches = _cleanup_incarnation_matches(
        precheck, expected_incarnation
    )
    if not incarnation_matches:
        return {
            "kill_returncode": None,
            "precheck_returncode": precheck.returncode,
            "precheck_stdout": precheck.stdout,
            "precheck_stderr": precheck.stderr,
            "verify_returncode": None,
            "verify_stdout": "",
            "verify_stderr": "",
            "post_verified_absent": False,
            "errors": ["tmux_server_incarnation_mismatch_or_unavailable"],
        }
    kill = _cleanup_command(
        [tmux_bin, "-L", server_name, "-N", "kill-server"], timeout_seconds
    )
    verify_argv = [tmux_bin, "-L", server_name, "-N", "list-sessions"]
    verify = _cleanup_command(verify_argv, timeout_seconds)
    if _tmux_server_exit_in_progress(verify):
        time.sleep(0.05)
        verify = _cleanup_command(verify_argv, timeout_seconds)
    return {
        "kill_returncode": kill.returncode,
        "verify_returncode": verify.returncode,
        "verify_stdout": verify.stdout,
        "verify_stderr": verify.stderr,
        "post_verified_absent": _tmux_server_conclusively_absent(verify),
    }


def _cleanup_incarnation_matches(
    completed: subprocess.CompletedProcess[str], expected: dict[str, Any]
) -> bool:
    if completed.returncode != 0:
        return False
    records = {tuple(line.split("\t")) for line in completed.stdout.splitlines() if line}
    wanted = (
        str(expected.get("socket_path")),
        str(expected.get("server_pid")),
        str(expected.get("server_start_time")),
        str(expected.get("server_version")),
    )
    return records == {wanted}


def _cleanup_command(
    argv: list[str], timeout_seconds: float
) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            argv,
            text=True,
            capture_output=True,
            check=False,
            timeout=timeout_seconds,
        )
    except subprocess.TimeoutExpired as exc:
        return subprocess.CompletedProcess(
            argv,
            124,
            str(exc.stdout or ""),
            str(exc.stderr or "tmux cleanup command timed out"),
        )


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")


def _classify_tmux_surface(tmux_bin: str) -> str:
    discovered = shutil.which("tmux")
    if discovered is None:
        return "fixture"
    try:
        requested = (
            shutil.which(tmux_bin)
            if Path(tmux_bin).name == tmux_bin
            else str(Path(tmux_bin).expanduser().resolve())
        )
        if requested is None:
            return "fixture"
        return (
            "real"
            if Path(requested).resolve() == Path(discovered).resolve()
            else "fixture"
        )
    except OSError:
        return "fixture"


def _tmux_server_conclusively_absent(
    completed: subprocess.CompletedProcess[str],
) -> bool:
    if completed.returncode == 0:
        return False
    diagnostic = f"{completed.stdout}\n{completed.stderr}".lower()
    return "no server running" in diagnostic or (
        "error connecting" in diagnostic and "no such file or directory" in diagnostic
    )


def _tmux_server_exit_in_progress(
    completed: subprocess.CompletedProcess[str],
) -> bool:
    if completed.returncode == 0:
        return False
    diagnostic = f"{completed.stdout}\n{completed.stderr}".lower()
    return "server exited unexpectedly" in diagnostic


if __name__ == "__main__":
    raise SystemExit(main())
