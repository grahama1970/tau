from __future__ import annotations

import json
import os
import shutil
import stat
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from pathlib import Path

import pytest

import tau_coding.runtime_backends.worktrees as worktrees_module
from tau_coding.runtime_backends import (
    GitWorktreeLeaseError,
    GitWorktreeLeaseManager,
    worktree_discard_authorization,
)


def _git(cwd: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(cwd), *args],
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


@pytest.fixture
def repository(tmp_path: Path) -> Path:
    repo = tmp_path / "repository"
    repo.mkdir()
    _git(repo, "init", "--initial-branch=main")
    _git(repo, "config", "user.email", "tau-tests@example.invalid")
    _git(repo, "config", "user.name", "Tau Tests")
    (repo / "src").mkdir()
    (repo / "src" / "allowed.txt").write_text("base\n", encoding="utf-8")
    (repo / "outside.txt").write_text("outside\n", encoding="utf-8")
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "fixture")
    return repo


def _allocate(
    manager: GitWorktreeLeaseManager,
    repository: Path,
    *,
    attempt_id: str = "attempt-1",
):
    return manager.allocate(
        repository=repository,
        run_id="run-1",
        plan_revision="plan-1",
        node_id="writer",
        attempt_id=attempt_id,
        base_commit="HEAD",
        allowed_paths=("src",),
    )


def test_real_worktree_is_distinct_hash_bound_and_restart_rediscoverable(
    tmp_path: Path, repository: Path
) -> None:
    state_root = tmp_path / "lease-state"
    manager = GitWorktreeLeaseManager(state_root)
    lease = _allocate(manager, repository)

    assert Path(lease.worktree_path).is_dir()
    assert Path(lease.worktree_path).resolve() != repository.resolve()
    assert _git(Path(lease.worktree_path), "rev-parse", "HEAD") == lease.base_commit
    assert lease.head_commit == lease.base_commit
    assert lease.detached is True
    assert lease.branch is None
    assert lease.allowed_paths == ("src",)
    assert lease.pre_status_sha256.startswith("sha256:")

    restarted = GitWorktreeLeaseManager(state_root)
    assert restarted.rediscover(run_id="run-1") == (lease,)
    inspection = restarted.inspect(lease)
    assert inspection["status"] == "PASS"
    assert inspection["dirty"] is False

    cleanup = restarted.cleanup(lease)
    assert cleanup["status"] == "PASS"
    assert cleanup["post_verified_absent"] is True
    assert not Path(lease.worktree_path).exists()
    assert len(tuple((state_root / "retired-leases").glob("*.json"))) == 1
    with pytest.raises(GitWorktreeLeaseError, match="worktree_attempt_retired"):
        _allocate(restarted, repository)


def test_symlinked_state_root_is_rejected_before_storage_mutation(
    tmp_path: Path
) -> None:
    target = tmp_path / "redirected-state"
    target.mkdir()
    configured = tmp_path / "configured-state"
    configured.symlink_to(target, target_is_directory=True)

    with pytest.raises(GitWorktreeLeaseError, match="worktree_state_root_symlink_forbidden"):
        GitWorktreeLeaseManager(configured)

    assert not tuple(target.iterdir())


def test_attempt_identity_cannot_share_mutable_worktree(tmp_path: Path, repository: Path) -> None:
    manager = GitWorktreeLeaseManager(tmp_path / "lease-state")
    _allocate(manager, repository)

    with pytest.raises(GitWorktreeLeaseError, match="worktree_attempt_already_leased"):
        _allocate(manager, repository)


def test_lease_expiry_starts_after_allocation_lock_is_acquired(
    tmp_path: Path, repository: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manager = GitWorktreeLeaseManager(tmp_path / "lease-state")
    original_locked = manager._locked

    @contextmanager
    def delayed_lock():
        with original_locked():
            time.sleep(1.1)
            yield

    monkeypatch.setattr(manager, "_locked", delayed_lock)
    lease = manager.allocate(
        repository=repository,
        run_id="run-1",
        plan_revision="plan-1",
        node_id="writer",
        attempt_id="attempt-1",
        base_commit="HEAD",
        allowed_paths=("src",),
        expires_in_seconds=1,
    )
    monkeypatch.setattr(manager, "_locked", original_locked)

    assert manager.inspect(lease)["status"] == "PASS"
    assert manager.cleanup(lease)["status"] == "PASS"


def test_linked_checkout_cannot_bypass_attempt_identity(
    tmp_path: Path, repository: Path
) -> None:
    manager = GitWorktreeLeaseManager(tmp_path / "lease-state")
    _allocate(manager, repository)
    linked = tmp_path / "linked-source"
    _git(repository, "worktree", "add", "--detach", str(linked), "HEAD")

    with pytest.raises(GitWorktreeLeaseError, match="worktree_attempt_already_leased"):
        _allocate(manager, linked)

    _git(repository, "worktree", "remove", "--force", str(linked))


def test_lease_from_linked_checkout_survives_source_checkout_removal(
    tmp_path: Path, repository: Path
) -> None:
    manager = GitWorktreeLeaseManager(tmp_path / "lease-state")
    linked = tmp_path / "linked-source"
    _git(repository, "worktree", "add", "--detach", str(linked), "HEAD")
    lease = _allocate(manager, linked)
    assert lease.repository == str(repository.resolve())

    _git(repository, "worktree", "remove", "--force", str(linked))

    assert GitWorktreeLeaseManager(tmp_path / "lease-state").rediscover() == (lease,)
    assert manager.cleanup(lease)["status"] == "PASS"


def test_linked_checkout_resolves_base_revision_from_callers_checkout(
    tmp_path: Path, repository: Path
) -> None:
    first_commit = _git(repository, "rev-parse", "HEAD")
    (repository / "src" / "allowed.txt").write_text("new primary head\n", encoding="utf-8")
    _git(repository, "add", "src/allowed.txt")
    _git(repository, "commit", "-m", "advance primary")
    linked = tmp_path / "linked-old-head"
    _git(repository, "worktree", "add", "--detach", str(linked), first_commit)
    manager = GitWorktreeLeaseManager(tmp_path / "lease-state")

    lease = _allocate(manager, linked)

    assert lease.repository == str(repository.resolve())
    assert lease.base_commit == first_commit
    assert lease.head_commit == first_commit
    assert manager.cleanup(lease)["status"] == "PASS"
    _git(repository, "worktree", "remove", "--force", str(linked))


def test_concurrent_attempts_receive_distinct_real_worktrees(
    tmp_path: Path, repository: Path
) -> None:
    state_root = tmp_path / "lease-state"

    def allocate(attempt: int):
        return _allocate(
            GitWorktreeLeaseManager(state_root),
            repository,
            attempt_id=f"attempt-{attempt}",
        )

    with ThreadPoolExecutor(max_workers=2) as executor:
        leases = tuple(executor.map(allocate, (1, 2)))

    assert len({lease.worktree_path for lease in leases}) == 2
    assert len({lease.attempt_id for lease in leases}) == 2
    assert all(Path(lease.worktree_path).is_dir() for lease in leases)

    manager = GitWorktreeLeaseManager(state_root)
    assert manager.cleanup(leases[0])["status"] == "PASS"
    assert manager.cleanup(leases[1])["status"] == "PASS"


def test_dirty_worktree_cleanup_requires_admission_or_exact_discard(
    tmp_path: Path, repository: Path
) -> None:
    manager = GitWorktreeLeaseManager(tmp_path / "lease-state")
    lease = _allocate(manager, repository)
    (Path(lease.worktree_path) / "src" / "allowed.txt").write_text("changed\n", encoding="utf-8")

    inspection = manager.inspect(lease)
    assert inspection["status"] == "PASS"
    assert inspection["dirty"] is True
    assert inspection["changed_paths"] == ["src/allowed.txt"]
    blocked = manager.cleanup(lease)
    assert blocked["status"] == "BLOCKED"
    assert blocked["errors"] == ["worktree_unadmitted_changes"]
    assert Path(lease.worktree_path).exists()

    wrong_authorization = dict(worktree_discard_authorization(lease, inspection))
    wrong_authorization["diff_sha256"] = "sha256:" + "0" * 64
    assert manager.cleanup(lease, discard_authorization=wrong_authorization)["status"] == (
        "BLOCKED"
    )

    authorization = worktree_discard_authorization(lease, inspection)
    cleanup = manager.cleanup(lease, discard_authorization=authorization)
    assert cleanup["status"] == "PASS"
    assert cleanup["removed"] is True


@pytest.mark.parametrize("index_flag", ["--assume-unchanged", "--skip-worktree"])
def test_index_flags_cannot_hide_disallowed_tracked_changes(
    tmp_path: Path, repository: Path, index_flag: str
) -> None:
    manager = GitWorktreeLeaseManager(tmp_path / "lease-state")
    lease = _allocate(manager, repository)
    worktree = Path(lease.worktree_path)
    _git(worktree, "update-index", index_flag, "outside.txt")
    (worktree / "outside.txt").write_text("concealed\n", encoding="utf-8")

    inspection = manager.inspect(lease)

    assert inspection["status"] == "BLOCKED"
    assert inspection["dirty"] is True
    assert inspection["changed_paths"] == ["outside.txt"]
    assert "worktree_path_not_allowed:outside.txt" in inspection["errors"]
    assert "worktree_index_flag_forbidden:outside.txt" in inspection["errors"]
    cleanup = manager.cleanup(lease)
    assert cleanup["status"] == "BLOCKED"
    assert Path(lease.worktree_path).exists()


def test_untracked_executable_mode_change_invalidates_admission(
    tmp_path: Path, repository: Path
) -> None:
    manager = GitWorktreeLeaseManager(tmp_path / "lease-state")
    lease = _allocate(manager, repository)
    script = Path(lease.worktree_path) / "src" / "run.sh"
    script.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    script.chmod(0o644)
    admission = manager.admit(lease)

    script.chmod(0o755)
    inspection = manager.inspect(lease)

    assert inspection["diff_sha256"] != admission["diff_sha256"]
    cleanup = manager.cleanup(lease)
    assert cleanup["status"] == "BLOCKED"
    assert cleanup["errors"] == ["worktree_unadmitted_changes"]
    assert script.exists()


def test_shared_repository_ref_mutation_blocks_admission_and_cleanup(
    tmp_path: Path, repository: Path
) -> None:
    manager = GitWorktreeLeaseManager(tmp_path / "lease-state")
    lease = _allocate(manager, repository)
    worktree = Path(lease.worktree_path)
    _git(worktree, "update-ref", "refs/heads/lease-escape", "HEAD")

    inspection = manager.inspect(lease)

    assert inspection["status"] == "BLOCKED"
    assert inspection["hash_complete"] is True
    assert "worktree_repository_control_changed" in inspection["errors"]
    with pytest.raises(GitWorktreeLeaseError, match="worktree_admission_inspection_blocked"):
        manager.admit(lease)
    assert manager.cleanup(lease)["status"] == "BLOCKED"
    authorization = worktree_discard_authorization(lease, inspection)
    blocked = manager.cleanup(lease, discard_authorization=authorization)
    assert blocked["status"] == "BLOCKED"
    assert blocked["errors"] == ["worktree_repository_control_restore_required"]
    assert Path(lease.worktree_path).exists()
    _git(repository, "update-ref", "-d", "refs/heads/lease-escape")
    assert manager.cleanup(lease)["status"] == "PASS"


def test_git_timeout_fails_closed_without_holding_operation_forever(
    tmp_path: Path,
    repository: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager = GitWorktreeLeaseManager(tmp_path / "lease-state")
    lease = _allocate(manager, repository)
    monkeypatch.setattr(worktrees_module, "DEFAULT_GIT_TIMEOUT_SECONDS", 0.05)
    monkeypatch.setattr(
        worktrees_module,
        "_git_command",
        lambda *_args: [sys.executable, "-c", "import time; time.sleep(2)"],
    )

    started = time.monotonic()
    with pytest.raises(GitWorktreeLeaseError, match="worktree_git_timeout"):
        manager.inspect(lease)

    assert time.monotonic() - started < 1.0


def test_git_timeout_terminates_descendant_processes(
    tmp_path: Path,
    repository: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager = GitWorktreeLeaseManager(tmp_path / "lease-state")
    lease = _allocate(manager, repository)
    marker = tmp_path / "descendant-side-effect"
    child = f"import time; time.sleep(.4); open({str(marker)!r}, 'w').write('late')"
    parent = (
        "import subprocess,sys; "
        f"subprocess.Popen([sys.executable, '-c', {child!r}])"
    )
    monkeypatch.setattr(worktrees_module, "DEFAULT_GIT_TIMEOUT_SECONDS", 0.05)
    monkeypatch.setattr(
        worktrees_module,
        "_git_command",
        lambda *_args: [sys.executable, "-c", parent],
    )

    with pytest.raises(GitWorktreeLeaseError, match="worktree_git_timeout"):
        manager.inspect(lease)
    time.sleep(0.7)

    assert not marker.exists()


def test_cleanup_blocks_while_writer_guard_is_active(
    tmp_path: Path, repository: Path
) -> None:
    manager = GitWorktreeLeaseManager(tmp_path / "lease-state")
    lease = _allocate(manager, repository)

    with manager.writer_guard(lease) as worktree:
        (worktree / "src" / "active.txt").write_text("active\n", encoding="utf-8")
        with pytest.raises(GitWorktreeLeaseError, match="worktree_writer_active"):
            manager.inspect(lease)
        with pytest.raises(GitWorktreeLeaseError, match="worktree_writer_active"):
            manager.admit(lease)
        blocked = manager.cleanup(lease)
        assert blocked["status"] == "BLOCKED"
        assert blocked["errors"] == ["worktree_writer_active"]
        assert Path(lease.worktree_path).exists()

    inspection = manager.inspect(lease)
    authorization = worktree_discard_authorization(lease, inspection)
    assert manager.cleanup(lease, discard_authorization=authorization)["status"] == "PASS"


def test_writer_guard_rechecks_registration_after_lock(
    tmp_path: Path, repository: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manager = GitWorktreeLeaseManager(tmp_path / "lease-state")
    lease = _allocate(manager, repository)

    @contextmanager
    def remove_before_lock_yields(_lease, *, blocking):
        assert blocking is False
        _git(repository, "worktree", "remove", "--force", lease.worktree_path)
        manager._lease_path(lease).unlink()
        yield

    monkeypatch.setattr(manager, "_writer_lock", remove_before_lock_yields)
    with (
        pytest.raises(GitWorktreeLeaseError, match="worktree_lease_not_registered"),
        manager.writer_guard(lease),
    ):
        pass


def test_admitted_diff_can_be_cleaned_without_discard(tmp_path: Path, repository: Path) -> None:
    manager = GitWorktreeLeaseManager(tmp_path / "lease-state")
    lease = _allocate(manager, repository)
    (Path(lease.worktree_path) / "src" / "new.txt").write_text("new\n", encoding="utf-8")

    admission = manager.admit(lease)
    assert admission["status"] == "PASS"
    assert admission["changed_paths"] == ["src/new.txt"]
    assert manager.cleanup(lease)["status"] == "PASS"


def test_disallowed_path_and_symlink_escape_block_admission_and_cleanup(
    tmp_path: Path, repository: Path
) -> None:
    manager = GitWorktreeLeaseManager(tmp_path / "lease-state")
    lease = _allocate(manager, repository)
    worktree = Path(lease.worktree_path)
    (worktree / "outside.txt").write_text("mutated\n", encoding="utf-8")
    external = tmp_path / "external.txt"
    external.write_text("external\n", encoding="utf-8")
    (worktree / "src" / "escape").symlink_to(external)

    inspection = manager.inspect(lease)
    assert inspection["status"] == "BLOCKED"
    assert inspection["disallowed_paths"] == ["outside.txt"]
    assert inspection["escaped_paths"] == ["src/escape"]
    with pytest.raises(GitWorktreeLeaseError, match="worktree_admission_inspection_blocked"):
        manager.admit(lease)
    assert manager.cleanup(lease)["status"] == "BLOCKED"
    assert Path(lease.worktree_path).exists()

    with pytest.raises(
        GitWorktreeLeaseError,
        match="worktree_discard_requires_complete_hash",
    ):
        worktree_discard_authorization(lease, inspection)
    (worktree / "src" / "escape").unlink()
    authorization = worktree_discard_authorization(lease, manager.inspect(lease))
    assert manager.cleanup(lease, discard_authorization=authorization)["status"] == "PASS"


def test_committed_symlink_descendant_blocks_allocation_before_writer_access(
    tmp_path: Path, repository: Path
) -> None:
    external = tmp_path / "external.txt"
    external.write_text("outside\n", encoding="utf-8")
    (repository / "src" / "escape").symlink_to(external)
    _git(repository, "add", "src/escape")
    _git(repository, "commit", "-m", "symlink fixture")
    manager = GitWorktreeLeaseManager(tmp_path / "lease-state")

    with pytest.raises(
        GitWorktreeLeaseError,
        match="worktree_symlink_forbidden",
    ):
        _allocate(manager, repository)

    assert external.read_text(encoding="utf-8") == "outside\n"
    assert _git(repository, "worktree", "list", "--porcelain").count("worktree ") == 1


def test_committed_symlink_outside_allowlist_blocks_allocation(
    tmp_path: Path, repository: Path
) -> None:
    external = tmp_path / "external.txt"
    external.write_text("outside\n", encoding="utf-8")
    (repository / "outside-link").symlink_to(external)
    _git(repository, "add", "outside-link")
    _git(repository, "commit", "-m", "outside symlink fixture")

    with pytest.raises(GitWorktreeLeaseError, match="worktree_symlink_forbidden"):
        _allocate(GitWorktreeLeaseManager(tmp_path / "lease-state"), repository)

    assert external.read_text(encoding="utf-8") == "outside\n"


def test_repository_post_checkout_hook_is_not_executed_during_allocation(
    tmp_path: Path, repository: Path
) -> None:
    marker = tmp_path / "hook-side-effect"
    hook = repository / ".git" / "hooks" / "post-checkout"
    hook.write_text(f"#!/bin/sh\nprintf hook > {marker}\n", encoding="utf-8")
    hook.chmod(0o755)
    manager = GitWorktreeLeaseManager(tmp_path / "lease-state")

    lease = _allocate(manager, repository)

    assert not marker.exists()
    assert manager.inspect(lease)["status"] == "PASS"
    assert manager.cleanup(lease)["status"] == "PASS"


def test_clean_uninitialized_submodule_is_not_reported_as_untracked_empty_directory(
    tmp_path: Path, repository: Path
) -> None:
    submodule = tmp_path / "submodule"
    submodule.mkdir()
    _git(submodule, "init", "--initial-branch=main")
    _git(submodule, "config", "user.email", "tau-tests@example.invalid")
    _git(submodule, "config", "user.name", "Tau Tests")
    (submodule / "payload").write_text("payload\n", encoding="utf-8")
    _git(submodule, "add", ".")
    _git(submodule, "commit", "-m", "submodule fixture")
    _git(
        repository,
        "-c",
        "protocol.file.allow=always",
        "submodule",
        "add",
        str(submodule),
        "module",
    )
    _git(repository, "commit", "-m", "add submodule")
    manager = GitWorktreeLeaseManager(tmp_path / "lease-state")

    lease = _allocate(manager, repository)
    inspection = manager.inspect(lease)

    assert inspection["status"] == "PASS"
    assert inspection["dirty"] is False
    assert inspection["changed_paths"] == []
    assert manager.cleanup(lease)["status"] == "PASS"


@pytest.mark.parametrize(
    "config_key",
    [
        "filter.evil.smudge",
        "diff.evil.textconv",
    ],
)
def test_external_git_content_drivers_block_before_worktree_creation(
    tmp_path: Path,
    repository: Path,
    config_key: str,
) -> None:
    marker = tmp_path / "driver-side-effect"
    _git(repository, "config", config_key, f"sh -c 'touch {marker}'")
    manager = GitWorktreeLeaseManager(tmp_path / "lease-state")

    with pytest.raises(GitWorktreeLeaseError, match="worktree_external_git_driver_forbidden"):
        _allocate(manager, repository)

    assert _git(repository, "worktree", "list", "--porcelain").count("worktree ") == 1
    assert not marker.exists()


def test_worktree_local_filter_injection_blocks_before_filter_execution(
    tmp_path: Path, repository: Path
) -> None:
    _git(repository, "config", "extensions.worktreeConfig", "true")
    (repository / ".gitattributes").write_text("src/allowed.txt filter=evil\n", encoding="utf-8")
    _git(repository, "add", ".gitattributes")
    _git(repository, "commit", "-m", "filter fixture")
    manager = GitWorktreeLeaseManager(tmp_path / "lease-state")
    lease = _allocate(manager, repository)
    marker = tmp_path / "filter-side-effect"
    _git(
        Path(lease.worktree_path),
        "config",
        "--worktree",
        "filter.evil.clean",
        f"sh -c 'touch {marker}; cat'",
    )

    with pytest.raises(
        GitWorktreeLeaseError, match="worktree_external_git_driver_forbidden"
    ):
        manager.inspect(lease)

    assert not marker.exists()


def test_external_git_config_include_blocks_before_worktree_creation(
    tmp_path: Path, repository: Path
) -> None:
    included = tmp_path / "included.conf"
    included.write_text("[core]\n\tfilemode = false\n", encoding="utf-8")
    _git(repository, "config", "include.path", str(included))

    with pytest.raises(
        GitWorktreeLeaseError, match="worktree_external_git_include_forbidden"
    ):
        _allocate(GitWorktreeLeaseManager(tmp_path / "lease-state"), repository)

    assert _git(repository, "worktree", "list", "--porcelain").count("worktree ") == 1


def test_top_level_gitfile_symlink_blocks_inspection(
    tmp_path: Path, repository: Path
) -> None:
    manager = GitWorktreeLeaseManager(tmp_path / "lease-state")
    lease = _allocate(manager, repository)
    gitfile = Path(lease.worktree_path) / ".git"
    external = tmp_path / "external-gitfile"
    external.write_bytes(gitfile.read_bytes())
    gitfile.unlink()
    gitfile.symlink_to(external)

    with pytest.raises(GitWorktreeLeaseError, match="worktree_gitfile_invalid"):
        manager.inspect(lease)


def test_linked_gitdir_parent_symlink_blocks_before_cleanup(
    tmp_path: Path, repository: Path
) -> None:
    manager = GitWorktreeLeaseManager(tmp_path / "lease-state")
    lease = _allocate(manager, repository)
    worktrees_admin = repository / ".git" / "worktrees"
    external = tmp_path / "external-worktrees-admin"
    worktrees_admin.rename(external)
    worktrees_admin.symlink_to(external, target_is_directory=True)

    with pytest.raises(GitWorktreeLeaseError, match="worktree_gitdir_parent_invalid"):
        manager.inspect(lease)
    with pytest.raises(GitWorktreeLeaseError, match="worktree_gitdir_parent_invalid"):
        manager.cleanup(lease)

    assert external.exists()


def test_linked_commondir_redirection_blocks_inspection_and_cleanup(
    tmp_path: Path, repository: Path
) -> None:
    manager = GitWorktreeLeaseManager(tmp_path / "lease-state")
    lease = _allocate(manager, repository)
    worktree = Path(lease.worktree_path)
    gitdir = Path((worktree / ".git").read_text(encoding="utf-8").strip().removeprefix("gitdir: "))
    foreign = tmp_path / "foreign"
    _git(tmp_path, "clone", str(repository), str(foreign))
    (gitdir / "commondir").write_text(str(foreign / ".git") + "\n", encoding="utf-8")

    with pytest.raises(GitWorktreeLeaseError, match="worktree_commondir_changed"):
        manager.inspect(lease)
    with pytest.raises(GitWorktreeLeaseError, match="worktree_commondir_changed"):
        manager.cleanup(lease)


def test_primary_common_directory_symlink_blocks_inspection_and_cleanup(
    tmp_path: Path, repository: Path
) -> None:
    manager = GitWorktreeLeaseManager(tmp_path / "lease-state")
    lease = _allocate(manager, repository)
    external = tmp_path / "external-common-dir"
    (repository / ".git").rename(external)
    (repository / ".git").symlink_to(external, target_is_directory=True)

    with pytest.raises(GitWorktreeLeaseError, match="worktree_common_dir_invalid"):
        manager.inspect(lease)
    with pytest.raises(GitWorktreeLeaseError, match="worktree_common_dir_invalid"):
        manager.cleanup(lease)


def test_core_filemode_false_cannot_hide_disallowed_tracked_mode_change(
    tmp_path: Path, repository: Path
) -> None:
    _git(repository, "config", "core.filemode", "false")
    manager = GitWorktreeLeaseManager(tmp_path / "lease-state")
    lease = _allocate(manager, repository)
    outside = Path(lease.worktree_path) / "outside.txt"
    outside.chmod(outside.stat().st_mode | stat.S_IXUSR)

    inspection = manager.inspect(lease)

    assert inspection["status"] == "BLOCKED"
    assert inspection["dirty"] is True
    assert inspection["changed_paths"] == ["outside.txt"]
    assert "worktree_path_not_allowed:outside.txt" in inspection["errors"]
    assert manager.cleanup(lease)["status"] == "BLOCKED"


def test_symlinked_repository_refs_directory_blocks_shared_control_admission(
    tmp_path: Path, repository: Path
) -> None:
    _git(repository, "config", "core.logAllRefUpdates", "false")
    manager = GitWorktreeLeaseManager(tmp_path / "lease-state")
    lease = _allocate(manager, repository)
    refs = repository / ".git" / "refs"
    external = tmp_path / "external-refs"
    refs.rename(external)
    refs.symlink_to(external, target_is_directory=True)
    _git(Path(lease.worktree_path), "update-ref", "refs/heads/escaped", "HEAD")

    inspection = manager.inspect(lease)

    assert inspection["status"] == "BLOCKED"
    assert "worktree_repository_control_special_directory" in inspection["errors"]
    assert manager.cleanup(lease)["status"] == "BLOCKED"
    assert (external / "heads" / "escaped").exists()


def test_full_worktree_validation_has_an_explicit_entry_bound(
    tmp_path: Path, repository: Path
) -> None:
    manager = GitWorktreeLeaseManager(
        tmp_path / "lease-state",
        max_validation_entries=1,
    )

    with pytest.raises(
        GitWorktreeLeaseError,
        match="worktree_validation_entry_limit_exceeded",
    ):
        _allocate(manager, repository)

    assert _git(repository, "worktree", "list", "--porcelain").count("worktree ") == 1


def test_nested_repository_blocks_incomplete_admission_and_discard(
    tmp_path: Path, repository: Path
) -> None:
    manager = GitWorktreeLeaseManager(tmp_path / "lease-state")
    lease = _allocate(manager, repository)
    nested = Path(lease.worktree_path) / "src" / "nested"
    nested.mkdir()
    _git(nested, "init", "--initial-branch=main")
    (nested / "payload.txt").write_text("untracked nested payload\n", encoding="utf-8")

    inspection = manager.inspect(lease)

    assert inspection["status"] == "BLOCKED"
    assert inspection["hash_complete"] is False
    assert any("worktree_nested_repository_forbidden" in error for error in inspection["errors"])
    with pytest.raises(
        GitWorktreeLeaseError,
        match="worktree_discard_requires_complete_hash",
    ):
        worktree_discard_authorization(lease, inspection)
    shutil.rmtree(nested)
    assert manager.cleanup(lease)["status"] == "PASS"


def test_nested_git_metadata_cannot_escape_inventory_and_admission(
    tmp_path: Path, repository: Path
) -> None:
    manager = GitWorktreeLeaseManager(tmp_path / "lease-state")
    lease = _allocate(manager, repository)
    hidden = Path(lease.worktree_path) / "src" / "box" / ".git"
    hidden.mkdir(parents=True)
    (hidden / "secret").write_text("must not be silently deleted\n", encoding="utf-8")
    (hidden.parent / "visible").write_text("visible\n", encoding="utf-8")

    inspection = manager.inspect(lease)

    assert inspection["status"] == "BLOCKED"
    assert inspection["hash_complete"] is False
    assert "src/box/.git" in inspection["changed_paths"]
    assert any("worktree_nested_repository_forbidden" in error for error in inspection["errors"])
    with pytest.raises(GitWorktreeLeaseError, match="worktree_admission_inspection_blocked"):
        manager.admit(lease)
    assert manager.cleanup(lease)["status"] == "BLOCKED"
    assert (hidden / "secret").exists()


def test_hash_limit_blocks_admission_and_discard_without_reading_unbounded_file(
    tmp_path: Path, repository: Path
) -> None:
    manager = GitWorktreeLeaseManager(tmp_path / "lease-state", max_hash_bytes=1024)
    lease = _allocate(manager, repository)
    huge = Path(lease.worktree_path) / "src" / "huge.bin"
    with huge.open("wb") as handle:
        handle.truncate(4096)

    inspection = manager.inspect(lease)

    assert inspection["status"] == "BLOCKED"
    assert inspection["hash_complete"] is False
    assert "worktree_hash_byte_limit_exceeded" in inspection["errors"]
    with pytest.raises(
        GitWorktreeLeaseError,
        match="worktree_discard_requires_complete_hash",
    ):
        worktree_discard_authorization(lease, inspection)
    huge.unlink()
    assert manager.cleanup(lease)["status"] == "PASS"


def test_file_growth_during_hashing_is_bounded(
    tmp_path: Path, repository: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manager = GitWorktreeLeaseManager(tmp_path / "lease-state", max_hash_bytes=2048)
    lease = _allocate(manager, repository)
    growing = Path(lease.worktree_path) / "src" / "growing.bin"
    growing.write_bytes(b"a" * 512)
    original_open = Path.open

    def grow_before_read(path: Path, *args, **kwargs):
        if path == growing and args and args[0] == "rb":
            with original_open(path, "ab") as handle:
                handle.write(b"b" * 4096)
        return original_open(path, *args, **kwargs)

    monkeypatch.setattr(Path, "open", grow_before_read)
    inspection = manager.inspect(lease)

    assert inspection["status"] == "BLOCKED"
    assert inspection["hash_complete"] is False
    assert "worktree_hash_byte_limit_exceeded" in inspection["errors"]


def test_large_tracked_blob_blocks_before_binary_diff_processing(
    tmp_path: Path, repository: Path
) -> None:
    large = repository / "src" / "large.bin"
    large.write_bytes(b"x" * 4096)
    _git(repository, "add", "src/large.bin")
    _git(repository, "commit", "-m", "large tracked fixture")
    manager = GitWorktreeLeaseManager(tmp_path / "lease-state", max_hash_bytes=1024)
    lease = _allocate(manager, repository)
    (Path(lease.worktree_path) / "src" / "large.bin").unlink()

    inspection = manager.inspect(lease)

    assert inspection["status"] == "BLOCKED"
    assert inspection["hash_complete"] is False
    assert "worktree_hash_byte_limit_exceeded" in inspection["errors"]


def test_entry_limit_marks_hash_incomplete_and_blocks_discard(
    tmp_path: Path, repository: Path
) -> None:
    manager = GitWorktreeLeaseManager(tmp_path / "lease-state", max_hash_entries=1)
    lease = _allocate(manager, repository)
    worktree = Path(lease.worktree_path)
    (worktree / "src" / "one.txt").write_text("one\n", encoding="utf-8")
    (worktree / "src" / "two.txt").write_text("two\n", encoding="utf-8")

    inspection = manager.inspect(lease)

    assert inspection["status"] == "BLOCKED"
    assert inspection["hash_complete"] is False
    assert "worktree_hash_entry_limit_exceeded" in inspection["errors"]
    with pytest.raises(
        GitWorktreeLeaseError,
        match="worktree_discard_requires_complete_hash",
    ):
        worktree_discard_authorization(lease, inspection)
    (worktree / "src" / "one.txt").unlink()
    (worktree / "src" / "two.txt").unlink()
    assert manager.cleanup(lease)["status"] == "PASS"


def test_untracked_empty_directory_requires_exact_admission_or_discard(
    tmp_path: Path, repository: Path
) -> None:
    manager = GitWorktreeLeaseManager(tmp_path / "lease-state")
    lease = _allocate(manager, repository)
    empty = Path(lease.worktree_path) / "src" / "valuable-empty-dir"
    empty.mkdir()

    inspection = manager.inspect(lease)

    assert inspection["dirty"] is True
    assert inspection["changed_paths"] == ["src/valuable-empty-dir"]
    assert inspection["hash_complete"] is True
    assert manager.cleanup(lease)["errors"] == ["worktree_unadmitted_changes"]
    authorization = worktree_discard_authorization(lease, inspection)
    assert manager.cleanup(lease, discard_authorization=authorization)["status"] == "PASS"


def test_directory_mode_change_invalidates_prior_admission(
    tmp_path: Path, repository: Path
) -> None:
    manager = GitWorktreeLeaseManager(tmp_path / "lease-state")
    lease = _allocate(manager, repository)
    directory = Path(lease.worktree_path) / "src" / "mode-bound"
    directory.mkdir(mode=0o755)
    (directory / "payload").write_text("payload\n", encoding="utf-8")
    admission = manager.admit(lease)

    directory.chmod(0o700)
    inspection = manager.inspect(lease)

    assert inspection["diff_sha256"] != admission["diff_sha256"]
    assert "src/mode-bound" in inspection["changed_paths"]
    assert manager.cleanup(lease)["errors"] == ["worktree_unadmitted_changes"]
    authorization = worktree_discard_authorization(lease, inspection)
    assert manager.cleanup(lease, discard_authorization=authorization)["status"] == "PASS"


def test_special_files_block_hash_completion_and_automated_cleanup(
    tmp_path: Path, repository: Path
) -> None:
    manager = GitWorktreeLeaseManager(tmp_path / "lease-state")
    lease = _allocate(manager, repository)
    pipe = Path(lease.worktree_path) / "src" / "work.pipe"
    os.mkfifo(pipe)

    inspection = manager.inspect(lease)

    assert inspection["status"] == "BLOCKED"
    assert inspection["hash_complete"] is False
    assert "worktree_special_file_unsupported:src/work.pipe" in inspection["errors"]
    pipe.unlink()
    assert manager.cleanup(lease)["status"] == "PASS"


def test_diff_hash_frames_paths_and_content_without_concatenation_collision(
    tmp_path: Path, repository: Path
) -> None:
    manager = GitWorktreeLeaseManager(tmp_path / "lease-state")
    lease = _allocate(manager, repository)
    worktree = Path(lease.worktree_path)
    first = worktree / "src" / "a"
    first.write_bytes(b"bc")
    first_inspection = manager.inspect(lease)
    first.unlink()
    second = worktree / "src" / "ab"
    second.write_bytes(b"c")
    second_inspection = manager.inspect(lease)

    assert first_inspection["diff_sha256"] != second_inspection["diff_sha256"]
    admission = manager.admit(lease)
    assert admission["diff_sha256"] == second_inspection["diff_sha256"]
    assert manager.cleanup(lease)["status"] == "PASS"


def test_discard_authorization_cannot_replay_after_head_changes(
    tmp_path: Path, repository: Path
) -> None:
    manager = GitWorktreeLeaseManager(tmp_path / "lease-state")
    lease = _allocate(manager, repository)
    clean = manager.inspect(lease)
    stale_authorization = worktree_discard_authorization(lease, clean)
    worktree = Path(lease.worktree_path)
    (worktree / "src" / "committed.txt").write_text("committed\n", encoding="utf-8")
    _git(worktree, "add", "src/committed.txt")
    _git(
        worktree,
        "-c",
        "user.name=Tau",
        "-c",
        "user.email=tau@example.invalid",
        "commit",
        "-m",
        "unexpected head",
    )

    moved = manager.inspect(lease)

    assert moved["status"] == "BLOCKED"
    assert "worktree_head_changed" in moved["errors"]
    assert moved["diff_sha256"] != clean["diff_sha256"]
    result = manager.cleanup(lease, discard_authorization=stale_authorization)
    assert result["status"] == "BLOCKED"
    assert Path(lease.worktree_path).exists()
    exact = worktree_discard_authorization(lease, moved)
    assert manager.cleanup(lease, discard_authorization=exact)["status"] == "PASS"


def test_rename_from_disallowed_path_keeps_source_in_policy_check(
    tmp_path: Path, repository: Path
) -> None:
    manager = GitWorktreeLeaseManager(tmp_path / "lease-state")
    lease = _allocate(manager, repository)
    worktree = Path(lease.worktree_path)
    _git(worktree, "mv", "outside.txt", "src/renamed.txt")

    inspection = manager.inspect(lease)

    assert inspection["status"] == "BLOCKED"
    assert inspection["changed_paths"] == ["outside.txt", "src/renamed.txt"]
    assert inspection["disallowed_paths"] == ["outside.txt"]
    authorization = worktree_discard_authorization(lease, inspection)
    assert manager.cleanup(lease, discard_authorization=authorization)["status"] == "PASS"


def test_ignored_files_are_dirty_and_require_admission_or_discard(
    tmp_path: Path, repository: Path
) -> None:
    (repository / ".gitignore").write_text("*.ignored\n", encoding="utf-8")
    _git(repository, "add", ".gitignore")
    _git(repository, "commit", "-m", "ignore fixture")
    manager = GitWorktreeLeaseManager(tmp_path / "lease-state")
    lease = _allocate(manager, repository)
    ignored = Path(lease.worktree_path) / "src" / "generated.ignored"
    ignored.write_text("must not disappear silently\n", encoding="utf-8")

    inspection = manager.inspect(lease)

    assert inspection["status"] == "PASS"
    assert inspection["dirty"] is True
    assert inspection["changed_paths"] == ["src/generated.ignored"]
    assert manager.cleanup(lease)["errors"] == ["worktree_unadmitted_changes"]
    authorization = worktree_discard_authorization(lease, inspection)
    assert manager.cleanup(lease, discard_authorization=authorization)["status"] == "PASS"


@pytest.mark.parametrize("allowed", (("../escape",), ("/absolute",), (".",), (".git/config",), ()))
def test_unsafe_allowed_path_contracts_fail_before_git_mutation(
    tmp_path: Path, repository: Path, allowed: tuple[str, ...]
) -> None:
    manager = GitWorktreeLeaseManager(tmp_path / "lease-state")

    with pytest.raises(GitWorktreeLeaseError, match="worktree_allowed_path"):
        manager.allocate(
            repository=repository,
            run_id="run-1",
            plan_revision="plan-1",
            node_id="writer",
            attempt_id="attempt-1",
            base_commit="HEAD",
            allowed_paths=allowed,
        )

    assert _git(repository, "worktree", "list", "--porcelain").count("worktree ") == 1
    assert not (repository / ".tau").exists()


def test_lease_state_root_inside_primary_checkout_is_rejected(repository: Path) -> None:
    manager = GitWorktreeLeaseManager(repository / ".tau" / "runtime-leases")

    with pytest.raises(
        GitWorktreeLeaseError,
        match="worktree_root_inside_registered_worktree_forbidden",
    ):
        _allocate(manager, repository)

    assert _git(repository, "worktree", "list", "--porcelain").count("worktree ") == 1
    assert not (repository / ".tau").exists()


def test_symlinked_storage_directory_cannot_escape_state_root(
    tmp_path: Path, repository: Path
) -> None:
    state_root = tmp_path / "lease-state"
    state_root.mkdir()
    redirected = repository / "redirected"
    redirected.mkdir()
    (state_root / "worktrees").symlink_to(redirected, target_is_directory=True)

    with pytest.raises(GitWorktreeLeaseError, match="worktree_state_directory_invalid"):
        _allocate(GitWorktreeLeaseManager(state_root), repository)

    assert not tuple(redirected.iterdir())
    assert _git(repository, "worktree", "list", "--porcelain").count("worktree ") == 1


def test_lease_storage_prevents_world_traversal(
    tmp_path: Path, repository: Path
) -> None:
    repository.chmod(0o700)
    state_root = tmp_path / "lease-state"
    lease = _allocate(GitWorktreeLeaseManager(state_root), repository)

    assert stat.S_IMODE(state_root.stat().st_mode) == 0o700
    assert stat.S_IMODE((state_root / "worktrees").stat().st_mode) == 0o700
    assert stat.S_IMODE(Path(lease.worktree_path).stat().st_mode) == 0o700


def test_newline_repository_path_cannot_bypass_primary_checkout_boundary(tmp_path: Path) -> None:
    repository = tmp_path / "repo\nname"
    repository.mkdir()
    _git(repository, "init", "--initial-branch=main")
    _git(repository, "config", "user.email", "tau-tests@example.invalid")
    _git(repository, "config", "user.name", "Tau Tests")
    (repository / "src").mkdir()
    (repository / "src" / "allowed.txt").write_text("base\n", encoding="utf-8")
    _git(repository, "add", ".")
    _git(repository, "commit", "-m", "fixture")

    with pytest.raises(
        GitWorktreeLeaseError,
        match="worktree_root_inside_registered_worktree_forbidden",
    ):
        _allocate(GitWorktreeLeaseManager(repository / ".tau"), repository)

    assert not (repository / ".tau").exists()


def test_lease_state_root_inside_linked_worktree_is_rejected(
    tmp_path: Path, repository: Path
) -> None:
    linked = tmp_path / "linked"
    _git(repository, "worktree", "add", "--detach", str(linked), "HEAD")
    manager = GitWorktreeLeaseManager(linked / ".tau" / "runtime-leases")

    with pytest.raises(
        GitWorktreeLeaseError,
        match="worktree_root_inside_registered_worktree_forbidden",
    ):
        _allocate(manager, repository)

    assert not (linked / ".tau").exists()
    _git(repository, "worktree", "remove", "--force", str(linked))


def test_foreign_owner_cannot_exercise_lease_authority(
    tmp_path: Path, repository: Path
) -> None:
    state_root = tmp_path / "lease-state"
    owner = GitWorktreeLeaseManager(state_root, owner="owner-a")
    lease = _allocate(owner, repository)
    foreign = GitWorktreeLeaseManager(state_root, owner="owner-b")

    with pytest.raises(GitWorktreeLeaseError, match="worktree_lease_owner_mismatch"):
        foreign.rediscover()
    with pytest.raises(GitWorktreeLeaseError, match="worktree_lease_owner_mismatch"):
        foreign.inspect(lease)
    with pytest.raises(GitWorktreeLeaseError, match="worktree_lease_owner_mismatch"):
        foreign.admit(lease)
    with pytest.raises(GitWorktreeLeaseError, match="worktree_lease_owner_mismatch"):
        foreign.cleanup(lease)

    assert owner.cleanup(lease)["status"] == "PASS"


def test_expired_lease_requires_exact_reclamation_authorization(
    tmp_path: Path, repository: Path
) -> None:
    manager = GitWorktreeLeaseManager(tmp_path / "lease-state")
    lease = manager.allocate(
        repository=repository,
        run_id="run-1",
        plan_revision="plan-1",
        node_id="writer",
        attempt_id="attempt-1",
        base_commit="HEAD",
        allowed_paths=("src",),
        expires_in_seconds=1,
    )
    time.sleep(1.05)

    assert GitWorktreeLeaseManager(
        tmp_path / "lease-state"
    ).rediscover(run_id="run-1") == (lease,)

    with pytest.raises(GitWorktreeLeaseError, match="worktree_lease_expired"):
        manager.inspect(lease)
    with pytest.raises(GitWorktreeLeaseError, match="worktree_lease_expired"):
        manager.admit(lease)
    blocked = manager.cleanup(lease)
    assert blocked["errors"] == ["worktree_expired_reclamation_authorization_required"]

    inspection = manager.inspect_for_reclamation(lease)
    assert inspection["expired"] is True
    authorization = worktree_discard_authorization(lease, inspection)
    assert manager.cleanup(lease, discard_authorization=authorization)["status"] == "PASS"


def test_tampered_persisted_lease_blocks_restart_rediscovery(
    tmp_path: Path, repository: Path
) -> None:
    state_root = tmp_path / "lease-state"
    manager = GitWorktreeLeaseManager(state_root)
    _allocate(manager, repository)
    lease_file = next((state_root / "leases").glob("*.json"))
    payload = lease_file.read_text(encoding="utf-8").replace('"owner":"tau"', '"owner":"other"')
    lease_file.write_text(payload, encoding="utf-8")

    with pytest.raises(GitWorktreeLeaseError, match="worktree_lease_hash_mismatch"):
        GitWorktreeLeaseManager(state_root).rediscover()


def test_restart_recovers_crash_after_git_add_before_lease_record(
    tmp_path: Path, repository: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    state_root = tmp_path / "lease-state"
    manager = GitWorktreeLeaseManager(state_root)
    original_write = worktrees_module._atomic_json_write

    def crash_before_lease_record(path: Path, payload):
        if payload.get("schema") == worktrees_module.WORKTREE_LEASE_RECORD_SCHEMA:
            raise KeyboardInterrupt("simulated process loss")
        original_write(path, payload)

    monkeypatch.setattr(worktrees_module, "_atomic_json_write", crash_before_lease_record)
    with pytest.raises(KeyboardInterrupt, match="simulated process loss"):
        _allocate(manager, repository)
    monkeypatch.setattr(worktrees_module, "_atomic_json_write", original_write)

    recovered = GitWorktreeLeaseManager(state_root).rediscover(run_id="run-1")

    assert len(recovered) == 1
    assert recovered[0].attempt_id == "attempt-1"
    assert Path(recovered[0].worktree_path).is_dir()
    assert GitWorktreeLeaseManager(state_root).cleanup(recovered[0])["status"] == "PASS"


def test_restart_recovery_blocks_injected_filter_before_execution(
    tmp_path: Path, repository: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (repository / ".gitattributes").write_text("src/allowed.txt filter=evil\n", encoding="utf-8")
    _git(repository, "add", ".gitattributes")
    _git(repository, "commit", "-m", "filter fixture")
    state_root = tmp_path / "lease-state"
    original_write = worktrees_module._atomic_json_write

    def crash_before_lease_record(path: Path, payload):
        if payload.get("schema") == worktrees_module.WORKTREE_LEASE_RECORD_SCHEMA:
            raise KeyboardInterrupt("simulated process loss")
        original_write(path, payload)

    monkeypatch.setattr(worktrees_module, "_atomic_json_write", crash_before_lease_record)
    with pytest.raises(KeyboardInterrupt, match="simulated process loss"):
        _allocate(GitWorktreeLeaseManager(state_root), repository)
    monkeypatch.setattr(worktrees_module, "_atomic_json_write", original_write)
    marker = tmp_path / "filter-side-effect"
    _git(repository, "config", "filter.evil.clean", f"sh -c 'touch {marker}; cat'")

    with pytest.raises(
        GitWorktreeLeaseError, match="worktree_external_git_driver_forbidden"
    ):
        GitWorktreeLeaseManager(state_root).rediscover(run_id="run-1")

    assert not marker.exists()


def test_restart_ignores_stale_atomic_write_temporary_from_same_process(
    tmp_path: Path, repository: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    state_root = tmp_path / "lease-state"
    manager = GitWorktreeLeaseManager(state_root)
    original_replace = worktrees_module.os.replace

    def crash_before_lease_replace(source: str | Path, target: str | Path) -> None:
        if Path(target).parent.name == "leases":
            raise KeyboardInterrupt("simulated replace crash")
        original_replace(source, target)

    monkeypatch.setattr(worktrees_module.os, "replace", crash_before_lease_replace)
    with pytest.raises(KeyboardInterrupt, match="simulated replace crash"):
        _allocate(manager, repository)
    assert len(tuple((state_root / "leases").glob(".*.tmp"))) == 1
    monkeypatch.setattr(worktrees_module.os, "replace", original_replace)

    recovered = GitWorktreeLeaseManager(state_root).rediscover(run_id="run-1")

    assert len(recovered) == 1
    assert GitWorktreeLeaseManager(state_root).cleanup(recovered[0])["status"] == "PASS"


def test_restart_recovery_rebinds_repository_control_after_primary_commit(
    tmp_path: Path, repository: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    state_root = tmp_path / "lease-state"
    original_write = worktrees_module._atomic_json_write

    def crash_before_lease_record(path: Path, payload):
        if payload.get("schema") == worktrees_module.WORKTREE_LEASE_RECORD_SCHEMA:
            raise KeyboardInterrupt("simulated process loss")
        original_write(path, payload)

    monkeypatch.setattr(worktrees_module, "_atomic_json_write", crash_before_lease_record)
    with pytest.raises(KeyboardInterrupt, match="simulated process loss"):
        _allocate(GitWorktreeLeaseManager(state_root), repository)
    monkeypatch.setattr(worktrees_module, "_atomic_json_write", original_write)
    (repository / "later").write_text("later\n", encoding="utf-8")
    _git(repository, "add", "later")
    _git(repository, "commit", "-m", "advance primary during recovery")

    manager = GitWorktreeLeaseManager(state_root)
    recovered = manager.rediscover(run_id="run-1")

    assert len(recovered) == 1
    policy = recovered[0].cleanup_policy.to_value()
    assert policy["repository_control_sha256"] != policy[
        "repository_control_sha256_at_intent"
    ]
    assert manager.inspect(recovered[0])["status"] == "PASS"
    assert manager.cleanup(recovered[0])["status"] == "PASS"


def test_intent_cleanup_failure_after_lease_persistence_preserves_worktree(
    tmp_path: Path, repository: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    state_root = tmp_path / "lease-state"
    manager = GitWorktreeLeaseManager(state_root)
    original_unlink = worktrees_module._durable_unlink

    def fail_intent_cleanup(path: Path) -> None:
        if path.parent.name == "allocation-intents":
            raise OSError("simulated intent cleanup failure")
        original_unlink(path)

    monkeypatch.setattr(worktrees_module, "_durable_unlink", fail_intent_cleanup)
    with pytest.raises(OSError, match="simulated intent cleanup failure"):
        _allocate(manager, repository)

    lease_file = next((state_root / "leases").glob("*.json"))
    persisted = json.loads(lease_file.read_text(encoding="utf-8"))["lease"]
    assert Path(persisted["worktree_path"]).is_dir()
    assert len(tuple((state_root / "allocation-intents").glob("*.json"))) == 1

    monkeypatch.setattr(worktrees_module, "_durable_unlink", original_unlink)
    recovered = GitWorktreeLeaseManager(state_root).rediscover(run_id="run-1")
    assert len(recovered) == 1
    assert GitWorktreeLeaseManager(state_root).cleanup(recovered[0])["status"] == "PASS"


def test_restart_refuses_clean_commit_added_before_allocation_recovery(
    tmp_path: Path, repository: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    state_root = tmp_path / "lease-state"
    manager = GitWorktreeLeaseManager(state_root)
    original_write = worktrees_module._atomic_json_write

    def crash_before_lease_record(path: Path, payload):
        if payload.get("schema") == worktrees_module.WORKTREE_LEASE_RECORD_SCHEMA:
            raise KeyboardInterrupt("simulated process loss")
        original_write(path, payload)

    monkeypatch.setattr(worktrees_module, "_atomic_json_write", crash_before_lease_record)
    with pytest.raises(KeyboardInterrupt, match="simulated process loss"):
        _allocate(manager, repository)
    monkeypatch.setattr(worktrees_module, "_atomic_json_write", original_write)
    orphan = next((state_root / "worktrees").iterdir())
    (orphan / "src" / "allowed.txt").write_text("unexpected commit\n", encoding="utf-8")
    _git(orphan, "add", "src/allowed.txt")
    _git(
        orphan,
        "-c",
        "user.name=Tau",
        "-c",
        "user.email=tau@example.invalid",
        "commit",
        "-m",
        "unexpected",
    )

    with pytest.raises(
        GitWorktreeLeaseError,
        match="worktree_allocation_recovery_head_changed",
    ):
        GitWorktreeLeaseManager(state_root).rediscover(run_id="run-1")


def test_restart_rejects_tampered_allocation_intent(
    tmp_path: Path, repository: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    state_root = tmp_path / "lease-state"
    original_write = worktrees_module._atomic_json_write

    def crash_before_lease_record(path: Path, payload):
        if payload.get("schema") == worktrees_module.WORKTREE_LEASE_RECORD_SCHEMA:
            raise KeyboardInterrupt("simulated process loss")
        original_write(path, payload)

    monkeypatch.setattr(worktrees_module, "_atomic_json_write", crash_before_lease_record)
    with pytest.raises(KeyboardInterrupt):
        _allocate(GitWorktreeLeaseManager(state_root), repository)
    monkeypatch.setattr(worktrees_module, "_atomic_json_write", original_write)
    intent_path = next((state_root / "allocation-intents").glob("*.json"))
    record = json.loads(intent_path.read_text(encoding="utf-8"))
    record["intent"]["allowed_paths"] = ["outside.txt"]
    intent_path.write_text(json.dumps(record), encoding="utf-8")

    with pytest.raises(GitWorktreeLeaseError, match="worktree_allocation_intent_hash_mismatch"):
        GitWorktreeLeaseManager(state_root).rediscover(run_id="run-1")


def test_failed_allocation_rollback_preserves_recoverable_intent(
    tmp_path: Path, repository: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    state_root = tmp_path / "lease-state"
    manager = GitWorktreeLeaseManager(state_root)
    original_write = worktrees_module._atomic_json_write
    original_run_git = worktrees_module._run_git

    def fail_lease_write(path: Path, payload):
        if payload.get("schema") == worktrees_module.WORKTREE_LEASE_RECORD_SCHEMA:
            raise RuntimeError("simulated lease write failure")
        original_write(path, payload)

    def fail_rollback(cwd: Path, *args: str):
        if args[:3] == ("worktree", "remove", "--force"):
            return subprocess.CompletedProcess(args, 1, "", "simulated removal failure")
        return original_run_git(cwd, *args)

    monkeypatch.setattr(worktrees_module, "_atomic_json_write", fail_lease_write)
    monkeypatch.setattr(worktrees_module, "_run_git", fail_rollback)
    with pytest.raises(RuntimeError, match="simulated lease write failure"):
        _allocate(manager, repository)
    assert len(tuple((state_root / "allocation-intents").glob("*.json"))) == 1
    monkeypatch.setattr(worktrees_module, "_atomic_json_write", original_write)
    monkeypatch.setattr(worktrees_module, "_run_git", original_run_git)

    recovered = GitWorktreeLeaseManager(state_root).rediscover(run_id="run-1")
    assert len(recovered) == 1
    assert GitWorktreeLeaseManager(state_root).cleanup(recovered[0])["status"] == "PASS"


def test_restart_retires_lease_after_crash_between_remove_and_record_cleanup(
    tmp_path: Path, repository: Path
) -> None:
    state_root = tmp_path / "lease-state"
    manager = GitWorktreeLeaseManager(state_root)
    lease = _allocate(manager, repository)
    _git(repository, "worktree", "remove", "--force", lease.worktree_path)

    restarted = GitWorktreeLeaseManager(state_root)
    assert restarted.rediscover(run_id="run-1") == ()
    assert len(tuple((state_root / "retired-leases").glob("*.json"))) == 1

    with pytest.raises(GitWorktreeLeaseError, match="worktree_attempt_retired"):
        _allocate(restarted, repository)


def test_missing_worktree_retirement_blocks_primary_git_symlink(
    tmp_path: Path, repository: Path
) -> None:
    state_root = tmp_path / "lease-state"
    manager = GitWorktreeLeaseManager(state_root)
    lease = _allocate(manager, repository)
    shutil.rmtree(lease.worktree_path)
    external = tmp_path / "external-common-dir"
    (repository / ".git").rename(external)
    (repository / ".git").symlink_to(external, target_is_directory=True)

    with pytest.raises(GitWorktreeLeaseError, match="worktree_common_dir_invalid"):
        manager.rediscover(run_id="run-1")

    assert not tuple((state_root / "retired-leases").glob("*.json"))


def test_missing_worktree_retirement_blocks_commondir_redirection(
    tmp_path: Path, repository: Path
) -> None:
    state_root = tmp_path / "lease-state"
    manager = GitWorktreeLeaseManager(state_root)
    lease = _allocate(manager, repository)
    policy = lease.cleanup_policy.to_value()
    admin_dir = Path(policy["worktree_admin_dir"])
    foreign = tmp_path / "foreign"
    _git(tmp_path, "clone", str(repository), str(foreign))
    (admin_dir / "commondir").write_text(str(foreign / ".git") + "\n", encoding="utf-8")
    shutil.rmtree(lease.worktree_path)

    with pytest.raises(GitWorktreeLeaseError, match="worktree_commondir_changed"):
        manager.rediscover(run_id="run-1")

    assert admin_dir.exists()
    assert not tuple((state_root / "retired-leases").glob("*.json"))


def test_missing_worktree_retirement_blocks_replacement_admin_registration(
    tmp_path: Path, repository: Path
) -> None:
    state_root = tmp_path / "lease-state"
    manager = GitWorktreeLeaseManager(state_root)
    lease = _allocate(manager, repository)
    admin_dir = Path(lease.cleanup_policy.to_value()["worktree_admin_dir"])
    replacement = admin_dir.with_name(f"{admin_dir.name}-replacement")
    admin_dir.rename(replacement)
    shutil.rmtree(lease.worktree_path)

    with pytest.raises(GitWorktreeLeaseError, match="worktree_admin_registration_mismatch"):
        manager.rediscover(run_id="run-1")

    assert replacement.exists()
    assert not tuple((state_root / "retired-leases").glob("*.json"))


def test_missing_worktree_retirement_blocks_symlinked_admin_parent(
    tmp_path: Path, repository: Path
) -> None:
    state_root = tmp_path / "lease-state"
    manager = GitWorktreeLeaseManager(state_root)
    lease = _allocate(manager, repository)
    admin_parent = Path(lease.cleanup_policy.to_value()["worktree_admin_dir"]).parent
    external = tmp_path / "external-worktrees-admin"
    admin_parent.rename(external)
    admin_parent.symlink_to(external, target_is_directory=True)
    shutil.rmtree(lease.worktree_path)

    with pytest.raises(GitWorktreeLeaseError, match="worktree_gitdir_parent_invalid"):
        manager.rediscover(run_id="run-1")

    assert external.exists()
    assert not tuple((state_root / "retired-leases").glob("*.json"))


def test_missing_worktree_retirement_blocks_duplicate_admin_registration(
    tmp_path: Path, repository: Path
) -> None:
    state_root = tmp_path / "lease-state"
    manager = GitWorktreeLeaseManager(state_root)
    lease = _allocate(manager, repository)
    admin_dir = Path(lease.cleanup_policy.to_value()["worktree_admin_dir"])
    duplicate = admin_dir.with_name(f"{admin_dir.name}-duplicate")
    shutil.copytree(admin_dir, duplicate)
    shutil.rmtree(lease.worktree_path)

    with pytest.raises(
        GitWorktreeLeaseError, match="worktree_admin_registration_ambiguous"
    ):
        manager.rediscover(run_id="run-1")

    assert admin_dir.exists()
    assert duplicate.exists()
    assert not tuple((state_root / "retired-leases").glob("*.json"))


def test_missing_worktree_retirement_blocks_copied_admin_replacement(
    tmp_path: Path, repository: Path
) -> None:
    state_root = tmp_path / "lease-state"
    manager = GitWorktreeLeaseManager(state_root)
    lease = _allocate(manager, repository)
    admin_dir = Path(lease.cleanup_policy.to_value()["worktree_admin_dir"])
    original = admin_dir.with_name(f"{admin_dir.name}-original")
    admin_dir.rename(original)
    shutil.copytree(original, admin_dir)
    (original / "gitdir").unlink()
    shutil.rmtree(lease.worktree_path)

    with pytest.raises(GitWorktreeLeaseError, match="worktree_admin_identity_changed"):
        manager.rediscover(run_id="run-1")

    assert admin_dir.exists()
    assert original.exists()
    assert not tuple((state_root / "retired-leases").glob("*.json"))


def test_cleanup_does_not_prune_unrelated_missing_worktree(
    tmp_path: Path, repository: Path
) -> None:
    unrelated = tmp_path / "unrelated-worktree"
    _git(repository, "worktree", "add", "--detach", str(unrelated), "HEAD")
    _git(repository, "config", "gc.worktreePruneExpire", "now")
    manager = GitWorktreeLeaseManager(tmp_path / "lease-state")
    lease = _allocate(manager, repository)
    shutil.rmtree(unrelated)

    assert manager.cleanup(lease)["status"] == "PASS"
    registered = _git(repository, "worktree", "list", "--porcelain")

    assert str(unrelated) in registered
    _git(repository, "worktree", "remove", "--force", str(unrelated))


def test_failed_allocation_does_not_prune_unrelated_missing_worktree(
    tmp_path: Path, repository: Path
) -> None:
    unrelated = tmp_path / "unrelated-worktree"
    _git(repository, "worktree", "add", "--detach", str(unrelated), "HEAD")
    _git(repository, "config", "gc.worktreePruneExpire", "now")
    shutil.rmtree(unrelated)
    external = tmp_path / "external"
    external.write_text("outside\n", encoding="utf-8")
    (repository / "src" / "escape").symlink_to(external)
    _git(repository, "add", "src/escape")
    _git(repository, "commit", "-m", "symlink fixture")

    with pytest.raises(GitWorktreeLeaseError, match="worktree_symlink_forbidden"):
        _allocate(GitWorktreeLeaseManager(tmp_path / "lease-state"), repository)

    registered = _git(repository, "worktree", "list", "--porcelain")
    assert str(unrelated) in registered
    _git(repository, "worktree", "remove", "--force", str(unrelated))


def test_missing_registered_worktree_does_not_block_unrelated_rediscovery(
    tmp_path: Path, repository: Path
) -> None:
    state_root = tmp_path / "lease-state"
    manager = GitWorktreeLeaseManager(state_root)
    missing = _allocate(manager, repository, attempt_id="missing")
    live = _allocate(manager, repository, attempt_id="live")
    shutil.rmtree(missing.worktree_path)

    recovered = GitWorktreeLeaseManager(state_root).rediscover(run_id="run-1")

    assert recovered == (live,)
    assert Path(live.worktree_path).is_dir()
    assert len(tuple((state_root / "retired-leases").glob("*.json"))) == 1
    assert GitWorktreeLeaseManager(state_root).cleanup(live)["status"] == "PASS"
