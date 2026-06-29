"""GitHub-ticket repair bridge for Tau self-fix loops."""

from __future__ import annotations

import json
import re
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from shutil import which
from typing import Any

from tau_coding.self_fix_repair_loop import write_coder_reviewer_repair_loop

REPAIR_REQUEST_SCHEMA = "tau.self_fix_repair_request.v1"
REPAIR_RECEIPT_SCHEMA = "tau.self_fix_ticket_repair_receipt.v1"
_JSON_BLOCK_RE = re.compile(r"```json\s*(\{.*?\})\s*```", re.DOTALL)
_HELPER = Path(
    "/home/graham/workspace/experiments/agent-skills/skills/"
    "best-practices-github-ticket/scripts/gh-ticket-tools.sh"
)


def extract_repair_request(issue_body: str) -> dict[str, Any] | None:
    """Extract a Tau repair request JSON block from a GitHub issue body."""

    for match in _JSON_BLOCK_RE.finditer(issue_body):
        try:
            payload = json.loads(match.group(1))
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict) and payload.get("schema") == REPAIR_REQUEST_SCHEMA:
            try:
                return _normalize_repair_request(payload)
            except ValueError:
                return None
    return None


def run_ticket_repair(
    *,
    repo: str,
    issue_payload: dict[str, Any],
    repo_root: Path,
    receipt_dir: Path,
    memory_base_url: str,
    scillm_base_url: str,
    model: str,
    active_goal_hash: str | None,
    apply_github: bool,
) -> dict[str, Any]:
    """Run a contract-backed repair for one GitHub issue."""

    resolved_repo = repo_root.expanduser().resolve()
    resolved_receipt_dir = receipt_dir.expanduser().resolve()
    resolved_receipt_dir.mkdir(parents=True, exist_ok=True)
    issue_number = issue_payload.get("number")
    issue_title = str(issue_payload.get("title") or f"Issue {issue_number}")
    issue_body = str(issue_payload.get("body") or "")
    request = extract_repair_request(issue_body)
    receipt: dict[str, Any] = {
        "schema": REPAIR_RECEIPT_SCHEMA,
        "ok": False,
        "status": "BLOCKED",
        "mocked": False,
        "live": True,
        "created_at": _now_iso(),
        "repo": repo,
        "issue": {
            "number": issue_number,
            "title": issue_title,
            "url": issue_payload.get("url"),
        },
        "apply_github": apply_github,
        "repair_request": request,
        "artifacts": {},
        "commands": [],
        "claims": {
            "proves": [],
            "does_not_prove": [
                "Unbounded autonomous issue processing.",
                "GitHub Actions event wiring.",
                "Semantic reviewer correctness beyond Scillm stream plus deterministic checks.",
            ],
        },
    }
    if not isinstance(issue_number, int):
        receipt["error"] = "issue_number_missing"
        return _write_and_return(resolved_receipt_dir, receipt)
    if request is None:
        receipt["error"] = "repair_request_contract_missing"
        return _write_and_return(resolved_receipt_dir, receipt)

    clean = _tracked_worktree_clean(resolved_repo)
    receipt["commands"].append(clean)
    if not clean["ok"]:
        receipt["error"] = "tracked_worktree_not_clean"
        return _write_and_return(resolved_receipt_dir, receipt)

    if apply_github:
        lease = _run_ticket_helper(["lease", str(issue_number), "--repo", repo, "--agent", "tau"])
        receipt["commands"].append(lease)
        if not lease["ok"] and "already has maintainer-active" not in lease.get("stderr", ""):
            receipt["error"] = "lease_failed"
            return _write_and_return(resolved_receipt_dir, receipt)

    loop_dir = resolved_receipt_dir / "coder-reviewer-loop"
    loop_receipt = write_coder_reviewer_repair_loop(
        repo_root=resolved_repo,
        out_dir=loop_dir,
        request=f"{repo}#{issue_number}: {issue_title}\n\n{request['request']}",
        target_file=Path(request["target_file"]),
        find_text=request["find_text"],
        replace_text=request["replace_text"],
        verification_commands=list(request["verification_commands"]),
        memory_base_url=memory_base_url,
        scillm_base_url=scillm_base_url,
        model=model,
        max_review_cycles=int(request.get("max_review_cycles", 1)),
        github_repo=repo,
        github_target=f"issue#{issue_number}",
        active_goal_hash=active_goal_hash,
    )
    receipt["artifacts"]["coder_reviewer_loop"] = str(
        loop_dir / "self-fix-coder-reviewer-loop-receipt.json"
    )
    if not loop_receipt.get("ok"):
        receipt["error"] = "coder_reviewer_loop_failed"
        return _write_and_return(resolved_receipt_dir, receipt)

    commit = _commit_and_push_repair(
        resolved_repo,
        target_file=Path(request["target_file"]),
        message=str(
            request.get("commit_message")
            or f"Resolve issue #{issue_number}: {issue_title[:64]}"
        ),
        repo=repo,
    )
    receipt["commands"].extend(commit["commands"])
    if not commit["ok"]:
        receipt["rollback"] = _rollback_failed_commit_or_push(
            resolved_repo,
            target_file=Path(request["target_file"]),
            checkpoint_head=_loop_checkpoint_head(loop_receipt),
        )
        receipt["error"] = "commit_or_push_failed"
        return _write_and_return(resolved_receipt_dir, receipt)
    receipt["commit"] = commit

    proof_path = _write_proof_markdown(
        resolved_receipt_dir,
        repo=repo,
        issue_number=issue_number,
        issue_title=issue_title,
        loop_receipt=loop_receipt,
        commit=commit,
    )
    receipt["artifacts"]["proof_markdown"] = str(proof_path)

    if apply_github:
        close = _run_ticket_helper(
            [
                "close",
                str(issue_number),
                "--repo",
                repo,
                "--proof",
                str(proof_path),
                "--reason",
                "completed",
            ]
        )
        receipt["commands"].append(close)
        if not close["ok"]:
            receipt["error"] = "ticket_close_failed"
            return _write_and_return(resolved_receipt_dir, receipt)

    receipt["ok"] = True
    receipt["status"] = "PASS"
    receipt["claims"]["proves"] = [
        "Tau extracted a schema-valid repair request from a live GitHub issue.",
        "Tau leased the issue through the guarded ticket workflow when apply_github=true.",
        "Tau ran the Memory-first streaming Scillm coder/reviewer repair loop.",
        "Tau committed and pushed the scoped repair after deterministic checks passed.",
        "Tau attached a proof comment and closed the issue when apply_github=true.",
    ]
    return _write_and_return(resolved_receipt_dir, receipt)


def _normalize_repair_request(payload: dict[str, Any]) -> dict[str, Any]:
    required = ["request", "target_file", "find_text", "replace_text", "verification_commands"]
    missing = [field for field in required if field not in payload]
    if missing:
        raise ValueError(f"repair request missing fields: {', '.join(missing)}")
    commands = payload["verification_commands"]
    if isinstance(commands, str):
        commands = [commands]
    if not isinstance(commands, list) or not all(isinstance(item, str) for item in commands):
        raise ValueError("verification_commands must be a string or string array")
    target = str(payload["target_file"])
    if Path(target).is_absolute() or ".." in Path(target).parts:
        raise ValueError("target_file must be a repo-relative path")
    return {
        **payload,
        "request": str(payload["request"]),
        "target_file": target,
        "find_text": str(payload["find_text"]),
        "replace_text": str(payload["replace_text"]),
        "verification_commands": commands,
    }


def _tracked_worktree_clean(repo_root: Path) -> dict[str, Any]:
    command = ["git", "status", "--porcelain", "--untracked-files=no"]
    completed = _run(command, cwd=repo_root, timeout=30)
    completed["ok"] = completed["exit_code"] == 0 and not completed["stdout"].strip()
    return completed


def _commit_and_push_repair(
    repo_root: Path,
    *,
    target_file: Path,
    message: str,
    repo: str,
) -> dict[str, Any]:
    commands: list[dict[str, Any]] = []
    commands.append(_run(["git", "add", str(target_file)], cwd=repo_root, timeout=30))
    commands.append(_run(["git", "commit", "-m", message], cwd=repo_root, timeout=60))
    remote = _remote_for_repo(repo_root, repo)
    branch = _current_branch(repo_root)
    commands.append(_run(["git", "push", remote, branch], cwd=repo_root, timeout=120))
    commit = _run(["git", "rev-parse", "--short", "HEAD"], cwd=repo_root, timeout=30)
    commands.append(commit)
    return {
        "ok": all(item["exit_code"] == 0 for item in commands),
        "remote": remote,
        "branch": branch,
        "commit": commit["stdout"].strip() if commit["exit_code"] == 0 else None,
        "commands": commands,
    }


def _loop_checkpoint_head(loop_receipt: dict[str, Any]) -> str | None:
    checkpoint = loop_receipt.get("checkpoint")
    if not isinstance(checkpoint, dict):
        return None
    head = checkpoint.get("head")
    return head if isinstance(head, str) and head else None


def _rollback_failed_commit_or_push(
    repo_root: Path,
    *,
    target_file: Path,
    checkpoint_head: str | None,
) -> dict[str, Any]:
    rollback: dict[str, Any] = {
        "attempted": False,
        "restored": False,
        "checkpoint_head": checkpoint_head,
        "target_file": str(target_file),
        "commands": [],
    }
    if not checkpoint_head:
        rollback["reason"] = "checkpoint_head_missing"
        return rollback

    rollback["attempted"] = True
    current_head = _run(["git", "rev-parse", "HEAD"], cwd=repo_root, timeout=30)
    rollback["commands"].append(current_head)
    if not current_head["ok"]:
        rollback["error"] = "current_head_unavailable"
        return rollback

    if current_head["stdout"].strip() != checkpoint_head:
        restore = _run(["git", "reset", "--hard", checkpoint_head], cwd=repo_root, timeout=60)
    else:
        restore = _run(
            [
                "git",
                "restore",
                "--source",
                checkpoint_head,
                "--staged",
                "--worktree",
                "--",
                str(target_file),
            ],
            cwd=repo_root,
            timeout=60,
        )
    rollback["commands"].append(restore)
    status = _run(["git", "status", "--porcelain", "--untracked-files=no"], cwd=repo_root, timeout=30)
    rollback["commands"].append(status)
    rollback["restored"] = bool(
        restore["ok"]
        and status["ok"]
        and str(target_file) not in status["stdout"]
    )
    if not rollback["restored"]:
        rollback["error"] = "tracked_target_not_restored"
    return rollback


def _remote_for_repo(repo_root: Path, repo: str) -> str:
    remotes = _run(["git", "remote", "-v"], cwd=repo_root, timeout=30)
    for line in remotes["stdout"].splitlines():
        parts = line.split()
        if len(parts) >= 2 and repo in parts[1]:
            return parts[0]
    return "origin"


def _current_branch(repo_root: Path) -> str:
    branch = _run(["git", "branch", "--show-current"], cwd=repo_root, timeout=30)
    return branch["stdout"].strip() or "main"


def _write_proof_markdown(
    receipt_dir: Path,
    *,
    repo: str,
    issue_number: int,
    issue_title: str,
    loop_receipt: dict[str, Any],
    commit: dict[str, Any],
) -> Path:
    coder = loop_receipt["cycles"][0]["coder"]["scillm_call"]
    reviewer = loop_receipt["cycles"][0]["reviewer"]["scillm_call"]
    text = "\n".join(
        [
            f"## Tau self-fix proof for {repo}#{issue_number}",
            "",
            f"Title: {issue_title}",
            "",
            "Status: evidence-backed repair applied by Tau self-fix.",
            "",
            "```text",
            f"commit: {commit.get('commit')}",
            f"loop_receipt: {receipt_dir / 'coder-reviewer-loop' / 'self-fix-coder-reviewer-loop-receipt.json'}",
            f"coder_scillm_receipt: {coder}",
            f"reviewer_scillm_receipt: {reviewer}",
            "mocked: false",
            "live: true",
            "memory_first: /intent and /recall recorded in the loop receipt",
            "scillm_streaming: coder and reviewer receipts use stream=true",
            "```",
            "",
            "Closure boundary: this proves one bounded ticket repair. It does not prove unbounded autonomous operation.",
            "",
        ]
    )
    path = receipt_dir / "proof.md"
    path.write_text(text, encoding="utf-8")
    return path


def _run_ticket_helper(args: list[str]) -> dict[str, Any]:
    if not _HELPER.exists():
        return {"ok": False, "command": [str(_HELPER), *args], "error": "ticket_helper_missing"}
    return _run([str(_HELPER), *args], cwd=Path.cwd(), timeout=120)


def _run(command: list[str], *, cwd: Path, timeout: int) -> dict[str, Any]:
    started = datetime.now(UTC)
    if command and which(command[0]) is None and command[0].startswith("git"):
        return {"ok": False, "command": command, "exit_code": 127, "error": "command_not_found"}
    completed = subprocess.run(
        command,
        cwd=cwd,
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )
    return {
        "ok": completed.returncode == 0,
        "command": command,
        "exit_code": completed.returncode,
        "stdout": completed.stdout.strip(),
        "stderr": completed.stderr.strip(),
        "duration_seconds": (datetime.now(UTC) - started).total_seconds(),
    }


def _write_and_return(receipt_dir: Path, receipt: dict[str, Any]) -> dict[str, Any]:
    path = receipt_dir / "ticket-repair-receipt.json"
    path.write_text(json.dumps(receipt, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    receipt["artifacts"]["ticket_repair_receipt"] = str(path)
    return receipt


def _now_iso() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")
