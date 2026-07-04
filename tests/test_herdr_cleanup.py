import hashlib
import json
import os
from pathlib import Path

from tau_coding.herdr_cleanup import run_herdr_cleanup, run_herdr_gc


def _write_manifest(run_dir: Path) -> None:
    run_dir.mkdir(parents=True)
    (run_dir / "runtime-manifest.json").write_text(
        json.dumps(
            {
                "schema": "tau.provider_dag_runtime_manifest.v1",
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


def test_herdr_cleanup_dry_run_collects_run_owned_workspace(tmp_path: Path) -> None:
    run_dir = tmp_path / "run"
    _write_manifest(run_dir)

    receipt = run_herdr_cleanup(run_dir=run_dir, mode="dry-run")

    assert receipt["schema"] == "tau.herdr_cleanup_receipt.v1"
    assert receipt["ok"] is True
    assert receipt["live"] is False
    assert receipt["runtime_manifest_sha256"] == hashlib.sha256(
        (run_dir / "runtime-manifest.json").read_bytes()
    ).hexdigest()
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

    receipt = run_herdr_cleanup(run_dir=run_dir, mode="dry-run")

    assert receipt["candidate_count"] == 0
    assert receipt["current_workspace"] == "w-run"


def test_herdr_cleanup_apply_uses_herdr_workspace_close(
    tmp_path: Path,
    monkeypatch,
) -> None:
    run_dir = tmp_path / "run"
    _write_manifest(run_dir)
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    calls_path = tmp_path / "calls.jsonl"
    fake_herdr = bin_dir / "herdr"
    fake_herdr.write_text(
        "#!/usr/bin/env bash\n"
        "printf '{\"argv\":[' >> \"$HERDR_CALLS\"\n"
        "first=1\n"
        "for arg in \"$@\"; do\n"
        "  if [ \"$first\" = 0 ]; then printf ',' >> \"$HERDR_CALLS\"; fi\n"
        "  first=0\n"
        "  python3 -c 'import json,sys; print(json.dumps(sys.argv[1]), end=\"\")' \"$arg\" >> \"$HERDR_CALLS\"\n"
        "done\n"
        "printf ']}\\n' >> \"$HERDR_CALLS\"\n"
        "if [ \"$1 $2 $3\" = \"workspace get w-run\" ]; then\n"
        "  printf '{\"error\":{\"code\":\"workspace_not_found\",\"message\":\"workspace w-run not found\"}}\\n'\n"
        "  exit 1\n"
        "fi\n"
        "printf '{\"result\":{\"type\":\"ok\"}}\\n'\n",
        encoding="utf-8",
    )
    fake_herdr.chmod(0o755)
    monkeypatch.setenv("HERDR_CALLS", str(calls_path))

    receipt = run_herdr_cleanup(run_dir=run_dir, mode="apply", herdr_bin=str(fake_herdr))

    assert receipt["ok"] is True
    assert receipt["live"] is True
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
    calls = [json.loads(line) for line in calls_path.read_text(encoding="utf-8").splitlines()]
    assert calls == [
        {"argv": ["workspace", "close", "w-run"]},
        {"argv": ["workspace", "get", "w-run"]},
    ]
    assert receipt["command_results"][0]["argv"] == [str(fake_herdr), "workspace", "close", "w-run"]
    assert receipt["command_results"][1]["argv"] == [str(fake_herdr), "workspace", "get", "w-run"]


def test_herdr_cleanup_apply_blocks_when_workspace_still_exists(
    tmp_path: Path,
    monkeypatch,
) -> None:
    run_dir = tmp_path / "run"
    _write_manifest(run_dir)
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    calls_path = tmp_path / "calls.jsonl"
    fake_herdr = bin_dir / "herdr"
    fake_herdr.write_text(
        "#!/usr/bin/env bash\n"
        "printf '{\"argv\":[' >> \"$HERDR_CALLS\"\n"
        "first=1\n"
        "for arg in \"$@\"; do\n"
        "  if [ \"$first\" = 0 ]; then printf ',' >> \"$HERDR_CALLS\"; fi\n"
        "  first=0\n"
        "  python3 -c 'import json,sys; print(json.dumps(sys.argv[1]), end=\"\")' \"$arg\" >> \"$HERDR_CALLS\"\n"
        "done\n"
        "printf ']}\\n' >> \"$HERDR_CALLS\"\n"
        "if [ \"$1 $2 $3\" = \"workspace get w-run\" ]; then\n"
        "  printf '{\"workspace\":{\"id\":\"w-run\"}}\\n'\n"
        "  exit 0\n"
        "fi\n"
        "printf '{\"result\":{\"type\":\"ok\"}}\\n'\n",
        encoding="utf-8",
    )
    fake_herdr.chmod(0o755)
    monkeypatch.setenv("HERDR_CALLS", str(calls_path))

    receipt = run_herdr_cleanup(run_dir=run_dir, mode="apply", herdr_bin=str(fake_herdr))

    assert receipt["ok"] is False
    assert receipt["status"] == "BLOCKED"
    assert receipt["applied_actions"][0]["applied"] is True
    assert receipt["applied_actions"][0]["post_verify_returncode"] == 0
    assert receipt["applied_actions"][0]["post_verify_error_code"] is None
    assert receipt["applied_actions"][0]["post_verified_absent"] is False
    calls = [json.loads(line) for line in calls_path.read_text(encoding="utf-8").splitlines()]
    assert calls == [
        {"argv": ["workspace", "close", "w-run"]},
        {"argv": ["workspace", "get", "w-run"]},
    ]


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


def test_herdr_gc_dry_run_selects_stale_tau_workspaces(
    tmp_path: Path,
    monkeypatch,
) -> None:
    fake_herdr = _write_fake_gc_herdr(tmp_path)
    monkeypatch.setenv("HERDR_WORKSPACE_ID", "w-current")

    receipt = run_herdr_gc(
        run_dir=tmp_path / "gc",
        herdr_bin=str(fake_herdr),
    )

    assert receipt["schema"] == "tau.herdr_gc_receipt.v1"
    assert receipt["ok"] is True
    assert receipt["live"] is False
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


def test_herdr_gc_apply_closes_and_verifies_absence(
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

    assert receipt["ok"] is True
    assert receipt["live"] is True
    assert receipt["mode"] == "apply"
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
        {"argv": ["workspace", "list"]},
        {"argv": ["workspace", "close", "w-old"]},
        {"argv": ["workspace", "get", "w-old"]},
        {"argv": ["workspace", "close", "w-generic"]},
        {"argv": ["workspace", "get", "w-generic"]},
    ]


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
        "printf '{\"argv\":[' >> \"$HERDR_GC_CALLS\"\n"
        "first=1\n"
        "for arg in \"$@\"; do\n"
        "  if [ \"$first\" = 0 ]; then printf ',' >> \"$HERDR_GC_CALLS\"; fi\n"
        "  first=0\n"
        "  python3 -c 'import json,sys; print(json.dumps(sys.argv[1]), end=\"\")' \"$arg\" >> \"$HERDR_GC_CALLS\"\n"
        "done\n"
        "printf ']}\\n' >> \"$HERDR_GC_CALLS\"\n"
        "if [ \"$1 $2\" = \"workspace list\" ]; then\n"
        "  cat \"$HERDR_GC_WORKSPACES\"\n"
        "  exit 0\n"
        "fi\n"
        "if [ \"$1 $2\" = \"workspace get\" ]; then\n"
        "  printf '{\"error\":{\"code\":\"workspace_not_found\",\"message\":\"workspace not found\"}}\\n'\n"
        "  exit 1\n"
        "fi\n"
        "printf '{\"result\":{\"type\":\"ok\"}}\\n'\n",
        encoding="utf-8",
    )
    fake_herdr.chmod(0o755)
    os.environ["HERDR_GC_CALLS"] = str(calls_path)
    os.environ["HERDR_GC_WORKSPACES"] = str(workspaces_path)
    return fake_herdr
