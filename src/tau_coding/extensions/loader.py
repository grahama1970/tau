"""Python extension discovery and loading for coding sessions."""

from __future__ import annotations

import sys
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

from tau_agent.tools import AgentTool
from tau_coding.extensions.api import ExtensionAPI, ExtensionCommand
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


def load_extension_tools(
    paths: TauResourcePaths,
    *,
    explicit_paths: Sequence[Path] = (),
    discover_user_extensions: bool = True,
) -> ExtensionLoadResult:
    """Load Python extensions and return tools/commands they register.

    This remains a bounded Pi-compatible extension slice. It supports tool
    registration and synchronous slash-command registration; UI hooks, lifecycle
    hooks, and project-local automatic extension execution are left out until
    their full contracts exist. Explicit paths always load, even when
    `discover_user_extensions` is false.
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
    for path in discovered:
        extension = _load_one_extension(path, diagnostics)
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
) -> LoadedExtension | None:
    setup = _load_setup(path, diagnostics)
    if setup is None:
        return None
    api = ExtensionAPI()
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
    return LoadedExtension(name=name, path=path, tools=api.tools, commands=api.commands)


def _load_setup(
    path: Path,
    diagnostics: list[ResourceDiagnostic],
) -> Callable[[ExtensionAPI], object] | None:
    global _load_counter
    _load_counter += 1
    module_name = f"{_MODULE_NAME_PREFIX}_{path.stem}_{_load_counter}"
    spec = spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        diagnostics.append(
            ResourceDiagnostic(
                kind="extension",
                path=path,
                message="could not create import spec",
                severity="error",
            )
        )
        return None
    module = module_from_spec(spec)
    sys.modules[module_name] = module
    try:
        spec.loader.exec_module(module)
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
