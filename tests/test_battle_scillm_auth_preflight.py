import json
import sys
from pathlib import Path

from tau_coding import battle_live_handoff, battle_scillm


def test_codex_auth_problem_accepts_configured_status() -> None:
    assert (
        battle_scillm._codex_auth_problem(
            {"codex": {"status": "configured"}},
            model="gpt-5.5",
        )
        is None
    )


def test_codex_auth_problem_still_blocks_expired_status() -> None:
    assert (
        battle_scillm._codex_auth_problem(
            {"codex": {"status": "expired"}},
            model="gpt-5.5",
        )
        == "scillm_codex_auth_expired"
    )


def test_scillm_auth_preflight_accepts_configured_codex_status(monkeypatch) -> None:
    def fake_auth(base_url: str, api_key: str) -> dict[str, object]:
        return {
            "status": "PASS",
            "status_code": 200,
            "body": {"codex": {"status": "configured"}},
        }

    monkeypatch.setattr(battle_scillm, "_request_scillm_auth", fake_auth)

    receipt = battle_scillm.preflight_battle_scillm_auth(
        scillm_base_url="http://127.0.0.1:4001",
        model="gpt-5.5",
        api_key="test-key",
    )

    assert receipt["status"] == "PASS"
    assert receipt["ok"] is True
    assert "reason" not in receipt
    assert receipt["repair_attempted"] is False


def test_scillm_auth_preflight_blocks_stale_codex_container_without_repair(
    monkeypatch,
) -> None:
    def fake_auth(base_url: str, api_key: str) -> dict[str, object]:
        return {
            "status": "PASS",
            "status_code": 200,
            "body": {"codex": {"status": "expired"}},
        }

    monkeypatch.setattr(battle_scillm, "_request_scillm_auth", fake_auth)
    monkeypatch.setattr(
        battle_scillm,
        "_codex_auth_diagnostics",
        lambda: {
            "container_auth_stale": True,
            "repair_cwd": "/home/graham/workspace/experiments/scillm/deploy/docker",
            "repair_command": "docker compose up -d --force-recreate scillm-proxy",
        },
    )

    receipt = battle_scillm.preflight_battle_scillm_auth(
        scillm_base_url="http://127.0.0.1:4001",
        model="gpt-5.5",
        api_key="test-key",
        allow_repair=False,
    )

    assert receipt["schema"] == "tau.battle_scillm_auth_preflight.v1"
    assert receipt["status"] == "BLOCKED"
    assert receipt["ok"] is False
    assert receipt["mocked"] is False
    assert receipt["live"] is True
    assert receipt["reason"] == "scillm_codex_oauth_stale_container_mount"
    assert receipt["repair_attempted"] is False
    assert "recreate the proxy" in receipt["errors"][0]


def test_scillm_auth_preflight_repairs_stale_codex_container_internally(
    monkeypatch,
) -> None:
    calls: list[str] = []

    def fake_auth(base_url: str, api_key: str) -> dict[str, object]:
        calls.append("auth")
        if len(calls) == 1:
            return {
                "status": "PASS",
                "status_code": 200,
                "body": {"codex": {"status": "expired"}},
            }
        return {
            "status": "PASS",
            "status_code": 200,
            "body": {"codex": {"status": "ok"}},
        }

    monkeypatch.setattr(battle_scillm, "_request_scillm_auth", fake_auth)
    monkeypatch.setattr(
        battle_scillm,
        "_codex_auth_diagnostics",
        lambda: {
            "container_auth_stale": True,
            "repair_cwd": "/home/graham/workspace/experiments/scillm/deploy/docker",
            "repair_command": "docker compose up -d --force-recreate scillm-proxy",
        },
    )
    monkeypatch.setattr(
        battle_scillm,
        "_recreate_scillm_proxy",
        lambda: {"status": "PASS", "returncode": 0},
    )

    receipt = battle_scillm.preflight_battle_scillm_auth(
        scillm_base_url="http://127.0.0.1:4001",
        model="gpt-5.5",
        api_key="test-key",
    )

    assert receipt["status"] == "PASS"
    assert receipt["ok"] is True
    assert receipt["repair_allowed"] is True
    assert receipt["repair_attempted"] is True
    assert receipt["repair_status"] == "PASS"
    assert len(calls) == 2


def test_scillm_auth_preflight_prefers_active_docker_key_over_stale_host_env(
    monkeypatch,
) -> None:
    used_keys: list[str] = []

    monkeypatch.setenv("SCILLM_MASTER_KEY", "stale-host-key")
    monkeypatch.setattr(
        battle_scillm,
        "_docker_api_key",
        lambda: ("active-docker-key", "docker:docker-scillm-proxy-1:SCILLM_MASTER_KEY", None),
    )

    def fake_auth(base_url: str, api_key: str) -> dict[str, object]:
        used_keys.append(api_key)
        return {
            "status": "PASS",
            "status_code": 200,
            "body": {"codex": {"status": "ok"}},
        }

    monkeypatch.setattr(battle_scillm, "_request_scillm_auth", fake_auth)

    receipt = battle_scillm.preflight_battle_scillm_auth(
        scillm_base_url="http://127.0.0.1:4001",
        model="gpt-5.5",
    )

    assert receipt["status"] == "PASS"
    assert receipt["ok"] is True
    assert receipt["api_key_source"] == "docker:docker-scillm-proxy-1:SCILLM_MASTER_KEY"
    assert used_keys == ["active-docker-key"]


def test_battle_live_handoff_blocks_before_workers_when_scillm_auth_fails(
    tmp_path: Path,
    monkeypatch,
) -> None:
    def fail_if_called(*args, **kwargs):  # noqa: ANN002, ANN003
        raise AssertionError("worker materialization should not run after auth preflight failure")

    monkeypatch.setattr(
        battle_live_handoff,
        "_run_auth_preflight",
        lambda args: {
            "schema": "tau.battle_scillm_auth_preflight.v1",
            "status": "BLOCKED",
            "ok": False,
            "mocked": False,
            "live": True,
            "reason": "scillm_codex_oauth_stale_container_mount",
            "errors": ["stale auth"],
        },
    )
    monkeypatch.setattr(battle_live_handoff, "call_battle_subagent", fail_if_called)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "battle-live-handoff",
            "--out-dir",
            str(tmp_path),
            "--battle-id",
            "battle-004",
            "--run-id",
            "run-1",
            "--scenario-id",
            "battle-004",
            "--red-persona",
            "red",
            "--blue-persona",
            "blue",
            "--model",
            "gpt-5.5",
            "--scillm-base-url",
            "http://127.0.0.1:4001",
            "--red-workers",
            "2",
            "--blue-workers",
            "2",
        ],
    )

    battle_live_handoff.main()

    manifest = json.loads((tmp_path / "manifest.json").read_text(encoding="utf-8"))
    preflight = json.loads(
        (tmp_path / "scillm-auth-preflight.json").read_text(encoding="utf-8")
    )
    assert manifest["status"] == "BLOCKED"
    assert manifest["reason"] == "scillm_codex_oauth_stale_container_mount"
    assert manifest["materialized_counts"] == {"blue": 0, "red": 0}
    assert manifest["teams"] == []
    assert preflight["ok"] is False
    assert not (tmp_path / "red").exists()
    assert not (tmp_path / "blue").exists()
