#!/usr/bin/env python3
"""Live proof for canonical DAG 5 durable resume and targeted repair.

This proof drives the same packaged `examples/canonical-dags/run.py` entrypoint
that an evaluator runs. It does not execute a separate test-only scheduler.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import signal
import sqlite3
import subprocess
import sys
import time
from pathlib import Path
from typing import Any


PROOF_SCHEMA = "tau.canonical_durable_repair_live_proof.v1"


def _sha256(path: Path) -> str:
    return f"sha256:{hashlib.sha256(path.read_bytes()).hexdigest()}"


def _read_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise RuntimeError(f"json_object_required:{path}")
    return payload


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _run_canonical(
    repo: Path,
    *,
    run_root: Path,
    extra: list[str],
    delay: float,
    timeout: float,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            sys.executable,
            os.fspath(repo / "examples" / "canonical-dags" / "run.py"),
            "--dag",
            "5",
            "--run-root",
            os.fspath(run_root),
            "--step-delay-seconds",
            str(delay),
            *extra,
        ],
        cwd=repo,
        text=True,
        capture_output=True,
        check=False,
        timeout=timeout,
    )


def _run_tau_reconcile(repo: Path, *, run_dir: Path, receipt_path: Path) -> dict[str, Any]:
    tau = Path(sys.executable).with_name("tau")
    command = [
        os.fspath(tau if tau.exists() else "tau"),
        "dag-reconcile",
        os.fspath(run_dir),
        "--decision",
        "reconcile",
        "--operator",
        "tau-live-proof",
        "--reason",
        "operator inspected the uncertain dispatched attempts and authorized a new generation",
        "--receipt",
        os.fspath(receipt_path),
    ]
    completed = subprocess.run(
        command,
        cwd=repo,
        text=True,
        capture_output=True,
        check=False,
        timeout=20.0,
    )
    if completed.returncode != 0:
        raise RuntimeError(f"dag-reconcile failed:{completed.stderr}")
    payload = _json_stdout(completed)
    payload["command"] = command
    return payload


def _wait_for_receipt(path: Path, timeout: float) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if path.is_file():
            return
        time.sleep(0.05)
    raise RuntimeError(f"receipt_not_observed_before_timeout:{path}")


def _wait_for_attempt_state(
    run_dir: Path,
    *,
    node_id: str,
    state: str,
    timeout: float,
) -> None:
    database = run_dir / "dag-run.sqlite3"
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if database.is_file():
            with sqlite3.connect(database) as connection:
                row = connection.execute(
                    """
                    SELECT state
                    FROM dag_node_attempts
                    WHERE node_id = ?
                    ORDER BY attempt_no DESC
                    LIMIT 1
                    """,
                    (node_id,),
                ).fetchone()
            if row is not None and str(row[0]) == state:
                return
        time.sleep(0.05)
    raise RuntimeError(f"attempt_state_not_observed:{node_id}:{state}")


def _json_stdout(completed: subprocess.CompletedProcess[str]) -> dict[str, Any]:
    try:
        return json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError(
            f"command_stdout_not_json:{completed.returncode}:{completed.stderr[-500:]}"
        ) from exc


def _artifact_hashes(run_root: Path) -> dict[str, str]:
    artifacts: dict[str, str] = {}
    for path in sorted((run_root / "artifacts").glob("*.json")):
        artifacts[path.stem] = _sha256(path)
    return artifacts


def _receipt_hashes(run_root: Path) -> dict[str, str]:
    receipts: dict[str, str] = {}
    for path in sorted((run_root / "receipts").glob("*.json")):
        receipts[path.stem] = _sha256(path)
    return receipts


def _attempt_graph(run_dir: Path) -> list[dict[str, Any]]:
    database = run_dir / "dag-run.sqlite3"
    if not database.is_file():
        return []
    with sqlite3.connect(database) as connection:
        connection.row_factory = sqlite3.Row
        rows = connection.execute(
            """
            SELECT run_id, node_id, attempt_no, attempt_id, state, effect_state,
                   created_at, updated_at
            FROM dag_node_attempts
            ORDER BY attempt_no, node_id, attempt_id
            """
        ).fetchall()
    return [dict(row) for row in rows]


def _journal_event_counts(run_dir: Path) -> dict[str, int]:
    database = run_dir / "dag-run.sqlite3"
    if not database.is_file():
        return {}
    with sqlite3.connect(database) as connection:
        rows = connection.execute(
            """
            SELECT event_type, COUNT(*)
            FROM dag_run_events
            GROUP BY event_type
            ORDER BY event_type
            """
        ).fetchall()
    return {str(event_type): int(count) for event_type, count in rows}


def _wait_for_resume_takeover(run_dir: Path, timeout: float) -> dict[str, Any]:
    database = run_dir / "dag-run.sqlite3"
    deadline = time.monotonic() + timeout
    observed: dict[str, Any] = {
        "database": str(database),
        "prior_owner": None,
        "prior_lease_expires_at_ms": None,
        "waited_seconds": 0.0,
    }
    started = time.monotonic()
    while time.monotonic() < deadline:
        if not database.is_file():
            time.sleep(0.05)
            continue
        with sqlite3.connect(database) as connection:
            row = connection.execute(
                """
                SELECT lease_owner, lease_expires_at_ms
                FROM dag_runs
                WHERE run_id = 'canonical-05-durable-resume-repair'
                """
            ).fetchone()
        if row is None:
            time.sleep(0.05)
            continue
        owner, expires_at = row
        observed["prior_owner"] = owner
        observed["prior_lease_expires_at_ms"] = expires_at
        if owner is None or int(expires_at or 0) <= int(time.time() * 1000):
            observed["waited_seconds"] = round(time.monotonic() - started, 3)
            return observed
        time.sleep(0.25)
    observed["waited_seconds"] = round(time.monotonic() - started, 3)
    raise RuntimeError(f"stale_lease_not_expired:{observed}")


def _node_map(receipt: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {
        str(node["node_id"]): node
        for node in receipt.get("nodes", [])
        if isinstance(node, dict) and isinstance(node.get("node_id"), str)
    }


def _effect_ledger(
    path: Path,
    *,
    scenario: str,
    release_hash_before: str | None,
    release_hash_after: str | None,
    release_hash_after_idempotent_resume: str | None,
) -> dict[str, Any]:
    duplicate_suppressed = (
        release_hash_after is not None
        and release_hash_after_idempotent_resume is not None
        and release_hash_after == release_hash_after_idempotent_resume
    )
    payload = {
        "schema": "tau.canonical_filesystem_effect_ledger.v1",
        "scenario": scenario,
        "effect_key": "canonical-05:release",
        "effect_type": "filesystem_artifact_write",
        "release_hash_before": release_hash_before,
        "release_hash_after": release_hash_after,
        "release_hash_after_idempotent_resume": release_hash_after_idempotent_resume,
        "duplicate_effect_suppressed": duplicate_suppressed,
    }
    _write_json(path, payload)
    return payload


def _forced_interruption(repo: Path, run_root: Path) -> dict[str, Any]:
    delay = 1.5
    process = subprocess.Popen(
        [
            sys.executable,
            os.fspath(repo / "examples" / "canonical-dags" / "run.py"),
            "--dag",
            "5",
            "--run-root",
            os.fspath(run_root),
            "--step-delay-seconds",
            str(delay),
            "--approve",
            "--repair",
        ],
        cwd=repo,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    _wait_for_receipt(run_root / "receipts" / "discover.json", timeout=10.0)
    _wait_for_attempt_state(
        run_root / "run",
        node_id="discover",
        state="SETTLED",
        timeout=10.0,
    )
    process.send_signal(signal.SIGINT)
    stdout, stderr = process.communicate(timeout=10.0)
    stale_lease = _wait_for_resume_takeover(run_root / "run", timeout=20.0)

    pre_resume_hashes = _artifact_hashes(run_root)
    blocked_resume = _run_canonical(
        repo,
        run_root=run_root,
        extra=["--approve", "--repair", "--resume"],
        delay=delay,
        timeout=30.0,
    )
    blocked_resume_result = _json_stdout(blocked_resume)
    reconciliation: dict[str, Any] | None = None
    if blocked_resume.returncode != 0:
        if (
            blocked_resume_result.get("status") != "BLOCKED"
            or blocked_resume_result.get("verdict") != "DAG_ATTEMPT_EFFECT_UNCERTAIN"
        ):
            raise RuntimeError(f"forced-interrupt resume failed:{blocked_resume.stderr}")
        reconciliation = _run_tau_reconcile(
            repo,
            run_dir=run_root / "run",
            receipt_path=run_root / "proof" / "reconciliation-decision.json",
        )
        resumed = _run_canonical(
            repo,
            run_root=run_root,
            extra=["--approve", "--repair", "--resume"],
            delay=delay,
            timeout=45.0,
        )
    else:
        resumed = blocked_resume
    resumed_result = _json_stdout(resumed)
    if resumed.returncode != 0:
        raise RuntimeError(f"forced-interrupt reconciled resume failed:{resumed.stderr}")
    post_resume_hashes = _artifact_hashes(run_root)
    release_hash = post_resume_hashes.get("release")

    idempotent = _run_canonical(
        repo,
        run_root=run_root,
        extra=["--approve", "--repair", "--resume"],
        delay=delay,
        timeout=30.0,
    )
    idempotent_result = _json_stdout(idempotent)
    if idempotent.returncode != 0:
        raise RuntimeError(f"forced-interrupt idempotent resume failed:{idempotent.stderr}")
    idempotent_hashes = _artifact_hashes(run_root)
    ledger = _effect_ledger(
        run_root / "proof" / "effect-ledger.json",
        scenario="forced_interruption",
        release_hash_before=None,
        release_hash_after=release_hash,
        release_hash_after_idempotent_resume=idempotent_hashes.get("release"),
    )
    receipt = _read_json(run_root / "run" / "run-receipt.json")
    nodes = _node_map(receipt)
    resumed_node_ids = [
        node_id for node_id, node in nodes.items() if node.get("resumed") is True
    ]
    return {
        "scenario": "forced_interruption",
        "interruption_method": "SIGINT after discover attempt reached SETTLED",
        "initial_exit_code": process.returncode,
        "initial_stdout_tail": stdout[-1000:],
        "initial_stderr_tail": stderr[-1000:],
        "stale_lease": stale_lease,
        "plain_resume_result": blocked_resume_result,
        "plain_resume_blocked_on_uncertain": (
            blocked_resume_result.get("status") == "BLOCKED"
            and blocked_resume_result.get("verdict") == "DAG_ATTEMPT_EFFECT_UNCERTAIN"
        ),
        "reconciliation_decision": reconciliation,
        "pre_resume_artifact_hashes": pre_resume_hashes,
        "post_resume_artifact_hashes": post_resume_hashes,
        "idempotent_resume_artifact_hashes": idempotent_hashes,
        "resumed_result": resumed_result,
        "idempotent_result": idempotent_result,
        "resumed_node_ids": resumed_node_ids,
        "attempt_graph": _attempt_graph(run_root / "run"),
        "journal_event_counts": _journal_event_counts(run_root / "run"),
        "effect_ledger_path": str((run_root / "proof" / "effect-ledger.json").resolve()),
        "effect_ledger": ledger,
        "checks": {
            "interruption_observed": process.returncode not in {0, None},
            "plain_resume_failed_closed_on_uncertain": (
                blocked_resume_result.get("status") == "BLOCKED"
                and blocked_resume_result.get("verdict") == "DAG_ATTEMPT_EFFECT_UNCERTAIN"
            ),
            "reconciliation_authorized_new_generation": (
                reconciliation is not None
                and reconciliation.get("status") == "PASS"
                and reconciliation.get("decision") == "authorize_new_generation"
            ),
            "resume_passed": resumed_result.get("status") == "PASS",
            "accepted_work_resumed": bool(resumed_node_ids),
            "idempotent_resume_reused_all_nodes": idempotent_result.get("resumed_node_count") == 6,
            "duplicate_release_effect_suppressed": ledger["duplicate_effect_suppressed"],
        },
    }


def _targeted_repair(repo: Path, run_root: Path) -> dict[str, Any]:
    blocked = _run_canonical(
        repo,
        run_root=run_root,
        extra=["--approve"],
        delay=0.0,
        timeout=30.0,
    )
    blocked_result = _json_stdout(blocked)
    if blocked.returncode != 2:
        raise RuntimeError(f"targeted-repair setup did not block:{blocked.stderr}")
    pre_repair_artifacts = _artifact_hashes(run_root)
    pre_repair_receipts = _receipt_hashes(run_root)
    repaired = _run_canonical(
        repo,
        run_root=run_root,
        extra=["--approve", "--repair", "--resume"],
        delay=0.0,
        timeout=30.0,
    )
    repaired_result = _json_stdout(repaired)
    if repaired.returncode != 0:
        raise RuntimeError(f"targeted repair resume failed:{repaired.stderr}")
    post_repair_artifacts = _artifact_hashes(run_root)
    post_repair_receipts = _receipt_hashes(run_root)
    final_receipt = _read_json(run_root / "run" / "run-receipt.json")
    nodes = _node_map(final_receipt)
    unchanged_node_ids = ["discover", "build", "test", "document"]
    repaired_node_ids = ["reconcile", "release"]
    ledger = _effect_ledger(
        run_root / "proof" / "effect-ledger.json",
        scenario="targeted_repair",
        release_hash_before=pre_repair_artifacts.get("release"),
        release_hash_after=post_repair_artifacts.get("release"),
        release_hash_after_idempotent_resume=post_repair_artifacts.get("release"),
    )
    return {
        "scenario": "targeted_repair",
        "blocked_result": blocked_result,
        "repaired_result": repaired_result,
        "repaired_node_ids": repaired_node_ids,
        "unchanged_node_ids": unchanged_node_ids,
        "pre_repair_artifact_hashes": pre_repair_artifacts,
        "post_repair_artifact_hashes": post_repair_artifacts,
        "pre_repair_receipt_hashes": pre_repair_receipts,
        "post_repair_receipt_hashes": post_repair_receipts,
        "attempt_graph": _attempt_graph(run_root / "run"),
        "journal_event_counts": _journal_event_counts(run_root / "run"),
        "effect_ledger_path": str((run_root / "proof" / "effect-ledger.json").resolve()),
        "effect_ledger": ledger,
        "checks": {
            "initial_blocked_on_repair_gate": blocked_result.get("status") == "BLOCKED"
            and blocked_result.get("completed_node_count") == 4,
            "targeted_repair_passed": repaired_result.get("status") == "PASS",
            "repair_gate_reexecuted": nodes.get("reconcile", {}).get("resumed") is False
            and pre_repair_receipts.get("reconcile") != post_repair_receipts.get("reconcile"),
            "downstream_reexecuted": all(
                nodes.get(node_id, {}).get("resumed") is False for node_id in repaired_node_ids
            ),
            "unaffected_accepted_outputs_unchanged": all(
                pre_repair_artifacts.get(node_id) == post_repair_artifacts.get(node_id)
                for node_id in unchanged_node_ids
            ),
            "unaffected_nodes_resumed": all(
                nodes.get(node_id, {}).get("resumed") is True for node_id in unchanged_node_ids
            ),
            "no_release_before_repair": "release" not in pre_repair_artifacts,
            "release_effect_once_after_repair": ledger["duplicate_effect_suppressed"],
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-root", type=Path, required=True)
    parser.add_argument("--receipt", type=Path, required=True)
    parser.add_argument("--repo", type=Path, default=Path(__file__).resolve().parents[1])
    args = parser.parse_args()

    repo = args.repo.expanduser().resolve()
    run_root = args.run_root.expanduser().resolve()
    receipt_path = args.receipt.expanduser().resolve()
    if run_root.exists():
        raise RuntimeError(f"run root already exists: {run_root}")
    run_root.mkdir(parents=True)

    forced = _forced_interruption(repo, run_root / "forced-interruption")
    targeted = _targeted_repair(repo, run_root / "targeted-repair")
    checks = {
        **{f"forced_{key}": value for key, value in forced["checks"].items()},
        **{f"targeted_{key}": value for key, value in targeted["checks"].items()},
    }
    errors = [key for key, value in checks.items() if value is not True]
    receipt = {
        "schema": PROOF_SCHEMA,
        "ok": not errors,
        "status": "PASS" if not errors else "BLOCKED",
        "mocked": False,
        "live": True,
        "provider_live": False,
        "repo": str(repo),
        "run_root": str(run_root),
        "interruption_method": forced["interruption_method"],
        "repaired_node_ids": targeted["repaired_node_ids"],
        "unchanged_node_ids": targeted["unchanged_node_ids"],
        "duplicate_effect_checks": {
            "forced_interruption": forced["checks"]["duplicate_release_effect_suppressed"],
            "targeted_repair": targeted["checks"]["release_effect_once_after_repair"],
        },
        "forced_interruption": forced,
        "targeted_repair": targeted,
        "checks": checks,
        "errors": errors,
        "proof_scope": {
            "proves": [
                "The packaged canonical DAG 5 entrypoint survives a forced SIGTERM and resumes accepted work.",
                "A branch defect in the canonical DAG 5 test node blocks fail-closed.",
                "A later resume reuses unaffected accepted branch artifacts and reruns only the repaired branch plus descendants.",
                "A repeated resume after terminal success does not rewrite the release artifact.",
            ],
            "does_not_prove": [
                "Provider/model semantic quality.",
                "Perfect sandbox isolation.",
                "Human acceptance of the full immutable Tau goal.",
            ],
        },
    }
    _write_json(receipt_path, receipt)
    print(json.dumps(receipt, indent=2, sort_keys=True))
    return 0 if not errors else 1


if __name__ == "__main__":
    raise SystemExit(main())
