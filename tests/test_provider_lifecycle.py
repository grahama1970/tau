from tau_coding.provider_lifecycle import (
    PROVIDER_SESSION_STATE_SCHEMA,
    build_provider_session_state,
    compact_provider_session_state,
)


def test_provider_session_state_normalizes_ready_process() -> None:
    readiness = _readiness()

    state = build_provider_session_state(readiness)

    assert state["schema"] == PROVIDER_SESSION_STATE_SCHEMA
    assert state["provider_id"] == "codex"
    assert state["state"] == "ready"
    assert state["ready"] is True
    assert state["process"]["alive"] is True
    assert state["process"]["command"] == "codex"
    assert state["diagnostics"]["visible_prompt_is_gate"] is False


def test_provider_session_state_detects_auth_required_from_visible_text() -> None:
    readiness = _readiness()

    state = build_provider_session_state(readiness, visible_text="Login required to continue")

    assert state["state"] == "auth_required"
    assert state["ready"] is False
    assert state["auth"]["status"] == "auth_required"


def test_provider_session_state_preserves_interstitial_state() -> None:
    readiness = _readiness()
    readiness["diagnostics"]["interstitial_visible"] = True

    state = build_provider_session_state(readiness, visible_text="Hooks need review")

    assert state["state"] == "interstitial"
    assert state["ready"] is False
    assert state["interstitial"]["present"] is True
    assert state["interstitial"]["kind"] == "hook_trust"


def test_provider_session_state_blocks_visible_initializing_provider() -> None:
    readiness = _readiness()
    readiness["diagnostics"]["provider_initializing_visible"] = True

    state = build_provider_session_state(
        readiness,
        visible_text="OpenAI Codex\nmodel:       loading\n\n› Explain this codebase",
    )

    assert state["state"] == "blocked"
    assert state["ready"] is False


def test_provider_session_state_classifies_crashed_process() -> None:
    readiness = _readiness()
    readiness["state"] = "crashed"
    readiness["ready"] = False
    readiness["evidence"]["process_alive"] = False
    readiness["evidence"]["foreground_command"] = ""

    state = build_provider_session_state(readiness)

    assert state["state"] == "crashed"
    assert state["ready"] is False
    assert state["process"]["alive"] is False
    assert state["process"]["foreground"] is False


def test_provider_session_state_crashed_process_overrides_ready_claim() -> None:
    readiness = _readiness()
    readiness["state"] = "ready"
    readiness["ready"] = True
    readiness["evidence"]["process_alive"] = False
    readiness["evidence"]["foreground_command"] = ""

    state = build_provider_session_state(readiness)

    assert state["state"] == "crashed"
    assert state["ready"] is False
    assert state["process"]["alive"] is False


def test_compact_provider_session_state_keeps_observability_fields() -> None:
    state = build_provider_session_state(_readiness())

    compact = compact_provider_session_state(state)

    assert compact == {
        "schema": "tau.provider_session_state.v1",
        "provider_id": "codex",
        "workspace_id": "w1",
        "pane_id": "w1:p1",
        "terminal_id": "term-codex",
        "state": "ready",
        "ready": True,
        "source": "herdr_process_info",
        "observed_at": "2026-07-03T00:00:00Z",
        "process_alive": True,
        "foreground_command": "codex",
        "auth_status": "unknown",
        "interstitial_present": False,
        "interstitial_kind": None,
        "provider_api_available": False,
        "visible_log_path": "/tmp/codex.visible.txt",
        "provider_readiness_path": "/tmp/codex.readiness.json",
        "provider_event_log_path": None,
    }


def _readiness() -> dict[str, object]:
    return {
        "schema": "tau.provider_readiness.v1",
        "run_id": "run-1",
        "provider_id": "codex",
        "workspace_id": "w1",
        "pane_id": "w1:p1",
        "terminal_id": "term-codex",
        "state": "ready",
        "ready": True,
        "source": "herdr_process_info",
        "observed_at": "2026-07-03T00:00:00Z",
        "evidence": {
            "process_alive": True,
            "foreground_command": "codex",
            "foreground_argv": ["codex", "--cd", "/repo"],
            "foreground_pid": 123,
            "foreground_cwd": "/repo",
            "pane_agent_status": "working",
            "pane_label": "codex",
            "visible_log_path": "/tmp/codex.visible.txt",
            "provider_readiness_path": "/tmp/codex.readiness.json",
            "provider_event_log_path": None,
        },
        "diagnostics": {
            "visible_prompt_observed": True,
            "visible_prompt_is_gate": False,
            "interstitial_visible": False,
            "readiness_actions": [],
        },
    }
