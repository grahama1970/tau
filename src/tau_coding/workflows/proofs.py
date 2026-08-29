"""Acceptance proof helpers for packaged Tau workflow ladder rungs."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from tau_coding.dag_runtime.replay import replay_dag_run_at_sequence
from tau_coding.dag_runtime.run_store import SqliteDagRunReader
from tau_coding.workflows.catalog import dag_ladder_manifest_payload

RUNG1_PROOF_SCHEMA = "tau.dag_ladder_rung1_clean_checkout_proof.v1"
RUNG1_VERIFIER_SCHEMA = "tau.dag_ladder_rung1_verifier_receipt.v1"
RUNG1_NEGATIVE_SCHEMA = "tau.dag_ladder_rung1_negative_mutation_receipt.v1"

RUNG1_GOAL = "Prove the first canonical Tau DAG rung from a clean checkout."
RUNG1_NODES = ("inspect-repository", "validate-readiness", "publish-readiness")


def write_dag_ladder_rung1_clean_checkout_proof(
    *,
    source_repo: Path,
    output_dir: Path,
) -> dict[str, Any]:
    """Run rung 1 through the public CLI from a temporary clean checkout."""

    source = source_repo.expanduser().resolve()
    output = output_dir.expanduser().resolve()
    if not source.is_dir():
        raise RuntimeError(f"source repository is not a directory: {source}")
    source_root = _git(source, "rev-parse", "--show-toplevel").strip()
    source = Path(source_root).resolve()
    source_commit = _git(source, "rev-parse", "HEAD").strip()
    source_branch = _git(source, "branch", "--show-current").strip()
    source_status = _git(source, "status", "--porcelain=v1", "--untracked-files=all")
    if source_status.strip():
        raise RuntimeError("source repository must be clean before clean-checkout proof starts")

    _replace_output_dir(output)
    output.mkdir(parents=True, exist_ok=True)
    manifest = dag_ladder_manifest_payload()
    _write_json(output / "ladder-manifest.json", manifest)

    commands: list[dict[str, Any]] = []
    with tempfile.TemporaryDirectory(prefix="tau-rung1-clean-") as temp_text:
        temp_root = Path(temp_text)
        clean_checkout = temp_root / "checkout"
        temp_home = temp_root / "home"
        temp_home.mkdir()
        env = _clean_env(temp_home=temp_home, temp_root=temp_root)

        _run(
            ["git", "clone", "--no-local", str(source), str(clean_checkout)],
            cwd=temp_root,
            env=os.environ.copy(),
            label="git-clone-clean-checkout",
            output_dir=output,
            commands=commands,
        )
        _run(
            ["git", "checkout", "--quiet", source_commit],
            cwd=clean_checkout,
            env=os.environ.copy(),
            label="git-checkout-source-commit",
            output_dir=output,
            commands=commands,
        )
        checkout_status = _run(
            ["git", "status", "--porcelain=v1", "--untracked-files=all"],
            cwd=clean_checkout,
            env=os.environ.copy(),
            label="git-status-clean-checkout",
            output_dir=output,
            commands=commands,
        )
        if checkout_status.stdout.strip():
            raise RuntimeError("temporary checkout is not clean")

        ladder = _run(
            ["uv", "run", "tau", "workflows", "ladder", "--json"],
            cwd=clean_checkout,
            env=env,
            label="tau-workflows-ladder",
            output_dir=output,
            commands=commands,
        )
        _write_json(output / "ladder-list.json", _json_from_stdout(ladder.stdout, "ladder"))

        run_dir = output / "rung-1-run"
        workflow = _run(
            [
                "uv",
                "run",
                "tau",
                "workflows",
                "run",
                "repository-readiness",
                "--repo",
                str(clean_checkout),
                "--goal",
                RUNG1_GOAL,
                "--require-clean",
                "--run-dir",
                str(run_dir),
            ],
            cwd=clean_checkout,
            env=env,
            label="tau-workflows-run-rung-1",
            output_dir=output,
            commands=commands,
        )
        _write_json(
            output / "rung-1-workflow-command-receipt.json",
            _json_from_stdout(workflow.stdout, "workflow run"),
        )

    _write_bootstrap_log(
        output / "clean-checkout-bootstrap.log",
        source=source,
        source_commit=source_commit,
        source_branch=source_branch,
        commands=commands,
    )
    verifier = verify_dag_ladder_rung1_clean_checkout_proof(output_dir=output)
    negative = write_rung1_negative_mutation_receipt(output_dir=output)
    status = "PASS" if verifier["status"] == "PASS" and negative["status"] == "PASS" else "FAIL"
    receipt = {
        "schema": RUNG1_PROOF_SCHEMA,
        "status": status,
        "ok": status == "PASS",
        "mocked": False,
        "live": True,
        "provider_live": False,
        "source_repo": str(source),
        "source_branch": source_branch,
        "source_commit": source_commit,
        "output_dir": str(output),
        "ladder_manifest": _artifact(output / "ladder-manifest.json"),
        "bootstrap_log": _artifact(output / "clean-checkout-bootstrap.log"),
        "rung_1_artifacts": {
            "dag_progress": _artifact(output / "rung-1-dag-progress.json"),
            "events": _artifact(output / "rung-1-events.jsonl"),
            "ledger": _artifact(output / "rung-1-ledger.json"),
            "ledger_replay": _artifact(output / "rung-1-ledger-replay.json"),
            "result": _artifact(output / "rung-1-result.json"),
        },
        "verifier_receipt": _artifact(output / "verifier-pass-receipt.json"),
        "negative_mutation_receipt": _artifact(output / "negative-mutation-receipt.json"),
        "proof_boundary": {
            "proves": [
                "The canonical ladder manifest is discoverable from the public Tau CLI.",
                "Rung 1 runs from a temporary clean checkout through the public Tau CLI.",
                "Rung 1 writes useful readiness JSON and Markdown output.",
                "The DAG journal replays to PASS from the retained SQLite ledger.",
                "The verifier rejects a missing required event artifact.",
            ],
            "does_not_prove": [
                "Rungs 2 through 5 have fresh retained clean-checkout proof.",
                "Provider or model semantic quality.",
                "Human acceptance of the full GOAL.md product outcome.",
            ],
        },
        "verified_at": _now(),
    }
    _write_json(output / "proof-receipt.json", receipt)
    return receipt


def verify_dag_ladder_rung1_clean_checkout_proof(*, output_dir: Path) -> dict[str, Any]:
    output = output_dir.expanduser().resolve()
    errors: list[str] = []
    details: dict[str, Any] = {"output_dir": str(output)}

    manifest = _read_json(output / "ladder-manifest.json", errors=errors)
    ladder_list = _read_json(output / "ladder-list.json", errors=errors)
    run_dir = output / "rung-1-run"
    request = _read_json(run_dir / "input" / "repository-readiness-request.json", errors=errors)
    dag = _read_json(run_dir / "workflow" / "dag.json", errors=errors)
    run_receipt = _read_json(run_dir / "run-receipt.json", errors=errors)
    workflow_receipt = _read_json(run_dir / "workflow-receipt.json", errors=errors)
    progress = _read_json(run_dir / "current-state.json", errors=errors)
    result = _read_json(run_dir / "results" / "repository-readiness.json", errors=errors)
    markdown_path = run_dir / "results" / "repository-readiness.md"
    events_path = run_dir / "events.jsonl"
    store_path = run_dir / "dag-run.sqlite3"

    for required in (markdown_path, events_path, store_path):
        if not required.is_file():
            errors.append(f"missing_required_artifact:{required.relative_to(output)}")

    events = _read_events(events_path, errors=errors)
    if manifest and manifest.get("schema") != "tau.dag_ladder_manifest.v1":
        errors.append("manifest_schema_invalid")
    if ladder_list and ladder_list != manifest:
        errors.append("ladder_cli_output_does_not_match_manifest")
    rungs = manifest.get("rungs") if isinstance(manifest, dict) else None
    if not isinstance(rungs, list) or len(rungs) != 5:
        errors.append("manifest_does_not_name_five_rungs")
    elif rungs[0].get("workflow_id") != "repository-readiness":
        errors.append("manifest_rung_1_workflow_invalid")

    goal_hashes = _collect_goal_hashes(request, dag, run_receipt, workflow_receipt, result)
    if len(goal_hashes) != 1:
        errors.append("goal_hash_not_preserved")
    details["goal_hashes"] = sorted(goal_hashes)

    if run_receipt.get("status") != "PASS" or run_receipt.get("ok") is not True:
        errors.append("run_receipt_not_pass")
    if workflow_receipt.get("status") != "PASS" or workflow_receipt.get("ok") is not True:
        errors.append("workflow_receipt_not_pass")
    if result.get("status") != "READY":
        errors.append("result_status_not_ready")
    if result.get("summary") != "Repository is ready for focused work.":
        errors.append("result_is_not_useful_readiness_output")
    if progress.get("status") != "PASS" or progress.get("verdict") != "PASS":
        errors.append("dag_progress_not_pass")

    node_ids = tuple(str(node.get("node_id")) for node in run_receipt.get("nodes", []))
    if node_ids != RUNG1_NODES:
        errors.append("run_receipt_node_order_invalid")
    if [event.get("kind") for event in events[:1]] != ["dag_started"]:
        errors.append("events_do_not_start_with_dag_started")
    if not events or events[-1].get("kind") != "dag_finished":
        errors.append("events_do_not_finish_with_dag_finished")

    replay = _replay_ledger(run_dir=run_dir, run_receipt=run_receipt, errors=errors)
    details["ledger_replay"] = replay

    _write_json(output / "rung-1-dag-progress.json", progress)
    if events_path.is_file():
        _copy(events_path, output / "rung-1-events.jsonl")
    _write_json(output / "rung-1-ledger.json", run_receipt)
    _write_json(output / "rung-1-ledger-replay.json", replay)
    _write_json(output / "rung-1-result.json", result)

    receipt = {
        "schema": RUNG1_VERIFIER_SCHEMA,
        "status": "PASS" if not errors else "FAIL",
        "ok": not errors,
        "mocked": False,
        "live": True,
        "provider_live": False,
        "errors": errors,
        "details": details,
        "verified_artifacts": [
            str((output / "ladder-manifest.json").resolve()),
            str((output / "clean-checkout-bootstrap.log").resolve()),
            str((output / "rung-1-dag-progress.json").resolve()),
            str((output / "rung-1-events.jsonl").resolve()),
            str((output / "rung-1-ledger.json").resolve()),
            str((output / "rung-1-ledger-replay.json").resolve()),
            str((output / "rung-1-result.json").resolve()),
        ],
        "verified_at": _now(),
    }
    _write_json(output / "verifier-pass-receipt.json", receipt)
    return receipt


def write_rung1_negative_mutation_receipt(*, output_dir: Path) -> dict[str, Any]:
    output = output_dir.expanduser().resolve()
    with tempfile.TemporaryDirectory(prefix="tau-rung1-negative-") as temp_text:
        candidate = Path(temp_text) / "candidate"
        shutil.copytree(output, candidate, ignore=shutil.ignore_patterns("negative-*"))
        mutated = candidate / "rung-1-run" / "events.jsonl"
        mutated.unlink()
        receipt = verify_dag_ladder_rung1_clean_checkout_proof(output_dir=candidate)
    errors = receipt.get("errors")
    passed = (
        receipt.get("status") == "FAIL"
        and isinstance(errors, list)
        and "missing_required_artifact:rung-1-run/events.jsonl" in errors
    )
    negative = {
        "schema": RUNG1_NEGATIVE_SCHEMA,
        "status": "PASS" if passed else "FAIL",
        "ok": passed,
        "mutation": "removed rung-1-run/events.jsonl from a copied proof bundle",
        "verifier_status": receipt.get("status"),
        "verifier_errors": errors if isinstance(errors, list) else [],
        "mocked": False,
        "live": True,
        "provider_live": False,
        "verified_at": _now(),
    }
    _write_json(output / "negative-mutation-receipt.json", negative)
    return negative


def _replay_ledger(
    *,
    run_dir: Path,
    run_receipt: dict[str, Any],
    errors: list[str],
) -> dict[str, Any]:
    run_id = run_receipt.get("run_id")
    if not isinstance(run_id, str) or not run_id:
        errors.append("run_receipt_missing_run_id")
        return {"status": "FAIL", "errors": ["run_receipt_missing_run_id"]}
    try:
        with SqliteDagRunReader(run_dir / "dag-run.sqlite3") as reader:
            replay = replay_dag_run_at_sequence(reader, run_id, None)
            admissions = reader.list_admissions(run_id)
    except Exception as exc:
        errors.append(f"ledger_replay_failed:{type(exc).__name__}:{exc}")
        return {"status": "FAIL", "errors": [str(exc)]}
    result_nodes = [item.node_id for item in replay.replay.results]
    if replay.replay.run_status != "PASS":
        errors.append("ledger_replay_status_not_pass")
    if tuple(result_nodes) != RUNG1_NODES:
        errors.append("ledger_replay_node_order_invalid")
    if len(admissions) < len(RUNG1_NODES):
        errors.append("ledger_admission_count_too_low")
    return {
        "schema": "tau.dag_ladder_rung1_ledger_replay.v1",
        "status": replay.replay.run_status,
        "verdict": replay.replay.run_verdict,
        "run_id": run_id,
        "head_sequence": replay.head_sequence,
        "event_count": len(replay.events),
        "result_nodes": result_nodes,
        "admission_count": len(admissions),
        "journal_sequence": replay.replay.journal_sequence,
    }


def _collect_goal_hashes(*payloads: dict[str, Any]) -> set[str]:
    hashes: set[str] = set()

    def visit(value: Any) -> None:
        if isinstance(value, dict):
            goal_hash = value.get("goal_hash")
            if isinstance(goal_hash, str) and goal_hash:
                hashes.add(goal_hash)
            for child in value.values():
                visit(child)
        elif isinstance(value, list):
            for child in value:
                visit(child)

    for payload in payloads:
        visit(payload)
    return hashes


def _run(
    argv: list[str],
    *,
    cwd: Path,
    env: dict[str, str],
    label: str,
    output_dir: Path,
    commands: list[dict[str, Any]],
) -> subprocess.CompletedProcess[str]:
    started = _now()
    result = subprocess.run(argv, cwd=cwd, env=env, check=False, capture_output=True, text=True)
    stdout_path = output_dir / "bootstrap" / f"{label}.stdout"
    stderr_path = output_dir / "bootstrap" / f"{label}.stderr"
    stdout_path.parent.mkdir(parents=True, exist_ok=True)
    stdout_path.write_text(result.stdout, encoding="utf-8")
    stderr_path.write_text(result.stderr, encoding="utf-8")
    commands.append(
        {
            "label": label,
            "argv": argv,
            "cwd": str(cwd),
            "returncode": result.returncode,
            "stdout": str(stdout_path),
            "stderr": str(stderr_path),
            "started_at": started,
            "finished_at": _now(),
        }
    )
    if result.returncode != 0:
        raise RuntimeError(f"{label} failed with exit {result.returncode}: {result.stderr}")
    return result


def _git(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(repo), *args],
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} failed: {result.stderr.strip()}")
    return result.stdout


def _clean_env(*, temp_home: Path, temp_root: Path) -> dict[str, str]:
    uv = shutil.which("uv")
    git = shutil.which("git")
    path = os.environ.get("PATH", "")
    if uv is None or git is None:
        raise RuntimeError("uv and git must be available on PATH")
    return {
        "HOME": str(temp_home),
        "PATH": path,
        "TERM": "dumb",
        "UV_CACHE_DIR": str(temp_root / "uv-cache"),
        "UV_LINK_MODE": "copy",
        "TAU_RUN_REGISTRY": str(temp_root / "tau-runs.json"),
        "PYTHONUTF8": "1",
    }


def _replace_output_dir(output: Path) -> None:
    if output.exists() and not output.is_dir():
        raise RuntimeError(f"proof output exists and is not a directory: {output}")
    if output.exists():
        shutil.rmtree(output)


def _read_json(path: Path, *, errors: list[str]) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        errors.append(f"missing_required_artifact:{path}")
        return {}
    except json.JSONDecodeError as exc:
        errors.append(f"invalid_json:{path.name}:{exc}")
        return {}
    if not isinstance(payload, dict):
        errors.append(f"json_not_object:{path.name}")
        return {}
    return payload


def _read_events(path: Path, *, errors: list[str]) -> list[dict[str, Any]]:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except FileNotFoundError:
        errors.append(f"missing_required_artifact:{path.name}")
        return []
    events: list[dict[str, Any]] = []
    for index, line in enumerate(lines, start=1):
        try:
            payload = json.loads(line)
        except json.JSONDecodeError as exc:
            errors.append(f"invalid_event_json:{index}:{exc}")
            continue
        if not isinstance(payload, dict):
            errors.append(f"event_not_object:{index}")
            continue
        events.append(payload)
    if not events:
        errors.append("events_empty")
    return events


def _json_from_stdout(stdout: str, label: str) -> dict[str, Any]:
    try:
        payload = json.loads(stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"{label} did not emit JSON") from exc
    if not isinstance(payload, dict):
        raise RuntimeError(f"{label} JSON output must be an object")
    return payload


def _write_bootstrap_log(
    path: Path,
    *,
    source: Path,
    source_commit: str,
    source_branch: str,
    commands: list[dict[str, Any]],
) -> None:
    lines = [
        "Tau DAG ladder rung 1 clean-checkout bootstrap",
        f"source_repo: {source}",
        f"source_branch: {source_branch}",
        f"source_commit: {source_commit}",
        "",
    ]
    for command in commands:
        lines.extend(
            [
                f"[{command['label']}]",
                f"cwd: {command['cwd']}",
                f"argv: {' '.join(command['argv'])}",
                f"returncode: {command['returncode']}",
                f"stdout: {command['stdout']}",
                f"stderr: {command['stderr']}",
                "",
            ]
        )
    path.write_text("\n".join(lines), encoding="utf-8")


def _copy(source: Path, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, target)


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _artifact(path: Path) -> dict[str, Any]:
    data = path.read_bytes()
    return {
        "path": str(path.resolve()),
        "sha256": "sha256:" + hashlib.sha256(data).hexdigest(),
        "bytes": len(data),
    }


def _now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")
