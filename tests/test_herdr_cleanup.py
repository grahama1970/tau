import hashlib
import json
import os
from pathlib import Path

from typer.testing import CliRunner

from tau_coding.cli import app
from tau_coding.herdr_cleanup import (
    DEFAULT_GC_LABEL_PREFIXES,
    classify_herdr_surface,
    run_herdr_cleanup,
    run_herdr_gc,
)


def _write_manifest(run_dir: Path, *, session: str = "default") -> None:
    run_dir.mkdir(parents=True)
    (run_dir / "runtime-manifest.json").write_text(
        json.dumps(
            {
                "schema": "tau.provider_dag_runtime_manifest.v1",
                "backend_session_id": session,
                "provider_sessions": {
                    "codex": {
                        "workspace_id": "w-run",
                        "pane_id": "w-run:p5",
                        "terminal_id": "term-coder",
                    }
                },
                "visible_subagents": {
                    "planner": {
                        "workspace_id": "w-run",
                        "pane_id": "w-run:p7",
                        "terminal_id": "term-planner",
                    },
                    "orchestrator": {
                        "workspace_id": "w-run",
                        "pane_id": "w-run:p8",
                        "terminal_id": "term-orchestrator",
                    },
                },
            }
        ),
        encoding="utf-8",
    )


def test_classify_herdr_surface_rejects_substitute_binary(
    tmp_path: Path, monkeypatch
) -> None:
    real = tmp_path / "herdr"
    substitute = tmp_path / "herdr-fixture"
    real.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    substitute.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    real.chmod(0o755)
    substitute.chmod(0o755)
    binaries = {"herdr": str(real), "herdr-fixture": str(substitute)}
    monkeypatch.setattr(
        "tau_coding.herdr_cleanup.shutil.which", lambda name: binaries.get(name)
    )

    assert classify_herdr_surface(str(substitute)) == "fixture"
    assert classify_herdr_surface("herdr-fixture") == "fixture"
    assert classify_herdr_surface("herdr") == "real"


def test_herdr_cleanup_dry_run_collects_run_owned_workspace(tmp_path: Path) -> None:
    run_dir = tmp_path / "run"
    _write_manifest(run_dir)

    receipt = run_herdr_cleanup(run_dir=run_dir, mode="dry-run")

    assert receipt["schema"] == "tau.herdr_cleanup_receipt.v1"
    assert receipt["ok"] is True
    assert receipt["live"] is False
    assert (
        receipt["runtime_manifest_sha256"]
        == hashlib.sha256((run_dir / "runtime-manifest.json").read_bytes()).hexdigest()
    )
    assert receipt["candidate_count"] == 1
    assert receipt["candidates"][0]["action"] == "workspace_close"
    assert receipt["candidates"][0]["workspace_id"] == "w-run"
    assert set(receipt["candidates"][0]["roles"]) == {"codex", "planner", "orchestrator"}
    assert receipt["command_results"] == []
    assert (run_dir / "herdr-cleanup-receipt.json").exists()


def test_herdr_cleanup_refuses_current_workspace_by_default(
    tmp_path: Path,
    monkeypatch,
) -> None:
    run_dir = tmp_path / "run"
    _write_manifest(run_dir)
    monkeypatch.setenv("HERDR_WORKSPACE_ID", "w-run")
    monkeypatch.setenv("HERDR_SESSION", "default")

    receipt = run_herdr_cleanup(run_dir=run_dir, mode="dry-run")

    assert receipt["candidate_count"] == 0
    assert receipt["current_workspace"] == "w-run"


def test_herdr_cleanup_apply_uses_herdr_workspace_close(
    tmp_path: Path,
    monkeypatch,
) -> None:
    run_dir = tmp_path / "run"
    _write_manifest(run_dir)
    lease_path = _write_workspace_lease(run_dir)
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    calls_path = tmp_path / "calls.jsonl"
    fake_herdr = bin_dir / "herdr"
    fake_herdr.write_text(
        "#!/usr/bin/env bash\n"
        'printf \'{"argv":[\' >> "$HERDR_CALLS"\n'
        "first=1\n"
        'for arg in "$@"; do\n'
        '  if [ "$first" = 0 ]; then printf \',\' >> "$HERDR_CALLS"; fi\n'
        "  first=0\n"
        '  python3 -c \'import json,sys; '
        'print(json.dumps(sys.argv[1]), end="")\' "$arg" >> "$HERDR_CALLS"\n'
        "done\n"
        "printf ']}\\n' >> \"$HERDR_CALLS\"\n"
        'if [ "$1" = "--session" ]; then shift 2; fi\n'
        'if [ "$1 $2 $3" = "workspace get w-run" ]; then\n'
        '  printf \'{"error":{"code":"workspace_not_found",'
        '"message":"workspace w-run not found"}}\\n\'\n'
        "  exit 1\n"
        "fi\n"
        'printf \'{"result":{"type":"ok"}}\\n\'\n',
        encoding="utf-8",
    )
    fake_herdr.chmod(0o755)
    monkeypatch.setenv("HERDR_CALLS", str(calls_path))

    receipt = run_herdr_cleanup(
        run_dir=run_dir,
        mode="apply",
        herdr_bin=str(fake_herdr),
        workspace_lease_path=lease_path,
    )

    assert receipt["ok"] is True
    assert receipt["live"] is False
    assert receipt["herdr_surface"] == "fixture"
    assert receipt["workspace_lease"] == str(lease_path.resolve())
    assert receipt["workspace_lease_sha256"] == hashlib.sha256(lease_path.read_bytes()).hexdigest()
    assert receipt["applied_actions"] == [
        {
            "action": "workspace_close",
            "workspace_id": "w-run",
            "roles": ["codex", "planner", "orchestrator"],
            "pane_ids": ["w-run:p5", "w-run:p7", "w-run:p8"],
            "reason": "run-owned workspace recorded in runtime manifest",
            "returncode": 0,
            "applied": True,
            "post_verify_action": "workspace_get",
            "post_verify_returncode": 1,
            "post_verify_error_code": "workspace_not_found",
            "post_verified_absent": True,
        }
    ]
    assert receipt["applied_action_count"] == 1
    assert receipt["post_verified_absent_count"] == 1
    calls = [json.loads(line) for line in calls_path.read_text(encoding="utf-8").splitlines()]
    assert calls == [
        {"argv": ["--session", "default", "workspace", "close", "w-run"]},
        {"argv": ["--session", "default", "workspace", "get", "w-run"]},
    ]
    assert receipt["command_results"][0]["argv"] == [
        str(fake_herdr),
        "--session",
        "default",
        "workspace",
        "close",
        "w-run",
    ]
    assert receipt["command_results"][1]["argv"] == [
        str(fake_herdr),
        "--session",
        "default",
        "workspace",
        "get",
        "w-run",
    ]


def test_herdr_cleanup_targets_explicit_session_for_workspace_mutation(
    tmp_path: Path,
    monkeypatch,
) -> None:
    run_dir = tmp_path / "run"
    _write_manifest(run_dir, session="named-session")
    lease_path = _write_workspace_lease(run_dir)
    calls_path = tmp_path / "calls.jsonl"
    fake_herdr = tmp_path / "herdr"
    fake_herdr.write_text(
        "#!/usr/bin/env bash\n"
        'python3 -c \'import json,os,sys; '
        'open(os.environ["HERDR_CALLS"], "a").write(json.dumps(sys.argv[1:]) + "\\n")\' "$@"\n'
        'if [ "$3 $4 $5" = "workspace get w-run" ]; then\n'
        '  printf \'{"error":{"code":"workspace_not_found"}}\\n\'\n'
        "  exit 1\n"
        "fi\n"
        'printf \'{"result":{"type":"ok"}}\\n\'\n',
        encoding="utf-8",
    )
    fake_herdr.chmod(0o755)
    monkeypatch.setenv("HERDR_CALLS", str(calls_path))

    receipt = run_herdr_cleanup(
        run_dir=run_dir,
        mode="apply",
        herdr_bin=str(fake_herdr),
        session="named-session",
        workspace_lease_path=lease_path,
    )

    calls = [json.loads(line) for line in calls_path.read_text(encoding="utf-8").splitlines()]
    assert calls == [
        ["--session", "named-session", "workspace", "close", "w-run"],
        ["--session", "named-session", "workspace", "get", "w-run"],
    ]
    assert receipt["backend_session_id"] == "named-session"
    assert receipt["status"] == "PASS"


def test_herdr_cleanup_blocks_session_mismatch_before_mutation(
    tmp_path: Path,
    monkeypatch,
) -> None:
    run_dir = tmp_path / "run"
    _write_manifest(run_dir, session="owned-session")
    lease_path = _write_workspace_lease(run_dir)
    calls_path = tmp_path / "calls.jsonl"
    fake_herdr = tmp_path / "herdr"
    fake_herdr.write_text(
        "#!/usr/bin/env bash\n"
        'printf "%s\\n" "$*" >> "$HERDR_CALLS"\n'
        'printf \'{"result":{"type":"ok"}}\\n\'\n',
        encoding="utf-8",
    )
    fake_herdr.chmod(0o755)
    monkeypatch.setenv("HERDR_CALLS", str(calls_path))

    receipt = run_herdr_cleanup(
        run_dir=run_dir,
        mode="apply",
        herdr_bin=str(fake_herdr),
        session="other-session",
        workspace_lease_path=lease_path,
    )

    assert receipt["status"] == "BLOCKED"
    assert any(
        alert["code"] == "backend_session_id_mismatch"
        for alert in receipt["alerts"]
    )
    assert receipt["command_results"] == []
    assert not calls_path.exists()


def test_herdr_cleanup_apply_blocks_without_workspace_lease(tmp_path: Path) -> None:
    run_dir = tmp_path / "run"
    _write_manifest(run_dir)

    receipt = run_herdr_cleanup(
        run_dir=run_dir,
        mode="apply",
        herdr_bin="/tmp/should-not-run-herdr",
    )

    assert receipt["ok"] is False
    assert receipt["status"] == "BLOCKED"
    assert receipt["alerts"][0]["code"] == "missing_workspace_lease"
    assert receipt["applied_actions"] == []
    assert receipt["command_results"] == []


def test_cli_herdr_cleanup_apply_blocks_without_workspace_lease(tmp_path: Path) -> None:
    run_dir = tmp_path / "run"
    _write_manifest(run_dir)

    result = CliRunner().invoke(
        app,
        [
            "herdr-cleanup",
            "apply",
            "--run-dir",
            str(run_dir),
            "--herdr-bin",
            "/tmp/should-not-run-herdr",
        ],
    )
    payload = json.loads(result.output)

    assert result.exit_code == 1
    assert payload["ok"] is False
    assert payload["alerts"][0]["code"] == "missing_workspace_lease"
    assert payload["command_results"] == []


def test_herdr_cleanup_apply_blocks_when_workspace_still_exists(
    tmp_path: Path,
    monkeypatch,
) -> None:
    run_dir = tmp_path / "run"
    _write_manifest(run_dir)
    lease_path = _write_workspace_lease(run_dir)
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    calls_path = tmp_path / "calls.jsonl"
    fake_herdr = bin_dir / "herdr"
    fake_herdr.write_text(
        "#!/usr/bin/env bash\n"
        'printf \'{"argv":[\' >> "$HERDR_CALLS"\n'
        "first=1\n"
        'for arg in "$@"; do\n'
        '  if [ "$first" = 0 ]; then printf \',\' >> "$HERDR_CALLS"; fi\n'
        "  first=0\n"
        '  python3 -c \'import json,sys; '
        'print(json.dumps(sys.argv[1]), end="")\' "$arg" >> "$HERDR_CALLS"\n'
        "done\n"
        "printf ']}\\n' >> \"$HERDR_CALLS\"\n"
        'if [ "$1" = "--session" ]; then shift 2; fi\n'
        'if [ "$1 $2 $3" = "workspace get w-run" ]; then\n'
        '  printf \'{"workspace":{"id":"w-run"}}\\n\'\n'
        "  exit 0\n"
        "fi\n"
        'printf \'{"result":{"type":"ok"}}\\n\'\n',
        encoding="utf-8",
    )
    fake_herdr.chmod(0o755)
    monkeypatch.setenv("HERDR_CALLS", str(calls_path))

    receipt = run_herdr_cleanup(
        run_dir=run_dir,
        mode="apply",
        herdr_bin=str(fake_herdr),
        workspace_lease_path=lease_path,
    )

    assert receipt["ok"] is False
    assert receipt["status"] == "BLOCKED"
    assert receipt["applied_actions"][0]["applied"] is True
    assert receipt["applied_action_count"] == 1
    assert receipt["post_verified_absent_count"] == 0
    assert receipt["applied_actions"][0]["post_verify_returncode"] == 0
    assert receipt["applied_actions"][0]["post_verify_error_code"] is None
    assert receipt["applied_actions"][0]["post_verified_absent"] is False
    calls = [json.loads(line) for line in calls_path.read_text(encoding="utf-8").splitlines()]
    assert calls == [
        {"argv": ["--session", "default", "workspace", "close", "w-run"]},
        {"argv": ["--session", "default", "workspace", "get", "w-run"]},
    ]


def test_herdr_cleanup_blocks_mismatched_workspace_lease(tmp_path: Path) -> None:
    run_dir = tmp_path / "run"
    _write_manifest(run_dir)
    lease_path = _write_workspace_lease(run_dir, workspace_ids=["w-other"])

    receipt = run_herdr_cleanup(
        run_dir=run_dir,
        mode="apply",
        herdr_bin="/tmp/should-not-run-herdr",
        workspace_lease_path=lease_path,
    )

    assert receipt["ok"] is False
    assert receipt["alerts"][0]["code"] == "workspace_lease_missing_workspace"
    assert receipt["applied_actions"] == []
    assert receipt["command_results"] == []


def test_herdr_cleanup_blocks_expired_workspace_lease(tmp_path: Path) -> None:
    run_dir = tmp_path / "run"
    _write_manifest(run_dir)
    lease_path = _write_workspace_lease(run_dir, expires_at="2000-01-01T00:00:00Z")

    receipt = run_herdr_cleanup(
        run_dir=run_dir,
        mode="apply",
        herdr_bin="/tmp/should-not-run-herdr",
        workspace_lease_path=lease_path,
    )

    assert receipt["ok"] is False
    assert receipt["alerts"][0]["code"] == "workspace_lease_expired"
    assert receipt["applied_actions"] == []
    assert receipt["command_results"] == []


def test_herdr_cleanup_can_include_current_workspace(
    tmp_path: Path,
    monkeypatch,
) -> None:
    run_dir = tmp_path / "run"
    _write_manifest(run_dir)
    monkeypatch.setenv("HERDR_WORKSPACE_ID", "w-run")

    receipt = run_herdr_cleanup(
        run_dir=run_dir,
        mode="dry-run",
        include_current_workspace=True,
    )

    assert receipt["candidate_count"] == 1


def test_herdr_cleanup_ignores_unrelated_environment(monkeypatch, tmp_path: Path) -> None:
    run_dir = tmp_path / "run"
    _write_manifest(run_dir)
    monkeypatch.setenv("HERDR_WORKSPACE_ID", "w-other")
    monkeypatch.setenv("HERDR_SESSION", "default")

    receipt = run_herdr_cleanup(run_dir=run_dir, mode="audit")

    assert receipt["candidate_count"] == 1
    assert receipt["candidates"][0]["workspace_id"] == "w-run"


def test_herdr_cleanup_collects_provider_session_state_paths(tmp_path: Path) -> None:
    run_dir = tmp_path / "readiness-run"
    readiness_dir = run_dir / "readiness"
    readiness_dir.mkdir(parents=True)
    session_state = readiness_dir / "codex.session-state.json"
    session_state.write_text(
        json.dumps(
            {
                "schema": "tau.provider_session_state.v1",
                "provider_id": "codex",
                "workspace_id": "w-life",
                "pane_id": "w-life:p5",
                "terminal_id": "term-life",
                "state": "ready",
                "ready": True,
            }
        ),
        encoding="utf-8",
    )
    (run_dir / "runtime-manifest.json").write_text(
        json.dumps(
            {
                "schema": "tau.provider_readiness_runtime_manifest.v1",
                "run_id": "run-life",
                "provider_session_states": [str(session_state)],
            }
        ),
        encoding="utf-8",
    )

    receipt = run_herdr_cleanup(run_dir=run_dir, mode="dry-run")

    assert receipt["candidate_count"] == 1
    assert receipt["resource_count"] == 1
    assert receipt["candidates"][0]["workspace_id"] == "w-life"
    assert receipt["candidates"][0]["pane_ids"] == ["w-life:p5"]
    assert receipt["resources"][0]["sources"] == ["provider_session_states"]


def test_herdr_cleanup_apply_blocks_recorded_session_candidates(
    tmp_path: Path,
) -> None:
    run_dir = tmp_path / "run"
    _write_manifest(run_dir)
    manifest_path = run_dir / "runtime-manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["provider_sessions"]["codex"]["session"] = "session-codex"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    lease_path = _write_workspace_lease(run_dir)

    receipt = run_herdr_cleanup(
        run_dir=run_dir,
        mode="apply",
        herdr_bin="/tmp/should-not-run-herdr",
        workspace_lease_path=lease_path,
    )

    assert receipt["ok"] is False
    assert receipt["status"] == "BLOCKED"
    assert receipt["candidate_count"] == 2
    assert {candidate["action"] for candidate in receipt["candidates"]} == {
        "session_stop",
        "workspace_close",
    }
    assert receipt["alerts"][0]["code"] == "missing_session_ownership"
    assert receipt["alerts"][0]["evidence"]["sessions"] == ["session-codex"]
    assert receipt["applied_actions"] == []
    assert receipt["command_results"] == []


def test_herdr_cleanup_apply_stops_owned_sessions(
    tmp_path: Path,
    monkeypatch,
) -> None:
    run_dir = tmp_path / "run"
    _write_manifest(run_dir)
    manifest_path = run_dir / "runtime-manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["run_id"] = "run-session"
    manifest["provider_sessions"]["codex"]["session"] = "session-codex"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    lease_path = _write_workspace_lease(run_dir, run_id="run-session")
    ownership_path = _write_session_ownership(run_dir, run_id="run-session")
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    calls_path = tmp_path / "calls.jsonl"
    fake_herdr = bin_dir / "herdr"
    fake_herdr.write_text(
        "#!/usr/bin/env bash\n"
        'printf \'{"argv":[\' >> "$HERDR_CALLS"\n'
        "first=1\n"
        'for arg in "$@"; do\n'
        '  if [ "$first" = 0 ]; then printf \',\' >> "$HERDR_CALLS"; fi\n'
        "  first=0\n"
        '  python3 -c \'import json,sys; '
        'print(json.dumps(sys.argv[1]), end="")\' "$arg" >> "$HERDR_CALLS"\n'
        "done\n"
        "printf ']}\\n' >> \"$HERDR_CALLS\"\n"
        'if [ "$1" = "--session" ]; then shift 2; fi\n'
        'if [ "$1 $2 $3" = "workspace get w-run" ]; then\n'
        '  printf \'{"error":{"code":"workspace_not_found",'
        '"message":"workspace w-run not found"}}\\n\'\n'
        "  exit 1\n"
        "fi\n"
        'if [ "$1 $2 $3" = "session get session-codex" ]; then\n'
        '  printf \'{"error":{"code":"session_not_found",'
        '"message":"session session-codex not found"}}\\n\'\n'
        "  exit 1\n"
        "fi\n"
        'printf \'{"result":{"type":"ok"}}\\n\'\n',
        encoding="utf-8",
    )
    fake_herdr.chmod(0o755)
    monkeypatch.setenv("HERDR_CALLS", str(calls_path))

    receipt = run_herdr_cleanup(
        run_dir=run_dir,
        mode="apply",
        herdr_bin=str(fake_herdr),
        workspace_lease_path=lease_path,
        session_ownership_path=ownership_path,
    )

    assert receipt["ok"] is True
    assert receipt["session_ownership"] == str(ownership_path.resolve())
    assert (
        receipt["session_ownership_sha256"]
        == hashlib.sha256(ownership_path.read_bytes()).hexdigest()
    )
    assert [action["action"] for action in receipt["applied_actions"]] == [
        "session_stop",
        "workspace_close",
    ]
    assert receipt["applied_actions"][0]["session"] == "session-codex"
    assert receipt["applied_actions"][0]["post_verify_action"] == "session_get"
    assert receipt["applied_actions"][0]["post_verify_error_code"] == "session_not_found"
    assert receipt["applied_actions"][0]["post_verified_absent"] is True
    calls = [json.loads(line) for line in calls_path.read_text(encoding="utf-8").splitlines()]
    assert calls == [
        {"argv": ["session", "stop", "session-codex"]},
        {"argv": ["session", "get", "session-codex"]},
        {"argv": ["--session", "default", "workspace", "close", "w-run"]},
        {"argv": ["--session", "default", "workspace", "get", "w-run"]},
    ]


def test_herdr_gc_dry_run_selects_stale_tau_workspaces(
    tmp_path: Path,
    monkeypatch,
) -> None:
    fake_herdr = _write_fake_gc_herdr(tmp_path)
    monkeypatch.setenv("HERDR_WORKSPACE_ID", "w-current")
    monkeypatch.setenv("HERDR_SESSION", "default")

    receipt = run_herdr_gc(
        run_dir=tmp_path / "gc",
        herdr_bin=str(fake_herdr),
    )

    assert receipt["schema"] == "tau.herdr_gc_receipt.v1"
    assert receipt["ok"] is True
    assert receipt["live"] is False
    assert receipt["herdr_surface"] == "fixture"
    assert receipt["mode"] == "dry-run"
    assert receipt["workspace_count"] == 7
    assert [item["workspace_id"] for item in receipt["candidates"]] == ["w-old", "w-generic"]
    assert {item["workspace_id"]: item["reason"] for item in receipt["skipped"]} == {
        "w-current": "current_workspace",
        "w-focused": "focused_workspace",
        "w-working": "agent_status_not_done_or_idle",
    }
    assert receipt["applied_actions"] == []
    assert (tmp_path / "gc" / "herdr-gc-receipt.json").exists()


def test_herdr_gc_does_not_apply_ambient_workspace_id_to_another_session(
    tmp_path: Path,
    monkeypatch,
) -> None:
    fake_herdr = _write_fake_gc_herdr(tmp_path)
    monkeypatch.setenv("HERDR_WORKSPACE_ID", "w-current")
    monkeypatch.setenv("HERDR_SESSION", "ambient-session")

    receipt = run_herdr_gc(
        run_dir=tmp_path / "gc",
        herdr_bin=str(fake_herdr),
        session="selected-session",
    )

    assert receipt["current_workspace"] is None
    assert "w-current" in {item["workspace_id"] for item in receipt["candidates"]}


def test_herdr_gc_apply_closes_and_verifies_absence(
    tmp_path: Path,
    monkeypatch,
) -> None:
    fake_herdr = _write_fake_gc_herdr(tmp_path)
    approval_path = _write_gc_approval_receipt(tmp_path)
    monkeypatch.setenv("HERDR_WORKSPACE_ID", "w-current")
    monkeypatch.setenv("HERDR_SESSION", "default")

    receipt = run_herdr_gc(
        run_dir=tmp_path / "gc",
        apply=True,
        herdr_bin=str(fake_herdr),
        approval_receipt_path=approval_path,
    )

    assert receipt["ok"] is True
    assert receipt["live"] is False
    assert receipt["herdr_surface"] == "fixture"
    assert receipt["mode"] == "apply"
    assert receipt["approval_receipt"] == str(approval_path.resolve())
    assert receipt["approval_target_id_expected"] == _gc_target_id()
    assert receipt["approval_receipt_sha256"] == (
        f"sha256:{hashlib.sha256(approval_path.read_bytes()).hexdigest()}"
    )
    assert receipt["candidate_count"] == 2
    assert receipt["applied_action_count"] == 2
    assert receipt["post_verified_absent_count"] == 2
    assert [
        (action["workspace_id"], action["post_verify_error_code"])
        for action in receipt["applied_actions"]
    ] == [
        ("w-old", "workspace_not_found"),
        ("w-generic", "workspace_not_found"),
    ]
    calls = [
        json.loads(line)
        for line in (tmp_path / "gc-calls.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    assert calls == [
        {"argv": ["--session", "default", "workspace", "list"]},
        {"argv": ["--session", "default", "workspace", "close", "w-old"]},
        {"argv": ["--session", "default", "workspace", "get", "w-old"]},
        {"argv": ["--session", "default", "workspace", "close", "w-generic"]},
        {"argv": ["--session", "default", "workspace", "get", "w-generic"]},
    ]


def test_herdr_gc_apply_blocks_without_approval_receipt(
    tmp_path: Path,
    monkeypatch,
) -> None:
    fake_herdr = _write_fake_gc_herdr(tmp_path)
    monkeypatch.setenv("HERDR_WORKSPACE_ID", "w-current")

    receipt = run_herdr_gc(
        run_dir=tmp_path / "gc",
        apply=True,
        herdr_bin=str(fake_herdr),
    )

    assert receipt["ok"] is False
    assert receipt["status"] == "BLOCKED"
    assert receipt["approval_required"] is True
    assert receipt["alerts"][0]["code"] == "missing_approval_receipt"
    assert receipt["applied_actions"] == []
    calls = [
        json.loads(line)
        for line in (tmp_path / "gc-calls.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    assert calls == [{"argv": ["--session", "default", "workspace", "list"]}]


def test_herdr_gc_apply_blocks_wrong_approval_action(
    tmp_path: Path,
    monkeypatch,
) -> None:
    fake_herdr = _write_fake_gc_herdr(tmp_path)
    approval_path = _write_gc_approval_receipt(tmp_path, requested_action="memory_upsert")
    monkeypatch.setenv("HERDR_WORKSPACE_ID", "w-current")

    receipt = run_herdr_gc(
        run_dir=tmp_path / "gc",
        apply=True,
        herdr_bin=str(fake_herdr),
        approval_receipt_path=approval_path,
    )

    assert receipt["ok"] is False
    assert receipt["status"] == "BLOCKED"
    assert receipt["approval_receipt"] == str(approval_path.resolve())
    assert receipt["alerts"][0]["code"] == "approval_action_mismatch"
    assert receipt["applied_actions"] == []


def test_herdr_gc_apply_blocks_wrong_approval_target(
    tmp_path: Path,
    monkeypatch,
) -> None:
    fake_herdr = _write_fake_gc_herdr(tmp_path)
    approval_path = _write_gc_approval_receipt(tmp_path, target_id="herdr-gc:other-prefix")
    monkeypatch.setenv("HERDR_WORKSPACE_ID", "w-current")

    receipt = run_herdr_gc(
        run_dir=tmp_path / "gc",
        apply=True,
        herdr_bin=str(fake_herdr),
        approval_receipt_path=approval_path,
    )

    assert receipt["ok"] is False
    assert receipt["status"] == "BLOCKED"
    assert receipt["approval_target_id_expected"] == _gc_target_id()
    assert receipt["alerts"][0]["code"] == "approval_target_mismatch"
    assert receipt["alerts"][0]["evidence"]["observed"] == "herdr-gc:other-prefix"
    assert receipt["applied_actions"] == []


def test_herdr_gc_approval_cannot_be_replayed_across_sessions(
    tmp_path: Path,
    monkeypatch,
) -> None:
    fake_herdr = _write_fake_gc_herdr(tmp_path)
    approval_path = _write_gc_approval_receipt(tmp_path, session="session-a")
    monkeypatch.setenv("HERDR_WORKSPACE_ID", "w-current")

    receipt = run_herdr_gc(
        run_dir=tmp_path / "gc",
        apply=True,
        herdr_bin=str(fake_herdr),
        session="session-b",
        approval_receipt_path=approval_path,
    )

    assert receipt["status"] == "BLOCKED"
    assert receipt["alerts"][0]["code"] == "approval_target_mismatch"
    assert receipt["applied_actions"] == []


def test_cli_herdr_gc_apply_without_approval_exits_nonzero(tmp_path: Path) -> None:
    fake_herdr = _write_fake_gc_herdr(tmp_path)

    result = CliRunner().invoke(
        app,
        [
            "herdr-cleanup",
            "gc",
            "--run-dir",
            str(tmp_path / "gc"),
            "--apply",
            "--herdr-bin",
            str(fake_herdr),
            "--session",
            "issue-90",
        ],
    )

    assert result.exit_code == 1
    payload = json.loads(result.stdout)
    assert payload["status"] == "BLOCKED"
    assert payload["backend_session_id"] == "issue-90"
    assert payload["alerts"][0]["code"] == "missing_approval_receipt"
    calls = [
        json.loads(line)
        for line in (tmp_path / "gc-calls.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    assert calls == [{"argv": ["--session", "issue-90", "workspace", "list"]}]


def _write_fake_gc_herdr(tmp_path: Path) -> Path:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    calls_path = tmp_path / "gc-calls.jsonl"
    workspaces_path = tmp_path / "gc-workspaces.json"
    fake_herdr = bin_dir / "herdr"
    workspaces = {
        "result": {
            "workspaces": [
                {
                    "workspace_id": "w-old",
                    "label": "rw-sanity-provider-readiness",
                    "agent_status": "done",
                    "focused": False,
                    "pane_count": 6,
                    "tab_count": 4,
                },
                {
                    "workspace_id": "w-generic",
                    "label": "rw-sanity-generic-provider-dag-adapter-readiness",
                    "agent_status": "idle",
                    "focused": False,
                    "pane_count": 8,
                    "tab_count": 4,
                },
                {
                    "workspace_id": "w-current",
                    "label": "rw-sanity-provider-dag-repair-readiness",
                    "agent_status": "idle",
                    "focused": False,
                },
                {
                    "workspace_id": "w-focused",
                    "label": "tau-live-provider-dag-stress-a1-t60-readiness",
                    "agent_status": "done",
                    "focused": True,
                },
                {
                    "workspace_id": "w-working",
                    "label": "tau-traycer-repair-loop-a2-t120-readiness",
                    "agent_status": "working",
                    "focused": False,
                },
                {
                    "workspace_id": "w-other",
                    "label": "unrelated",
                    "agent_status": "done",
                    "focused": False,
                },
                {
                    "workspace_id": "w-agent-skills",
                    "label": "agent-skills",
                    "agent_status": "idle",
                    "focused": False,
                },
            ]
        }
    }
    workspaces_path.write_text(json.dumps(workspaces), encoding="utf-8")
    fake_herdr.write_text(
        "#!/usr/bin/env bash\n"
        'printf \'{"argv":[\' >> "$HERDR_GC_CALLS"\n'
        "first=1\n"
        'for arg in "$@"; do\n'
        '  if [ "$first" = 0 ]; then printf \',\' >> "$HERDR_GC_CALLS"; fi\n'
        "  first=0\n"
        '  python3 -c \'import json,sys; '
        'print(json.dumps(sys.argv[1]), end="")\' "$arg" >> "$HERDR_GC_CALLS"\n'
        "done\n"
        "printf ']}\\n' >> \"$HERDR_GC_CALLS\"\n"
        'if [ "$1" = "--session" ]; then shift 2; fi\n'
        'if [ "$1 $2" = "workspace list" ]; then\n'
        '  cat "$HERDR_GC_WORKSPACES"\n'
        "  exit 0\n"
        "fi\n"
        'if [ "$1 $2" = "workspace get" ]; then\n'
        '  printf \'{"error":{"code":"workspace_not_found","message":"workspace not found"}}\\n\'\n'
        "  exit 1\n"
        "fi\n"
        'printf \'{"result":{"type":"ok"}}\\n\'\n',
        encoding="utf-8",
    )
    fake_herdr.chmod(0o755)
    os.environ["HERDR_GC_CALLS"] = str(calls_path)
    os.environ["HERDR_GC_WORKSPACES"] = str(workspaces_path)
    return fake_herdr


def _write_gc_approval_receipt(
    tmp_path: Path,
    *,
    requested_action: str = "herdr_gc_apply",
    target_id: str | None = None,
    session: str = "default",
) -> Path:
    path = tmp_path / f"approval-{requested_action}.json"
    path.write_text(
        json.dumps(
            {
                "schema": "tau.approval_gate_receipt.v1",
                "ok": True,
                "status": "PASS",
                "requested_action": requested_action,
                "approval_packet": str(tmp_path / "approval-packet.json"),
                "approval_packet_sha256": "sha256:test-approval",
                "packet_summary": {
                    "target_id": target_id or _gc_target_id(session),
                },
                "errors": [],
            }
        ),
        encoding="utf-8",
    )
    return path


def _gc_target_id(session: str = "default") -> str:
    return f"herdr-gc:{session}:{','.join(DEFAULT_GC_LABEL_PREFIXES)}"


def _write_workspace_lease(
    run_dir: Path,
    *,
    run_id: str | None = None,
    workspace_ids: list[str] | None = None,
    expires_at: str = "2099-01-01T00:00:00Z",
    cleanup_policy: str = "apply",
) -> Path:
    lease_path = run_dir / "herdr-workspace-lease.json"
    lease_path.write_text(
        json.dumps(
            {
                "schema": "tau.herdr_workspace_lease.v1",
                "run_id": run_id,
                "dag_id": "test-dag",
                "owner": "tau-orchestrator",
                "created_at": "2026-07-05T00:00:00Z",
                "expires_at": expires_at,
                "cleanup_policy": cleanup_policy,
                "workspace_ids": workspace_ids or ["w-run"],
            }
        ),
        encoding="utf-8",
    )
    return lease_path


def _write_session_ownership(
    run_dir: Path,
    *,
    run_id: str | None = None,
    session_ids: list[str] | None = None,
    expires_at: str = "2099-01-01T00:00:00Z",
    cleanup_policy: str = "apply",
) -> Path:
    ownership_path = run_dir / "herdr-session-ownership.json"
    ownership_path.write_text(
        json.dumps(
            {
                "schema": "tau.herdr_session_ownership.v1",
                "run_id": run_id,
                "dag_id": "test-dag",
                "owner": "tau-orchestrator",
                "created_at": "2026-07-05T00:00:00Z",
                "expires_at": expires_at,
                "cleanup_policy": cleanup_policy,
                "session_ids": session_ids or ["session-codex"],
            }
        ),
        encoding="utf-8",
    )
    return ownership_path
