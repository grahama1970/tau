"""Browser/CDP proof helpers for Tau UI proof lanes."""

from __future__ import annotations

import json
import os
import re
import struct
import subprocess
import time
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from shutil import which
from typing import Any

BROWSER_CDP_PROOF_SCHEMA = "tau.browser_cdp_proof.v1"
BROWSER_DAG_NODE_SCHEMA = "tau.browser_dag_node.v1"
BROWSER_DAG_RECEIPT_SCHEMA = "tau.browser_dag_receipt.v1"
GENERIC_DAG_NODE_RECEIPT_SCHEMA = "tau.generic_dag_node_receipt.v1"
DEFAULT_BROWSER_PROOF_RUN_ID = "tau-browser-cdp-proof"
DEFAULT_SURF_WRAPPER = (
    Path.home() / "workspace/experiments/agent-skills/skills/surf/run.sh"
)


@dataclass(frozen=True)
class BrowserDagSpec:
    node_id: str
    output_dir: Path
    operations: tuple[dict[str, Any], ...]
    surf_bin: str | None
    browser_oracle_binding: dict[str, Any] | None
    keep_tab: bool
    command_timeout_seconds: float


def write_browser_cdp_proof(
    *,
    output_dir: Path,
    run_id: str = DEFAULT_BROWSER_PROOF_RUN_ID,
    surf_bin: Path | str | None = None,
    keep_tab: bool = False,
) -> dict[str, Any]:
    """Render a local Tau proof page through Surf and write screenshot + receipt."""

    resolved_output = output_dir.expanduser().resolve()
    resolved_output.mkdir(parents=True, exist_ok=True)
    html_path = resolved_output / "tau-browser-cdp-proof.html"
    screenshot_path = resolved_output / "tau-browser-cdp-proof.png"
    receipt_path = resolved_output / "browser-cdp-proof-receipt.json"
    surf_command = resolve_surf_command(surf_bin)
    tab_id: str | None = None
    command_results: list[dict[str, Any]] = []

    html_path.write_text(_proof_html(run_id=run_id), encoding="utf-8")
    url = html_path.as_uri()
    errors: list[str] = []
    surf_available = surf_command is not None
    read_text = ""

    if surf_command is None:
        errors.append("Surf wrapper or surf executable was not found.")
    else:
        tab_result = _run_surf(surf_command, ["tab.new", url])
        command_results.append(tab_result)
        if tab_result["exit_code"] != 0:
            errors.append("surf tab.new failed")
        else:
            tab_id = _parse_tab_id(str(tab_result["stdout"]))
            read_result = _run_surf(surf_command, ["read", "--filter", "all"])
            command_results.append(read_result)
            read_text = str(read_result["stdout"])
            if read_result["exit_code"] != 0:
                errors.append("surf read failed")
            snap_result = _run_surf(
                surf_command,
                ["snap", "--output", str(screenshot_path)],
            )
            command_results.append(snap_result)
            if snap_result["exit_code"] != 0:
                errors.append("surf snap failed")
            if tab_id and not keep_tab:
                close_result = _run_surf(surf_command, ["tab.close", tab_id])
                command_results.append(close_result)

    png_size = _png_size(screenshot_path)
    visible_assertions = {
        "page_text_contains_title": "Tau Browser/CDP Proof" in read_text,
        "page_text_contains_handoff_schema": "tau.agent_handoff.v1" in read_text,
        "page_text_contains_receipt_schema": BROWSER_CDP_PROOF_SCHEMA in read_text,
        "screenshot_exists": screenshot_path.exists(),
        "screenshot_nonempty": screenshot_path.exists() and screenshot_path.stat().st_size > 0,
        "screenshot_png_dimensions": bool(png_size),
    }
    ok = surf_available and not errors and all(visible_assertions.values())
    status = "PASS" if ok else "BLOCKED"
    if not surf_available:
        verdict = "SURF_UNAVAILABLE"
    elif errors:
        verdict = "SURF_BROWSER_PROOF_FAILED"
    elif not all(visible_assertions.values()):
        verdict = "VISIBLE_ASSERTION_FAILED"
    else:
        verdict = "PASS"

    receipt: dict[str, Any] = {
        "schema": BROWSER_CDP_PROOF_SCHEMA,
        "status": status,
        "ok": ok,
        "verdict": verdict,
        "mocked": False,
        "live": bool(surf_available),
        "provider_live": False,
        "run_id": run_id,
        "timestamp": datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "surface": "local Tau browser proof page",
        "transport": {
            "kind": "surf",
            "command": str(surf_command) if surf_command else None,
            "tab_id": tab_id,
            "url": url,
            "keep_tab": keep_tab,
        },
        "artifacts": {
            "html": str(html_path),
            "screenshot_png": str(screenshot_path),
            "receipt": str(receipt_path),
        },
        "screenshot": {
            "path": str(screenshot_path),
            "sha256": _safe_file_sha256(screenshot_path),
            "size_bytes": screenshot_path.stat().st_size if screenshot_path.exists() else 0,
            "width": png_size[0] if png_size else None,
            "height": png_size[1] if png_size else None,
        },
        "visible_assertions": visible_assertions,
        "errors": errors,
        "commands": command_results,
        "proof_scope": {
            "proves": [
                "Surf browser transport opened a local Tau proof page.",
                "Surf read observed required Tau proof text from the rendered page.",
                "Surf screenshot wrote a non-empty PNG artifact.",
                "No provider, GitHub, Memory, or DAG route mutation was performed.",
            ],
            "does_not_prove": [
                "Production chat UX acceptance.",
                "Live Memory backend behavior.",
                "Live provider/model semantic quality.",
                "GitHub mutation.",
                "Arbitrary browser UI correctness beyond this proof page.",
            ],
        },
    }
    receipt_path.write_text(json.dumps(receipt, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return receipt


def parse_browser_dag_spec(raw: Any, *, base_dir: Path, node_id: str) -> BrowserDagSpec:
    if not isinstance(raw, dict):
        raise RuntimeError(f"node {node_id} browser must be an object")
    if raw.get("schema") != BROWSER_DAG_NODE_SCHEMA:
        raise RuntimeError(f"node {node_id} browser schema must be {BROWSER_DAG_NODE_SCHEMA}")
    output_dir = _browser_resolve_path(_browser_required_string(raw, "output_dir"), base_dir)
    operations = raw.get("operations")
    if not isinstance(operations, list) or not operations:
        raise RuntimeError(f"node {node_id} browser operations must be a non-empty list")
    parsed_operations: list[dict[str, Any]] = []
    for index, operation in enumerate(operations):
        if not isinstance(operation, dict):
            raise RuntimeError(f"node {node_id} browser operation {index} must be an object")
        verb = operation.get("verb")
        if verb not in {"navigate", "read", "click", "type", "screenshot"}:
            raise RuntimeError(
                f"node {node_id} browser operation {index} verb must be one of "
                "navigate, read, click, type, screenshot"
            )
        if verb == "navigate":
            _browser_required_string(operation, "url")
        if verb in {"click", "type"}:
            _browser_required_string(operation, "ref")
        if verb == "type":
            _browser_required_string(operation, "text")
        parsed_operations.append(dict(operation))
    binding = raw.get("browser_oracle_binding")
    if binding is not None and not isinstance(binding, dict):
        raise RuntimeError(f"node {node_id} browser_oracle_binding must be an object")
    surf_bin = raw.get("surf_bin")
    if surf_bin is not None and not isinstance(surf_bin, str):
        raise RuntimeError(f"node {node_id} surf_bin must be a string when present")
    command_timeout_seconds = float(raw.get("command_timeout_seconds", 30))
    if command_timeout_seconds <= 0:
        raise RuntimeError(f"node {node_id} command_timeout_seconds must be positive")
    return BrowserDagSpec(
        node_id=node_id,
        output_dir=output_dir,
        operations=tuple(parsed_operations),
        surf_bin=surf_bin,
        browser_oracle_binding=binding,
        keep_tab=bool(raw.get("keep_tab", False)),
        command_timeout_seconds=command_timeout_seconds,
    )


def execute_browser_dag_node(
    *,
    spec: BrowserDagSpec,
    run_id: str,
    node_id: str,
    goal_hash: str | None,
    work_order_sha256: str | None,
) -> dict[str, Any]:
    resolved_output = spec.output_dir.expanduser().resolve()
    resolved_output.mkdir(parents=True, exist_ok=True)
    typed_receipt_path = resolved_output / "browser-receipt.json"
    surf_command = resolve_surf_command(spec.surf_bin)
    started_at = datetime.now(UTC)
    command_results: list[dict[str, Any]] = []
    operation_records: list[dict[str, Any]] = []
    errors: list[str] = []
    created_tab_id: str | None = None
    tab_id = _binding_tab_id(spec.browser_oracle_binding)
    screenshot_path: Path | None = None
    screenshot_sha256: str | None = None
    screenshot_size: tuple[int, int] | None = None

    if surf_command is None:
        errors.append("surf_command_unavailable")
    else:
        for index, operation in enumerate(spec.operations, start=1):
            command_args, artifact_path = _browser_operation_command(
                operation,
                tab_id=tab_id,
                output_dir=resolved_output,
                index=index,
            )
            started = time.monotonic()
            result = _run_surf_with_timeout(
                surf_command,
                command_args,
                timeout_seconds=spec.command_timeout_seconds,
            )
            result["elapsed_seconds"] = round(time.monotonic() - started, 3)
            command_results.append(result)
            if operation["verb"] == "navigate" and tab_id is None and result["exit_code"] == 0:
                created_tab_id = _parse_tab_id(str(result.get("stdout") or ""))
                tab_id = created_tab_id
            if artifact_path is not None:
                screenshot_path = artifact_path
            operation_records.append(
                {
                    "index": index,
                    "verb": operation["verb"],
                    "command": result["command"],
                    "exit_code": result["exit_code"],
                    "artifact_path": str(artifact_path) if artifact_path else None,
                }
            )
            if result["exit_code"] != 0:
                errors.append(f"browser_operation_failed:{operation['verb']}")
                break
        if created_tab_id and not spec.keep_tab:
            close_result = _run_surf_with_timeout(
                surf_command,
                ["tab.close", created_tab_id],
                timeout_seconds=spec.command_timeout_seconds,
            )
            command_results.append(close_result)

    if screenshot_path is not None:
        screenshot_sha256 = _safe_file_sha256(screenshot_path)
        screenshot_size = _png_size(screenshot_path)
        if screenshot_sha256 is None:
            errors.append(f"screenshot_missing:{screenshot_path}")
        elif screenshot_size is None:
            errors.append(f"screenshot_not_png:{screenshot_path}")

    status = "PASS" if not errors else "BLOCKED"
    verdict = "PASS" if not errors else "BROWSER_HANDLER_BLOCKED"
    finished_at = datetime.now(UTC)
    typed_receipt = {
        "schema": BROWSER_DAG_RECEIPT_SCHEMA,
        "status": status,
        "verdict": verdict,
        "mocked": False,
        "live": surf_command is not None,
        "provider_live": False,
        "run_id": run_id,
        "node_id": node_id,
        "goal_hash": goal_hash,
        "work_order_sha256": work_order_sha256,
        "timestamp": _iso_stamp(finished_at),
        "transport": {
            "kind": "surf",
            "command": surf_command,
            "browser_oracle_binding": spec.browser_oracle_binding,
            "tab_id": tab_id,
            "created_tab_id": created_tab_id,
            "keep_tab": spec.keep_tab,
        },
        "operations": operation_records,
        "screenshot": {
            "path": str(screenshot_path) if screenshot_path else None,
            "sha256": screenshot_sha256,
            "size_bytes": screenshot_path.stat().st_size
            if screenshot_path is not None and screenshot_path.exists()
            else 0,
            "width": screenshot_size[0] if screenshot_size else None,
            "height": screenshot_size[1] if screenshot_size else None,
        },
        "commands": command_results,
        "errors": errors,
        "proof_scope": {
            "proves": [
                "Tau generic DAG can route a browser node through Surf.",
                "Browser operations are recorded with command-level receipts.",
                "Screenshot artifacts are hash-bound when a screenshot operation succeeds.",
            ],
            "does_not_prove": [
                "Remote browser availability outside this Surf command boundary.",
                "Provider/model semantic quality.",
                "Arbitrary UI correctness beyond requested browser operations.",
            ],
        },
    }
    typed_receipt_path.write_text(
        json.dumps(typed_receipt, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    artifacts = [
        _browser_artifact(
            typed_receipt_path,
            artifact_id="browser_receipt",
            schema=BROWSER_DAG_RECEIPT_SCHEMA,
        )
    ]
    if screenshot_path is not None:
        artifacts.append(
            _browser_artifact(
                screenshot_path,
                artifact_id="screenshot",
                schema="image/png",
            )
        )
    duration_seconds = (finished_at - started_at).total_seconds()
    return {
        "schema": GENERIC_DAG_NODE_RECEIPT_SCHEMA,
        "status": status,
        "verdict": verdict,
        "mocked": False,
        "live": surf_command is not None,
        "provider_live": False,
        "browser_provider": "surf",
        "capability": "browser_handler",
        "node_id": node_id,
        "run_id": run_id,
        "goal_hash": goal_hash,
        "work_order_sha256": work_order_sha256,
        "started_at": _iso_stamp(started_at),
        "finished_at": _iso_stamp(finished_at),
        "duration_seconds": round(duration_seconds, 3),
        "artifacts": artifacts,
        "commands_run": [result["command"] for result in command_results],
        "errors": errors,
        "policy_exceptions": [],
        "handoff_summary": (
            "Browser DAG node routed through Surf"
            if status == "PASS"
            else "Browser DAG node blocked before admissible browser proof"
        ),
        "browser_receipt_path": str(typed_receipt_path),
        "browser_receipt_schema": BROWSER_DAG_RECEIPT_SCHEMA,
    }


def resolve_surf_command(
    surf_bin: Path | str | None,
    *,
    known_locations: Iterable[Path] | None = None,
) -> str | None:
    if surf_bin is not None:
        candidate = Path(surf_bin).expanduser()
        if candidate.exists():
            return str(candidate.resolve())
        resolved = which(str(surf_bin))
        return resolved
    resolved = which("surf")
    if resolved:
        return resolved
    for candidate in known_locations or _known_surf_wrappers():
        if candidate.exists():
            return str(candidate.expanduser().resolve())
    return None


def _resolve_surf_command(surf_bin: Path | str | None) -> str | None:
    return resolve_surf_command(surf_bin)


def _run_surf(command: str, args: list[str]) -> dict[str, Any]:
    return _run_surf_with_timeout(command, args, timeout_seconds=30)


def _run_surf_with_timeout(
    command: str,
    args: list[str],
    *,
    timeout_seconds: float,
) -> dict[str, Any]:
    result = subprocess.run(
        [command, *args],
        text=True,
        capture_output=True,
        check=False,
        timeout=timeout_seconds,
    )
    return {
        "command": [command, *args],
        "exit_code": result.returncode,
        "stdout": result.stdout,
        "stderr": result.stderr,
    }


def _known_surf_wrappers() -> tuple[Path, ...]:
    candidates: list[Path] = []
    env_wrapper = os.environ.get("TAU_SURF_WRAPPER")
    if env_wrapper:
        candidates.append(Path(env_wrapper).expanduser())
    skills_root = os.environ.get("TAU_SKILLS_ROOT")
    if skills_root:
        candidates.append(Path(skills_root).expanduser() / "surf" / "run.sh")
    candidates.append(DEFAULT_SURF_WRAPPER)
    return tuple(candidates)


def _browser_operation_command(
    operation: dict[str, Any],
    *,
    tab_id: str | None,
    output_dir: Path,
    index: int,
) -> tuple[list[str], Path | None]:
    verb = str(operation["verb"])
    tab_args = ["--tab-id", tab_id] if tab_id else []
    if verb == "navigate":
        url = str(operation["url"])
        if tab_id:
            return ["tab.navigate", tab_id, url], None
        return ["tab.new", url], None
    if verb == "read":
        read_filter = str(operation.get("filter") or "all")
        return ["read", "--filter", read_filter, *tab_args], None
    if verb == "click":
        return ["click", str(operation["ref"]), *tab_args], None
    if verb == "type":
        return ["type", str(operation["ref"]), str(operation["text"]), *tab_args], None
    artifact_id = str(operation.get("artifact_id") or f"screenshot-{index:03d}")
    screenshot_path = output_dir / f"{artifact_id}.png"
    return ["snap", "--output", str(screenshot_path), *tab_args], screenshot_path


def _parse_tab_id(stdout: str) -> str | None:
    match = re.search(r"Created tab\s+(\d+):", stdout)
    return match.group(1) if match else None


def _binding_tab_id(binding: dict[str, Any] | None) -> str | None:
    if not isinstance(binding, dict):
        return None
    tab_id = binding.get("tab_id")
    if isinstance(tab_id, str) and tab_id.strip():
        return tab_id.strip()
    return None


def _proof_html(*, run_id: str) -> str:
    payload = {
        "schema": BROWSER_CDP_PROOF_SCHEMA,
        "run_id": run_id,
        "handoff_schema": "tau.agent_handoff.v1",
        "next_agent": "reviewer",
    }
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>Tau Browser/CDP Proof</title>
  <style>
    body {{
      margin: 0;
      background: #101417;
      color: #f2f5f4;
      font-family: system-ui, sans-serif;
    }}
    main {{
      max-width: 860px;
      margin: 64px auto;
      padding: 32px;
      border: 1px solid #40515a;
      background: #182023;
    }}
    code, pre {{
      color: #55e6c1;
    }}
  </style>
</head>
<body>
  <main id="tau-browser-proof" data-schema="{BROWSER_CDP_PROOF_SCHEMA}">
    <h1>Tau Browser/CDP Proof</h1>
    <p>Rendered by Surf browser transport for Tau proof boundary inspection.</p>
    <p>Required handoff schema: <code>tau.agent_handoff.v1</code></p>
    <p>Receipt schema: <code>{BROWSER_CDP_PROOF_SCHEMA}</code></p>
    <pre id="proof-json">{json.dumps(payload, indent=2, sort_keys=True)}</pre>
  </main>
</body>
</html>
"""


def _png_size(path: Path) -> tuple[int, int] | None:
    if not path.exists():
        return None
    with path.open("rb") as handle:
        header = handle.read(24)
    if len(header) < 24 or header[:8] != b"\x89PNG\r\n\x1a\n":
        return None
    return struct.unpack(">II", header[16:24])


def _safe_file_sha256(path: Path) -> str | None:
    if not path.exists():
        return None
    import hashlib

    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            digest.update(chunk)
    return f"sha256:{digest.hexdigest()}"


def _browser_artifact(path: Path, *, artifact_id: str, schema: str) -> dict[str, Any]:
    sha256 = _safe_file_sha256(path)
    return {
        "artifact_id": artifact_id,
        "schema": schema,
        "path": str(path),
        "sha256": sha256.removeprefix("sha256:") if isinstance(sha256, str) else None,
        "size_bytes": path.stat().st_size if path.exists() else 0,
    }


def _browser_required_string(payload: dict[str, Any], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value.strip():
        raise RuntimeError(f"{key} must be a non-empty string")
    return value


def _browser_resolve_path(value: str, base_dir: Path) -> Path:
    path = Path(value).expanduser()
    if not path.is_absolute():
        path = base_dir / path
    return path.resolve()


def _iso_stamp(value: datetime) -> str:
    return value.replace(microsecond=0).isoformat().replace("+00:00", "Z")
