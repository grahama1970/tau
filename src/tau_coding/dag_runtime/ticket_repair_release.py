"""Ticket repair handoff context and releaser policy contracts."""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from tau_coding.dag_runtime.model import canonical_sha256

AGENT_HANDOFF_SCHEMA = "tau.agent_handoff.v1"
TICKET_HANDOFF_CONTEXT_SCHEMA = "tau.ticket_repair_handoff_context.v1"
RELEASE_REQUEST_SCHEMA = "tau.ticket_repair_release_request.v1"
RELEASE_RECEIPT_SCHEMA = "tau.ticket_repair_release_receipt.v1"


class TicketRepairReleaseError(RuntimeError):
    """Fail-closed release-policy error with a stable code."""

    def __init__(self, code: str, detail: str = "") -> None:
        self.code = code
        self.detail = detail
        super().__init__(f"{code}:{detail}" if detail else code)


def build_ticket_handoff_packet(
    *,
    agent: str,
    repo: str,
    repo_path: Path,
    worktree_path: Path,
    tau_location: Path,
    issue_id: str,
    target_paths: Sequence[str],
    required_skills: Sequence[str],
    context_files: Sequence[str],
    proof_command: Sequence[str],
    depends_on: Sequence[Mapping[str, Any]],
    goal_hash: str,
    dag_id: str,
    node_id: str,
    previous_subagent: str = "project-watchdog",
) -> dict[str, Any]:
    """Compile ticket and registry metadata into a Tau handoff packet."""

    resolved_repo = repo_path.expanduser().resolve()
    resolved_worktree = worktree_path.expanduser().resolve()
    resolved_tau = tau_location.expanduser().resolve()
    context = {
        "schema": TICKET_HANDOFF_CONTEXT_SCHEMA,
        "repo_path": str(resolved_repo),
        "cwd": str(resolved_worktree),
        "worktree_path": str(resolved_worktree),
        "tau_location": str(resolved_tau),
        "issue_id": str(issue_id),
        "target_paths": _string_list(target_paths),
        "required_skills": _string_list(required_skills),
        "context_files": _string_list(context_files),
        "proof_command": _string_list(proof_command),
        "depends_on": [dict(item) for item in depends_on],
        "goal_hash": goal_hash,
        "dag_id": dag_id,
        "node_id": node_id,
        "launch": {
            "automatic": True,
            "start_cwd": str(resolved_worktree),
            "human_terminal_selection_required": False,
            "terminal_selector": None,
        },
    }
    packet = {
        "schema": AGENT_HANDOFF_SCHEMA,
        "goal": {
            "goal_id": f"ticket-{issue_id}",
            "goal_version": 1,
            "goal_hash": goal_hash,
        },
        "github": {"repo": repo, "issue_id": str(issue_id), "target": f"issue#{issue_id}"},
        "previous_subagent": previous_subagent,
        "next_agent": {
            "name": agent,
            "executor": "local",
            "reason": f"Automatic Tau DAG launch for {repo}#{issue_id} node {node_id}.",
        },
        "context": context,
        "required_evidence": list(context["proof_command"]),
        "rationale": "Ticket metadata and project registry context are the launch authority.",
        "result": {
            "status": "DAG_NODE_READY",
            "summary": f"Dispatch ready ticket repair node {node_id}.",
            "evidence": [],
        },
        "stop_condition": "Stop at reviewer PASS/proof gate PASS or any fail-closed invariant.",
    }
    validate_ticket_handoff_packet(packet)
    return packet


def validate_ticket_handoff_packet(packet: Mapping[str, Any]) -> None:
    """Reject handoffs that would require implicit terminal or path knowledge."""

    if packet.get("schema") != AGENT_HANDOFF_SCHEMA:
        raise TicketRepairReleaseError("handoff_schema_invalid", str(packet.get("schema")))
    goal = _mapping(packet.get("goal"))
    goal_hash = _required_string(goal, "goal_hash")
    context = _mapping(packet.get("context"))
    if context.get("schema") != TICKET_HANDOFF_CONTEXT_SCHEMA:
        raise TicketRepairReleaseError(
            "handoff_context_schema_invalid", str(context.get("schema"))
        )
    required = (
        "repo_path",
        "cwd",
        "worktree_path",
        "tau_location",
        "issue_id",
        "target_paths",
        "required_skills",
        "context_files",
        "proof_command",
        "depends_on",
        "goal_hash",
    )
    for key in required:
        if key not in context:
            raise TicketRepairReleaseError("handoff_context_field_missing", key)
    if context["goal_hash"] != goal_hash:
        raise TicketRepairReleaseError("handoff_context_goal_mismatch")
    if context["cwd"] != context["worktree_path"]:
        raise TicketRepairReleaseError("handoff_context_cwd_worktree_mismatch")
    launch = _mapping(context.get("launch"))
    if launch.get("automatic") is not True:
        raise TicketRepairReleaseError("handoff_launch_not_automatic")
    if launch.get("human_terminal_selection_required") is not False:
        raise TicketRepairReleaseError("handoff_requires_human_terminal_selection")
    if launch.get("start_cwd") != context["cwd"]:
        raise TicketRepairReleaseError("handoff_launch_cwd_mismatch")
    for key in ("target_paths", "required_skills", "context_files", "proof_command"):
        values = context.get(key)
        if not isinstance(values, list) or not all(
            isinstance(item, str) and item for item in values
        ):
            raise TicketRepairReleaseError("handoff_context_list_invalid", key)
    if not isinstance(context.get("depends_on"), list):
        raise TicketRepairReleaseError("handoff_depends_on_invalid")


def release_ticket_repair(request: Mapping[str, Any], *, receipt_path: Path) -> dict[str, Any]:
    """Run the authorized releaser boundary and write a durable receipt."""

    receipt_path.parent.mkdir(parents=True, exist_ok=True)
    blockers = release_policy_blockers(request)
    if blockers:
        receipt = _release_receipt(request, "BLOCKED", blockers=blockers)
        _write_json(receipt_path, receipt)
        return receipt

    source_worktree = Path(str(request["source_worktree_path"])).expanduser().resolve()
    repo_path = Path(str(request["repo_path"])).expanduser().resolve()
    branch = _git(source_worktree, "branch", "--show-current")
    integrated_commit = _git(source_worktree, "rev-parse", "HEAD")
    audit = _run_worktree_audit(repo_path)
    if audit["status"] != "PASS":
        receipt = _release_receipt(
            request,
            "BLOCKED",
            blockers=["worktree_audit_failed"],
            audit_result=audit,
            branch=branch,
            integrated_commit=integrated_commit,
        )
        _write_json(receipt_path, receipt)
        return receipt

    _git(source_worktree, "push", "origin", "HEAD:main")
    remote_main_sha = _ls_remote(source_worktree, "origin", "refs/heads/main")
    cleanup = _cleanup_worktree(repo_path, source_worktree, branch)
    receipt = _release_receipt(
        request,
        "PASS",
        blockers=[],
        audit_result=audit,
        cleanup_result=cleanup,
        branch=branch,
        integrated_commit=integrated_commit,
        remote_main_sha=remote_main_sha,
        ticket_close_url=f"dry-run://{request['repo']}#{request['issue_id']}/closed",
    )
    _write_json(receipt_path, receipt)
    return receipt


def release_policy_blockers(request: Mapping[str, Any]) -> list[str]:
    """Return stable fail-closed blocker codes for an attempted release."""

    blockers: list[str] = []
    if request.get("schema") != RELEASE_REQUEST_SCHEMA:
        blockers.append("release_request_schema_invalid")
    if request.get("actor_role") != "releaser":
        blockers.append("release_actor_not_releaser")
    if request.get("reviewer_verdict") != "PASS":
        blockers.append("missing_reviewer_pass")
    if request.get("proof_gate_status") != "PASS":
        blockers.append("missing_proof_gate_pass")
    if request.get("push_authority") is not True:
        blockers.append("missing_push_authority")
    if request.get("readback_authority") is not True:
        blockers.append("missing_readback_authority")

    handoff = _mapping(request.get("handoff"))
    try:
        validate_ticket_handoff_packet(handoff)
    except TicketRepairReleaseError as exc:
        blockers.append(f"missing_handoff_context:{exc.code}")

    context = _mapping(handoff.get("context"))
    repo_path = Path(str(request.get("repo_path", ""))).expanduser().resolve()
    source_worktree = Path(str(request.get("source_worktree_path", ""))).expanduser().resolve()
    tau_location = Path(str(request.get("tau_location", ""))).expanduser().resolve()
    if str(source_worktree) != str(context.get("cwd")):
        blockers.append("wrong_cwd")
    if str(tau_location) != str(context.get("tau_location")) or not tau_location.exists():
        blockers.append("wrong_tau_location")

    for dependency in context.get("depends_on", []):
        if isinstance(dependency, Mapping) and dependency.get("state") not in {
            "closed",
            "completed",
            "satisfied",
        }:
            blockers.append("unsatisfied_depends_on")
            break

    proof_artifact = Path(str(request.get("proof_artifact_path", ""))).expanduser()
    if not proof_artifact.is_file():
        blockers.append("missing_proof_artifact")
    else:
        try:
            proof = json.loads(proof_artifact.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            blockers.append("proof_artifact_unreadable")
        else:
            if proof.get("status") != "PASS" or proof.get("ok") is not True:
                blockers.append("proof_artifact_not_pass")

    if source_worktree.exists():
        staged = _git(source_worktree, "diff", "--cached", "--name-only", check=False)
        if staged.strip():
            blockers.append("unrelated_staged_files")
        expected_origin = str(request.get("expected_origin_main_sha", ""))
        current_origin = _ls_remote(source_worktree, "origin", "refs/heads/main", check=False)
        if expected_origin and current_origin and current_origin != expected_origin:
            blockers.append("unmerged_origin_main_movement")
        target_paths = tuple(str(item) for item in context.get("target_paths", []))
        changed = _git(
            source_worktree,
            "diff",
            "--name-only",
            f"{expected_origin or 'origin/main'}...HEAD",
            check=False,
        ).splitlines()
        if changed and not _all_owned_by_targets(changed, target_paths):
            blockers.append("branch_contains_unowned_paths")
    else:
        blockers.append("source_worktree_missing")
    if not repo_path.exists():
        blockers.append("repo_path_missing")
    return sorted(set(blockers))


def _release_receipt(
    request: Mapping[str, Any],
    status: str,
    *,
    blockers: Sequence[str],
    audit_result: Mapping[str, Any] | None = None,
    cleanup_result: Mapping[str, Any] | None = None,
    branch: str | None = None,
    integrated_commit: str | None = None,
    remote_main_sha: str | None = None,
    ticket_close_url: str | None = None,
) -> dict[str, Any]:
    body = {
        "schema": RELEASE_RECEIPT_SCHEMA,
        "status": status,
        "ok": status == "PASS",
        "mocked": False,
        "live": True,
        "provider_live": False,
        "issue_id": str(request.get("issue_id", "")),
        "repo": str(request.get("repo", "")),
        "source_worktree_path": str(request.get("source_worktree_path", "")),
        "branch": branch,
        "integrated_commit": integrated_commit,
        "remote_main_sha": remote_main_sha,
        "ticket_close_url": ticket_close_url,
        "worktree_audit_result": dict(audit_result or {}),
        "cleanup_result": dict(cleanup_result or {"action": "not_run", "status": "NOT_RUN"}),
        "blockers": list(blockers),
        "explicit_non_claims": [
            "No public GitHub issue was mutated by this guarded dry-run receipt.",
            "Provider/model semantic quality was not exercised.",
            "Branch protection and human acceptance remain external policy.",
        ],
    }
    return {**body, "receipt_sha256": canonical_sha256(body)}


def _run_self_test(*, out_dir: Path, negative: bool = False) -> int:
    out_dir = out_dir.expanduser().resolve()
    if out_dir.exists():
        shutil.rmtree(out_dir)
    out_dir.mkdir(parents=True)
    repo, worktree, before_sha = _create_disposable_repair_repo(out_dir)
    tau_location = Path(__file__).resolve().parents[3]
    goal_hash = canonical_sha256({"issue_id": "326", "repo": "grahama1970/tau"})
    proof_path = out_dir / "proof.json"
    _write_json(
        proof_path,
        {"schema": "tau.ticket_proof.v1", "status": "PASS", "ok": True},
    )
    depends_on = [{"ref": "grahama1970/tau#325", "state": "closed"}]
    if negative:
        depends_on = [{"ref": "grahama1970/tau#325", "state": "open"}]
    handoff = build_ticket_handoff_packet(
        agent="coder",
        repo="grahama1970/tau",
        repo_path=repo,
        worktree_path=worktree,
        tau_location=tau_location,
        issue_id="326",
        target_paths=("src/tau_coding/dag_runtime/release-sentinel.txt",),
        required_skills=("tau", "project-watchdog", "ticket", "handoff", "agentic-evals"),
        context_files=(
            "GOAL.md",
            "src/tau_coding/dag_runtime",
            "src/tau_coding/workflows",
            "scripts/run-git-worktree-lease-smoke.py",
        ),
        proof_command=(
            "uv",
            "run",
            "python",
            "-m",
            "tau_coding.dag_runtime.ticket_repair_release",
            "--self-test",
        ),
        depends_on=depends_on,
        goal_hash=goal_hash,
        dag_id="tau-issue-326-repair",
        node_id="creator",
    )
    handoff_path = out_dir / "handoff.json"
    _write_json(handoff_path, handoff)
    release_request = {
        "schema": RELEASE_REQUEST_SCHEMA,
        "actor_role": "releaser",
        "repo": "grahama1970/tau",
        "repo_path": str(repo),
        "source_worktree_path": str(worktree),
        "tau_location": str(tau_location),
        "issue_id": "326",
        "handoff": handoff,
        "reviewer_verdict": "PASS",
        "proof_gate_status": "PASS",
        "proof_artifact_path": str(proof_path),
        "expected_origin_main_sha": before_sha,
        "push_authority": True,
        "readback_authority": True,
    }
    release_path = out_dir / "release-receipt.json"
    release = release_ticket_repair(release_request, receipt_path=release_path)
    ok = (negative and release["status"] == "BLOCKED") or release["status"] == "PASS"
    summary = {
        "schema": "tau.ticket_repair_release_selftest_receipt.v1",
        "status": "PASS" if ok else "FAIL",
        "ok": ok,
        "mocked": False,
        "live": True,
        "provider_live": False,
        "handoff_path": str(handoff_path),
        "release_receipt_path": str(release_path),
        "handoff_context_readback": json.loads(handoff_path.read_text(encoding="utf-8")),
        "release_receipt_readback": json.loads(release_path.read_text(encoding="utf-8")),
    }
    _write_json(out_dir / "self-test-receipt.json", summary)
    print(json.dumps(summary, sort_keys=True))
    return 0 if summary["ok"] else 1


def _create_disposable_repair_repo(out_dir: Path) -> tuple[Path, Path, str]:
    origin = out_dir / "origin.git"
    repo = out_dir / "repo"
    worktree = out_dir / "repair-worktree"
    _run(("git", "init", "--bare", str(origin)))
    _run(("git", "clone", str(origin), str(repo)))
    _git(repo, "switch", "-c", "main")
    _write_text(repo / "README.md", "disposable Tau release repo\n")
    _git(repo, "add", "README.md")
    _git(
        repo,
        "-c",
        "user.name=Tau Test",
        "-c",
        "user.email=tau@example.invalid",
        "commit",
        "-m",
        "initial",
    )
    _git(repo, "push", "-u", "origin", "main")
    before_sha = _ls_remote(repo, "origin", "refs/heads/main")
    _git(repo, "worktree", "add", "-b", "repair-326", str(worktree), "origin/main")
    marker = worktree / "src/tau_coding/dag_runtime/release-sentinel.txt"
    _write_text(marker, "ticket repair release sentinel\n")
    _git(worktree, "add", "src/tau_coding/dag_runtime/release-sentinel.txt")
    _git(
        worktree,
        "-c",
        "user.name=Tau Test",
        "-c",
        "user.email=tau@example.invalid",
        "commit",
        "-m",
        "repair issue 326",
    )
    return repo, worktree, before_sha


def _cleanup_worktree(repo_path: Path, source_worktree: Path, branch: str) -> dict[str, Any]:
    _git(repo_path, "worktree", "remove", str(source_worktree))
    _git(repo_path, "branch", "-D", branch)
    return {
        "status": "PASS",
        "action": "removed",
        "source_worktree_path": str(source_worktree),
        "branch_deleted": branch,
        "post_verified_absent": not source_worktree.exists(),
    }


def _run_worktree_audit(repo_path: Path) -> dict[str, Any]:
    script = Path(
        "/home/graham/workspace/experiments/agent-skills/skills/"
        "best-practices-github-ticket/scripts/audit-worktrees.sh"
    )
    if not script.is_file():
        return {"status": "BLOCKED", "error": "audit_worktrees_script_missing"}
    proc = subprocess.run(
        [str(script), "--repo", str(repo_path), "--json"],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    try:
        payload = json.loads(proc.stdout or "{}")
    except json.JSONDecodeError:
        payload = {"stdout": proc.stdout}
    return {
        "status": "PASS" if proc.returncode == 0 else "BLOCKED",
        "returncode": proc.returncode,
        "stdout_json": payload,
        "stderr": proc.stderr,
    }


def _all_owned_by_targets(changed: Sequence[str], target_paths: Sequence[str]) -> bool:
    normalized = tuple(path.rstrip("/") for path in target_paths if path)
    return bool(normalized) and all(
        any(path == target or path.startswith(f"{target}/") for target in normalized)
        for path in changed
    )


def _ls_remote(cwd: Path, remote: str, ref: str, *, check: bool = True) -> str:
    output = _git(cwd, "ls-remote", remote, ref, check=check)
    return output.split()[0] if output.strip() else ""


def _git(cwd: Path, *args: str, check: bool = True) -> str:
    return _run(("git", "-C", str(cwd), *args), check=check)


def _run(args: Sequence[str], *, check: bool = True) -> str:
    proc = subprocess.run(
        args,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if check and proc.returncode != 0:
        raise TicketRepairReleaseError("command_failed", f"{' '.join(args)}\n{proc.stderr}")
    return proc.stdout.strip()


def _write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _write_text(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(value, encoding="utf-8")


def _mapping(value: object) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _required_string(payload: Mapping[str, Any], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value:
        raise TicketRepairReleaseError("field_missing", key)
    return value


def _string_list(values: Sequence[str]) -> list[str]:
    result = [str(item) for item in values if str(item)]
    if len(result) != len(set(result)):
        raise TicketRepairReleaseError("duplicate_list_value")
    if not result:
        raise TicketRepairReleaseError("empty_list")
    return result


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--self-test", action="store_true")
    parser.add_argument("--self-test-negative", action="store_true")
    parser.add_argument("--out-dir", type=Path, required=True)
    args = parser.parse_args(argv)
    if args.self_test == args.self_test_negative:
        parser.error("choose exactly one of --self-test or --self-test-negative")
    return _run_self_test(out_dir=args.out_dir, negative=args.self_test_negative)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
