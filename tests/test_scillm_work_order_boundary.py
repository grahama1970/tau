"""tau#341: strict typed boundary for tau.executor.scillm_worker.v1 work orders.

Before the fix, `tau scillm-worker-launch` dry runs returned PASS with no
alerts for work orders missing dag_id/node_id/agent/goal_hash/attempt/task and
for work orders with wrongly typed identity fields plus an undeclared field.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from tau_coding.cli import app
from tau_coding.coding_worker_adapters import write_scillm_worker_launch_receipt
from tau_coding.scillm_work_order import (
    ScillmWorkerWorkOrder,
    legacy_alert_codes,
    work_order_validation_errors,
)

IDENTITY = ("dag_id", "node_id", "agent", "goal_hash", "attempt", "task")


def _valid(tmp_path: Path) -> dict[str, Any]:
    repo = tmp_path / "repo"
    repo.mkdir(exist_ok=True)
    return {
        "schema": "tau.executor.scillm_worker.v1",
        "dag_id": "tau341-dag",
        "node_id": "coder",
        "agent": "coder",
        "goal_hash": "sha256:" + "a" * 64,
        "attempt": 1,
        "task": "Make a bounded change.",
        "repo": str(repo),
        "allowed_paths": ["src/**"],
        "forbidden_paths": [".git/**"],
        "result_path": "worker-result.json",
        "receipt_path": "worker-receipt.json",
        "model_provider_route": {
            "surface": "opencode_serve",
            "endpoint": "/v1/scillm/opencode/runs",
            "agent": "build",
        },
    }


def _launch(tmp_path: Path, work_order: dict[str, Any], *, via_cli: bool = False) -> dict[str, Any]:
    path = tmp_path / "work-order.json"
    path.write_text(json.dumps(work_order, indent=2), encoding="utf-8")
    out = tmp_path / "launch-receipt.json"
    if via_cli:
        result = CliRunner().invoke(
            app, ["scillm-worker-launch", "--work-order", str(path), "--out", str(out)]
        )
        payload = json.loads(out.read_text(encoding="utf-8"))
        payload["_cli_exit_code"] = result.exit_code
        return payload
    return write_scillm_worker_launch_receipt(work_order_path=path, output_path=out)


def test_valid_work_order_passes_strict_model_and_dry_run(tmp_path: Path) -> None:
    order = _valid(tmp_path)
    assert work_order_validation_errors(order) == []
    ScillmWorkerWorkOrder.model_validate(order)
    receipt = _launch(tmp_path, order, via_cli=True)
    assert receipt["_cli_exit_code"] == 0
    assert receipt["status"] == "PASS"
    assert receipt["alert_codes"] == []
    assert receipt["http_executed"] is False
    assert receipt["request_payload"]["scillm_metadata"]["dag_id"] == "tau341-dag"


def test_missing_identity_is_blocked_before_any_request(tmp_path: Path) -> None:
    order = _valid(tmp_path)
    for key in IDENTITY:
        order.pop(key)
    receipt = _launch(tmp_path, order, via_cli=True)
    assert receipt["_cli_exit_code"] != 0
    assert receipt["status"] == "BLOCKED"
    assert receipt["http_executed"] is False
    assert receipt["launch_skipped"] is True
    assert receipt["request_constructed"] is False
    assert receipt["request_payload"] == {}
    assert "invalid_work_order" in receipt["alert_codes"]
    locs = {e["loc"]: e["type"] for e in receipt["work_order_validation"]["errors"]}
    assert locs == dict.fromkeys(IDENTITY, "missing")


def test_wrong_types_and_undeclared_field_are_blocked(tmp_path: Path) -> None:
    order = _valid(tmp_path)
    order.update(
        {
            "dag_id": 123,
            "node_id": [],
            "agent": {},
            "goal_hash": False,
            "attempt": "first",
            "task": {},
            "undeclared_field": True,
        }
    )
    receipt = _launch(tmp_path, order, via_cli=True)
    assert receipt["status"] == "BLOCKED"
    assert receipt["http_executed"] is False
    locs = {e["loc"]: e["type"] for e in receipt["work_order_validation"]["errors"]}
    assert locs["dag_id"] == "string_type"
    assert locs["node_id"] == "string_type"
    assert locs["agent"] == "string_type"
    assert locs["goal_hash"] == "string_type"
    assert locs["attempt"] == "int_type"
    assert locs["task"] == "string_type"
    assert locs["undeclared_field"] == "extra_forbidden"


@pytest.mark.parametrize(
    ("mutation", "loc", "err_type"),
    [
        ({"attempt": 0}, "attempt", "greater_than_equal"),
        ({"attempt": True}, "attempt", "int_type"),
        ({"attempt": "1"}, "attempt", "int_type"),
        ({"goal_hash": "goalgoalgoal"}, "goal_hash", "value_error"),
        ({"allowed_paths": []}, "allowed_paths", "too_short"),
        ({"allowed_paths": ["src/**", ""]}, "allowed_paths", "value_error"),
        ({"timeout_s": 0}, "timeout_s", "greater_than_equal"),
        ({"schema": "tau.executor.omp.v1"}, "schema", "literal_error"),
        (
            {"model_provider_route": {"surface": "chat", "agent": "build"}},
            "model_provider_route.surface",
            "literal_error",
        ),
        (
            {"model_provider_route": {"surface": "opencode_serve", "agent": "opencode-go/kimi"}},
            "model_provider_route.agent",
            "value_error",
        ),
        (
            {"model_provider_route": {"surface": "opencode_serve", "agent": "build", "bogus": 1}},
            "model_provider_route.bogus",
            "extra_forbidden",
        ),
    ],
)
def test_field_level_rejections(
    tmp_path: Path, mutation: dict[str, Any], loc: str, err_type: str
) -> None:
    order = {**_valid(tmp_path), **mutation}
    errors = work_order_validation_errors(order)
    assert any(e["loc"] == loc and e["type"] == err_type for e in errors), errors
    receipt = _launch(tmp_path, order)
    assert receipt["status"] == "BLOCKED"
    assert receipt["http_executed"] is False


def test_legacy_alert_codes_are_kept_alongside_typed_alert(tmp_path: Path) -> None:
    order = {**_valid(tmp_path), "timeout_s": 0}
    order["model_provider_route"]["agent"] = "opencode-go/kimi"
    receipt = _launch(tmp_path, order)
    assert "invalid_work_order" in receipt["alert_codes"]
    assert "invalid_scillm_worker_timeout" in receipt["alert_codes"]
    assert "chat_model_used_as_agent" in receipt["alert_codes"]
    assert sorted(legacy_alert_codes(work_order_validation_errors(order))) == [
        "chat_model_used_as_agent",
        "invalid_scillm_worker_timeout",
    ]


def test_apply_with_invalid_work_order_never_posts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import tau_coding.coding_worker_adapters as adapters

    def _explode(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("HTTP must not be attempted for an invalid work order")

    monkeypatch.setattr(adapters, "_maybe_post_scillm_opencode_run", _explode)
    order = _valid(tmp_path)
    order.pop("goal_hash")
    path = tmp_path / "work-order.json"
    path.write_text(json.dumps(order), encoding="utf-8")
    receipt = write_scillm_worker_launch_receipt(
        work_order_path=path, output_path=tmp_path / "r.json", apply=True, auth_token="x"
    )
    assert receipt["status"] == "BLOCKED"
    assert receipt["apply_requested"] is True
    assert receipt["http_executed"] is False


def test_watchdog_captured_inputs_are_blocked_when_present() -> None:
    base = Path(
        "/mnt/storage12tb/skills/project-watchdog/outputs/ask-native-integration/worker-boundary"
    )
    if not base.is_dir():
        pytest.skip("watchdog evidence directory not available on this host")
    for name, expected in (("missing-identity", "missing"), ("malformed-identity", "string_type")):
        errors = work_order_validation_errors(
            json.loads((base / f"{name}.json").read_text(encoding="utf-8"))
        )
        assert any(e["loc"] == "dag_id" and e["type"] == expected for e in errors), errors


def _with_workspace(order: dict[str, Any], tmp_path: Path, **overrides: Any) -> dict[str, Any]:
    # Shape-only tests: use synthetic non-/tmp paths (the worktree audit and
    # this boundary both refuse /tmp worktrees, and pytest tmp_path IS /tmp).
    workspace = {
        "transport": "opencode.serve",
        "worktree_state_root": "/workspace-leases/tau-355",
        "release_required": True,
        "agent_profile": "build",
    }
    workspace.update(overrides)
    order["workspace"] = workspace
    return order


def test_workspace_binding_valid_on_opencode_serve_surface(tmp_path: Path) -> None:
    order = _with_workspace(_valid(tmp_path), tmp_path)
    assert ScillmWorkerWorkOrder.model_validate(order) is not None


def test_workspace_binding_rejected_without_opencode_serve_surface(tmp_path: Path) -> None:
    order = _with_workspace(_valid(tmp_path), tmp_path)
    order["model_provider_route"] = None
    errors = work_order_validation_errors(order)
    assert any("surface='opencode_serve'" in e["msg"] for e in errors), errors
    assert "invalid_workspace_binding" in legacy_alert_codes(errors) or not legacy_alert_codes(errors)


def test_workspace_agent_profile_must_match_route_agent(tmp_path: Path) -> None:
    order = _with_workspace(_valid(tmp_path), tmp_path, agent_profile="other-profile")
    errors = work_order_validation_errors(order)
    assert any("must equal model_provider_route.agent" in e["msg"] for e in errors), errors


def test_workspace_rejects_chat_model_agent_profile(tmp_path: Path) -> None:
    order = _with_workspace(_valid(tmp_path), tmp_path, agent_profile="opencode-go/glm-5.3")
    errors = work_order_validation_errors(order)
    assert any(e["loc"] == "workspace.agent_profile" for e in errors), errors
    assert "chat_model_used_as_agent" in legacy_alert_codes(errors)


def test_workspace_rejects_tmp_paths(tmp_path: Path) -> None:
    for field in ("worktree_state_root", "worktree_root", "lease_receipt_path", "admission_receipt_path"):
        order = _with_workspace(_valid(tmp_path), tmp_path, **{field: "/tmp/authoring"})
        errors = work_order_validation_errors(order)
        assert any(e["loc"] == f"workspace.{field}" for e in errors), (field, errors)


def test_workspace_declared_cross_check_paths_accepted(tmp_path: Path) -> None:
    order = _with_workspace(
        _valid(tmp_path),
        tmp_path,
        worktree_root="/workspace-leases/tau-355/derived-wt",
        lease_receipt_path="/workspace-leases/tau-355/derived-lease.json",
        admission_receipt_path="/workspace-leases/tau-355/derived-admission.json",
    )
    assert ScillmWorkerWorkOrder.model_validate(order) is not None


def test_workspace_release_required_cannot_be_false(tmp_path: Path) -> None:
    order = _with_workspace(_valid(tmp_path), tmp_path, release_required=False)  # type: ignore[call-overload]
    errors = work_order_validation_errors(order)
    assert any(e["loc"] == "workspace.release_required" for e in errors), errors


def test_workspace_absent_still_valid(tmp_path: Path) -> None:
    order = _valid(tmp_path)
    assert ScillmWorkerWorkOrder.model_validate(order) is not None


def _git(cwd: Path, *args: str) -> str:
    import subprocess
    result = subprocess.run(["git", "-C", str(cwd), *args], check=True, capture_output=True, text=True)
    return result.stdout.strip()


@pytest.fixture
def git_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repository"
    repo.mkdir()
    _git(repo, "init", "--initial-branch=main")
    _git(repo, "config", "user.email", "tau-tests@example.invalid")
    _git(repo, "config", "user.name", "Tau Tests")
    (repo / "src").mkdir()
    (repo / "src" / "file.txt").write_text("base\n", encoding="utf-8")
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "fixture")
    return repo


def _home_lease_root(key: str) -> Path:
    # The binding refuses /tmp lease roots (worktrees must survive reboots);
    # pytest tmp_path IS /tmp, so tests use a scrubbed home-based root.
    root = Path.home() / ".cache" / "tau-test-leases" / key
    if root.exists():
        import shutil
        shutil.rmtree(root)
    root.parent.mkdir(parents=True, exist_ok=True)
    return root


def test_workspace_launch_allocates_and_admits_lease(tmp_path: Path, git_repo: Path) -> None:
    state_root = _home_lease_root("dry-run")
    order = _valid(tmp_path)
    order["repo"] = str(git_repo)
    order["workspace"] = {
        "transport": "opencode.serve",
        "worktree_state_root": str(state_root),
        "release_required": True,
        "agent_profile": "build",
    }
    path = tmp_path / "work-order.json"
    path.write_text(json.dumps(order), encoding="utf-8")
    receipt = write_scillm_worker_launch_receipt(
        work_order_path=path, output_path=tmp_path / "r.json", apply=False
    )
    ws = receipt["workspace"]
    assert ws["status"] == "dry_run"
    assert not state_root.exists() or not any(state_root.iterdir())


def test_workspace_launch_cross_check_mismatch_blocks(tmp_path: Path, git_repo: Path) -> None:
    state_root = _home_lease_root("cross-check")
    order = _valid(tmp_path)
    order["repo"] = str(git_repo)
    order["workspace"] = {
        "transport": "opencode.serve",
        "worktree_state_root": str(state_root),
        "worktree_root": "/nonexistent/declared-wt",
        "release_required": True,
        "agent_profile": "build",
    }
    path = tmp_path / "work-order.json"
    path.write_text(json.dumps(order), encoding="utf-8")

    # Force allocation path with apply=True but a dead scillm URL: the
    # workspace stage must still run and record the mismatch before HTTP.
    receipt = write_scillm_worker_launch_receipt(
        work_order_path=path,
        output_path=tmp_path / "r.json",
        apply=True,
        auth_token="x",
        scillm_base_url="http://127.0.0.1:1",
    )
    assert receipt["workspace"] is None
    assert any(a["code"] == "workspace_worktree_mismatch" for a in receipt["alerts"])


def test_workspace_launch_apply_allocates_real_lease_and_redirects_cwd(
    tmp_path: Path, git_repo: Path
) -> None:
    state_root = _home_lease_root("apply-allocates")
    order = _valid(tmp_path)
    order["repo"] = str(git_repo)
    order["workspace"] = {
        "transport": "opencode.serve",
        "worktree_state_root": str(state_root),
        "release_required": True,
        "agent_profile": "build",
    }
    path = tmp_path / "work-order.json"
    path.write_text(json.dumps(order), encoding="utf-8")
    receipt = write_scillm_worker_launch_receipt(
        work_order_path=path,
        output_path=tmp_path / "r.json",
        apply=True,
        auth_token="x",
        scillm_base_url="http://127.0.0.1:1",  # dead: workspace stage must still complete
    )
    ws = receipt["workspace"]
    assert ws["status"] == "allocated"
    assert ws["admission_status"] == "PASS"
    assert ws["worktree_path"] and Path(ws["worktree_path"]).is_dir()
    assert ws["worktree_path"] not in str(git_repo)  # separate leased worktree
    # HTTP failure is lane-local: the lease/admission evidence survives.
    assert receipt["http_executed"] is True and receipt["status"] == "BLOCKED"
