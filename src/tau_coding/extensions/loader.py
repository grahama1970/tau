"""Python extension discovery and loading for coding sessions."""

from __future__ import annotations

import sys
import types
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from importlib import invalidate_caches
from pathlib import Path

from tau_agent.tools import AgentTool
from tau_coding.extensions.api import (
    ExtensionAPI,
    ExtensionCommand,
    ExtensionEntryRenderer,
    ExtensionEventBus,
    ExtensionFlag,
    ExtensionLifecycleHandler,
    ExtensionMessageRenderer,
    ExtensionShortcut,
    ExtensionToolRenderers,
)
from tau_coding.provider_config import ProviderConfig
from tau_coding.resources import ResourceDiagnostic, TauResourcePaths

_MODULE_NAME_PREFIX = "tau_extension"
_load_counter = 0


@dataclass(frozen=True, slots=True)
class LoadedExtension:
    """A loaded extension module and its registered resources."""

    name: str
    path: Path
    tools: tuple[AgentTool, ...]
    commands: tuple[ExtensionCommand, ...] = ()
    shortcuts: tuple[ExtensionShortcut, ...] = ()
    flags: tuple[ExtensionFlag, ...] = ()
    entry_renderers: Mapping[str, ExtensionEntryRenderer] | None = None
    message_renderers: Mapping[str, ExtensionMessageRenderer] | None = None
    tool_renderers: Mapping[str, ExtensionToolRenderers] | None = None
    provider_configs: tuple[ProviderConfig, ...] = ()
    event_handlers: Mapping[str, tuple[ExtensionLifecycleHandler, ...]] | None = None


@dataclass(frozen=True, slots=True)
class ExtensionLoadResult:
    """Loaded extension tools plus non-fatal diagnostics."""

    extensions: tuple[LoadedExtension, ...]
    diagnostics: tuple[ResourceDiagnostic, ...]

    @property
    def tools(self) -> tuple[AgentTool, ...]:
        """Return all registered extension tools in load order."""
        return tuple(tool for extension in self.extensions for tool in extension.tools)

    @property
    def commands(self) -> tuple[ExtensionCommand, ...]:
        """Return all registered extension slash commands in load order."""
        return tuple(command for extension in self.extensions for command in extension.commands)

    @property
    def shortcuts(self) -> tuple[ExtensionShortcut, ...]:
        """Return all registered extension keyboard shortcuts in load order."""
        return tuple(shortcut for extension in self.extensions for shortcut in extension.shortcuts)

    @property
    def flags(self) -> tuple[ExtensionFlag, ...]:
        """Return all registered extension flags in load order."""
        return tuple(flag for extension in self.extensions for flag in extension.flags)

    @property
    def entry_renderers(self) -> Mapping[str, ExtensionEntryRenderer]:
        """Return all registered custom-entry renderers in load order."""
        renderers: dict[str, ExtensionEntryRenderer] = {}
        for extension in self.extensions:
            renderers.update(extension.entry_renderers or {})
        return renderers

    @property
    def message_renderers(self) -> Mapping[str, ExtensionMessageRenderer]:
        """Return all registered custom-message renderers in load order."""
        renderers: dict[str, ExtensionMessageRenderer] = {}
        for extension in self.extensions:
            renderers.update(extension.message_renderers or {})
        return renderers

    @property
    def tool_renderers(self) -> Mapping[str, ExtensionToolRenderers]:
        """Return all registered custom tool renderers in load order."""
        renderers: dict[str, ExtensionToolRenderers] = {}
        for extension in self.extensions:
            renderers.update(extension.tool_renderers or {})
        return renderers

    @property
    def provider_configs(self) -> tuple[ProviderConfig, ...]:
        """Return all registered provider configs in load order."""
        return tuple(
            provider
            for extension in self.extensions
            for provider in extension.provider_configs
        )


def load_extension_tools(
    paths: TauResourcePaths,
    *,
    explicit_paths: Sequence[Path] = (),
    discover_user_extensions: bool = True,
    flag_values: Mapping[str, bool | str] | None = None,
) -> ExtensionLoadResult:
    """Load Python extensions and return tools/commands they register.

    Explicit paths always load, even when `discover_user_extensions` is false.
    """
    diagnostics: list[ResourceDiagnostic] = []
    discovered: list[Path] = []
    seen_paths: set[Path] = set()

    def add(path: Path) -> None:
        resolved = path.expanduser().resolve()
        if resolved in seen_paths:
            return
        seen_paths.add(resolved)
        discovered.append(path)

    if discover_user_extensions:
        for path in _discover_extension_dir(paths.extensions_dir):
            add(path)

    for raw_path in explicit_paths:
        expanded = raw_path.expanduser()
        if expanded.is_file():
            add(expanded)
            continue
        if expanded.is_dir():
            entry = expanded / "extension.py"
            if entry.is_file():
                add(entry)
                continue
            found_any = False
            for path in _discover_extension_dir(expanded):
                found_any = True
                add(path)
            if found_any:
                continue
            diagnostics.append(
                ResourceDiagnostic(
                    kind="extension",
                    path=expanded,
                    message="no extension.py or Python extension files found",
                    severity="error",
                )
            )
            continue
        diagnostics.append(
            ResourceDiagnostic(
                kind="extension",
                path=expanded,
                message="extension path does not exist",
                severity="error",
            )
        )

    loaded: list[LoadedExtension] = []
    tool_names: set[str] = set()
    event_bus = ExtensionEventBus()
    for path in discovered:
        extension = _load_one_extension(
            path,
            diagnostics,
            flag_values=flag_values,
            event_bus=event_bus,
        )
        if extension is None:
            continue
        duplicate = next((tool.name for tool in extension.tools if tool.name in tool_names), None)
        if duplicate is not None:
            diagnostics.append(
                ResourceDiagnostic(
                    kind="extension",
                    name=extension.name,
                    path=extension.path,
                    message=f"duplicate extension tool ignored: {duplicate}",
                    severity="error",
                )
            )
            continue
        tool_names.update(tool.name for tool in extension.tools)
        loaded.append(extension)

    if flag_values:
        registered_flags = {flag.name for extension in loaded for flag in extension.flags}
        for name in sorted(set(_normalize_flag_name(name) for name in flag_values)):
            if name not in registered_flags:
                diagnostics.append(
                    ResourceDiagnostic(
                        kind="extension",
                        name=name,
                        message=f"unknown extension flag ignored: --{name}",
                        severity="error",
                    )
                )

    return ExtensionLoadResult(extensions=tuple(loaded), diagnostics=tuple(diagnostics))


def _discover_extension_dir(directory: Path) -> tuple[Path, ...]:
    if not directory.is_dir():
        return ()
    paths: list[Path] = []
    for child in sorted(directory.iterdir(), key=lambda item: item.name):
        if child.name.startswith((".", "_")):
            continue
        if child.is_file() and child.suffix == ".py":
            paths.append(child)
        elif child.is_dir() and (child / "extension.py").is_file():
            paths.append(child / "extension.py")
    return tuple(paths)


def _load_one_extension(
    path: Path,
    diagnostics: list[ResourceDiagnostic],
    *,
    flag_values: Mapping[str, bool | str] | None = None,
    event_bus: ExtensionEventBus | None = None,
) -> LoadedExtension | None:
    setup = _load_setup(path, diagnostics)
    if setup is None:
        return None
    api = ExtensionAPI(flag_values=flag_values, event_bus=event_bus)
    try:
        setup(api)
    except Exception as exc:  # noqa: BLE001 - extensions are an isolation boundary
        diagnostics.append(
            ResourceDiagnostic(
                kind="extension",
                name=path.parent.name if path.name == "extension.py" else path.stem,
                path=path,
                message=f"setup failed: {exc!r}",
                severity="error",
            )
        )
        return None
    name = path.parent.name if path.name == "extension.py" else path.stem
    return LoadedExtension(
        name=name,
        path=path,
        tools=api.tools,
        commands=api.commands,
        shortcuts=api.shortcuts,
        flags=api.flags,
        entry_renderers=api.entry_renderers,
        message_renderers=api.message_renderers,
        tool_renderers=api.tool_renderers,
        provider_configs=api.provider_configs,
        event_handlers=api.event_handlers,
    )


def _normalize_flag_name(name: str) -> str:
    return str(name).strip().removeprefix("--").lower()


def _load_setup(
    path: Path,
    diagnostics: list[ResourceDiagnostic],
) -> Callable[[ExtensionAPI], object] | None:
    global _load_counter
    _load_counter += 1
    invalidate_caches()
    module_name = f"{_MODULE_NAME_PREFIX}_{path.stem}_{_load_counter}"
    module = types.ModuleType(module_name)
    module.__file__ = str(path)
    module.__package__ = ""
    sys.modules[module_name] = module
    try:
        source = path.read_text(encoding="utf-8")
        exec(compile(source, str(path), "exec"), module.__dict__)  # noqa: S102
    except Exception as exc:  # noqa: BLE001 - extensions are an isolation boundary
        diagnostics.append(
            ResourceDiagnostic(
                kind="extension",
                path=path,
                message=f"import failed: {exc!r}",
                severity="error",
            )
        )
        return None
    setup = getattr(module, "setup", None)
    if not callable(setup):
        diagnostics.append(
            ResourceDiagnostic(
                kind="extension",
                path=path,
                message="extension must define callable setup(tau)",
                severity="error",
            )
        )
        return None
    return setup
