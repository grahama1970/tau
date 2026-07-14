"""Hash-bound ownership for per-attempt Git worktrees."""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import secrets
import selectors
import signal
import stat
import subprocess
import time
from collections.abc import Iterator, Mapping
from contextlib import contextmanager, suppress
from datetime import UTC, datetime, timedelta
from pathlib import Path, PurePosixPath
from typing import Any

from tau_coding.dag_runtime.model import FrozenJson, canonical_json, canonical_sha256
from tau_coding.runtime_backends.contracts import GitWorktreeLease

WORKTREE_INSPECTION_RECEIPT_SCHEMA = "tau.git_worktree_inspection_receipt.v1"
WORKTREE_ADMISSION_SCHEMA = "tau.git_worktree_admission.v1"
WORKTREE_CLEANUP_AUTHORIZATION_SCHEMA = "tau.git_worktree_cleanup_authorization.v1"
WORKTREE_CLEANUP_RECEIPT_SCHEMA = "tau.git_worktree_cleanup_receipt.v1"
WORKTREE_LEASE_RECORD_SCHEMA = "tau.git_worktree_lease_record.v1"
WORKTREE_ALLOCATION_INTENT_SCHEMA = "tau.git_worktree_allocation_intent.v1"
WORKTREE_ALLOCATION_INTENT_RECORD_SCHEMA = "tau.git_worktree_allocation_intent_record.v1"
DEFAULT_MAX_HASH_BYTES = 64 * 1024 * 1024
DEFAULT_MAX_HASH_ENTRIES = 10_000
DEFAULT_MAX_VALIDATION_ENTRIES = 250_000
DEFAULT_GIT_TIMEOUT_SECONDS = 30.0


class GitWorktreeLeaseError(RuntimeError):
    """Fail-closed worktree lease error with a stable code."""

    def __init__(self, code: str, detail: str = "") -> None:
        self.code = code
        self.detail = detail
        super().__init__(f"{code}:{detail}" if detail else code)


class GitWorktreeLeaseManager:
    """Allocate, inspect, rediscover, admit, and clean real Git worktrees."""

    def __init__(
        self,
        root: Path,
        *,
        owner: str = "tau",
        max_hash_bytes: int = DEFAULT_MAX_HASH_BYTES,
        max_hash_entries: int = DEFAULT_MAX_HASH_ENTRIES,
        max_validation_entries: int = DEFAULT_MAX_VALIDATION_ENTRIES,
    ) -> None:
        configured_root = root.expanduser().absolute()
        if configured_root.is_symlink():
            raise GitWorktreeLeaseError("worktree_state_root_symlink_forbidden", str(root))
        self.root = configured_root.resolve()
        self.owner = _required_text(owner, "owner")
        if type(max_hash_bytes) is not int or max_hash_bytes < 1:
            raise GitWorktreeLeaseError("worktree_invalid_hash_byte_limit")
        if type(max_hash_entries) is not int or max_hash_entries < 1:
            raise GitWorktreeLeaseError("worktree_invalid_hash_entry_limit")
        if type(max_validation_entries) is not int or max_validation_entries < 1:
            raise GitWorktreeLeaseError("worktree_invalid_validation_entry_limit")
        self.max_hash_bytes = max_hash_bytes
        self.max_hash_entries = max_hash_entries
        self.max_validation_entries = max_validation_entries
        self.worktrees_dir = self.root / "worktrees"
        self.leases_dir = self.root / "leases"
        self.admissions_dir = self.root / "admissions"
        self.intents_dir = self.root / "allocation-intents"
        self.retired_dir = self.root / "retired-leases"
        self._lock_path = self.root / ".lease.lock"

    def allocate(
        self,
        *,
        repository: Path,
        run_id: str,
        plan_revision: str,
        node_id: str,
        attempt_id: str,
        base_commit: str,
        allowed_paths: tuple[str, ...],
        expires_in_seconds: int = 3600,
    ) -> GitWorktreeLease:
        identity = {
            "run_id": _required_text(run_id, "run_id"),
            "plan_revision": _required_text(plan_revision, "plan_revision"),
            "node_id": _required_text(node_id, "node_id"),
            "attempt_id": _required_text(attempt_id, "attempt_id"),
        }
        if type(expires_in_seconds) is not int or expires_in_seconds < 1:
            raise GitWorktreeLeaseError("worktree_invalid_expiry")
        normalized_paths = _normalize_allowed_paths(allowed_paths)
        source_checkout = _repository_root(repository)
        repo = _repository_identity(source_checkout)
        _validate_repository_git_drivers(repo)
        _require_state_root_outside_worktrees(self.root, repo)
        self._ensure_storage()
        resolved_base = _git(
            source_checkout, "rev-parse", "--verify", f"{base_commit}^{{commit}}"
        )
        lease_key = canonical_sha256({**identity, "repository": str(repo)}).removeprefix("sha256:")
        lease_path = self.leases_dir / f"{lease_key}.json"
        worktree_path = self.worktrees_dir / lease_key
        intent_path = self.intents_dir / f"{lease_key}.json"
        with self._locked():
            now = datetime.now(UTC)
            intent: dict[str, Any] = {
                "schema": WORKTREE_ALLOCATION_INTENT_SCHEMA,
                **identity,
                "repository": str(repo),
                "worktree_path": str(worktree_path),
                "base_commit": resolved_base,
                "allowed_paths": list(normalized_paths),
                "owner": self.owner,
                "created_at": now.isoformat(),
                "expires_at": (now + timedelta(seconds=expires_in_seconds)).isoformat(),
                "repository_control_sha256": _repository_control_sha256(
                    repo,
                    max_bytes=DEFAULT_MAX_HASH_BYTES,
                    max_entries=DEFAULT_MAX_VALIDATION_ENTRIES,
                ),
            }
            recovered = self._recover_allocation_intent(intent_path, lease_path, intent)
            if recovered is not None:
                return recovered
            self._retire_missing_worktree_lease(lease_path)
            if tuple(self.retired_dir.glob(f"{lease_key}-*.json")):
                raise GitWorktreeLeaseError("worktree_attempt_retired", attempt_id)
            if lease_path.exists() or worktree_path.exists():
                raise GitWorktreeLeaseError("worktree_attempt_already_leased", attempt_id)
            if _registered_worktree_paths(repo) & {worktree_path.resolve()}:
                raise GitWorktreeLeaseError("worktree_path_already_registered", str(worktree_path))
            _atomic_json_write(
                intent_path,
                {
                    "schema": WORKTREE_ALLOCATION_INTENT_RECORD_SCHEMA,
                    "intent": intent,
                    "intent_sha256": canonical_sha256(intent),
                },
            )
            try:
                _git(repo, "worktree", "add", "--detach", str(worktree_path), resolved_base)
                worktree_path.chmod(0o700)
                actual_root = _repository_root(worktree_path)
                if actual_root != worktree_path.resolve():
                    raise GitWorktreeLeaseError(
                        "worktree_root_mismatch", f"{actual_root}!={worktree_path}"
                    )
                if actual_root == _primary_worktree(repo):
                    raise GitWorktreeLeaseError("worktree_primary_checkout_forbidden")
                _validate_worktree_no_symlinks(actual_root, self.max_validation_entries)
                _validate_allowed_targets(actual_root, normalized_paths)
                head_commit = _git(actual_root, "rev-parse", "HEAD")
                branch = _symbolic_branch(actual_root)
                status = _git_bytes(
                    actual_root, "status", "--porcelain=v1", "-z", "--untracked-files=all"
                )
                if status:
                    raise GitWorktreeLeaseError("worktree_allocation_not_clean")
                current_repository_control_sha256 = _repository_control_sha256(
                    repo,
                    max_bytes=DEFAULT_MAX_HASH_BYTES,
                    max_entries=DEFAULT_MAX_VALIDATION_ENTRIES,
                )
                if current_repository_control_sha256 != intent["repository_control_sha256"]:
                    raise GitWorktreeLeaseError(
                        "worktree_repository_control_changed_during_allocation"
                    )
                checkout_directory_mode = _measure_checkout_directory_mode(actual_root)
                worktree_gitfile_sha256 = _worktree_gitfile_sha256(actual_root, repo)
                worktree_admin_dir = _worktree_admin_dir_path(actual_root)
                worktree_admin_stat = worktree_admin_dir.stat()
                intent = {
                    **intent,
                    "worktree_admin_dir": str(worktree_admin_dir),
                    "worktree_admin_device": worktree_admin_stat.st_dev,
                    "worktree_admin_inode": worktree_admin_stat.st_ino,
                }
                _atomic_json_write(
                    intent_path,
                    {
                        "schema": WORKTREE_ALLOCATION_INTENT_RECORD_SCHEMA,
                        "intent": intent,
                        "intent_sha256": canonical_sha256(intent),
                    },
                )
                lease = GitWorktreeLease(
                    **identity,
                    repository=str(repo),
                    worktree_path=str(actual_root),
                    base_commit=resolved_base,
                    head_commit=head_commit,
                    branch=branch,
                    detached=branch is None,
                    allowed_paths=normalized_paths,
                    owner=self.owner,
                    created_at=now.isoformat(),
                    expires_at=(now + timedelta(seconds=expires_in_seconds)).isoformat(),
                    pre_status_sha256=_bytes_sha256(status),
                    cleanup_policy=FrozenJson.from_value(
                        {
                            "unadmitted_changes": "block",
                            "explicit_discard_authorization": "required",
                            "remove_registered_worktree": True,
                            "repository_control_sha256": intent[
                                "repository_control_sha256"
                            ],
                            "repository_control_sha256_at_intent": intent[
                                "repository_control_sha256"
                            ],
                            "checkout_directory_mode": checkout_directory_mode,
                            "worktree_gitfile_sha256": worktree_gitfile_sha256,
                            "worktree_admin_dir": str(worktree_admin_dir),
                            "worktree_admin_device": worktree_admin_stat.st_dev,
                            "worktree_admin_inode": worktree_admin_stat.st_ino,
                        }
                    ),
                )
                _atomic_json_write(
                    lease_path,
                    {
                        "schema": WORKTREE_LEASE_RECORD_SCHEMA,
                        "lease": lease.to_payload(),
                        "lease_sha256": lease.sha256,
                    },
                )
                _durable_unlink(intent_path)
                return lease
            except Exception:
                # Once the lease record is durable, the allocation is owned and
                # restart-recoverable. Never roll its worktree back merely because
                # best-effort intent cleanup failed afterward.
                if lease_path.exists():
                    raise
                if worktree_path.exists() or worktree_path.resolve() in _registered_worktree_paths(
                    repo
                ):
                    _run_git(repo, "worktree", "remove", "--force", str(worktree_path))
                if (
                    not worktree_path.exists()
                    and worktree_path.resolve() not in _registered_worktree_paths(repo)
                ):
                    _durable_unlink(intent_path)
                raise

    def rediscover(self, *, run_id: str | None = None) -> tuple[GitWorktreeLease, ...]:
        leases: list[GitWorktreeLease] = []
        self._ensure_storage()
        with self._locked():
            self._recover_allocation_intents()
            for path in sorted(self.leases_dir.glob("*.json")):
                if path.is_symlink():
                    raise GitWorktreeLeaseError("worktree_lease_symlink_forbidden", str(path))
                if self._retire_missing_worktree_lease(path):
                    continue
                lease = _load_registered_lease(path)
                if run_id is not None and lease.run_id != run_id:
                    continue
                self._assert_registered(lease, allow_expired=True)
                leases.append(lease)
        return tuple(leases)

    def inspect(self, lease: GitWorktreeLease) -> dict[str, Any]:
        with self._writer_lock(lease, blocking=False):
            self._assert_registered(lease)
            return self._inspect_registered(lease)

    def inspect_for_reclamation(self, lease: GitWorktreeLease) -> dict[str, Any]:
        """Inspect an expired owned lease only to build exact cleanup authorization."""

        with self._writer_lock(lease, blocking=False):
            inspection = self._inspect_registered(lease, allow_expired=True)
            return {**inspection, "reclamation_only": inspection["expired"]}

    @contextmanager
    def writer_guard(self, lease: GitWorktreeLease) -> Iterator[Path]:
        """Grant one Tau writer exclusive mutation ownership for this lease."""

        with self._writer_lock(lease, blocking=False):
            self._assert_registered(lease)
            _validate_worktree_no_symlinks(
                Path(lease.worktree_path), self.max_validation_entries
            )
            _validate_allowed_targets(Path(lease.worktree_path), lease.allowed_paths)
            yield Path(lease.worktree_path)

    def _inspect_registered(
        self, lease: GitWorktreeLease, *, allow_expired: bool = False
    ) -> dict[str, Any]:
        self._assert_registered(lease, allow_expired=allow_expired)
        worktree = Path(lease.worktree_path)
        _validate_repository_git_drivers(worktree)
        status, status_truncated = _git_bytes_limited(
            worktree,
            "status",
            "--porcelain=v1",
            "-z",
            "--untracked-files=all",
            max_bytes=self.max_hash_bytes,
        )
        changed_paths, path_errors = _changed_paths(
            worktree,
            self.max_hash_entries,
            self.max_hash_bytes,
            self.max_validation_entries,
        )
        cleanup_policy = lease.cleanup_policy.to_value()
        checkout_directory_mode = (
            cleanup_policy.get("checkout_directory_mode")
            if isinstance(cleanup_policy, dict)
            else None
        )
        if type(checkout_directory_mode) is not int:
            path_errors = (*path_errors, "worktree_directory_mode_baseline_missing")
        else:
            mode_paths, mode_paths_truncated = _nondefault_directory_mode_paths(
                worktree,
                expected_mode=checkout_directory_mode,
                max_entries=self.max_validation_entries,
            )
            changed_paths = tuple(sorted(set(changed_paths) | set(mode_paths)))
            if mode_paths_truncated:
                path_errors = (*path_errors, "worktree_validation_entry_limit_exceeded")
        if status_truncated:
            path_errors = (*path_errors, "worktree_hash_byte_limit_exceeded")
        disallowed_paths = tuple(
            path for path in changed_paths if not _path_allowed(path, lease.allowed_paths)
        )
        escaped_paths = tuple(
            path for path in changed_paths if not _path_stays_within(worktree, path)
        )
        diff_sha256, hash_errors, hash_complete = _worktree_diff_sha256(
            worktree,
            changed_paths,
            status,
            max_bytes=self.max_hash_bytes,
            max_entries=self.max_hash_entries,
        )
        repository_control_sha256: str | None = None
        try:
            repository_control_sha256 = _repository_control_sha256(
                Path(lease.repository),
                max_bytes=DEFAULT_MAX_HASH_BYTES,
                max_entries=DEFAULT_MAX_VALIDATION_ENTRIES,
            )
        except GitWorktreeLeaseError as exc:
            hash_errors = (*hash_errors, exc.code)
            hash_complete = False
        expected_repository_control_sha256 = (
            cleanup_policy.get("repository_control_sha256")
            if isinstance(cleanup_policy, dict)
            else None
        )
        if not isinstance(expected_repository_control_sha256, str):
            hash_errors = (*hash_errors, "worktree_repository_control_hash_missing")
            hash_complete = False
        elif repository_control_sha256 != expected_repository_control_sha256:
            hash_errors = (*hash_errors, "worktree_repository_control_changed")
        hash_complete = hash_complete and not path_errors
        head_commit = _git(worktree, "rev-parse", "HEAD")
        branch = _symbolic_branch(worktree)
        diff_sha256 = canonical_sha256(
            {
                "content_diff_sha256": diff_sha256,
                "head_commit": head_commit,
                "branch": branch,
                "repository_control_sha256": repository_control_sha256,
            }
        )
        errors = [*(f"worktree_path_not_allowed:{path}" for path in disallowed_paths)]
        errors.extend(f"worktree_path_escape:{path}" for path in escaped_paths)
        errors.extend(path_errors)
        errors.extend(hash_errors)
        if head_commit != lease.head_commit:
            errors.append("worktree_head_changed")
        if (branch is None) != lease.detached or branch != lease.branch:
            errors.append("worktree_branch_identity_changed")
        return {
            "schema": WORKTREE_INSPECTION_RECEIPT_SCHEMA,
            "status": "BLOCKED" if errors else "PASS",
            "ok": not errors,
            "lease_sha256": lease.sha256,
            "run_id": lease.run_id,
            "node_id": lease.node_id,
            "attempt_id": lease.attempt_id,
            "repository": lease.repository,
            "worktree_path": lease.worktree_path,
            "base_commit": lease.base_commit,
            "head_commit": head_commit,
            "branch": branch,
            "detached": branch is None,
            "pre_status_sha256": lease.pre_status_sha256,
            "post_status_sha256": _bytes_sha256(status),
            "diff_sha256": diff_sha256,
            "hash_complete": hash_complete,
            "repository_control_sha256": repository_control_sha256,
            "expired": _lease_expired(lease),
            "dirty": bool(status) or bool(changed_paths),
            "changed_paths": list(changed_paths),
            "disallowed_paths": list(disallowed_paths),
            "escaped_paths": list(escaped_paths),
            "errors": errors,
        }

    def admit(self, lease: GitWorktreeLease) -> dict[str, Any]:
        with self._locked(), self._writer_lock(lease, blocking=False):
            inspection = self._inspect_registered(lease)
            if inspection["status"] != "PASS":
                raise GitWorktreeLeaseError("worktree_admission_inspection_blocked")
            payload = {
                "schema": WORKTREE_ADMISSION_SCHEMA,
                "status": "PASS",
                "lease_sha256": lease.sha256,
                "diff_sha256": inspection["diff_sha256"],
                "changed_paths": inspection["changed_paths"],
                "admitted_at": datetime.now(UTC).isoformat(),
            }
            _atomic_json_write(self._admission_path(lease), payload)
            return payload

    def cleanup(
        self,
        lease: GitWorktreeLease,
        *,
        discard_authorization: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        with self._locked():
            try:
                with self._writer_lock(lease, blocking=False):
                    return self._cleanup_exclusive(lease, discard_authorization)
            except GitWorktreeLeaseError as exc:
                if exc.code != "worktree_writer_active":
                    raise
                return {
                    "schema": WORKTREE_CLEANUP_RECEIPT_SCHEMA,
                    "status": "BLOCKED",
                    "ok": False,
                    "lease_sha256": lease.sha256,
                    "diff_sha256": None,
                    "hash_complete": None,
                    "dirty": None,
                    "changed_paths": [],
                    "removed": False,
                    "post_verified_absent": False,
                    "errors": ["worktree_writer_active"],
                }

    def _cleanup_exclusive(
        self,
        lease: GitWorktreeLease,
        discard_authorization: Mapping[str, Any] | None,
    ) -> dict[str, Any]:
        inspection = self._inspect_registered(lease, allow_expired=True)
        admission = self._load_admission(lease)
        admitted = bool(
            admission
            and admission.get("lease_sha256") == lease.sha256
            and admission.get("diff_sha256") == inspection["diff_sha256"]
        )
        discard_authorized = _valid_discard_authorization(
            discard_authorization,
            lease,
            str(inspection["diff_sha256"]),
            hash_complete=bool(inspection["hash_complete"]),
        )
        if inspection["expired"] and not discard_authorized:
            return _cleanup_receipt(
                lease,
                inspection,
                status="BLOCKED",
                removed=False,
                errors=("worktree_expired_reclamation_authorization_required",),
            )
        if "worktree_repository_control_changed" in inspection["errors"]:
            return _cleanup_receipt(
                lease,
                inspection,
                status="BLOCKED",
                removed=False,
                errors=("worktree_repository_control_restore_required",),
            )
        if inspection["status"] != "PASS" and not discard_authorized:
            return _cleanup_receipt(
                lease,
                inspection,
                status="BLOCKED",
                removed=False,
                errors=("worktree_inspection_blocked",),
            )
        if inspection["dirty"] and not admitted and not discard_authorized:
            return _cleanup_receipt(
                lease,
                inspection,
                status="BLOCKED",
                removed=False,
                errors=("worktree_unadmitted_changes",),
            )
        repo = Path(lease.repository)
        _validate_lease_admin_identity(lease)
        result = _run_git(repo, "worktree", "remove", "--force", lease.worktree_path)
        if result.returncode != 0:
            return _cleanup_receipt(
                lease,
                inspection,
                status="BLOCKED",
                removed=False,
                errors=(f"worktree_remove_failed:{result.stderr.strip()}",),
            )
        absent = not Path(lease.worktree_path).exists() and Path(
            lease.worktree_path
        ).resolve() not in _registered_worktree_paths(repo)
        if not absent:
            return _cleanup_receipt(
                lease,
                inspection,
                status="BLOCKED",
                removed=False,
                errors=("worktree_cleanup_absence_not_verified",),
            )
        self._retire_lease_record(lease)
        _durable_unlink(self._admission_path(lease))
        return _cleanup_receipt(lease, inspection, status="PASS", removed=True, errors=())

    def _assert_registered(
        self, lease: GitWorktreeLease, *, allow_expired: bool = False
    ) -> None:
        lease_path = self._lease_path(lease)
        if not lease_path.is_file() or lease_path.is_symlink():
            raise GitWorktreeLeaseError("worktree_lease_not_registered", lease.attempt_id)
        stored = _load_registered_lease(lease_path)
        if stored.sha256 != lease.sha256:
            raise GitWorktreeLeaseError("worktree_lease_hash_mismatch", lease.attempt_id)
        if stored.owner != self.owner:
            raise GitWorktreeLeaseError("worktree_lease_owner_mismatch", lease.attempt_id)
        if _lease_expired(stored) and not allow_expired:
            raise GitWorktreeLeaseError("worktree_lease_expired", lease.attempt_id)
        worktree = Path(lease.worktree_path)
        if not worktree.is_dir() or worktree.is_symlink():
            raise GitWorktreeLeaseError("worktree_missing_or_symlinked", str(worktree))
        cleanup_policy = lease.cleanup_policy.to_value()
        expected_gitfile_sha256 = (
            cleanup_policy.get("worktree_gitfile_sha256")
            if isinstance(cleanup_policy, dict)
            else None
        )
        if not isinstance(expected_gitfile_sha256, str):
            raise GitWorktreeLeaseError("worktree_gitfile_hash_missing", str(worktree))
        if _worktree_gitfile_sha256(worktree, Path(lease.repository)) != expected_gitfile_sha256:
            raise GitWorktreeLeaseError("worktree_gitfile_changed", str(worktree))
        _validate_lease_admin_identity(lease)
        if _repository_root(worktree) != worktree.resolve():
            raise GitWorktreeLeaseError("worktree_root_mismatch", str(worktree))
        if worktree.resolve() not in _registered_worktree_paths(Path(lease.repository)):
            raise GitWorktreeLeaseError("worktree_not_registered_with_git", str(worktree))

    def _lease_path(self, lease: GitWorktreeLease) -> Path:
        key = canonical_sha256(
            {
                "run_id": lease.run_id,
                "plan_revision": lease.plan_revision,
                "node_id": lease.node_id,
                "attempt_id": lease.attempt_id,
                "repository": lease.repository,
            }
        ).removeprefix("sha256:")
        return self.leases_dir / f"{key}.json"

    def _admission_path(self, lease: GitWorktreeLease) -> Path:
        return self.admissions_dir / f"{lease.sha256.removeprefix('sha256:')}.json"

    def _load_admission(self, lease: GitWorktreeLease) -> dict[str, Any] | None:
        path = self._admission_path(lease)
        return _load_json_object(path) if path.is_file() and not path.is_symlink() else None

    def _ensure_storage(self) -> None:
        self.root.mkdir(parents=True, mode=0o700, exist_ok=True)
        if not self.root.is_dir() or self.root.is_symlink():
            raise GitWorktreeLeaseError("worktree_state_root_invalid", str(self.root))
        self.root.chmod(0o700)
        for directory in (
            self.worktrees_dir,
            self.leases_dir,
            self.admissions_dir,
            self.intents_dir,
            self.retired_dir,
        ):
            if directory.is_symlink() or (directory.exists() and not directory.is_dir()):
                raise GitWorktreeLeaseError("worktree_state_directory_invalid", str(directory))
            directory.mkdir(mode=0o700, exist_ok=True)
            if directory.resolve().parent != self.root:
                raise GitWorktreeLeaseError("worktree_state_directory_escape", str(directory))
            directory.chmod(0o700)

    def _recover_allocation_intents(self) -> None:
        for path in sorted(self.intents_dir.glob("*.json")):
            intent = _load_allocation_intent(path)
            lease_path = self.leases_dir / path.name
            self._recover_allocation_intent(path, lease_path, intent)

    def _recover_allocation_intent(
        self,
        intent_path: Path,
        lease_path: Path,
        expected: Mapping[str, Any],
    ) -> GitWorktreeLease | None:
        if not intent_path.exists():
            return None
        intent = _load_allocation_intent(intent_path)
        stable_keys = (
            "run_id",
            "plan_revision",
            "node_id",
            "attempt_id",
            "repository",
            "worktree_path",
            "base_commit",
            "allowed_paths",
            "owner",
            "repository_control_sha256",
        )
        if any(intent.get(key) != expected.get(key) for key in stable_keys):
            raise GitWorktreeLeaseError("worktree_allocation_intent_mismatch")
        if lease_path.exists():
            lease = _load_registered_lease(lease_path)
            if lease.owner != self.owner:
                raise GitWorktreeLeaseError("worktree_lease_owner_mismatch", lease.attempt_id)
            self._assert_registered(lease, allow_expired=True)
            _durable_unlink(intent_path)
            return lease
        worktree = Path(str(intent["worktree_path"])).resolve()
        repo = Path(str(intent["repository"])).resolve()
        _repository_common_dir(repo)
        _validate_repository_git_drivers(repo)
        registered = worktree in _registered_worktree_paths(repo)
        if not registered and not worktree.exists():
            _durable_unlink(intent_path)
            return None
        if not registered or not worktree.is_dir() or worktree.is_symlink():
            raise GitWorktreeLeaseError("worktree_allocation_recovery_ambiguous", str(worktree))
        _validate_allowed_targets(worktree, tuple(intent["allowed_paths"]))
        _validate_worktree_no_symlinks(worktree, self.max_validation_entries)
        _worktree_gitfile_sha256(worktree, repo)
        _validate_intent_admin_identity(intent, worktree)
        _validate_repository_git_drivers(worktree)
        status = _git_bytes(worktree, "status", "--porcelain=v1", "-z", "--untracked-files=all")
        if status:
            raise GitWorktreeLeaseError("worktree_allocation_recovery_dirty", str(worktree))
        head_commit = _git(worktree, "rev-parse", "HEAD")
        if head_commit != intent["base_commit"]:
            raise GitWorktreeLeaseError("worktree_allocation_recovery_head_changed", head_commit)
        branch = _symbolic_branch(worktree)
        if branch is not None:
            raise GitWorktreeLeaseError("worktree_allocation_recovery_branch_changed", branch)
        lease = GitWorktreeLease(
            run_id=str(intent["run_id"]),
            plan_revision=str(intent["plan_revision"]),
            node_id=str(intent["node_id"]),
            attempt_id=str(intent["attempt_id"]),
            repository=str(repo),
            worktree_path=str(worktree),
            base_commit=str(intent["base_commit"]),
            head_commit=head_commit,
            branch=branch,
            detached=branch is None,
            allowed_paths=tuple(intent["allowed_paths"]),
            owner=str(intent["owner"]),
            created_at=str(intent["created_at"]),
            expires_at=str(intent["expires_at"]),
            pre_status_sha256=_bytes_sha256(status),
            cleanup_policy=FrozenJson.from_value(
                {
                    "unadmitted_changes": "block",
                    "explicit_discard_authorization": "required",
                    "remove_registered_worktree": True,
                    "repository_control_sha256": _repository_control_sha256(
                        repo,
                        max_bytes=DEFAULT_MAX_HASH_BYTES,
                        max_entries=DEFAULT_MAX_VALIDATION_ENTRIES,
                    ),
                    "repository_control_sha256_at_intent": intent[
                        "repository_control_sha256"
                    ],
                    "checkout_directory_mode": _measure_checkout_directory_mode(worktree),
                    "worktree_gitfile_sha256": _worktree_gitfile_sha256(worktree, repo),
                    "worktree_admin_dir": str(_worktree_admin_dir_path(worktree)),
                    "worktree_admin_device": intent["worktree_admin_device"],
                    "worktree_admin_inode": intent["worktree_admin_inode"],
                }
            ),
        )
        _atomic_json_write(
            lease_path,
            {
                "schema": WORKTREE_LEASE_RECORD_SCHEMA,
                "lease": lease.to_payload(),
                "lease_sha256": lease.sha256,
            },
        )
        _durable_unlink(intent_path)
        return lease

    def _retire_missing_worktree_lease(self, lease_path: Path) -> bool:
        if not lease_path.exists():
            return False
        lease = _load_registered_lease(lease_path)
        if lease.owner != self.owner:
            raise GitWorktreeLeaseError("worktree_lease_owner_mismatch", lease.attempt_id)
        worktree = Path(lease.worktree_path).resolve()
        repository = Path(lease.repository)
        admin_present = _validate_missing_worktree_topology(lease, repository, worktree)
        registered = worktree in _registered_worktree_paths(repository)
        if registered and not admin_present:
            raise GitWorktreeLeaseError("worktree_bound_admin_dir_missing", lease.attempt_id)
        if not worktree.exists() and registered:
            _validate_missing_worktree_topology(lease, repository, worktree)
            result = _run_git(repository, "worktree", "remove", "--force", str(worktree))
            if result.returncode != 0:
                return False
            registered = worktree in _registered_worktree_paths(repository)
        if worktree.exists() or registered:
            return False
        self._retire_lease_record(lease)
        _durable_unlink(self._admission_path(lease))
        return True

    def _retire_lease_record(self, lease: GitWorktreeLease) -> None:
        lease_path = self._lease_path(lease)
        retired = self.retired_dir / (
            f"{lease_path.stem}-{lease.sha256.removeprefix('sha256:')}.json"
        )
        if retired.exists():
            raise GitWorktreeLeaseError("worktree_retired_lease_collision", str(retired))
        os.replace(lease_path, retired)
        _fsync_directory(lease_path.parent)
        _fsync_directory(retired.parent)

    @contextmanager
    def _writer_lock(self, lease: GitWorktreeLease, *, blocking: bool) -> Iterator[None]:
        path = self.root / f"writer-{lease.sha256.removeprefix('sha256:')}.lock"
        descriptor = os.open(path, os.O_CREAT | os.O_RDWR, 0o600)
        operation = fcntl.LOCK_EX | (0 if blocking else fcntl.LOCK_NB)
        try:
            try:
                fcntl.flock(descriptor, operation)
            except BlockingIOError as exc:
                raise GitWorktreeLeaseError("worktree_writer_active", lease.attempt_id) from exc
            yield
        finally:
            try:
                fcntl.flock(descriptor, fcntl.LOCK_UN)
            finally:
                os.close(descriptor)

    @contextmanager
    def _locked(self) -> Iterator[None]:
        descriptor = os.open(self._lock_path, os.O_CREAT | os.O_RDWR, 0o600)
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX)
            yield
        finally:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
            os.close(descriptor)


def worktree_discard_authorization(
    lease: GitWorktreeLease, inspection: Mapping[str, Any]
) -> dict[str, Any]:
    if inspection.get("lease_sha256") != lease.sha256:
        raise GitWorktreeLeaseError("worktree_discard_lease_mismatch")
    if inspection.get("hash_complete") is not True:
        raise GitWorktreeLeaseError("worktree_discard_requires_complete_hash")
    diff_sha256 = inspection.get("diff_sha256")
    if not isinstance(diff_sha256, str) or not diff_sha256.startswith("sha256:"):
        raise GitWorktreeLeaseError("worktree_discard_diff_missing")
    return {
        "schema": WORKTREE_CLEANUP_AUTHORIZATION_SCHEMA,
        "action": "DISCARD_AND_REMOVE",
        "lease_sha256": lease.sha256,
        "diff_sha256": diff_sha256,
    }


def _cleanup_receipt(
    lease: GitWorktreeLease,
    inspection: Mapping[str, Any],
    *,
    status: str,
    removed: bool,
    errors: tuple[str, ...],
) -> dict[str, Any]:
    return {
        "schema": WORKTREE_CLEANUP_RECEIPT_SCHEMA,
        "status": status,
        "ok": status == "PASS",
        "lease_sha256": lease.sha256,
        "diff_sha256": inspection["diff_sha256"],
        "hash_complete": inspection["hash_complete"],
        "dirty": inspection["dirty"],
        "changed_paths": inspection["changed_paths"],
        "removed": removed,
        "post_verified_absent": removed,
        "errors": list(errors),
    }


def _valid_discard_authorization(
    payload: Mapping[str, Any] | None,
    lease: GitWorktreeLease,
    diff_sha256: str,
    *,
    hash_complete: bool,
) -> bool:
    return bool(
        hash_complete
        and payload
        and payload.get("schema") == WORKTREE_CLEANUP_AUTHORIZATION_SCHEMA
        and payload.get("action") == "DISCARD_AND_REMOVE"
        and payload.get("lease_sha256") == lease.sha256
        and payload.get("diff_sha256") == diff_sha256
    )


def _repository_root(path: Path) -> Path:
    candidate = path.expanduser().resolve()
    result = _run_git(candidate, "rev-parse", "--show-toplevel")
    if result.returncode != 0:
        raise GitWorktreeLeaseError("worktree_repository_invalid", result.stderr.strip())
    return Path(result.stdout.strip()).resolve()


def _repository_identity(path: Path) -> Path:
    """Return the stable primary checkout shared by all linked worktrees."""

    checkout = _repository_root(path)
    return _primary_worktree(checkout)


def _primary_worktree(repository: Path) -> Path:
    paths = _worktree_porcelain_paths(repository)
    if paths:
        return paths[0]
    raise GitWorktreeLeaseError("worktree_primary_not_found")


def _require_state_root_outside_worktrees(root: Path, repository: Path) -> None:
    for registered in _registered_worktree_paths(repository):
        try:
            root.relative_to(registered)
        except ValueError:
            continue
        raise GitWorktreeLeaseError("worktree_root_inside_registered_worktree_forbidden")


def _registered_worktree_paths(repository: Path) -> set[Path]:
    return set(_worktree_porcelain_paths(repository))


def _worktree_porcelain_paths(repository: Path) -> tuple[Path, ...]:
    output = _git_bytes(repository, "worktree", "list", "--porcelain", "-z")
    prefix = b"worktree "
    return tuple(
        Path(field.removeprefix(prefix).decode("utf-8", errors="surrogateescape")).resolve()
        for field in output.split(b"\0")
        if field.startswith(prefix)
    )


def _normalize_allowed_paths(paths: tuple[str, ...]) -> tuple[str, ...]:
    if not paths:
        raise GitWorktreeLeaseError("worktree_allowed_paths_required")
    normalized: list[str] = []
    for raw in paths:
        if not isinstance(raw, str) or not raw.strip():
            raise GitWorktreeLeaseError("worktree_allowed_path_invalid")
        path = PurePosixPath(raw)
        if path.is_absolute() or ".." in path.parts or "." in path.parts:
            raise GitWorktreeLeaseError("worktree_allowed_path_escape", raw)
        if path.parts and path.parts[0] == ".git":
            raise GitWorktreeLeaseError("worktree_allowed_path_git_metadata", raw)
        value = path.as_posix().rstrip("/")
        if value in {"", "."}:
            raise GitWorktreeLeaseError("worktree_allowed_path_too_broad", raw)
        normalized.append(value)
    if len(normalized) != len(set(normalized)):
        raise GitWorktreeLeaseError("worktree_allowed_paths_duplicate")
    return tuple(sorted(normalized))


def _validate_allowed_targets(worktree: Path, allowed_paths: tuple[str, ...]) -> None:
    for value in allowed_paths:
        if not _path_stays_within(worktree, value):
            raise GitWorktreeLeaseError("worktree_allowed_path_symlink_escape", value)
        root = worktree / value
        if not root.exists():
            continue
        for current, directories, files in os.walk(root, followlinks=False):
            current_path = Path(current)
            for name in (*directories, *files):
                candidate = current_path / name
                if candidate.is_symlink():
                    raise GitWorktreeLeaseError(
                        "worktree_allowed_path_symlink_descendant_forbidden",
                        str(candidate.relative_to(worktree)),
                    )


def _validate_worktree_no_symlinks(worktree: Path, max_entries: int) -> None:
    entry_count = 0
    for current, directories, files in os.walk(worktree, topdown=True, followlinks=False):
        current_path = Path(current)
        directories[:] = [name for name in directories if name != ".git"]
        for name in (*directories, *files):
            entry_count += 1
            if entry_count > max_entries:
                raise GitWorktreeLeaseError("worktree_validation_entry_limit_exceeded")
            candidate = current_path / name
            if candidate.is_symlink():
                raise GitWorktreeLeaseError(
                    "worktree_symlink_forbidden",
                    str(candidate.relative_to(worktree)),
                )


def _path_allowed(path: str, allowed_paths: tuple[str, ...]) -> bool:
    candidate = PurePosixPath(path)
    return any(
        candidate == PurePosixPath(root) or candidate.is_relative_to(root) for root in allowed_paths
    )


def _path_stays_within(worktree: Path, relative: str) -> bool:
    candidate = worktree / PurePosixPath(relative)
    existing = candidate
    while not existing.exists() and existing != worktree:
        existing = existing.parent
    try:
        existing.resolve().relative_to(worktree.resolve())
        if candidate.is_symlink():
            candidate.resolve().relative_to(worktree.resolve())
    except ValueError:
        return False
    return True


def _changed_paths(
    worktree: Path,
    max_entries: int,
    max_output_bytes: int,
    max_validation_entries: int,
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    tracked, tracked_truncated = _git_bytes_limited(
        worktree,
        "diff",
        "--no-ext-diff",
        "--no-textconv",
        "--no-renames",
        "--name-only",
        "-z",
        "HEAD",
        max_bytes=max_output_bytes,
    )
    untracked, untracked_truncated = _git_bytes_limited(
        worktree,
        "ls-files",
        "--others",
        "--exclude-standard",
        "-z",
        max_bytes=max_output_bytes,
    )
    ignored, ignored_truncated = _git_bytes_limited(
        worktree,
        "ls-files",
        "--others",
        "--ignored",
        "--exclude-standard",
        "-z",
        max_bytes=max_output_bytes,
    )
    index_entries, index_entries_truncated = _git_bytes_limited(
        worktree,
        "ls-files",
        "-v",
        "-z",
        max_bytes=max_output_bytes,
    )
    staged_entries, staged_entries_truncated = _git_bytes_limited(
        worktree,
        "ls-files",
        "--stage",
        "-z",
        max_bytes=max_output_bytes,
    )
    gitlink_paths = {
        item.split(b"\t", 1)[1].decode("utf-8", errors="surrogateescape")
        for item in staged_entries.split(b"\0")
        if item.startswith(b"160000 ") and b"\t" in item
    }
    flagged_paths = tuple(
        item[2:].decode("utf-8", errors="surrogateescape")
        for item in index_entries.split(b"\0")
        if len(item) >= 3 and item[1:2] == b" " and item[:1] != b"H"
    )
    values = {
        item.decode("utf-8", errors="surrogateescape")
        for item in (*tracked.split(b"\0"), *untracked.split(b"\0"), *ignored.split(b"\0"))
        if item
    }
    values.update(flagged_paths)
    filesystem_paths, filesystem_truncated = _filesystem_extra_paths(
        worktree, max_validation_entries, ignored_empty_paths=gitlink_paths
    )
    values.update(filesystem_paths)
    ordered = tuple(sorted(values))
    output_errors = (
        ("worktree_hash_byte_limit_exceeded",)
        if tracked_truncated
        or untracked_truncated
        or ignored_truncated
        or index_entries_truncated
        or staged_entries_truncated
        else ()
    )
    if filesystem_truncated:
        output_errors = (*output_errors, "worktree_validation_entry_limit_exceeded")
    if len(ordered) > max_entries:
        return ordered[:max_entries], (
            *output_errors,
            "worktree_hash_entry_limit_exceeded",
        )
    errors = tuple(
        f"worktree_nested_repository_forbidden:{value}"
        for value in ordered
        if ".git" in PurePosixPath(value).parts or (worktree / value / ".git").exists()
    )
    index_errors = tuple(f"worktree_index_flag_forbidden:{value}" for value in flagged_paths)
    return ordered, (*output_errors, *errors, *index_errors)


def _worktree_diff_sha256(
    worktree: Path,
    paths: tuple[str, ...],
    status: bytes,
    *,
    max_bytes: int,
    max_entries: int,
) -> tuple[str, tuple[str, ...], bool]:
    digest = hashlib.sha256()
    _digest_frame(digest, b"status", status)
    consumed = len(status)
    errors: list[str] = []
    if consumed > max_bytes:
        return f"sha256:{digest.hexdigest()}", ("worktree_hash_byte_limit_exceeded",), False
    if not _changed_content_within_limit(worktree, paths, max_bytes - consumed):
        return f"sha256:{digest.hexdigest()}", ("worktree_hash_byte_limit_exceeded",), False
    tracked_diff, tracked_diff_truncated = _git_bytes_limited(
        worktree,
        "diff",
        "--no-ext-diff",
        "--no-textconv",
        "--binary",
        "HEAD",
        max_bytes=max_bytes - consumed,
    )
    if tracked_diff_truncated:
        return f"sha256:{digest.hexdigest()}", ("worktree_hash_byte_limit_exceeded",), False
    consumed += len(tracked_diff)
    _digest_frame(digest, b"tracked-diff", tracked_diff)
    if len(paths) > max_entries:
        return (
            f"sha256:{digest.hexdigest()}",
            ("worktree_hash_entry_limit_exceeded",),
            False,
        )
    for value in paths:
        path_bytes = value.encode("utf-8", errors="surrogateescape")
        consumed += len(path_bytes)
        if consumed > max_bytes:
            return f"sha256:{digest.hexdigest()}", ("worktree_hash_byte_limit_exceeded",), False
        _digest_frame(digest, b"path", path_bytes)
        candidate = worktree / value
        if candidate.exists() or candidate.is_symlink():
            mode = candidate.lstat().st_mode
            _digest_frame(digest, b"mode", f"{stat.S_IFMT(mode):o}:{stat.S_IMODE(mode):o}".encode())
        if candidate.is_file() and not candidate.is_symlink():
            size = candidate.stat().st_size
            if consumed + size > max_bytes:
                errors.append("worktree_hash_byte_limit_exceeded")
                return f"sha256:{digest.hexdigest()}", tuple(errors), False
            with candidate.open("rb") as handle:
                _digest_frame(digest, b"file-size", str(size).encode())
                while chunk := handle.read(1024 * 1024):
                    consumed += len(chunk)
                    if consumed > max_bytes:
                        errors.append("worktree_hash_byte_limit_exceeded")
                        return f"sha256:{digest.hexdigest()}", tuple(errors), False
                    _digest_frame(digest, b"file-chunk", chunk)
        elif candidate.is_symlink():
            target = os.readlink(candidate).encode()
            consumed += len(target)
            if consumed > max_bytes:
                return (
                    f"sha256:{digest.hexdigest()}",
                    ("worktree_hash_byte_limit_exceeded",),
                    False,
                )
            _digest_frame(digest, b"symlink", target)
            errors.append(f"worktree_symlink_forbidden:{value}")
        elif candidate.is_dir():
            _digest_frame(digest, b"empty-directory", b"")
        elif not candidate.exists() and _tracked_at_head(worktree, value):
            continue
        else:
            errors.append(f"worktree_special_file_unsupported:{value}")
    return f"sha256:{digest.hexdigest()}", tuple(errors), not errors


def _digest_frame(digest: Any, label: bytes, value: bytes) -> None:
    digest.update(len(label).to_bytes(4, "big"))
    digest.update(label)
    digest.update(len(value).to_bytes(8, "big"))
    digest.update(value)


def _filesystem_extra_paths(
    worktree: Path,
    max_entries: int,
    *,
    ignored_empty_paths: set[str],
) -> tuple[tuple[str, ...], bool]:
    extra: list[str] = []
    seen = 0
    for current, directories, files in os.walk(worktree, topdown=True, followlinks=False):
        current_path = Path(current)
        if current_path == worktree:
            directories[:] = [name for name in directories if name != ".git"]
        elif ".git" in directories:
            extra.append((current_path / ".git").relative_to(worktree).as_posix())
            directories.remove(".git")
        directories[:] = [name for name in directories if not (current_path / name).is_symlink()]
        seen += len(directories) + len(files)
        if seen > max_entries:
            return tuple(extra), True
        if current_path != worktree and not directories and not files:
            relative_current = current_path.relative_to(worktree).as_posix()
            if relative_current not in ignored_empty_paths:
                extra.append(relative_current)
        for name in files:
            candidate = current_path / name
            if name == ".git":
                if current_path != worktree:
                    extra.append(candidate.relative_to(worktree).as_posix())
                continue
            mode = candidate.lstat().st_mode
            if not (stat.S_ISREG(mode) or stat.S_ISLNK(mode)):
                extra.append(candidate.relative_to(worktree).as_posix())
    return tuple(extra), False


def _nondefault_directory_mode_paths(
    worktree: Path,
    *,
    expected_mode: int,
    max_entries: int,
) -> tuple[tuple[str, ...], bool]:
    paths: list[str] = []
    seen = 0
    for current, directories, _files in os.walk(worktree, topdown=True, followlinks=False):
        current_path = Path(current)
        directories[:] = [name for name in directories if name != ".git"]
        for name in sorted(directories):
            seen += 1
            if seen > max_entries:
                return tuple(paths), True
            candidate = current_path / name
            if candidate.is_symlink():
                continue
            if stat.S_IMODE(candidate.stat().st_mode) != expected_mode:
                paths.append(candidate.relative_to(worktree).as_posix())
    return tuple(paths), False


def _measure_checkout_directory_mode(worktree: Path) -> int:
    for counter in range(100):
        probe = worktree / f".tau-directory-mode-probe-{os.getpid()}-{counter}"
        try:
            probe.mkdir(mode=0o777)
        except FileExistsError:
            continue
        try:
            return stat.S_IMODE(probe.stat().st_mode)
        finally:
            probe.rmdir()
    raise GitWorktreeLeaseError("worktree_directory_mode_probe_unavailable")


def _changed_content_within_limit(
    worktree: Path,
    paths: tuple[str, ...],
    remaining_bytes: int,
) -> bool:
    consumed = 0
    for value in paths:
        consumed += len(value.encode("utf-8", errors="surrogateescape"))
        candidate = worktree / value
        if candidate.is_file() and not candidate.is_symlink():
            consumed += candidate.stat().st_size
        result = _run_git(worktree, "cat-file", "-s", f"HEAD:{value}")
        if result.returncode == 0:
            try:
                consumed += int(result.stdout.strip())
            except ValueError as exc:
                raise GitWorktreeLeaseError("worktree_git_blob_size_invalid", value) from exc
        if consumed > remaining_bytes:
            return False
    return True


def _tracked_at_head(worktree: Path, value: str) -> bool:
    return _run_git(worktree, "cat-file", "-e", f"HEAD:{value}").returncode == 0


def _symbolic_branch(worktree: Path) -> str | None:
    result = _run_git(worktree, "symbolic-ref", "--quiet", "--short", "HEAD")
    return result.stdout.strip() if result.returncode == 0 else None


def _repository_control_sha256(
    repository: Path,
    *,
    max_bytes: int,
    max_entries: int,
) -> str:
    common_dir = _repository_common_dir(repository)
    digest = hashlib.sha256()
    consumed = 0
    entries = 0
    excluded_roots = {"objects", "worktrees"}
    for current, directories, files in os.walk(common_dir, topdown=True, followlinks=False):
        current_path = Path(current)
        directories.sort()
        for name in directories:
            candidate = current_path / name
            relative = candidate.relative_to(common_dir).as_posix()
            if candidate.is_symlink() or not candidate.is_dir():
                raise GitWorktreeLeaseError(
                    "worktree_repository_control_special_directory", relative
                )
            if current_path == common_dir and name in excluded_roots:
                continue
            entries += 1
            if entries > max_entries:
                raise GitWorktreeLeaseError("worktree_repository_control_entry_limit_exceeded")
            relative_bytes = relative.encode()
            mode_bytes = f"{stat.S_IMODE(candidate.stat().st_mode):o}".encode()
            consumed += len(relative_bytes) + len(mode_bytes)
            if consumed > max_bytes:
                raise GitWorktreeLeaseError("worktree_repository_control_byte_limit_exceeded")
            _digest_frame(digest, b"directory", relative_bytes)
            _digest_frame(
                digest,
                b"directory-mode",
                mode_bytes,
            )
        if current_path == common_dir:
            directories[:] = [name for name in directories if name not in excluded_roots]
        files.sort()
        for name in files:
            candidate = current_path / name
            relative = candidate.relative_to(common_dir).as_posix()
            entries += 1
            if entries > max_entries:
                raise GitWorktreeLeaseError("worktree_repository_control_entry_limit_exceeded")
            if candidate.is_symlink() or not candidate.is_file():
                raise GitWorktreeLeaseError(
                    "worktree_repository_control_special_file", relative
                )
            path_bytes = relative.encode("utf-8", errors="surrogateescape")
            mode_bytes = f"{stat.S_IMODE(candidate.stat().st_mode):o}".encode()
            consumed += len(path_bytes) + len(mode_bytes)
            if consumed > max_bytes:
                raise GitWorktreeLeaseError("worktree_repository_control_byte_limit_exceeded")
            _digest_frame(digest, b"path", path_bytes)
            _digest_frame(digest, b"mode", mode_bytes)
            with candidate.open("rb") as handle:
                while chunk := handle.read(1024 * 1024):
                    consumed += len(chunk)
                    if consumed > max_bytes:
                        raise GitWorktreeLeaseError(
                            "worktree_repository_control_byte_limit_exceeded"
                        )
                    _digest_frame(digest, b"chunk", chunk)
    return f"sha256:{digest.hexdigest()}"


def _validate_repository_git_drivers(repository: Path) -> None:
    result = _run_git(
        repository,
        "config",
        "--name-only",
        "--get-regexp",
        r"^(filter\..*\.(clean|smudge|process)|diff\..*\.(command|textconv))$",
    )
    if result.returncode not in {0, 1}:
        raise GitWorktreeLeaseError(
            "worktree_git_failed", f"config driver inspection:{result.stderr.strip()}"
        )
    if result.stdout.strip():
        raise GitWorktreeLeaseError(
            "worktree_external_git_driver_forbidden", result.stdout.strip()
        )
    includes = _run_git(
        repository,
        "config",
        "--name-only",
        "--get-regexp",
        r"^(include\.path|includeIf\..*\.path)$",
    )
    if includes.returncode not in {0, 1}:
        raise GitWorktreeLeaseError(
            "worktree_git_failed", f"config include inspection:{includes.stderr.strip()}"
        )
    if includes.stdout.strip():
        raise GitWorktreeLeaseError(
            "worktree_external_git_include_forbidden", includes.stdout.strip()
        )


def _worktree_gitfile_sha256(worktree: Path, repository: Path) -> str:
    gitfile = worktree / ".git"
    try:
        mode = gitfile.lstat().st_mode
    except OSError as exc:
        raise GitWorktreeLeaseError("worktree_gitfile_missing", str(gitfile)) from exc
    if not stat.S_ISREG(mode) or gitfile.is_symlink():
        raise GitWorktreeLeaseError("worktree_gitfile_invalid", str(gitfile))
    content = gitfile.read_bytes()
    if len(content) > 4096:
        raise GitWorktreeLeaseError("worktree_gitfile_too_large", str(gitfile))
    try:
        line = content.decode("utf-8").strip()
    except UnicodeDecodeError as exc:
        raise GitWorktreeLeaseError("worktree_gitfile_invalid", str(gitfile)) from exc
    if not line.startswith("gitdir: ") or "\n" in line or "\r" in line:
        raise GitWorktreeLeaseError("worktree_gitfile_invalid", str(gitfile))
    gitdir = _worktree_admin_dir_path(worktree)
    common_dir = _repository_common_dir(repository)
    expected_parent = common_dir / "worktrees"
    if expected_parent.is_symlink() or not expected_parent.is_dir():
        raise GitWorktreeLeaseError("worktree_gitdir_parent_invalid", str(expected_parent))
    if gitdir.parent != expected_parent:
        raise GitWorktreeLeaseError("worktree_gitdir_outside_repository", str(gitdir))
    try:
        gitdir_mode = gitdir.lstat().st_mode
    except OSError as exc:
        raise GitWorktreeLeaseError("worktree_gitdir_missing", str(gitdir)) from exc
    if not stat.S_ISDIR(gitdir_mode) or gitdir.is_symlink():
        raise GitWorktreeLeaseError("worktree_gitdir_invalid", str(gitdir))
    commondir_content, admin_gitdir_content = _validate_worktree_admin_records(
        gitdir, common_dir=common_dir, expected_gitfile=gitfile
    )
    return canonical_sha256(
        {
            "gitfile_sha256": _bytes_sha256(content),
            "commondir_sha256": _bytes_sha256(commondir_content),
            "admin_gitdir_sha256": _bytes_sha256(admin_gitdir_content),
        }
    )


def _worktree_admin_dir_path(worktree: Path) -> Path:
    gitfile = worktree / ".git"
    content = _read_bounded_regular_file(gitfile)
    line = content.decode("utf-8").strip()
    if not line.startswith("gitdir: ") or "\n" in line or "\r" in line:
        raise GitWorktreeLeaseError("worktree_gitfile_invalid", str(gitfile))
    gitdir = Path(line.removeprefix("gitdir: "))
    if not gitdir.is_absolute():
        gitdir = gitfile.parent / gitdir
    return Path(os.path.abspath(gitdir))


def _validate_worktree_admin_records(
    gitdir: Path, *, common_dir: Path, expected_gitfile: Path
) -> tuple[bytes, bytes]:
    commondir_content = _read_bounded_regular_file(gitdir / "commondir")
    declared_common_dir = Path(commondir_content.decode("utf-8").strip())
    if not declared_common_dir.is_absolute():
        declared_common_dir = gitdir / declared_common_dir
    if Path(os.path.abspath(declared_common_dir)) != common_dir:
        raise GitWorktreeLeaseError("worktree_commondir_changed", str(gitdir / "commondir"))
    admin_gitdir_content = _read_bounded_regular_file(gitdir / "gitdir")
    declared_gitfile = Path(admin_gitdir_content.decode("utf-8").strip())
    if not declared_gitfile.is_absolute():
        declared_gitfile = gitdir / declared_gitfile
    if Path(os.path.abspath(declared_gitfile)) != expected_gitfile:
        raise GitWorktreeLeaseError("worktree_admin_gitdir_changed", str(gitdir / "gitdir"))
    return commondir_content, admin_gitdir_content


def _validate_missing_worktree_topology(
    lease: GitWorktreeLease, repository: Path, worktree: Path
) -> bool:
    common_dir = _repository_common_dir(repository)
    policy = lease.cleanup_policy.to_value()
    admin_value = policy.get("worktree_admin_dir") if isinstance(policy, dict) else None
    if not isinstance(admin_value, str):
        raise GitWorktreeLeaseError("worktree_admin_dir_missing", lease.attempt_id)
    admin_dir = Path(admin_value)
    expected_parent = common_dir / "worktrees"
    if expected_parent.is_symlink():
        raise GitWorktreeLeaseError("worktree_gitdir_parent_invalid", str(expected_parent))
    if not expected_parent.exists():
        return False
    if not expected_parent.is_dir():
        raise GitWorktreeLeaseError("worktree_gitdir_parent_invalid", str(expected_parent))
    if admin_dir.parent != expected_parent:
        raise GitWorktreeLeaseError("worktree_gitdir_outside_repository", str(admin_dir))
    try:
        mode = admin_dir.lstat().st_mode
    except OSError as exc:
        if _matching_worktree_admin_dirs(expected_parent, worktree / ".git"):
            raise GitWorktreeLeaseError(
                "worktree_admin_registration_mismatch", lease.attempt_id
            ) from exc
        return False
    if not stat.S_ISDIR(mode) or admin_dir.is_symlink():
        raise GitWorktreeLeaseError("worktree_gitdir_invalid", str(admin_dir))
    _validate_admin_identity(policy, admin_dir, lease.attempt_id)
    _validate_worktree_admin_records(
        admin_dir,
        common_dir=common_dir,
        expected_gitfile=worktree / ".git",
    )
    matches = _matching_worktree_admin_dirs(expected_parent, worktree / ".git")
    if matches != (admin_dir,):
        raise GitWorktreeLeaseError(
            "worktree_admin_registration_ambiguous", lease.attempt_id
        )
    return True


def _validate_lease_admin_identity(lease: GitWorktreeLease) -> None:
    policy = lease.cleanup_policy.to_value()
    if not isinstance(policy, dict):
        raise GitWorktreeLeaseError("worktree_admin_identity_missing", lease.attempt_id)
    admin_value = policy.get("worktree_admin_dir")
    if not isinstance(admin_value, str):
        raise GitWorktreeLeaseError("worktree_admin_dir_missing", lease.attempt_id)
    _validate_admin_identity(policy, Path(admin_value), lease.attempt_id)


def _validate_intent_admin_identity(intent: Mapping[str, Any], worktree: Path) -> None:
    admin_dir = _worktree_admin_dir_path(worktree)
    _validate_admin_identity(intent, admin_dir, str(intent.get("attempt_id", "unknown")))


def _validate_admin_identity(
    source: Mapping[str, Any], admin_dir: Path, attempt_id: str
) -> None:
    expected_device = source.get("worktree_admin_device")
    expected_inode = source.get("worktree_admin_inode")
    if type(expected_device) is not int or type(expected_inode) is not int:
        raise GitWorktreeLeaseError("worktree_admin_identity_missing", attempt_id)
    try:
        observed = admin_dir.stat()
    except OSError as exc:
        raise GitWorktreeLeaseError("worktree_bound_admin_dir_missing", attempt_id) from exc
    if observed.st_dev != expected_device or observed.st_ino != expected_inode:
        raise GitWorktreeLeaseError("worktree_admin_identity_changed", attempt_id)


def _matching_worktree_admin_dirs(parent: Path, expected_gitfile: Path) -> tuple[Path, ...]:
    matches: list[Path] = []
    for candidate in sorted(parent.iterdir()):
        if candidate.is_symlink() or not candidate.is_dir():
            raise GitWorktreeLeaseError("worktree_gitdir_invalid", str(candidate))
        gitdir_record = candidate / "gitdir"
        try:
            content = _read_bounded_regular_file(gitdir_record)
        except GitWorktreeLeaseError as exc:
            if exc.code == "worktree_admin_file_missing":
                continue
            raise
        declared = Path(content.decode("utf-8").strip())
        if not declared.is_absolute():
            declared = candidate / declared
        if Path(os.path.abspath(declared)) == expected_gitfile:
            matches.append(candidate)
    return tuple(matches)


def _repository_common_dir(repository: Path) -> Path:
    expected = repository / ".git"
    try:
        mode = expected.lstat().st_mode
    except OSError as exc:
        raise GitWorktreeLeaseError("worktree_common_dir_missing", str(expected)) from exc
    if not stat.S_ISDIR(mode) or expected.is_symlink():
        raise GitWorktreeLeaseError("worktree_common_dir_invalid", str(expected))
    common_dir_text = _git(repository, "rev-parse", "--git-common-dir")
    common_dir = Path(common_dir_text)
    if not common_dir.is_absolute():
        common_dir = repository / common_dir
    common_dir = Path(os.path.abspath(common_dir))
    if common_dir != expected:
        raise GitWorktreeLeaseError("worktree_common_dir_unexpected", str(common_dir))
    return common_dir


def _read_bounded_regular_file(path: Path) -> bytes:
    try:
        mode = path.lstat().st_mode
    except OSError as exc:
        raise GitWorktreeLeaseError("worktree_admin_file_missing", str(path)) from exc
    if not stat.S_ISREG(mode) or path.is_symlink():
        raise GitWorktreeLeaseError("worktree_admin_file_invalid", str(path))
    content = path.read_bytes()
    if len(content) > 4096:
        raise GitWorktreeLeaseError("worktree_admin_file_too_large", str(path))
    try:
        content.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise GitWorktreeLeaseError("worktree_admin_file_invalid", str(path)) from exc
    return content


def _lease_expired(lease: GitWorktreeLease) -> bool:
    try:
        expires_at = datetime.fromisoformat(lease.expires_at.replace("Z", "+00:00"))
    except ValueError as exc:
        raise GitWorktreeLeaseError("worktree_lease_expiry_invalid") from exc
    if expires_at.tzinfo is None:
        raise GitWorktreeLeaseError("worktree_lease_expiry_timezone_required")
    return expires_at.astimezone(UTC) <= datetime.now(UTC)


def _git(cwd: Path, *args: str) -> str:
    result = _run_git(cwd, *args)
    if result.returncode != 0:
        raise GitWorktreeLeaseError(
            "worktree_git_failed", f"{' '.join(args)}:{result.stderr.strip()}"
        )
    return result.stdout.strip()


def _git_bytes(cwd: Path, *args: str) -> bytes:
    process = _start_git_process(cwd, *args)
    try:
        stdout, stderr = process.communicate(timeout=DEFAULT_GIT_TIMEOUT_SECONDS)
    except subprocess.TimeoutExpired as exc:
        _terminate_git_process(process)
        raise GitWorktreeLeaseError("worktree_git_timeout", " ".join(args)) from exc
    if process.returncode != 0:
        raise GitWorktreeLeaseError(
            "worktree_git_failed",
            f"{' '.join(args)}:{stderr.decode(errors='replace').strip()}",
        )
    return stdout


def _git_bytes_limited(cwd: Path, *args: str, max_bytes: int) -> tuple[bytes, bool]:
    process = _start_git_process(cwd, *args)
    assert process.stdout is not None and process.stderr is not None
    selector = selectors.DefaultSelector()
    selector.register(process.stdout, selectors.EVENT_READ, "stdout")
    selector.register(process.stderr, selectors.EVENT_READ, "stderr")
    output = bytearray()
    error_output = bytearray()
    truncated = False
    deadline = time.monotonic() + DEFAULT_GIT_TIMEOUT_SECONDS
    try:
        while selector.get_map():
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                _terminate_git_process(process)
                raise GitWorktreeLeaseError("worktree_git_timeout", " ".join(args))
            for key, _ in selector.select(remaining):
                chunk = os.read(key.fd, 64 * 1024)
                if not chunk:
                    selector.unregister(key.fileobj)
                    continue
                if key.data == "stdout":
                    capacity = max_bytes + 1 - len(output)
                    output.extend(chunk[:capacity])
                    if len(output) > max_bytes:
                        truncated = True
                        _terminate_git_process(process)
                        selector.close()
                        break
                elif len(error_output) <= max_bytes:
                    error_output.extend(chunk[: max_bytes + 1 - len(error_output)])
            if truncated:
                break
        return_code = process.wait(timeout=max(0.0, deadline - time.monotonic()))
    except subprocess.TimeoutExpired as exc:
        _terminate_git_process(process)
        raise GitWorktreeLeaseError("worktree_git_timeout", " ".join(args)) from exc
    finally:
        selector.close()
        if process.poll() is None:
            _terminate_git_process(process)
    if not truncated and return_code != 0:
        raise GitWorktreeLeaseError(
            "worktree_git_failed",
            f"{' '.join(args)}:{error_output.decode(errors='replace').strip()}",
        )
    return bytes(output[:max_bytes]), truncated


def _run_git(cwd: Path, *args: str) -> subprocess.CompletedProcess[str]:
    process = _start_git_process(cwd, *args)
    try:
        stdout, stderr = process.communicate(timeout=DEFAULT_GIT_TIMEOUT_SECONDS)
    except subprocess.TimeoutExpired as exc:
        _terminate_git_process(process)
        raise GitWorktreeLeaseError("worktree_git_timeout", " ".join(args)) from exc
    return subprocess.CompletedProcess(
        process.args,
        process.returncode,
        stdout.decode(errors="replace"),
        stderr.decode(errors="replace"),
    )


def _start_git_process(cwd: Path, *args: str) -> subprocess.Popen[bytes]:
    kwargs: dict[str, Any] = {
        "stdout": subprocess.PIPE,
        "stderr": subprocess.PIPE,
        "env": _git_environment(),
    }
    if os.name == "posix":
        kwargs["start_new_session"] = True
    elif os.name == "nt":
        kwargs["creationflags"] = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    return subprocess.Popen(_git_command(cwd, *args), **kwargs)


def _terminate_git_process(process: subprocess.Popen[bytes]) -> None:
    if os.name == "posix":
        with suppress(ProcessLookupError):
            os.killpg(process.pid, signal.SIGKILL)
    elif os.name == "nt":
        subprocess.run(
            ["taskkill", "/PID", str(process.pid), "/T", "/F"],
            check=False,
            capture_output=True,
        )
    else:
        if process.poll() is None:
            process.kill()
    process.wait()


def _git_command(cwd: Path, *args: str) -> list[str]:
    return [
        "git",
        "-c",
        "core.fsmonitor=false",
        "-c",
        "core.hooksPath=/dev/null",
        "-c",
        "core.pager=cat",
        "-c",
        "core.filemode=true",
        "-c",
        "core.ignorecase=false",
        "-c",
        f"core.excludesFile={os.devnull}",
        "-C",
        str(cwd),
        *args,
    ]


def _git_environment() -> dict[str, str]:
    environment = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
    environment.update(
        {
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_CONFIG_GLOBAL": os.devnull,
            "GIT_TERMINAL_PROMPT": "0",
            "GIT_PAGER": "cat",
            "LC_ALL": "C.UTF-8",
        }
    )
    return environment


def _bytes_sha256(value: bytes) -> str:
    return f"sha256:{hashlib.sha256(value).hexdigest()}"


def _required_text(value: str, label: str) -> str:
    if not isinstance(value, str) or not value.strip() or any(char in value for char in "\r\n\t"):
        raise GitWorktreeLeaseError(f"worktree_{label}_invalid")
    return value


def _atomic_json_write(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(
        f".{path.name}.{os.getpid()}.{secrets.token_hex(8)}.tmp"
    )
    descriptor = os.open(temporary, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    try:
        content = (canonical_json(payload) + "\n").encode()
        written = 0
        while written < len(content):
            written += os.write(descriptor, content[written:])
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    os.replace(temporary, path)
    _fsync_directory(path.parent)


def _durable_unlink(path: Path) -> None:
    if not path.exists():
        return
    path.unlink()
    _fsync_directory(path.parent)


def _fsync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _load_json_object(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise GitWorktreeLeaseError("worktree_lease_unreadable", str(path)) from exc
    if not isinstance(payload, dict):
        raise GitWorktreeLeaseError("worktree_lease_not_object", str(path))
    return payload


def _load_registered_lease(path: Path) -> GitWorktreeLease:
    record = _load_json_object(path)
    if record.get("schema") != WORKTREE_LEASE_RECORD_SCHEMA:
        raise GitWorktreeLeaseError("worktree_lease_record_schema_invalid", str(path))
    payload = record.get("lease")
    if not isinstance(payload, dict):
        raise GitWorktreeLeaseError("worktree_lease_record_payload_invalid", str(path))
    lease = GitWorktreeLease.from_payload(payload)
    if record.get("lease_sha256") != lease.sha256:
        raise GitWorktreeLeaseError("worktree_lease_hash_mismatch", lease.attempt_id)
    return lease


def _load_allocation_intent(path: Path) -> dict[str, Any]:
    if path.is_symlink():
        raise GitWorktreeLeaseError("worktree_allocation_intent_symlink_forbidden", str(path))
    record = _load_json_object(path)
    if record.get("schema") != WORKTREE_ALLOCATION_INTENT_RECORD_SCHEMA:
        raise GitWorktreeLeaseError("worktree_allocation_intent_record_schema_invalid", str(path))
    intent = record.get("intent")
    if not isinstance(intent, dict):
        raise GitWorktreeLeaseError("worktree_allocation_intent_payload_invalid", str(path))
    if record.get("intent_sha256") != canonical_sha256(intent):
        raise GitWorktreeLeaseError("worktree_allocation_intent_hash_mismatch", str(path))
    if intent.get("schema") != WORKTREE_ALLOCATION_INTENT_SCHEMA:
        raise GitWorktreeLeaseError("worktree_allocation_intent_schema_invalid", str(path))
    required = {
        "run_id",
        "plan_revision",
        "node_id",
        "attempt_id",
        "repository",
        "worktree_path",
        "base_commit",
        "allowed_paths",
        "owner",
        "created_at",
        "expires_at",
    }
    if any(key not in intent for key in required):
        raise GitWorktreeLeaseError("worktree_allocation_intent_incomplete", str(path))
    return intent
