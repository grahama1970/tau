import hashlib
import json
import os
import stat
from pathlib import Path

import pytest

from tau_coding.browser_cdp_proof import (
    BROWSER_DAG_RECEIPT_SCHEMA,
    resolve_surf_command,
)
from tau_coding.generic_dag import run_generic_dag


def test_generic_dag_runs_browser_node_with_bound_tab_and_hash_bound_screenshot(
    tmp_path: Path,
) -> None:
    command_log = tmp_path / "surf-commands.jsonl"
    surf = _write_fake_surf(tmp_path, command_log=command_log)
    page = tmp_path / "page.html"
    page.write_text("<button id='target'>Tau</button>\n", encoding="utf-8")
    receipt_path = tmp_path / "browser-node-receipt.json"
    browser_out = tmp_path / "browser-out"
    spec_path = tmp_path / "dag.json"
    spec_path.write_text(
        json.dumps(
            {
                "schema": "tau.generic_dag_spec.v1",
                "run_id": "browser-handler-run",
                "run_dir": str(tmp_path / "run"),
                "goal_hash": "sha256:goal",
                "nodes": [
                    {
                        "node_id": "browser",
                        "role": "browser",
                        "receipt_path": str(receipt_path),
                        "browser": {
                            "schema": "tau.browser_dag_node.v1",
                            "output_dir": str(browser_out),
                            "surf_bin": str(surf),
                            "browser_oracle_binding": {
                                "project_id": "tau",
                                "tab_id": "837360873",
                                "expected_url": page.as_uri(),
                            },
                            "operations": [
                                {"verb": "navigate", "url": page.as_uri()},
                                {"verb": "read", "filter": "all"},
                                {"verb": "click", "ref": "e1"},
                                {"verb": "type", "ref": "e2", "text": "hello"},
                                {"verb": "screenshot", "artifact_id": "main"},
                            ],
                        },
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    receipt = run_generic_dag(spec_path=spec_path, resume=False)

    assert receipt["status"] == "PASS"
    node = receipt["nodes"][0]
    assert node["browser_provider"] == "surf"
    assert node["browser_live"] is True
    assert node["accepted_output"]["capability"] == "browser_handler"
    node_receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    typed_receipt = json.loads(
        Path(node_receipt["browser_receipt_path"]).read_text(encoding="utf-8")
    )
    screenshot_path = Path(typed_receipt["screenshot"]["path"])
    assert typed_receipt["schema"] == BROWSER_DAG_RECEIPT_SCHEMA
    assert typed_receipt["transport"]["tab_id"] == "837360873"
    assert typed_receipt["screenshot"]["sha256"] == (
        f"sha256:{hashlib.sha256(screenshot_path.read_bytes()).hexdigest()}"
    )
    commands = [
        json.loads(line)["args"]
        for line in command_log.read_text(encoding="utf-8").splitlines()
    ]
    assert commands[0] == ["tab.navigate", "837360873", page.as_uri()]
    assert commands[1] == ["read", "--filter", "all", "--tab-id", "837360873"]
    assert commands[2] == ["click", "e1", "--tab-id", "837360873"]
    assert commands[3] == ["type", "e2", "hello", "--tab-id", "837360873"]
    assert commands[4][:2] == ["snap", "--output"]
    assert commands[4][3:] == ["--tab-id", "837360873"]


def test_browser_node_fails_closed_when_surf_is_unavailable(tmp_path: Path) -> None:
    receipt_path = tmp_path / "browser-node-receipt.json"
    spec_path = tmp_path / "dag.json"
    spec_path.write_text(
        json.dumps(
            {
                "schema": "tau.generic_dag_spec.v1",
                "run_id": "browser-handler-unavailable",
                "run_dir": str(tmp_path / "run"),
                "nodes": [
                    {
                        "node_id": "browser",
                        "receipt_path": str(receipt_path),
                        "browser": {
                            "schema": "tau.browser_dag_node.v1",
                            "output_dir": str(tmp_path / "browser-out"),
                            "surf_bin": str(tmp_path / "missing-surf"),
                            "operations": [
                                {"verb": "navigate", "url": "http://127.0.0.1:9/"},
                                {"verb": "screenshot"},
                            ],
                        },
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    receipt = run_generic_dag(spec_path=spec_path, resume=False)

    assert receipt["status"] == "BLOCKED"
    node_receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    assert node_receipt["status"] == "BLOCKED"
    assert node_receipt["verdict"] == "BROWSER_HANDLER_BLOCKED"
    assert node_receipt["errors"] == ["surf_command_unavailable"]


def test_surf_command_resolution_prefers_override_then_path(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fallback = tmp_path / "fallback" / "run.sh"
    fallback.parent.mkdir()
    fallback.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    fallback.chmod(fallback.stat().st_mode | stat.S_IXUSR)
    path_surf = _write_fake_surf(tmp_path / "path", command_log=tmp_path / "path-log.jsonl")
    override_surf = _write_fake_surf(
        tmp_path / "override",
        command_log=tmp_path / "override-log.jsonl",
    )
    monkeypatch.setenv("PATH", str(path_surf.parent))

    assert resolve_surf_command(override_surf) == str(override_surf.resolve())
    assert resolve_surf_command(None, known_locations=(fallback,)) == str(path_surf.resolve())
    monkeypatch.setenv("PATH", os.devnull)
    assert resolve_surf_command(None, known_locations=(fallback,)) == str(fallback.resolve())


def _write_fake_surf(tmp_path: Path, *, command_log: Path) -> Path:
    tmp_path.mkdir(parents=True, exist_ok=True)
    surf = tmp_path / "surf"
    surf.write_text(
        f"""#!/usr/bin/env python3
import base64
import json
import pathlib
import sys

LOG = pathlib.Path({str(command_log)!r})
LOG.parent.mkdir(parents=True, exist_ok=True)
PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAIAAAABCAYAAAD0In+KAAAADElEQVR42mP8z8AARQAEmQH9"
    "CdhVvgAAAABJRU5ErkJggg=="
)
args = sys.argv[1:]
with LOG.open("a", encoding="utf-8") as handle:
    handle.write(json.dumps({{"args": args}}) + "\\n")
if args[:1] == ["tab.new"]:
    print(f"Created tab 2468: {{args[1]}}")
elif args[:1] == ["tab.navigate"]:
    print(f"Navigated {{args[1]}} to {{args[2]}}")
elif args[:1] == ["read"]:
    print("Tau browser handler local page")
elif args[:1] == ["click"]:
    print(f"Clicked {{args[1]}}")
elif args[:1] == ["type"]:
    print(f"Typed into {{args[1]}}")
elif args[:1] == ["snap"]:
    output = pathlib.Path(args[args.index("--output") + 1])
    output.write_bytes(PNG)
    print(f"Saved to {{output}}")
elif args[:1] == ["tab.close"]:
    print(f"Closed {{args[1]}}")
else:
    print(f"unexpected args: {{args}}", file=sys.stderr)
    raise SystemExit(2)
""",
        encoding="utf-8",
    )
    surf.chmod(surf.stat().st_mode | stat.S_IXUSR)
    return surf
