"""Prompt autocomplete helpers for Tau's Textual TUI."""

from collections.abc import Sequence
from dataclasses import dataclass

from tau_coding.commands import CommandRegistry, SlashCommand
from tau_coding.prompt_templates import PromptTemplate
from tau_coding.skills import Skill


@dataclass(frozen=True, slots=True)
class CompletionItem:
    """One selectable prompt completion."""

    display: str
    replacement: str
    start: int
    end: int
    description: str | None = None

    def apply(self, text: str) -> str:
        """Apply this completion to input text."""
        return f"{text[: self.start]}{self.replacement}{text[self.end :]}"


@dataclass(frozen=True, slots=True)
class CompletionState:
    """Current autocomplete state for the prompt input."""

    items: tuple[CompletionItem, ...] = ()
    selected_index: int = 0

    @property
    def selected(self) -> CompletionItem | None:
        """Return the currently selected completion item."""
        if not self.items:
            return None
        return self.items[self.selected_index]

    def select_next(self) -> CompletionState:
        """Return a state with the next item selected."""
        if not self.items:
            return self
        return CompletionState(
            items=self.items,
            selected_index=(self.selected_index + 1) % len(self.items),
        )

    def select_previous(self) -> CompletionState:
        """Return a state with the previous item selected."""
        if not self.items:
            return self
        return CompletionState(
            items=self.items,
            selected_index=(self.selected_index - 1) % len(self.items),
        )


def build_completion_state(
    text: str,
    *,
    command_registry: CommandRegistry,
    skills: Sequence[Skill],
    prompt_templates: Sequence[PromptTemplate],
    model_names: Sequence[str] = (),
    provider_names: Sequence[str] = (),
    session_ids: Sequence[str] = (),
) -> CompletionState:
    """Build autocomplete suggestions for the current prompt text."""
    del prompt_templates
    if not text.startswith("/") or text.startswith("//"):
        return CompletionState()

    token_end = _first_token_end(text)
    token = text[:token_end]
    if token.startswith("/skill:"):
        return CompletionState(_skill_completions(token=token, token_end=token_end, skills=skills))

    if ":" in token:
        return CompletionState()

    argument_completions = _command_argument_completions(
        text=text,
        token_end=token_end,
        model_names=model_names,
        provider_names=provider_names,
        session_ids=session_ids,
    )
    if argument_completions is not None:
        return CompletionState(argument_completions)

    return CompletionState(
        _command_completions(token=token, token_end=token_end, registry=command_registry)
    )


def _command_completions(
    *, token: str, token_end: int, registry: CommandRegistry
) -> tuple[CompletionItem, ...]:
    prefix = token.removeprefix("/").lower()
    suggestions: list[CompletionItem] = []
    for command in registry.list_commands():
        suggestions.extend(_command_alias_completions(command, prefix=prefix, token_end=token_end))
    return tuple(sorted(suggestions, key=lambda item: item.display))


def _command_alias_completions(
    command: SlashCommand, *, prefix: str, token_end: int
) -> list[CompletionItem]:
    names = (command.name, *command.aliases)
    suggestions: list[CompletionItem] = []
    for name in names:
        if not name.startswith(prefix):
            continue
        display = f"/{name}"
        replacement = f"/{name}"
        if command.name == "skill" and name == command.name:
            display = "/skill:"
            replacement = "/skill:"
        suggestions.append(
            CompletionItem(
                display=display,
                replacement=replacement,
                start=0,
                end=token_end,
                description=command.description,
            )
        )
    return suggestions


def _skill_completions(
    *, token: str, token_end: int, skills: Sequence[Skill]
) -> tuple[CompletionItem, ...]:
    prefix = token.removeprefix("/skill:").lower()
    suggestions = [
        CompletionItem(
            display=f"/skill:{skill.name}",
            replacement=f"/skill:{skill.name}",
            start=0,
            end=token_end,
            description=skill.description,
        )
        for skill in sorted(skills, key=lambda item: item.name)
        if skill.name.lower().startswith(prefix)
    ]
    return tuple(suggestions)


def _command_argument_completions(
    *,
    text: str,
    token_end: int,
    model_names: Sequence[str],
    provider_names: Sequence[str],
    session_ids: Sequence[str],
) -> tuple[CompletionItem, ...] | None:
    if token_end >= len(text):
        return None

    command_name = text[:token_end].removeprefix("/").lower()
    if command_name == "model":
        return _value_completions(
            text=text,
            start=token_end + 1,
            values=model_names,
            description="Switch model",
        )
    if command_name == "provider":
        return _value_completions(
            text=text,
            start=token_end + 1,
            values=provider_names,
            description="Switch provider",
        )
    if command_name == "resume":
        return _value_completions(
            text=text,
            start=token_end + 1,
            values=session_ids,
            description="Resume session",
        )
    return None


def _value_completions(
    *, text: str, start: int, values: Sequence[str], description: str
) -> tuple[CompletionItem, ...]:
    end = _argument_token_end(text, start)
    prefix = text[start:end].lower()
    return tuple(
        CompletionItem(
            display=value,
            replacement=value,
            start=start,
            end=end,
            description=description,
        )
        for value in sorted(values)
        if value.lower().startswith(prefix)
    )


def _first_token_end(text: str) -> int:
    separator = text.find(" ")
    return len(text) if separator == -1 else separator


def _argument_token_end(text: str, start: int) -> int:
    separator = text.find(" ", start)
    return len(text) if separator == -1 else separator
