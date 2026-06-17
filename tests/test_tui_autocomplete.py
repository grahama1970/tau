from pathlib import Path

from tau_coding.commands import create_default_command_registry
from tau_coding.skills import Skill
from tau_coding.tui.autocomplete import build_completion_state


def test_command_completion_suggests_registered_commands() -> None:
    state = build_completion_state(
        "/st",
        command_registry=create_default_command_registry(),
        skills=(),
        prompt_templates=(),
    )

    assert [item.display for item in state.items] == ["/status"]
    assert state.selected is not None
    assert state.selected.apply("/st") == "/status"


def test_skill_command_completion_prefers_colon_form() -> None:
    state = build_completion_state(
        "/ski",
        command_registry=create_default_command_registry(),
        skills=(),
        prompt_templates=(),
    )

    assert "/skill:" in [item.display for item in state.items]


def test_skill_name_completion_preserves_request_text() -> None:
    state = build_completion_state(
        "/skill:r fix tests",
        command_registry=create_default_command_registry(),
        skills=(
            Skill(
                name="review",
                path=Path("review.md"),
                content="Review code",
                description="Review code",
            ),
        ),
        prompt_templates=(),
    )

    assert [item.display for item in state.items] == ["/skill:review"]
    assert state.selected is not None
    assert state.selected.apply("/skill:r fix tests") == "/skill:review fix tests"


def test_completion_selection_wraps() -> None:
    state = build_completion_state(
        "/s",
        command_registry=create_default_command_registry(),
        skills=(),
        prompt_templates=(),
    )

    assert len(state.items) > 1
    assert state.select_previous().selected_index == len(state.items) - 1
    assert state.select_next().selected_index == 1


def test_model_argument_completion_preserves_existing_text() -> None:
    state = build_completion_state(
        "/model fak continue",
        command_registry=create_default_command_registry(),
        skills=(),
        prompt_templates=(),
        model_names=("fake-model", "other-model"),
    )

    assert [item.display for item in state.items] == ["fake-model"]
    assert state.selected is not None
    assert state.selected.apply("/model fak continue") == "/model fake-model continue"


def test_provider_argument_completion_uses_available_providers() -> None:
    state = build_completion_state(
        "/provider lo",
        command_registry=create_default_command_registry(),
        skills=(),
        prompt_templates=(),
        provider_names=("openai", "local"),
    )

    assert [item.display for item in state.items] == ["local"]


def test_resume_argument_completion_uses_session_ids() -> None:
    state = build_completion_state(
        "/resume sess",
        command_registry=create_default_command_registry(),
        skills=(),
        prompt_templates=(),
        session_ids=("session-1", "other"),
    )

    assert [item.display for item in state.items] == ["session-1"]
    assert state.selected is not None
    assert state.selected.apply("/resume sess") == "/resume session-1"
