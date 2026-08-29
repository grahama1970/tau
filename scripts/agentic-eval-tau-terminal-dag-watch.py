"""Agentic eval probe for the Tau terminal DAG watch surface.

This script exercises the real phart-dag-chart entrypoint against Tau-authored
DAG/progress artifacts and writes an independent readback receipt. It does not
mutate Tau run state.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import tempfile
from pathlib import Path
from typing import Any


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _phart_run() -> Path:
    return _repo_root().parent / "agent-skills" / "skills" / "phart-dag-chart" / "run.sh"


def _dag_file() -> Path:
    return _repo_root() / "local" / "issue-327-ledger-proof" / "issue-327-dag.json"


def _progress_file() -> Path:
    return _repo_root() / "local" / "issue-327-ledger-proof" / "run" / "dag-progress.json"


def _run_watch(progress: Path, *, max_seconds: str = "2") -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            str(_phart_run()),
            "watch",
            str(_dag_file()),
            "--progress",
            str(progress),
            "--max-seconds",
            max_seconds,
            "--interval",
            "0.1",
            "--no-clear",
            "--no-chart",
        ],
        cwd=_repo_root(),
        text=True,
        capture_output=True,
        timeout=30,
        check=False,
    )


def _write(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _positive(out: Path) -> int:
    phart = _phart_run()
    dag = _dag_file()
    progress = _progress_file()
    missing = [str(path) for path in [phart, dag, progress] if not path.exists()]
    if missing:
        _write(out, {"ok": False, "reason": "missing_required_artifact", "missing": missing})
        return 1

    before = progress.stat().st_mtime_ns
    proc = _run_watch(progress)
    after = progress.stat().st_mtime_ns
    progress_payload = _load_json(progress)
    stdout = proc.stdout
    required = [
        "Tau DAG terminal monitor · issue-327-ledger-live",
        "State: PASS",
        "✓ coder",
        "✓ reviewer",
        "✓ human",
    ]
    ok = (
        proc.returncode == 0
        and all(token in stdout for token in required)
        and progress_payload.get("status") == "PASS"
        and before == after
    )
    proof = {
        "schema": "tau.terminal_dag_watch.agentic_eval_receipt.v1",
        "ok": ok,
        "mocked": False,
        "live": True,
        "mode": "positive",
        "command": proc.args,
        "exit_code": proc.returncode,
        "stdout": stdout,
        "stderr": proc.stderr,
        "progress_file": str(progress),
        "dag_file": str(dag),
        "progress_readback": {
            "dag_id": progress_payload.get("dag_id"),
            "status": progress_payload.get("status"),
            "event_count": progress_payload.get("event_count"),
            "last_event": progress_payload.get("last_event"),
        },
        "progress_mtime_unchanged": before == after,
        "observed_tokens": {token: token in stdout for token in required},
    }
    _write(out, proof)
    print(
        json.dumps(
            {"status": "PASS" if ok else "FAIL", "proof": str(out), "mode": "positive"},
            sort_keys=True,
        )
    )
    return 0 if ok else 1


def _missing_progress(out: Path) -> int:
    with tempfile.TemporaryDirectory(prefix="tau-terminal-watch-") as tmp:
        missing_progress = Path(tmp) / "missing-dag-progress.json"
        proc = _run_watch(missing_progress)
    ok = (
        proc.returncode != 0
        and "progress_not_found" in proc.stderr
        and "dag-progress.json" in proc.stderr
    )
    proof = {
        "schema": "tau.terminal_dag_watch.agentic_eval_receipt.v1",
        "ok": ok,
        "mocked": False,
        "live": True,
        "mode": "missing-progress",
        "command": proc.args,
        "exit_code": proc.returncode,
        "stdout": proc.stdout,
        "stderr": proc.stderr,
        "expected_failure_code": "progress_not_found",
    }
    _write(out, proof)
    print(
        json.dumps(
            {"status": "PASS" if ok else "FAIL", "proof": str(out), "mode": "missing-progress"},
            sort_keys=True,
        )
    )
    return 0 if ok else 1


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=["positive", "missing-progress"], required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    if args.mode == "positive":
        return _positive(args.out)
    return _missing_progress(args.out)


if __name__ == "__main__":
    raise SystemExit(main())
