"""Declarative custom slash command policy and execution receipts."""

from __future__ import annotations

import hashlib
import json
import re
import shlex
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from tau_coding.approval_gate import APPROVAL_GATE_RECEIPT_SCHEMA
from tau_coding.commands import CommandArgumentCompletion, CommandContext, CommandResult
from tau_coding.permission_receipts import write_permission_request_receipt
from tau_coding.resources import ResourceDiagnostic, TauResourcePaths

COMMAND_SPEC_POLICY_SCHEMA = "tau.command_spec_policy.v1"
COMMAND_SPEC_EXECUTION_RECEIPT_SCHEMA = "tau.command_execution_receipt.v1"
COMMAND_SPEC_MANIFEST_GLOB = "*.json"
MAX_COMMAND_SPEC_OUTPUT_BYTES = 65536
MAX_COMMAND_SPEC_TIMEOUT_SECONDS = 120
READ_ONLY_BUILTIN_ROUTES = frozenset(
    {
        "changelog",
        "hotkeys",
        "resources",
        "session",
        "system",
        "tools",
        "workflows",
    }
)
FORBIDDEN_CONTROL_PLANE_NAMES = frozenset(
    {
        "approval",
        "approvals",
        "config",
        "credential",
        "credentials",
        "goal",
        "login",
        "logout",
        "model",
        "permission",
        "permissions",
        "policy",
        "provider",
        "reload",
        "scillm",
        "settlement",
        "trust",
        "workflow",
        "workflows",
    }
)

type CommandSpecRouteType = Literal["builtin_command", "editor_insert"]
type CommandSpecSideEffect = Literal["read_only", "prompt_editor_insert"]


@dataclass(frozen=True, slots=True)
class CommandSpecArgument:
    """One command argument declared by a custom command manifest."""

    name: str
    values: tuple[CommandArgumentCompletion, ...]
    required: bool = True


@dataclass(frozen=True, slots=True)
class CommandSpecRoute:
    """A route compiled from a declarative manifest into existing Tau behavior."""

    type: CommandSpecRouteType
    command: str | None = None
    args_by_enum: Mapping[str, str] | None = None
    text_by_enum: Mapping[str, str] | None = None


@dataclass(frozen=True, slots=True)
class CommandSpec:
    """A policy-accepted declarative slash command."""

    name: str
    description: str
    usage: str
    path: Path
    precedence: str
    manifest_sha256: str
    arguments: tuple[CommandSpecArgument, ...]
    route: CommandSpecRoute
    side_effect_class: CommandSpecSideEffect
    required_permission_class: str | None
    resources: tuple[str, ...]
    timeout_seconds: int
    max_output_bytes: int
    requires_network: bool
    requires_subprocess: bool
    requires_provider: bool

    @property
    def argument_completions(self) -> tuple[CommandArgumentCompletion, ...]:
        if not self.arguments:
            return ()
        return self.arguments[0].values

    @property
    def argument_hint(self) -> str | None:
        if not self.arguments:
            return None
        argument = self.arguments[0]
        suffix = "" if argument.required else "?"
        return f"<{argument.name}{suffix}>"


@dataclass(frozen=True, slots=True)
class RejectedCommandSpec:
    """A rejected custom slash command manifest with stable policy reasons."""

    path: Path
    name: str | None
    precedence: str
    reason_codes: tuple[str, ...]
    messages: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class CommandSpecLoadResult:
    """Accepted and rejected command-spec manifests from all resource roots."""

    accepted: tuple[CommandSpec, ...]
    rejected: tuple[RejectedCommandSpec, ...]
    diagnostics: tuple[ResourceDiagnostic, ...]


def command_spec_dirs(paths: TauResourcePaths) -> tuple[Path, ...]:
    """Return command-spec directories in increasing precedence order."""

    tau_paths = paths._paths()
    dirs = [paths.root / "commands"]
    if paths.agents_root is not None:
        dirs.append(paths.agents_root / "commands")
    if paths.cwd is not None:
        dirs.extend(
            [
                tau_paths.project_agents_dir(paths.cwd) / "commands",
                tau_paths.project_tau_dir(paths.cwd) / "commands",
            ]
        )
    return tuple(_dedupe_paths(dirs))


def load_command_specs_with_diagnostics(paths: TauResourcePaths) -> CommandSpecLoadResult:
    """Load, validate, and precedence-resolve declarative command manifests."""

    accepted_by_name: dict[str, CommandSpec] = {}
    accepted_order: list[str] = []
    rejected: list[RejectedCommandSpec] = []
    diagnostics: list[ResourceDiagnostic] = []

    for precedence_index, directory in enumerate(command_spec_dirs(paths)):
        if not directory.exists():
            continue
        if not directory.is_dir():
            rejected_item = RejectedCommandSpec(
                path=directory,
                name=None,
                precedence=str(precedence_index),
                reason_codes=("command_spec_dir_not_directory",),
                messages=("command spec resource path is not a directory",),
            )
            rejected.append(rejected_item)
            diagnostics.append(_diagnostic(rejected_item))
            continue

        parsed_level: list[CommandSpec] = []
        for manifest_path in sorted(directory.glob(COMMAND_SPEC_MANIFEST_GLOB)):
            spec, reject = _parse_manifest(
                manifest_path,
                root=paths.cwd,
                precedence=str(precedence_index),
            )
            if reject is not None:
                rejected.append(reject)
                diagnostics.append(_diagnostic(reject))
                continue
            if spec is not None:
                parsed_level.append(spec)

        name_counts: dict[str, int] = {}
        for spec in parsed_level:
            name_counts[spec.name] = name_counts.get(spec.name, 0) + 1
        duplicate_names = sorted(name for name, count in name_counts.items() if count > 1)
        if duplicate_names:
            for spec in parsed_level:
                if spec.name not in duplicate_names:
                    continue
                reject = RejectedCommandSpec(
                    path=spec.path,
                    name=spec.name,
                    precedence=spec.precedence,
                    reason_codes=("duplicate_custom_name_same_precedence",),
                    messages=(f"duplicate custom command name at precedence {spec.precedence}",),
                )
                rejected.append(reject)
                diagnostics.append(_diagnostic(reject))
            parsed_level = [spec for spec in parsed_level if spec.name not in duplicate_names]

        for spec in parsed_level:
            if spec.name not in accepted_by_name:
                accepted_order.append(spec.name)
            else:
                previous = accepted_by_name[spec.name]
                diagnostics.append(
                    ResourceDiagnostic(
                        kind="command_spec",
                        name=spec.name,
                        path=spec.path,
                        severity="warning",
                        message=f"overrides lower-precedence manifest at {previous.path}",
                    )
                )
            accepted_by_name[spec.name] = spec

    return CommandSpecLoadResult(
        accepted=tuple(
            accepted_by_name[name] for name in accepted_order if name in accepted_by_name
        ),
        rejected=tuple(rejected),
        diagnostics=tuple(diagnostics),
    )


def execute_command_spec(spec: CommandSpec, context: CommandContext) -> CommandResult:
    """Execute an accepted command spec through its governed route."""

    try:
        argument, approval_receipt = _parse_invocation_args(spec, context.args)
    except ValueError as exc:
        return CommandResult(handled=True, message=str(exc))

    receipt_dir = context.session.cwd / ".tau" / "receipts" / "command-specs"
    receipt_dir.mkdir(parents=True, exist_ok=True)
    policy_receipt_path = _receipt_path(receipt_dir, spec.name, "policy")
    route_receipt_path = _receipt_path(receipt_dir, spec.name, "route")
    resource_records = _resource_records(context.session.cwd, spec.resources)

    if spec.side_effect_class != "read_only":
        approval_errors = _approval_receipt_errors(
            approval_receipt=approval_receipt,
            command_name=spec.name,
            resources=spec.resources,
        )
        if approval_errors:
            request_receipt = write_permission_request_receipt(
                action="working_tree_mutation",
                resources=list(spec.resources),
                source_node=f"command-spec:{spec.name}",
                run_dir=receipt_dir,
                session_id=context.session.session_id,
                mode=spec.side_effect_class,
                proposed_save_rule=spec.required_permission_class,
                denied=False,
                reason="custom command side effect requires approval before mutation",
            )
            policy = _policy_receipt(
                spec,
                context=context,
                status="BLOCKED",
                accepted=False,
                reason_codes=("permission_required", *approval_errors),
                resources=resource_records,
                policy_receipt_path=policy_receipt_path,
                route_receipt_path=None,
            )
            _write_json(policy_receipt_path, policy)
            return CommandResult(
                handled=True,
                message=(
                    f"/{spec.name} blocked before side effect: permission_required\n"
                    f"permission_request={request_receipt['receipt_path']}\n"
                    f"policy_receipt={policy_receipt_path}"
                ),
            )

    route_result = _execute_route(spec, context, argument=argument)
    route_receipt = {
        "schema": COMMAND_SPEC_EXECUTION_RECEIPT_SCHEMA,
        "ok": route_result.handled,
        "status": "PASS" if route_result.handled else "BLOCKED",
        "mocked": False,
        "live": True,
        "provider_live": False,
        "command": spec.name,
        "route": {
            "type": spec.route.type,
            "command": spec.route.command,
            "side_effect_class": spec.side_effect_class,
        },
        "argument": argument,
        "resources": resource_records,
        "output_bytes": len((route_result.message or "").encode("utf-8")),
        "receipt_path": str(route_receipt_path),
        "timestamp": _utc_stamp(),
    }
    _write_json(route_receipt_path, route_receipt)
    policy = _policy_receipt(
        spec,
        context=context,
        status="PASS",
        accepted=True,
        reason_codes=(),
        resources=resource_records,
        policy_receipt_path=policy_receipt_path,
        route_receipt_path=route_receipt_path,
    )
    _write_json(policy_receipt_path, policy)
    message = route_result.message or ""
    receipt_message = (
        f"command_policy_receipt={policy_receipt_path}\n"
        f"command_route_receipt={route_receipt_path}"
    )
    if spec.route.type == "editor_insert":
        return CommandResult(
            handled=True,
            editor_insert_text=route_result.editor_insert_text,
            message=receipt_message,
        )
    return CommandResult(handled=True, message=f"{message}\n\n{receipt_message}".strip())


def format_command_spec_diagnostics(
    specs: Sequence[CommandSpec],
    diagnostics: Sequence[ResourceDiagnostic],
) -> str:
    """Return a concise command-spec readback for `/command-specs`."""

    lines = [
        "Command specs:",
        "Accepted:",
        *[
            f"- /{spec.name}: {spec.description} ({spec.precedence}; {spec.path})"
            for spec in specs
        ],
    ]
    if not specs:
        lines.append("- none")
    lines.append("Diagnostics:")
    command_diagnostics = [item for item in diagnostics if item.kind == "command_spec"]
    if command_diagnostics:
        lines.extend(f"- {item.format()}" for item in command_diagnostics)
    else:
        lines.append("- none")
    lines.append("Resource directories:")
    paths = TauResourcePaths(cwd=Path.cwd())
    lines.extend(f"- {path}" for path in command_spec_dirs(paths))
    return "\n".join(lines)


def _parse_manifest(
    path: Path,
    *,
    root: Path | None,
    precedence: str,
) -> tuple[CommandSpec | None, RejectedCommandSpec | None]:
    messages: list[str] = []
    reason_codes: list[str] = []
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        return None, _reject(path, None, precedence, ("invalid_json",), (str(exc),))
    except OSError as exc:
        return None, _reject(path, None, precedence, ("manifest_unreadable",), (str(exc),))
    if not isinstance(payload, dict):
        return None, _reject(
            path,
            None,
            precedence,
            ("manifest_not_object",),
            ("manifest must be a JSON object",),
        )

    if payload.get("schema") != COMMAND_SPEC_POLICY_SCHEMA:
        reason_codes.append("missing_or_unknown_schema_version")
        messages.append(f"schema must be {COMMAND_SPEC_POLICY_SCHEMA}")

    name = _string(payload, "name") or _string(payload, "id")
    if not name or not re.fullmatch(r"[a-z][a-z0-9_-]{1,48}", name):
        reason_codes.append("invalid_command_name")
        messages.append("command name must match [a-z][a-z0-9_-]{1,48}")
        name = name or None
    elif name in _built_in_command_names():
        reason_codes.append("built_in_name_collision")
        messages.append("custom command cannot replace a built-in command")
    elif name in FORBIDDEN_CONTROL_PLANE_NAMES:
        reason_codes.append("forbidden_control_plane_mutation")
        messages.append("custom command targets a forbidden control-plane name")

    route_payload = payload.get("route")
    route, route_errors = _parse_route(route_payload)
    reason_codes.extend(code for code, _message in route_errors)
    messages.extend(message for _code, message in route_errors)

    side_effect_class = _string(payload, "side_effect_class") or "read_only"
    required_permission_class = _string(payload, "required_permission_class")
    if side_effect_class not in {"read_only", "prompt_editor_insert"}:
        reason_codes.append("unknown_side_effect_class")
        messages.append("side_effect_class must be read_only or prompt_editor_insert")
    if side_effect_class != "read_only" and not required_permission_class:
        reason_codes.append("side_effect_permission_required")
        messages.append("side-effect commands must declare required_permission_class")

    requirements = payload.get("requirements")
    if not isinstance(requirements, dict):
        reason_codes.append("requirements_missing")
        messages.append("requirements must declare network/subprocess/provider booleans")
        requirements = {}
    requires_network = requirements.get("network")
    requires_subprocess = requirements.get("subprocess")
    requires_provider = requirements.get("provider")
    if requires_network is not False:
        reason_codes.append("undeclared_network_or_provider")
        messages.append("network access is not available for command specs")
    if requires_provider is not False:
        reason_codes.append("undeclared_network_or_provider")
        messages.append("provider access is not available for command specs")
    if requires_subprocess is not False:
        reason_codes.append("undeclared_subprocess")
        messages.append("subprocess access is not available for command specs")

    limits = payload.get("limits")
    if not isinstance(limits, dict):
        reason_codes.append("limit_outside_policy")
        messages.append("limits must declare timeout_seconds and max_output_bytes")
        limits = {}
    timeout_seconds = _int(limits, "timeout_seconds", default=30)
    max_output_bytes = _int(limits, "max_output_bytes", default=8192)
    if not 1 <= timeout_seconds <= MAX_COMMAND_SPEC_TIMEOUT_SECONDS:
        reason_codes.append("limit_outside_policy")
        messages.append(f"timeout_seconds must be 1..{MAX_COMMAND_SPEC_TIMEOUT_SECONDS}")
    if not 1 <= max_output_bytes <= MAX_COMMAND_SPEC_OUTPUT_BYTES:
        reason_codes.append("limit_outside_policy")
        messages.append(f"max_output_bytes must be 1..{MAX_COMMAND_SPEC_OUTPUT_BYTES}")

    resources, resource_errors = _parse_resources(payload.get("resources"), root=root)
    reason_codes.extend(code for code, _message in resource_errors)
    messages.extend(message for _code, message in resource_errors)

    arguments, argument_errors = _parse_arguments(payload.get("arguments"))
    reason_codes.extend(code for code, _message in argument_errors)
    messages.extend(message for _code, message in argument_errors)

    description = _string(payload, "description") or ""
    usage = _string(payload, "usage") or (f"/{name}" if name else "/custom")
    if not description:
        reason_codes.append("missing_description")
        messages.append("description is required")
    if reason_codes:
        return None, _reject(
            path,
            name,
            precedence,
            tuple(dict.fromkeys(reason_codes)),
            tuple(messages),
        )
    assert name is not None
    assert route is not None
    return CommandSpec(
        name=name,
        description=description,
        usage=usage,
        path=path,
        precedence=precedence,
        manifest_sha256=_sha256(path.read_bytes()),
        arguments=tuple(arguments),
        route=route,
        side_effect_class=side_effect_class,  # type: ignore[arg-type]
        required_permission_class=required_permission_class,
        resources=tuple(resources),
        timeout_seconds=timeout_seconds,
        max_output_bytes=max_output_bytes,
        requires_network=False,
        requires_subprocess=False,
        requires_provider=False,
    ), None


def _parse_route(value: object) -> tuple[CommandSpecRoute | None, list[tuple[str, str]]]:
    errors: list[tuple[str, str]] = []
    if not isinstance(value, dict):
        return None, [("route_missing", "route must be an object")]
    route_type = value.get("type")
    if route_type == "builtin_command":
        command = str(value.get("command") or "").strip().lower()
        if command not in READ_ONLY_BUILTIN_ROUTES:
            errors.append(("route_not_present", "builtin route is not allowlisted or present"))
        args_by_enum = value.get("args_by_enum")
        return CommandSpecRoute(
            type="builtin_command",
            command=command,
            args_by_enum=args_by_enum if isinstance(args_by_enum, dict) else None,
        ), errors
    if route_type == "editor_insert":
        text_by_enum = value.get("text_by_enum")
        if not isinstance(text_by_enum, dict) or not all(
            isinstance(key, str) and isinstance(item, str) for key, item in text_by_enum.items()
        ):
            errors.append(("route_invalid", "editor_insert route requires text_by_enum"))
            text_by_enum = {}
        return CommandSpecRoute(type="editor_insert", text_by_enum=text_by_enum), errors
    return None, [("route_not_present", "route type is not supported")]


def _parse_arguments(value: object) -> tuple[list[CommandSpecArgument], list[tuple[str, str]]]:
    if not isinstance(value, list) or not value:
        return [], [("argument_schema_invalid", "at least one enum argument is required")]
    arguments: list[CommandSpecArgument] = []
    errors: list[tuple[str, str]] = []
    for item in value:
        if not isinstance(item, dict):
            errors.append(("argument_schema_invalid", "argument must be an object"))
            continue
        if item.get("type") != "enum":
            errors.append(("argument_schema_invalid", "only enum arguments are supported"))
            continue
        values = item.get("values")
        if not isinstance(values, list) or not values:
            errors.append(("argument_schema_invalid", "enum argument requires values"))
            continue
        completions: list[CommandArgumentCompletion] = []
        for option in values:
            if isinstance(option, str):
                completions.append(CommandArgumentCompletion(value=option))
            elif isinstance(option, dict) and isinstance(option.get("value"), str):
                description = option.get("description")
                completions.append(
                    CommandArgumentCompletion(
                        value=option["value"],
                        description=description if isinstance(description, str) else None,
                    )
                )
        name = str(item.get("name") or "arg").strip()
        if not completions:
            errors.append(("argument_schema_invalid", "enum values must be non-empty strings"))
            continue
        arguments.append(
            CommandSpecArgument(
                name=name,
                values=tuple(completions),
                required=item.get("required", True) is not False,
            )
        )
    return arguments, errors


def _parse_resources(
    value: object,
    *,
    root: Path | None,
) -> tuple[list[str], list[tuple[str, str]]]:
    if value is None:
        return [], []
    if not isinstance(value, list):
        return [], [("resource_schema_invalid", "resources must be a list")]
    resources: list[str] = []
    errors: list[tuple[str, str]] = []
    for item in value:
        if not isinstance(item, str) or not item.strip():
            errors.append(("resource_schema_invalid", "resource must be a non-empty string"))
            continue
        normalized = item.strip()
        path = Path(normalized)
        if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
            errors.append(("path_escape", f"resource escapes project root: {normalized}"))
            continue
        if root is not None:
            try:
                (root / path).resolve().relative_to(root.resolve())
            except (OSError, ValueError):
                errors.append(("path_escape", f"resource escapes project root: {normalized}"))
                continue
        resources.append(path.as_posix())
    return resources, errors


def _execute_route(
    spec: CommandSpec,
    context: CommandContext,
    *,
    argument: str,
) -> CommandResult:
    if spec.route.type == "editor_insert":
        text_by_enum = spec.route.text_by_enum or {}
        return CommandResult(handled=True, editor_insert_text=text_by_enum.get(argument, ""))
    if spec.route.type == "builtin_command" and spec.route.command is not None:
        command = context.registry.get(spec.route.command)
        if command is None:
            return CommandResult(handled=True, message="route_not_present")
        route_args = ""
        if spec.route.args_by_enum is not None:
            route_args = str(spec.route.args_by_enum.get(argument, ""))
        return command.handler(
            CommandContext(
                session=context.session,
                registry=context.registry,
                text=f"/{spec.route.command} {route_args}".strip(),
                name=spec.route.command,
                args=route_args,
                current_editor_text=context.current_editor_text,
                show_tool_results=context.show_tool_results,
                current_theme=context.current_theme,
                async_ui_supported=context.async_ui_supported,
            )
        )
    return CommandResult(handled=True, message="route_not_present")


def _parse_invocation_args(spec: CommandSpec, raw_args: str) -> tuple[str, Path | None]:
    parts = shlex.split(raw_args)
    approval_receipt: Path | None = None
    values: list[str] = []
    index = 0
    while index < len(parts):
        part = parts[index]
        if part == "--approval-receipt":
            if index + 1 >= len(parts):
                raise ValueError("--approval-receipt requires a path")
            approval_receipt = Path(parts[index + 1])
            index += 2
            continue
        if part.startswith("--approval-receipt="):
            approval_receipt = Path(part.partition("=")[2])
            index += 1
            continue
        values.append(part)
        index += 1
    if not spec.arguments:
        return "", approval_receipt
    argument = values[0] if values else ""
    allowed = {item.value for item in spec.arguments[0].values}
    if argument not in allowed:
        choices = ", ".join(sorted(allowed))
        raise ValueError(f"Usage: {spec.usage}\nargument must be one of: {choices}")
    if len(values) > 1:
        raise ValueError(f"Usage: {spec.usage}")
    return argument, approval_receipt


def _approval_receipt_errors(
    *,
    approval_receipt: Path | None,
    command_name: str,
    resources: Sequence[str],
) -> tuple[str, ...]:
    if approval_receipt is None:
        return ("missing_approval_receipt",)
    try:
        payload = json.loads(approval_receipt.expanduser().read_text(encoding="utf-8"))
    except OSError:
        return ("approval_receipt_unreadable",)
    except json.JSONDecodeError:
        return ("approval_receipt_invalid_json",)
    if not isinstance(payload, dict):
        return ("approval_receipt_not_object",)
    errors: list[str] = []
    if payload.get("schema") != APPROVAL_GATE_RECEIPT_SCHEMA:
        errors.append("approval_receipt_invalid_schema")
    if payload.get("ok") is not True or payload.get("status") != "PASS":
        errors.append("approval_receipt_not_pass")
    if payload.get("requested_action") != "working_tree_mutation":
        errors.append("approval_action_mismatch")
    expected = payload.get("expected_target")
    if not isinstance(expected, dict):
        errors.append("approval_target_missing")
    else:
        if expected.get("command") != command_name:
            errors.append("approval_target_mismatch")
        if expected.get("resources_sha256") != _resources_digest(resources):
            errors.append("approval_resource_mismatch")
    return tuple(errors)


def _policy_receipt(
    spec: CommandSpec,
    *,
    context: CommandContext,
    status: Literal["PASS", "BLOCKED"],
    accepted: bool,
    reason_codes: Sequence[str],
    resources: Sequence[Mapping[str, Any]],
    policy_receipt_path: Path,
    route_receipt_path: Path | None,
) -> dict[str, Any]:
    return {
        "schema": COMMAND_SPEC_POLICY_SCHEMA,
        "ok": accepted,
        "status": status,
        "mocked": False,
        "live": True,
        "provider_live": False,
        "command": spec.name,
        "manifest": str(spec.path),
        "manifest_sha256": f"sha256:{spec.manifest_sha256}",
        "session_id": context.session.session_id,
        "project_root": str(context.session.cwd),
        "goal_hash": _project_goal_hash(context.session.cwd),
        "policy_profile": "tau.command_spec_policy.v1",
        "data_boundary": "project-root-relative-resources",
        "turn": None,
        "attempt": None,
        "side_effect_class": spec.side_effect_class,
        "required_permission_class": spec.required_permission_class,
        "requirements": {
            "network": spec.requires_network,
            "subprocess": spec.requires_subprocess,
            "provider": spec.requires_provider,
        },
        "limits": {
            "timeout_seconds": spec.timeout_seconds,
            "max_output_bytes": spec.max_output_bytes,
        },
        "resources": list(resources),
        "reason_codes": list(reason_codes),
        "route_receipt": str(route_receipt_path) if route_receipt_path is not None else None,
        "receipt_path": str(policy_receipt_path),
        "timestamp": _utc_stamp(),
    }


def _resource_records(root: Path, resources: Sequence[str]) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for resource in resources:
        path = root / resource
        record: dict[str, Any] = {"resource": resource, "path": str(path)}
        try:
            resolved = path.resolve()
            resolved.relative_to(root.resolve())
            record["exists"] = path.exists()
            record["sha256"] = f"sha256:{_sha256(path.read_bytes())}" if path.is_file() else None
        except OSError as exc:
            record["error"] = str(exc)
        except ValueError:
            record["error"] = "path_escape"
        records.append(record)
    return records


def _project_goal_hash(root: Path) -> str | None:
    goal = root / "GOAL.md"
    if not goal.is_file():
        return None
    return f"sha256:{_sha256(goal.read_bytes())}"


def _receipt_path(root: Path, name: str, kind: str) -> Path:
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
    return root / f"{name}-{kind}-{stamp}.json"


def _reject(
    path: Path,
    name: str | None,
    precedence: str,
    reason_codes: Sequence[str],
    messages: Sequence[str],
) -> RejectedCommandSpec:
    return RejectedCommandSpec(
        path=path,
        name=name,
        precedence=precedence,
        reason_codes=tuple(reason_codes),
        messages=tuple(messages),
    )


def _diagnostic(reject: RejectedCommandSpec) -> ResourceDiagnostic:
    return ResourceDiagnostic(
        kind="command_spec",
        name=reject.name,
        path=reject.path,
        severity="error",
        message="; ".join((*reject.reason_codes, *reject.messages)),
    )


def _string(payload: Mapping[str, object], key: str) -> str | None:
    value = payload.get(key)
    return value.strip() if isinstance(value, str) and value.strip() else None


def _int(payload: Mapping[str, object], key: str, *, default: int) -> int:
    value = payload.get(key)
    return value if isinstance(value, int) and not isinstance(value, bool) else default


def _dedupe_paths(paths: Sequence[Path]) -> list[Path]:
    seen: set[Path] = set()
    result: list[Path] = []
    for path in paths:
        normalized = path.expanduser()
        if normalized in seen:
            continue
        seen.add(normalized)
        result.append(normalized)
    return result


def _built_in_command_names() -> frozenset[str]:
    from tau_coding.commands import create_default_command_registry

    registry = create_default_command_registry()
    names = {command.name for command in registry.list_commands(include_hidden=True)}
    names.update(
        alias
        for command in registry.list_commands(include_hidden=True)
        for alias in command.aliases
    )
    return frozenset(names)


def _resources_digest(resources: Sequence[str]) -> str:
    payload = json.dumps(list(resources), sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(payload).hexdigest()


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _utc_stamp() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")
