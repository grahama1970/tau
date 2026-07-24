"""Markdown prompt template loading and rendering."""

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from tau_coding.resources import (
    ResourceDiagnostic,
    ResourceError,
    TauResourcePaths,
    derive_description,
    parse_markdown_resource,
)

_TEMPLATE_VARIABLE_RE = re.compile(r"{{\s*([a-zA-Z_][a-zA-Z0-9_]*)\s*}}")
_PROMPT_ARGUMENT_RE = re.compile(
    r"\$\{(\d+|ARGUMENTS|@):-([^}]*)\}|\$\{@:(\d+)(?::(\d+))?\}|\$(ARGUMENTS|@|\d+)"
)
_ARGUMENT_TEMPLATE_VARIABLES = {"arguments", "args"}


@dataclass(frozen=True, slots=True)
class PromptTemplate:
    """A markdown prompt template resource."""

    name: str
    path: Path
    content: str
    description: str | None = None
    argument_hint: str | None = None


def load_prompt_templates(paths: TauResourcePaths | None = None) -> list[PromptTemplate]:
    """Load markdown prompt templates from Tau and `.agents` resource directories."""
    resource_paths = paths or TauResourcePaths()
    templates_by_name: dict[str, PromptTemplate] = {}
    for prompts_dir in resource_paths.prompts_dirs:
        for template in _load_prompt_templates_from_dir(prompts_dir):
            templates_by_name[template.name] = template
    return sorted(templates_by_name.values(), key=lambda template: template.name)


def load_prompt_templates_with_diagnostics(
    paths: TauResourcePaths | None = None,
) -> tuple[list[PromptTemplate], list[ResourceDiagnostic]]:
    """Load prompt templates and return non-fatal discovery diagnostics."""
    resource_paths = paths or TauResourcePaths()
    templates_by_name: dict[str, PromptTemplate] = {}
    diagnostics: list[ResourceDiagnostic] = []
    for prompts_dir in resource_paths.prompts_dirs:
        templates, directory_diagnostics = _load_prompt_templates_from_dir_with_diagnostics(
            prompts_dir
        )
        diagnostics.extend(directory_diagnostics)
        for template in templates:
            previous = templates_by_name.get(template.name)
            if previous is not None:
                diagnostics.append(
                    ResourceDiagnostic(
                        kind="prompt",
                        name=template.name,
                        path=template.path,
                        message=f"overrides lower-precedence resource at {previous.path}",
                    )
                )
            templates_by_name[template.name] = template
    return sorted(templates_by_name.values(), key=lambda template: template.name), diagnostics


def render_prompt_template(
    template: PromptTemplate,
    variables: Mapping[str, str],
    *,
    missing: str | None = None,
) -> str:
    """Render a prompt template using `{{ variable }}` placeholders.

    By default, missing variables raise `ResourceError`. Callers that treat
    templates as user-facing shortcuts can pass `missing` to render absent
    variables as a fallback string instead.
    """

    def replace(match: re.Match[str]) -> str:
        name = match.group(1)
        value = variables.get(name)
        if value is None:
            if missing is None:
                raise ResourceError(f"Missing prompt template variable: {name}")
            return missing
        return value

    return _TEMPLATE_VARIABLE_RE.sub(replace, template.content)


def expand_prompt_template_command(
    text: str,
    templates: Sequence[PromptTemplate],
) -> str | None:
    """Expand `/name [arguments]` text with a loaded prompt template.

    Template names are matched by markdown filename stem. Invocation arguments are
    available to templates as `{{ arguments }}`, `{{ args }}`, or Pi-compatible
    shell-style placeholders such as `$1`, `$@`, `$ARGUMENTS`, `${1:-default}`,
    and `${@:2}`. If a template has no argument placeholders, arguments are
    appended after a blank line.
    """
    stripped = text.strip()
    if not stripped.startswith("/") or stripped.startswith("//") or stripped.startswith("/skill:"):
        return None

    name, args_string = _parse_prompt_template_command(stripped)
    if not name:
        return None

    template = _find_prompt_template(name, templates)
    if template is None:
        return None

    args = _parse_command_args(args_string)
    rendered = render_prompt_template(
        template,
        {"arguments": args_string, "args": args_string},
        missing="",
    )
    rendered = _substitute_command_args(rendered, args)
    if args_string and not _template_references_arguments(template.content):
        return f"{rendered.rstrip()}\n\n{args_string}"
    return rendered


def _template_references_arguments(content: str) -> bool:
    return _PROMPT_ARGUMENT_RE.search(content) is not None or any(
        match.group(1) in _ARGUMENT_TEMPLATE_VARIABLES
        for match in _TEMPLATE_VARIABLE_RE.finditer(content)
    )


def _find_prompt_template(
    name: str,
    templates: Sequence[PromptTemplate],
) -> PromptTemplate | None:
    normalized_name = name.strip().removeprefix("/").lower()
    for template in templates:
        if template.name.lower() == normalized_name:
            return template
    return None


def _parse_prompt_template_command(text: str) -> tuple[str, str]:
    command, separator, args = text[1:].partition(" ")
    return command.strip().lower(), args.strip() if separator else ""


def _parse_command_args(args_string: str) -> list[str]:
    args: list[str] = []
    current = ""
    in_quote: str | None = None

    for char in args_string:
        if in_quote:
            if char == in_quote:
                in_quote = None
            else:
                current += char
        elif char in {'"', "'"}:
            in_quote = char
        elif char.isspace():
            if current:
                args.append(current)
                current = ""
        else:
            current += char

    if current:
        args.append(current)
    return args


def _substitute_command_args(content: str, args: Sequence[str]) -> str:
    all_args = " ".join(args)

    def replace(match: re.Match[str]) -> str:
        default_target = match.group(1)
        default_value = match.group(2)
        slice_start = match.group(3)
        slice_length = match.group(4)
        simple = match.group(5)

        if default_target is not None:
            value = (
                all_args
                if default_target in {"@", "ARGUMENTS"}
                else _argument_at(args, int(default_target) - 1)
            )
            return value if value else default_value

        if slice_start is not None:
            start = max(int(slice_start) - 1, 0)
            if slice_length is not None:
                length = int(slice_length)
                return " ".join(args[start : start + length])
            return " ".join(args[start:])

        if simple in {"@", "ARGUMENTS"}:
            return all_args
        if simple is not None:
            return _argument_at(args, int(simple) - 1)
        return match.group(0)

    return _PROMPT_ARGUMENT_RE.sub(replace, content)


def _argument_at(args: Sequence[str], index: int) -> str:
    if index < 0 or index >= len(args):
        return ""
    return args[index]


def _load_prompt_templates_from_dir(prompts_dir: Path) -> list[PromptTemplate]:
    templates, diagnostics = _load_prompt_templates_from_dir_with_diagnostics(prompts_dir)
    if diagnostics:
        first = diagnostics[0]
        raise ResourceError(first.message)
    return templates


def _load_prompt_templates_from_dir_with_diagnostics(
    prompts_dir: Path,
) -> tuple[list[PromptTemplate], list[ResourceDiagnostic]]:
    if not prompts_dir.exists() or not prompts_dir.is_dir():
        return [], []

    templates: list[PromptTemplate] = []
    diagnostics: list[ResourceDiagnostic] = []
    seen: set[str] = set()
    for path in sorted(prompts_dir.glob("*.md"), key=lambda item: item.name):
        name = path.stem
        if name in seen:
            diagnostics.append(
                ResourceDiagnostic(
                    kind="prompt",
                    name=name,
                    path=path,
                    message=f"Duplicate prompt template name ignored in {prompts_dir}",
                )
            )
            continue
        seen.add(name)
        try:
            templates.append(_load_prompt_template(name, path))
        except (OSError, UnicodeDecodeError) as exc:
            diagnostics.append(
                ResourceDiagnostic(
                    kind="prompt",
                    name=name,
                    path=path,
                    message=f"could not read prompt template: {exc}",
                    severity="error",
                )
            )
    return templates, diagnostics


def _load_prompt_template(name: str, path: Path) -> PromptTemplate:
    raw = path.read_text(encoding="utf-8")
    metadata, content = parse_markdown_resource(raw)
    description = metadata.get("description") or derive_description(content)
    argument_hint = metadata.get("argument-hint")
    return PromptTemplate(
        name=name,
        path=path,
        content=content,
        description=description,
        argument_hint=argument_hint,
    )
