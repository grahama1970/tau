"""Provider-live acceptance harness for Tau's packaged workflow ladder."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sysconfig
import tempfile
import time
import venv
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

EXPECTED_WORKFLOW_IDS = (
    "repository-readiness",
    "tau-operator-reference",
    "repository-evidence-map",
    "approved-release-bundle",
    "durable-repository-qualification",
)
ACCEPTANCE_RECEIPT_SCHEMA = "tau.workflow_provider_live_acceptance_receipt.v1"
ACCEPTANCE_VERIFICATION_SCHEMA = "tau.workflow_provider_live_acceptance_verification.v1"


class WorkflowAcceptanceError(RuntimeError):
    """Raised when the acceptance harness or verifier fails closed."""


@dataclass(frozen=True, slots=True)
class CommandEvidence:
    command: list[str]
    cwd: str
    returncode: int
    stdout_path: str
    stderr_path: str


def run_provider_live_acceptance(
    *,
    repo: Path,
    output: Path,
    provider_url: str,
    model: str,
    wheel: Path | None = None,
    work_dir: Path | None = None,
    timeout_s: float = 5.0,
) -> dict[str, Any]:
    """Build/install Tau, run all five workflows, probe provider, and write a receipt."""

    resolved_repo = repo.expanduser().resolve()
    if not resolved_repo.is_dir():
        raise WorkflowAcceptanceError(f"repository path is not a directory: {resolved_repo}")
    resolved_output = output.expanduser().resolve()
    source = _source_state(resolved_repo)
    artifact_root = (
        work_dir.expanduser().resolve()
        if work_dir is not None
        else resolved_output.parent / f"{resolved_output.stem}-artifacts"
    )
    run_root = artifact_root / f"run-{int(time.time())}"
    run_root.mkdir(parents=True, exist_ok=False)

    wheel_path, build_evidence = _resolve_or_build_wheel(
        repo=resolved_repo,
        wheel=wheel,
        command_dir=run_root / "commands",
        wheel_dir=run_root / "wheel",
    )
    wheel_sha256 = _sha256(wheel_path)

    environment = run_root / "venv"
    bin_dir, environment_site, install_evidence = _install_wheel(
        wheel=wheel_path,
        environment=environment,
        command_dir=run_root / "commands",
    )
    installed_tau = bin_dir / ("tau.exe" if os.name == "nt" else "tau")
    if not installed_tau.is_file():
        raise WorkflowAcceptanceError(f"installed tau entrypoint missing: {installed_tau}")

    env = _installed_env(bin_dir)
    provider_receipt_path = run_root / "provider" / "provider-readiness.json"
    provider_evidence = _run_command(
        [
            str(installed_tau),
            "local-provider-readiness",
            "--provider-url",
            provider_url,
            "--model",
            model,
            "--timeout-s",
            str(timeout_s),
            "--out",
            str(provider_receipt_path),
        ],
        cwd=run_root,
        env=env,
        command_dir=run_root / "commands",
        label="provider-readiness",
        timeout=max(30.0, timeout_s + 10.0),
    )
    provider_receipt = _read_json_object(provider_receipt_path, "provider readiness receipt")

    fixture_repo = run_root / "fixture-repo"
    fixture_head = _create_fixture_repo(fixture_repo)
    publish_root = run_root / "published"
    rungs = [
        _run_workflow_rung(
            installed_tau=installed_tau,
            env=env,
            run_root=run_root,
            fixture_repo=fixture_repo,
            workflow_id="repository-readiness",
            expected_status="PASS",
            args=[
                "--repo",
                str(fixture_repo),
                "--goal",
                "Determine whether this checkout is ready for focused work.",
                "--require-clean",
            ],
        ),
        _run_workflow_rung(
            installed_tau=installed_tau,
            env=env,
            run_root=run_root,
            fixture_repo=fixture_repo,
            workflow_id="tau-operator-reference",
            expected_status="PASS",
            args=[
                "--repo",
                str(fixture_repo),
                "--required-workflow",
                "tau-operator-reference",
            ],
        ),
        _run_workflow_rung(
            installed_tau=installed_tau,
            env=env,
            run_root=run_root,
            fixture_repo=fixture_repo,
            workflow_id="repository-evidence-map",
            expected_status="PASS",
            args=[
                "--repo",
                str(fixture_repo),
                "--goal",
                "Map this repository for focused work.",
                "--require-tests",
            ],
        ),
        _run_workflow_rung(
            installed_tau=installed_tau,
            env=env,
            run_root=run_root,
            fixture_repo=fixture_repo,
            workflow_id="approved-release-bundle",
            expected_status="BLOCKED",
            args=[
                "--repo",
                str(fixture_repo),
                "--goal",
                "Prepare an approved release bundle and stop at exact human approval.",
                "--publish-path",
                str(publish_root / "approved-release-bundle"),
            ],
        ),
        _run_workflow_rung(
            installed_tau=installed_tau,
            env=env,
            run_root=run_root,
            fixture_repo=fixture_repo,
            workflow_id="durable-repository-qualification",
            expected_status="BLOCKED",
            args=[
                "--repo",
                str(fixture_repo),
                "--goal",
                "Qualify this repository durably and preserve accepted work across repair.",
                "--publish-path",
                str(publish_root / "durable-repository-qualification"),
                "--inject-test-branch-failure",
            ],
        ),
    ]

    provider_live = _provider_receipt_is_live(provider_receipt)
    ok = (
        source["clean"] is True
        and provider_live
        and all(rung["accepted_by_harness"] is True for rung in rungs)
    )
    receipt: dict[str, Any] = {
        "schema": ACCEPTANCE_RECEIPT_SCHEMA,
        "status": "PASS" if ok else "BLOCKED",
        "ok": ok,
        "mocked": False,
        "live": True,
        "provider_live": provider_live,
        "created_at": _utc_stamp(),
        "source": source,
        "wheel": {
            "path": str(wheel_path),
            "sha256": wheel_sha256,
            "built_by_command": wheel is None,
            "build_command": _command_evidence_payload(build_evidence)
            if build_evidence is not None
            else None,
        },
        "environment": {
            "mode": "fresh venv installed wheel via public tau entrypoint",
            "venv": str(environment),
            "site_packages": str(environment_site),
            "tau_entrypoint": str(installed_tau),
            "install_command": _command_evidence_payload(install_evidence),
        },
        "provider": {
            "receipt_path": str(provider_receipt_path),
            "receipt_sha256": _sha256(provider_receipt_path),
            "terminal_evidence": {
                "status": provider_receipt.get("status"),
                "ok": provider_receipt.get("ok"),
                "mocked": provider_receipt.get("mocked"),
                "live": provider_receipt.get("live"),
                "provider_live": provider_receipt.get("provider_live"),
                "checks": provider_receipt.get("checks"),
            },
            "command": _command_evidence_payload(provider_evidence),
        },
        "workflow_ids": list(EXPECTED_WORKFLOW_IDS),
        "fixture": {
            "repo": str(fixture_repo),
            "head_sha": fixture_head,
        },
        "rungs": rungs,
        "artifact_root": str(run_root),
        "proof_scope": {
            "proves": [
                "A freshly installed Tau wheel exposed exactly the five packaged workflow ids.",
                "The installed public tau entrypoint ran all five packaged workflows.",
                "The same installed tau entrypoint exercised the configured provider boundary.",
                "The receipt is bound to the source commit and wheel sha256.",
            ],
            "does_not_prove": [
                "Human acceptance of the immutable goal.",
                "Provider semantic quality or model intelligence.",
                "Production deployment readiness.",
            ],
        },
    }
    if not ok:
        receipt["errors"] = _acceptance_errors(
            source=source,
            provider_live=provider_live,
            rungs=rungs,
        )
    resolved_output.parent.mkdir(parents=True, exist_ok=True)
    resolved_output.write_text(
        json.dumps(receipt, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return receipt


def verify_provider_live_acceptance(
    *,
    receipt_path: Path,
    repo: Path | None = None,
    wheel: Path | None = None,
) -> dict[str, Any]:
    """Read back and verify a provider-live workflow acceptance receipt."""

    resolved_receipt = receipt_path.expanduser().resolve()
    payload = _read_json_object(resolved_receipt, "acceptance receipt")
    errors = verify_provider_live_acceptance_payload(
        payload,
        repo=repo.expanduser().resolve() if repo is not None else None,
        wheel=wheel.expanduser().resolve() if wheel is not None else None,
    )
    verification = {
        "schema": ACCEPTANCE_VERIFICATION_SCHEMA,
        "status": "PASS" if not errors else "BLOCKED",
        "ok": not errors,
        "mocked": False,
        "live": True,
        "provider_live": payload.get("provider_live") is True and not errors,
        "checked_at": _utc_stamp(),
        "receipt_path": str(resolved_receipt),
        "receipt_sha256": _sha256(resolved_receipt),
        "workflow_ids": payload.get("workflow_ids"),
        "artifact_root": payload.get("artifact_root"),
        "errors": errors,
    }
    return verification


def verify_provider_live_acceptance_payload(
    payload: Mapping[str, Any],
    *,
    repo: Path | None = None,
    wheel: Path | None = None,
) -> list[str]:
    """Return verifier errors for a provider-live workflow acceptance payload."""

    errors: list[str] = []
    if payload.get("schema") != ACCEPTANCE_RECEIPT_SCHEMA:
        errors.append("schema_invalid")
    for key, expected in {
        "status": "PASS",
        "ok": True,
        "mocked": False,
        "live": True,
        "provider_live": True,
    }.items():
        if payload.get(key) != expected:
            errors.append(f"{key}_invalid")

    source = payload.get("source")
    if not isinstance(source, dict):
        errors.append("source_missing")
    else:
        if source.get("clean") is not True:
            errors.append("source_not_clean")
        commit = source.get("commit")
        if not isinstance(commit, str) or len(commit) < 7:
            errors.append("source_commit_missing")
        if repo is not None and isinstance(commit, str):
            current = _git(["rev-parse", "HEAD"], cwd=repo)
            if commit != current:
                errors.append("source_commit_not_current")

    wheel_payload = payload.get("wheel")
    if not isinstance(wheel_payload, dict):
        errors.append("wheel_missing")
    else:
        wheel_path = Path(str(wheel_payload.get("path", ""))).expanduser()
        wheel_sha = wheel_payload.get("sha256")
        if not isinstance(wheel_sha, str) or not wheel_sha.startswith("sha256:"):
            errors.append("wheel_sha256_missing")
        if wheel is not None and wheel_path.resolve() != wheel:
            errors.append("wheel_path_mismatch")
        if wheel_path.is_file() and isinstance(wheel_sha, str) and _sha256(wheel_path) != wheel_sha:
            errors.append("wheel_sha256_mismatch")

    if payload.get("workflow_ids") != list(EXPECTED_WORKFLOW_IDS):
        errors.append("workflow_ids_not_exact")
    rungs = payload.get("rungs")
    if not isinstance(rungs, list) or len(rungs) != len(EXPECTED_WORKFLOW_IDS):
        errors.append("rungs_missing_or_wrong_count")
        rungs = []
    observed_ids: list[str] = []
    for item in rungs:
        if not isinstance(item, dict):
            errors.append("rung_not_object")
            continue
        workflow_id = item.get("workflow_id")
        if isinstance(workflow_id, str):
            observed_ids.append(workflow_id)
        if item.get("terminal") is not True:
            errors.append(f"rung_not_terminal:{workflow_id}")
        if item.get("accepted_by_harness") is not True:
            errors.append(f"rung_not_accepted:{workflow_id}")
        if item.get("mocked") is not False or item.get("live") is not True:
            errors.append(f"rung_boundary_invalid:{workflow_id}")
        if item.get("installed_entrypoint") is not True:
            errors.append(f"rung_not_installed_entrypoint:{workflow_id}")
        if not item.get("workflow_receipt_sha256"):
            errors.append(f"rung_receipt_sha_missing:{workflow_id}")
    if observed_ids and observed_ids != list(EXPECTED_WORKFLOW_IDS):
        errors.append("rung_ids_not_exact")

    provider = payload.get("provider")
    provider_terminal = provider.get("terminal_evidence") if isinstance(provider, dict) else None
    if not isinstance(provider_terminal, dict):
        errors.append("provider_terminal_evidence_missing")
    else:
        if provider_terminal.get("mocked") is not False:
            errors.append("provider_mocked_invalid")
        if provider_terminal.get("live") is not True:
            errors.append("provider_live_flag_invalid")
        if provider_terminal.get("provider_live") is not True:
            errors.append("provider_provider_live_flag_invalid")
        checks = provider_terminal.get("checks")
        if not _has_successful_provider_check(checks):
            errors.append("provider_successful_check_missing")
    return errors


def _resolve_or_build_wheel(
    *,
    repo: Path,
    wheel: Path | None,
    command_dir: Path,
    wheel_dir: Path,
) -> tuple[Path, CommandEvidence | None]:
    if wheel is not None:
        resolved = wheel.expanduser().resolve()
        if not resolved.is_file():
            raise WorkflowAcceptanceError(f"wheel is not a file: {resolved}")
        return resolved, None
    wheel_dir.mkdir(parents=True, exist_ok=True)
    evidence = _run_command(
        ["uv", "build", "--wheel", "--out-dir", str(wheel_dir)],
        cwd=repo,
        env=_command_env(),
        command_dir=command_dir,
        label="build-wheel",
        timeout=180,
    )
    wheels = sorted(wheel_dir.glob("*.whl"))
    if len(wheels) != 1:
        raise WorkflowAcceptanceError(f"expected exactly one built wheel, found {len(wheels)}")
    return wheels[0].resolve(), evidence


def _install_wheel(
    *,
    wheel: Path,
    environment: Path,
    command_dir: Path,
) -> tuple[Path, Path, CommandEvidence]:
    venv.EnvBuilder(with_pip=True).create(environment)
    bin_dir = environment / ("Scripts" if os.name == "nt" else "bin")
    environment_site = Path(
        _run_command(
            [
                str(bin_dir / "python"),
                "-c",
                "import sysconfig; print(sysconfig.get_path('purelib'))",
            ],
            cwd=environment,
            env=_command_env(),
            command_dir=command_dir,
            label="venv-site",
            timeout=30,
        ).stdout_path
    )
    environment_site = Path(Path(environment_site).read_text(encoding="utf-8").strip()).resolve()
    dependency_site = Path(sysconfig.get_path("purelib")).resolve()
    (environment_site / "tau-acceptance-dependencies.pth").write_text(
        str(dependency_site) + "\n", encoding="utf-8"
    )
    env = _installed_env(bin_dir)
    evidence = _run_command(
        [
            str(bin_dir / "python"),
            "-m",
            "pip",
            "install",
            "--quiet",
            "--no-index",
            "--no-deps",
            str(wheel),
        ],
        cwd=environment,
        env=env,
        command_dir=command_dir,
        label="install-wheel",
        timeout=120,
    )
    return bin_dir, environment_site, evidence


def _run_workflow_rung(
    *,
    installed_tau: Path,
    env: Mapping[str, str],
    run_root: Path,
    fixture_repo: Path,
    workflow_id: str,
    expected_status: str,
    args: list[str],
) -> dict[str, Any]:
    rung_index = EXPECTED_WORKFLOW_IDS.index(workflow_id) + 1
    run_dir = run_root / "workflows" / f"{rung_index}-{workflow_id}"
    command = [
        str(installed_tau),
        "workflows",
        "run",
        workflow_id,
        *args,
        "--run-dir",
        str(run_dir),
        "--no-browser-open",
    ]
    evidence = _run_command(
        command,
        cwd=run_root,
        env=env,
        command_dir=run_root / "commands",
        label=f"workflow-{rung_index}-{workflow_id}",
        timeout=240,
        check=False,
    )
    stdout = Path(evidence.stdout_path).read_text(encoding="utf-8")
    workflow_receipt = _json_from_text(stdout)
    if workflow_receipt is None and (run_dir / "workflow-receipt.json").is_file():
        workflow_receipt = _read_json_object(run_dir / "workflow-receipt.json", "workflow receipt")
    workflow_receipt = workflow_receipt or {}
    status = workflow_receipt.get("status")
    terminal = status in {"PASS", "BLOCKED"}
    node_receipts = _node_receipt_summaries(run_dir / "receipts")
    run_receipt = (
        _read_json_object(run_dir / "run-receipt.json", "generic DAG run receipt")
        if (run_dir / "run-receipt.json").is_file()
        else {}
    )
    result_artifacts = _result_artifacts(run_dir / "results")
    blockers = [
        {
            "node_id": receipt.get("node_id"),
            "status": receipt.get("status"),
            "errors": receipt.get("errors"),
            "source": "node_receipt",
            "path": receipt.get("path"),
        }
        for receipt in node_receipts
        if receipt.get("status") == "BLOCKED" or receipt.get("errors")
    ]
    blockers.extend(_approval_blocker_summaries(run_dir / "transactions"))
    accepted = status == expected_status and terminal
    if expected_status == "PASS":
        accepted = accepted and bool(result_artifacts)
    else:
        accepted = accepted and bool(blockers)
    workflow_receipt_path = run_dir / "workflow-receipt.json"
    return {
        "rung": rung_index,
        "workflow_id": workflow_id,
        "expected_terminal_status": expected_status,
        "terminal_status": status,
        "terminal": terminal,
        "accepted_by_harness": accepted,
        "mocked": workflow_receipt.get("mocked") is True,
        "live": workflow_receipt.get("live") is True,
        "provider_live": workflow_receipt.get("provider_live") is True,
        "installed_entrypoint": True,
        "run_dir": str(run_dir),
        "command": _command_evidence_payload(evidence),
        "workflow_receipt_path": str(workflow_receipt_path),
        "workflow_receipt_sha256": _sha256(workflow_receipt_path)
        if workflow_receipt_path.is_file()
        else None,
        "run_receipt_path": str(run_dir / "run-receipt.json"),
        "settlement": {
            "status": status,
            "ok": workflow_receipt.get("ok"),
            "dag_status": run_receipt.get("status"),
            "max_observed_concurrency": run_receipt.get("max_observed_concurrency"),
        },
        "accepted_outputs": {
            "result_artifacts": result_artifacts,
            "node_receipt_count": len(node_receipts),
            "accepted_node_count": sum(
                1 for receipt in node_receipts if receipt.get("accepted_output")
            ),
        },
        "blockers": blockers,
        "retries_recovery": {
            "attempt_count": _attempt_count(run_receipt),
            "resume": bool(run_receipt.get("resume")),
            "repair_blockers": [
                blocker
                for blocker in blockers
                if blocker.get("errors") == ["targeted_repair_required"]
            ],
        },
    }


def _create_fixture_repo(path: Path) -> str:
    path.mkdir(parents=True)
    (path / "README.md").write_text("# Tau acceptance fixture\n", encoding="utf-8")
    (path / "pyproject.toml").write_text(
        '[project]\nname = "tau-acceptance-fixture"\nversion = "0.1.0"\n',
        encoding="utf-8",
    )
    docs = path / "docs"
    docs.mkdir()
    for name in ("getting-started.md", "live-dag-viewer.md", "generic-dag-runner.md"):
        (docs / name).write_text(f"# {name}\n\nTau acceptance fixture source.\n", encoding="utf-8")
    tests = path / "tests"
    tests.mkdir()
    (tests / "test_fixture.py").write_text(
        "def test_fixture():\n    assert True\n",
        encoding="utf-8",
    )
    _run_command(["git", "init", "-q", "-b", "main", str(path)], cwd=path, env=_command_env())
    _run_command(["git", "add", "."], cwd=path, env=_command_env())
    _run_command(
        [
            "git",
            "-c",
            "user.name=Tau Acceptance",
            "-c",
            "user.email=tau-acceptance@example.invalid",
            "commit",
            "-qm",
            "fixture",
        ],
        cwd=path,
        env=_command_env(),
    )
    return _git(["rev-parse", "HEAD"], cwd=path)


def _source_state(repo: Path) -> dict[str, Any]:
    status_lines = _git(["status", "--porcelain"], cwd=repo).splitlines()
    tracked_status = [line for line in status_lines if not line.startswith("?? ")]
    untracked_status = [line for line in status_lines if line.startswith("?? ")]
    return {
        "repo": str(repo),
        "commit": _git(["rev-parse", "HEAD"], cwd=repo),
        "branch": _git(["branch", "--show-current"], cwd=repo),
        "clean": tracked_status == [],
        "tracked_clean": tracked_status == [],
        "status_porcelain": status_lines,
        "tracked_status_porcelain": tracked_status,
        "untracked_status_porcelain": untracked_status,
    }


def _git(args: list[str], *, cwd: Path) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=cwd,
        check=True,
        capture_output=True,
        text=True,
        timeout=30,
    )
    return result.stdout.strip()


def _run_command(
    command: list[str],
    *,
    cwd: Path,
    env: Mapping[str, str] | None = None,
    command_dir: Path | None = None,
    label: str | None = None,
    timeout: float = 120,
    check: bool = True,
) -> CommandEvidence:
    result = subprocess.run(
        command,
        cwd=cwd,
        env=dict(env) if env is not None else None,
        check=False,
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    if command_dir is None:
        command_dir = Path(tempfile.mkdtemp(prefix="tau-acceptance-command-"))
    command_dir.mkdir(parents=True, exist_ok=True)
    safe_label = label or f"command-{int(time.time() * 1000)}"
    stdout_path = command_dir / f"{safe_label}.stdout"
    stderr_path = command_dir / f"{safe_label}.stderr"
    stdout_path.write_text(result.stdout, encoding="utf-8")
    stderr_path.write_text(result.stderr, encoding="utf-8")
    if check and result.returncode != 0:
        raise WorkflowAcceptanceError(
            f"command failed {result.returncode}: {' '.join(command)}; stderr={stderr_path}"
        )
    return CommandEvidence(
        command=command,
        cwd=str(cwd),
        returncode=result.returncode,
        stdout_path=str(stdout_path),
        stderr_path=str(stderr_path),
    )


def _installed_env(bin_dir: Path) -> dict[str, str]:
    env = _command_env()
    env["PATH"] = f"{bin_dir}{os.pathsep}{env.get('PATH', '')}"
    env["PIP_NO_INDEX"] = "1"
    env["PIP_DISABLE_PIP_VERSION_CHECK"] = "1"
    return env


def _command_env() -> dict[str, str]:
    env = {
        key: value
        for key, value in os.environ.items()
        if key not in {"PYTHONHOME", "PYTHONPATH"}
    }
    env.setdefault("TERM", "dumb")
    env.setdefault("NO_PROXY", "127.0.0.1,localhost")
    return env


def _command_evidence_payload(evidence: CommandEvidence) -> dict[str, Any]:
    return {
        "argv": evidence.command,
        "cwd": evidence.cwd,
        "returncode": evidence.returncode,
        "stdout_path": evidence.stdout_path,
        "stderr_path": evidence.stderr_path,
    }


def _read_json_object(path: Path, label: str) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise WorkflowAcceptanceError(f"{label} unreadable: {path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise WorkflowAcceptanceError(f"{label} must be a JSON object: {path}")
    return payload


def _json_from_text(text: str) -> dict[str, Any] | None:
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        return None
    return payload if isinstance(payload, dict) else None


def _node_receipt_summaries(receipts_dir: Path) -> list[dict[str, Any]]:
    summaries: list[dict[str, Any]] = []
    if not receipts_dir.is_dir():
        return summaries
    for path in sorted(receipts_dir.glob("*.json")):
        payload = _read_json_object(path, f"node receipt {path.name}")
        summaries.append(
            {
                "path": str(path),
                "sha256": _sha256(path),
                "node_id": payload.get("node_id"),
                "status": payload.get("status"),
                "verdict": payload.get("verdict"),
                "errors": payload.get("errors"),
                "accepted_output": payload.get("accepted_output"),
            }
        )
    return summaries


def _result_artifacts(results_dir: Path) -> list[dict[str, Any]]:
    if not results_dir.is_dir():
        return []
    artifacts: list[dict[str, Any]] = []
    for path in sorted(results_dir.iterdir()):
        if path.is_file():
            artifacts.append(
                {
                    "path": str(path),
                    "relative_path": str(path.relative_to(results_dir.parent)),
                    "sha256": _sha256(path),
                    "size_bytes": path.stat().st_size,
                }
            )
    return artifacts


def _approval_blocker_summaries(transactions_dir: Path) -> list[dict[str, Any]]:
    if not transactions_dir.is_dir():
        return []
    blockers: list[dict[str, Any]] = []
    for path in sorted(transactions_dir.glob("*/approval-gate-receipt.json")):
        payload = _read_json_object(path, f"approval gate receipt {path.parent.name}")
        if payload.get("status") == "BLOCKED" or payload.get("errors"):
            target = payload.get("expected_target")
            node_id = target.get("node_id") if isinstance(target, dict) else None
            blockers.append(
                {
                    "node_id": node_id,
                    "transaction_id": path.parent.name,
                    "status": payload.get("status"),
                    "errors": payload.get("errors"),
                    "source": "approval_gate_receipt",
                    "path": str(path),
                    "sha256": _sha256(path),
                }
            )
    return blockers


def _attempt_count(run_receipt: Mapping[str, Any]) -> int | None:
    attempts = run_receipt.get("attempts")
    if isinstance(attempts, list):
        return len(attempts)
    transitions = run_receipt.get("transition_receipts")
    if isinstance(transitions, list):
        return len(transitions)
    return None


def _provider_receipt_is_live(receipt: Mapping[str, Any]) -> bool:
    return (
        receipt.get("mocked") is False
        and receipt.get("live") is True
        and receipt.get("provider_live") is True
        and receipt.get("status") == "PASS"
        and _has_successful_provider_check(receipt.get("checks"))
    )


def _has_successful_provider_check(checks: Any) -> bool:
    if not isinstance(checks, list):
        return False
    return any(
        isinstance(item, dict)
        and item.get("ok") is True
        and isinstance(item.get("status_code"), int)
        and 200 <= int(item["status_code"]) < 300
        for item in checks
    )


def _acceptance_errors(
    *,
    source: Mapping[str, Any],
    provider_live: bool,
    rungs: list[dict[str, Any]],
) -> list[str]:
    errors: list[str] = []
    if source.get("clean") is not True:
        errors.append("source_checkout_not_clean")
    if not provider_live:
        errors.append("provider_live_not_established")
    for rung in rungs:
        if rung.get("accepted_by_harness") is not True:
            errors.append(f"rung_not_accepted:{rung.get('workflow_id')}")
    return errors


def _sha256(path: Path) -> str:
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


def _utc_stamp() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")
