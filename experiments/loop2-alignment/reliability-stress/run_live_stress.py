#!/usr/bin/env python3
"""Run serial live Tau -> Loop2 reliability stress cases.

This is intentionally experiment-local. It creates fresh target repositories,
delegates each repair through Tau's Loop2 command, validates the native Loop2
artifacts, and writes a machine-readable summary.
"""

from __future__ import annotations

import argparse
import datetime as dt
import glob
import json
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

TAU_ROOT = Path(__file__).resolve().parents[3]
EXPERIMENT_ROOT = Path(__file__).resolve().parents[1]
LOOP2_SRC = Path("/home/graham/workspace/experiments/agent-skills/skills/loop2/src")
SCILLM_PROOF_ROOT = Path(
    "/home/graham/workspace/experiments/scillm/.scillm/proofs/project_agent_sanity"
)


@dataclass(frozen=True)
class StressCase:
    case_id: str
    objective: str
    files: dict[str, str]
    allowed_globs: list[str]
    checks: list[str]


CASES: list[StressCase] = [
    StressCase(
        case_id="math_add",
        objective=(
            "Fix src/target.py so add(a, b) returns the mathematical sum. "
            "Only edit src/target.py. Do not edit tests."
        ),
        files={
            "src/target.py": "def add(a, b):\n    return a - b\n",
            "tests/test_target.py": (
                "from target import add\n\n"
                "def test_add_positive_numbers():\n    assert add(2, 3) == 5\n\n"
                "def test_add_negative_number():\n    assert add(-2, 3) == 1\n"
            ),
        },
        allowed_globs=["src/target.py"],
        checks=["PYTHONPATH=src python3 -m pytest tests -q"],
    ),
    StressCase(
        case_id="string_slug",
        objective=(
            "Fix src/target.py so slugify lowercases text, trims outer whitespace, "
            "and converts internal whitespace runs to single hyphens. Only edit src/target.py."
        ),
        files={
            "src/target.py": "def slugify(text):\n    return text.replace(' ', '-')\n",
            "tests/test_target.py": (
                "from target import slugify\n\n"
                "def test_slugify_trims_and_lowers():\n"
                "    assert slugify('  Hello Tau Loop  ') == 'hello-tau-loop'\n\n"
                "def test_slugify_collapses_tabs():\n"
                "    assert slugify('Alpha\\t Beta') == 'alpha-beta'\n"
            ),
        },
        allowed_globs=["src/target.py"],
        checks=["PYTHONPATH=src python3 -m pytest tests -q"],
    ),
    StressCase(
        case_id="list_total",
        objective=(
            "Fix src/target.py so total(values) sums numeric values while ignoring None. "
            "Only edit src/target.py."
        ),
        files={
            "src/target.py": "def total(values):\n    return len(values)\n",
            "tests/test_target.py": (
                "from target import total\n\n"
                "def test_total_ignores_none():\n    assert total([1, None, 4]) == 5\n\n"
                "def test_total_empty():\n    assert total([]) == 0\n"
            ),
        },
        allowed_globs=["src/target.py"],
        checks=["PYTHONPATH=src python3 -m pytest tests -q"],
    ),
    StressCase(
        case_id="dict_get",
        objective=(
            "Fix src/target.py so pick(mapping, key, default=None) returns mapping[key] "
            "when present and default otherwise. Only edit src/target.py."
        ),
        files={
            "src/target.py": "def pick(mapping, key, default=None):\n    return default\n",
            "tests/test_target.py": (
                "from target import pick\n\n"
                "def test_pick_present_key():\n    assert pick({'a': 3}, 'a') == 3\n\n"
                "def test_pick_missing_key_default():\n    assert pick({}, 'x', 9) == 9\n"
            ),
        },
        allowed_globs=["src/target.py"],
        checks=["PYTHONPATH=src python3 -m pytest tests -q"],
    ),
    StressCase(
        case_id="bounds_clamp",
        objective=(
            "Fix src/target.py so clamp(value, low, high) limits value to the inclusive "
            "range [low, high]. Only edit src/target.py."
        ),
        files={
            "src/target.py": "def clamp(value, low, high):\n    return value\n",
            "tests/test_target.py": (
                "from target import clamp\n\n"
                "def test_clamp_inside():\n    assert clamp(5, 1, 10) == 5\n\n"
                "def test_clamp_low():\n    assert clamp(-3, 1, 10) == 1\n\n"
                "def test_clamp_high():\n    assert clamp(99, 1, 10) == 10\n"
            ),
        },
        allowed_globs=["src/target.py"],
        checks=["PYTHONPATH=src python3 -m pytest tests -q"],
    ),
    StressCase(
        case_id="csv_parse",
        objective=(
            "Fix src/target.py so parse_ints(text) returns a list of integers from a "
            "comma-separated string, skipping empty fields. Only edit src/target.py."
        ),
        files={
            "src/target.py": "def parse_ints(text):\n    return text.split(',')\n",
            "tests/test_target.py": (
                "from target import parse_ints\n\n"
                "def test_parse_ints_basic():\n    assert parse_ints('1,2,3') == [1, 2, 3]\n\n"
                "def test_parse_ints_skips_empty():\n    assert parse_ints('4,, 5') == [4, 5]\n"
            ),
        },
        allowed_globs=["src/target.py"],
        checks=["PYTHONPATH=src python3 -m pytest tests -q"],
    ),
    StressCase(
        case_id="two_file_import",
        objective=(
            "Fix src/math_ops.py so average(values) returns the arithmetic mean and raises "
            "ValueError for an empty list. Do not edit tests."
        ),
        files={
            "src/math_ops.py": "def average(values):\n    return sum(values)\n",
            "src/__init__.py": "",
            "tests/test_math_ops.py": (
                "import pytest\n\nfrom math_ops import average\n\n"
                "def test_average_values():\n    assert average([2, 4, 6]) == 4\n\n"
                "def test_average_empty_raises():\n"
                "    with pytest.raises(ValueError):\n        average([])\n"
            ),
        },
        allowed_globs=["src/math_ops.py"],
        checks=["PYTHONPATH=src python3 -m pytest tests -q"],
    ),
    StressCase(
        case_id="dataclass_status",
        objective=(
            "Fix src/target.py so summarize_task returns 'done:<title>' when completed "
            "is true and 'todo:<title>' otherwise. Only edit src/target.py."
        ),
        files={
            "src/target.py": (
                "from dataclasses import dataclass\n\n"
                "@dataclass\n"
                "class Task:\n"
                "    title: str\n"
                "    completed: bool = False\n\n"
                "def summarize_task(task):\n"
                "    return task.title\n"
            ),
            "tests/test_target.py": (
                "from target import Task, summarize_task\n\n"
                "def test_summarize_todo():\n"
                "    assert summarize_task(Task('ship')) == 'todo:ship'\n\n"
                "def test_summarize_done():\n"
                "    assert summarize_task(Task('ship', completed=True)) == 'done:ship'\n"
            ),
        },
        allowed_globs=["src/target.py"],
        checks=["PYTHONPATH=src python3 -m pytest tests -q"],
    ),
    StressCase(
        case_id="path_ext",
        objective=(
            "Fix src/target.py so extension(path) returns the lowercase file extension "
            "without the dot, or an empty string when no extension exists. Only edit src/target.py."
        ),
        files={
            "src/target.py": "def extension(path):\n    return path\n",
            "tests/test_target.py": (
                "from target import extension\n\n"
                "def test_extension_lowercase():\n"
                "    assert extension('/tmp/Report.PDF') == 'pdf'\n\n"
                "def test_extension_missing():\n    assert extension('README') == ''\n"
            ),
        },
        allowed_globs=["src/target.py"],
        checks=["PYTHONPATH=src python3 -m pytest tests -q"],
    ),
    StressCase(
        case_id="merge_defaults",
        objective=(
            "Fix src/target.py so merge_defaults(defaults, overrides) returns a new dict "
            "where override keys replace defaults without mutating either input. "
            "Only edit src/target.py."
        ),
        files={
            "src/target.py": (
                "def merge_defaults(defaults, overrides):\n"
                "    defaults.update(overrides)\n"
                "    return defaults\n"
            ),
            "tests/test_target.py": (
                "from target import merge_defaults\n\n"
                "def test_merge_defaults_override():\n"
                "    assert merge_defaults({'a': 1}, {'a': 2, 'b': 3}) == {'a': 2, 'b': 3}\n\n"
                "def test_merge_defaults_no_mutation():\n"
                "    defaults = {'a': 1}\n"
                "    result = merge_defaults(defaults, {'b': 2})\n"
                "    assert defaults == {'a': 1}\n"
                "    assert result == {'a': 1, 'b': 2}\n"
            ),
        },
        allowed_globs=["src/target.py"],
        checks=["PYTHONPATH=src python3 -m pytest tests -q"],
    ),
    StressCase(
        case_id="bool_parse",
        objective=(
            "Fix src/target.py so parse_bool accepts yes/true/1 as True, no/false/0 as "
            "False, case-insensitively, and raises ValueError otherwise. Only edit src/target.py."
        ),
        files={
            "src/target.py": "def parse_bool(text):\n    return bool(text)\n",
            "tests/test_target.py": (
                "import pytest\n\nfrom target import parse_bool\n\n"
                "def test_parse_true_values():\n    assert parse_bool('YES') is True\n\n"
                "def test_parse_false_values():\n    assert parse_bool('0') is False\n\n"
                "def test_parse_invalid():\n"
                "    with pytest.raises(ValueError):\n        parse_bool('maybe')\n"
            ),
        },
        allowed_globs=["src/target.py"],
        checks=["PYTHONPATH=src python3 -m pytest tests -q"],
    ),
    StressCase(
        case_id="nested_lookup",
        objective=(
            "Fix src/target.py so get_nested(mapping, keys, default=None) walks a sequence "
            "of keys through nested dictionaries and returns default when any key is missing. "
            "Only edit src/target.py."
        ),
        files={
            "src/target.py": "def get_nested(mapping, keys, default=None):\n    return mapping\n",
            "tests/test_target.py": (
                "from target import get_nested\n\n"
                "def test_get_nested_present():\n"
                "    assert get_nested({'a': {'b': 7}}, ['a', 'b']) == 7\n\n"
                "def test_get_nested_missing_default():\n"
                "    assert get_nested({'a': {}}, ['a', 'b'], default='x') == 'x'\n"
            ),
        },
        allowed_globs=["src/target.py"],
        checks=["PYTHONPATH=src python3 -m pytest tests -q"],
    ),
]


def utc_stamp() -> str:
    return dt.datetime.now(dt.UTC).strftime("%Y%m%dT%H%M%SZ")


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text())


def latest_pass_doctor_receipt() -> Path:
    candidates = sorted(glob.glob(str(SCILLM_PROOF_ROOT / "*" / "receipt.json")))
    for candidate in reversed(candidates):
        path = Path(candidate)
        try:
            receipt = read_json(path)
        except (json.JSONDecodeError, OSError):
            continue
        if (
            receipt.get("schema") == "scillm.project_agent_sanity.v1"
            and receipt.get("status") == "PASS"
        ):
            return path
    raise RuntimeError(f"No PASS Scillm doctor receipt found under {SCILLM_PROOF_ROOT}")


def scillm_key_from_docker() -> str:
    process = subprocess.run(
        [
            "docker",
            "inspect",
            "e5f5ec4d2078",
            "--format",
            "{{range .Config.Env}}{{println .}}{{end}}",
        ],
        cwd=TAU_ROOT,
        check=False,
        text=True,
        capture_output=True,
    )
    if process.returncode != 0:
        return ""
    for line in process.stdout.splitlines():
        name, _, value = line.partition("=")
        if name == "SCILLM_MASTER_KEY" and value:
            return value
    return ""


def resolve_scillm_key() -> tuple[str, str]:
    env_key = os.environ.get("SCILLM_API_KEY")
    if env_key:
        return env_key, "env:SCILLM_API_KEY"
    docker_key = scillm_key_from_docker()
    if docker_key:
        return docker_key, "docker:e5f5ec4d2078:SCILLM_MASTER_KEY"
    raise RuntimeError("SCILLM_API_KEY is unset and local Scillm proxy key was not found")


def write_case_repo(case: StressCase, repo: Path) -> None:
    if repo.exists():
        shutil.rmtree(repo)
    for rel_path, content in case.files.items():
        path = repo / rel_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)


def write_contract(case: StressCase, case_dir: Path, repo: Path) -> Path:
    contract = {
        "schema": "loop2.repair_node_contract.v1",
        "node_id": f"tau-stress-{case.case_id}",
        "objective": case.objective,
        "repo": str(repo),
        "allowed_globs": case.allowed_globs,
        "checks": case.checks,
        "max_attempts": 1,
        "backend": "scillm",
        "run_root": str(case_dir / ".loop2" / "runs"),
        "scillm": {
            "base_url": "http://127.0.0.1:4001",
            "api_key": "redacted-placeholder",
            "agent_id": "",
            "agent": "build",
            "mode": "workspace_write",
            "model": "opencode-go/kimi-k2.6",
            "timeout_s": 900,
        },
    }
    path = case_dir / "contract.json"
    path.write_text(json.dumps(contract, indent=2, sort_keys=True) + "\n")
    return path


def run_command(
    argv: list[str],
    *,
    env: dict[str, str],
    cwd: Path,
    stdout_path: Path,
    stderr_path: Path,
    timeout_s: int,
) -> subprocess.CompletedProcess[str]:
    process = subprocess.run(
        argv,
        cwd=cwd,
        env=env,
        check=False,
        text=True,
        capture_output=True,
        timeout=timeout_s,
    )
    stdout_path.write_text(process.stdout)
    stderr_path.write_text(process.stderr)
    return process


def extract_json_object(text: str) -> dict[str, Any]:
    start = text.find("{")
    end = text.rfind("}")
    if start < 0 or end < start:
        return {"parse_error": "no JSON object found"}
    try:
        parsed = json.loads(text[start : end + 1])
    except json.JSONDecodeError as exc:
        return {"parse_error": str(exc)}
    if isinstance(parsed, dict):
        return parsed
    return {"parse_error": "top-level JSON was not an object"}


def run_case(
    case: StressCase,
    *,
    run_root: Path,
    env: dict[str, str],
    doctor_receipt: Path,
    timeout_s: int,
) -> dict[str, Any]:
    case_dir = run_root / case.case_id
    case_dir.mkdir(parents=True, exist_ok=True)
    repo = case_dir / "repo"
    write_case_repo(case, repo)
    contract_path = write_contract(case, case_dir, repo)

    tau_stdout = case_dir / "tau-loop2-run.stdout.json"
    tau_stderr = case_dir / "tau-loop2-run.stderr.txt"
    tau_argv = [
        "uv",
        "run",
        "tau",
        "--loop2-src",
        str(LOOP2_SRC),
        "--loop2-scillm-doctor-receipt",
        str(doctor_receipt),
        "--provider",
        "chutes",
        "loop2-run",
        str(contract_path),
    ]
    tau_process = run_command(
        tau_argv,
        env=env,
        cwd=TAU_ROOT,
        stdout_path=tau_stdout,
        stderr_path=tau_stderr,
        timeout_s=timeout_s,
    )
    tau_payload = extract_json_object(tau_stdout.read_text())
    run_dir = Path(str(tau_payload.get("run_dir") or "")) if tau_payload.get("run_dir") else None

    native_payload: dict[str, Any] = {"ran": False, "ok": None, "errors": ["no run_dir"]}
    inspect_payload: dict[str, Any] = {"ran": False, "ok": None, "errors": ["no run_dir"]}
    if run_dir is not None and run_dir.exists():
        native_stdout = case_dir / "tau-loop2-validate-native.stdout.json"
        native_stderr = case_dir / "tau-loop2-validate-native.stderr.txt"
        native_process = run_command(
            [
                "uv",
                "run",
                "tau",
                "--loop2-src",
                str(LOOP2_SRC),
                "loop2-validate-native",
                str(run_dir),
            ],
            env=env,
            cwd=TAU_ROOT,
            stdout_path=native_stdout,
            stderr_path=native_stderr,
            timeout_s=120,
        )
        native_payload = extract_json_object(native_stdout.read_text())
        native_payload["returncode"] = native_process.returncode

        inspect_stdout = case_dir / "tau-loop2-inspect.stdout.json"
        inspect_stderr = case_dir / "tau-loop2-inspect.stderr.txt"
        inspect_process = run_command(
            [
                "uv",
                "run",
                "tau",
                "--loop2-src",
                str(LOOP2_SRC),
                "--loop2-inspect-validate",
                "loop2-inspect",
                str(run_dir),
            ],
            env=env,
            cwd=TAU_ROOT,
            stdout_path=inspect_stdout,
            stderr_path=inspect_stderr,
            timeout_s=120,
        )
        inspect_payload = extract_json_object(inspect_stdout.read_text())
        inspect_payload["returncode"] = inspect_process.returncode

    ok = (
        tau_process.returncode == 0
        and tau_payload.get("ok") is True
        and tau_payload.get("mocked") is False
        and tau_payload.get("live") is True
        and native_payload.get("ok") is True
    )
    return {
        "case_id": case.case_id,
        "ok": ok,
        "mocked": False,
        "live": True,
        "case_dir": str(case_dir),
        "repo": str(repo),
        "contract": str(contract_path),
        "tau_returncode": tau_process.returncode,
        "tau_stdout": str(tau_stdout),
        "tau_stderr": str(tau_stderr),
        "run_dir": str(run_dir) if run_dir is not None else "",
        "tau_payload": tau_payload,
        "native_validation": native_payload,
        "inspect": inspect_payload,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--cases", type=int, default=12, choices=range(1, len(CASES) + 1))
    parser.add_argument("--run-root", type=Path, default=None)
    parser.add_argument("--doctor-receipt", type=Path, default=None)
    parser.add_argument("--timeout-s", type=int, default=1200)
    parser.add_argument("--continue-on-failure", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    run_root = args.run_root or (EXPERIMENT_ROOT / f"reliability-stress-{utc_stamp()}")
    run_root.mkdir(parents=True, exist_ok=False)
    summary_path = run_root / "stress-summary.json"
    key, key_source = resolve_scillm_key()
    doctor_receipt = (args.doctor_receipt or latest_pass_doctor_receipt()).expanduser().resolve()
    env = dict(os.environ)
    env["SCILLM_API_KEY"] = key

    results: list[dict[str, Any]] = []
    for case in CASES[: args.cases]:
        result = run_case(
            case,
            run_root=run_root,
            env=env,
            doctor_receipt=doctor_receipt,
            timeout_s=args.timeout_s,
        )
        results.append(result)
        partial = {
            "schema": "tau.loop2_reliability_stress.v1",
            "status": "RUNNING",
            "run_root": str(run_root),
            "doctor_receipt": str(doctor_receipt),
            "scillm_key_source": key_source,
            "mocked": False,
            "live": True,
            "requested_cases": args.cases,
            "completed_cases": len(results),
            "passed_cases": sum(1 for item in results if item["ok"]),
            "failed_cases": sum(1 for item in results if not item["ok"]),
            "cases": results,
        }
        summary_path.write_text(json.dumps(partial, indent=2, sort_keys=True) + "\n")
        print(
            json.dumps(
                {
                    "case_id": case.case_id,
                    "ok": result["ok"],
                    "case_dir": result["case_dir"],
                }
            )
        )
        sys.stdout.flush()
        if not result["ok"] and not args.continue_on_failure:
            break

    failed = [item for item in results if not item["ok"]]
    summary = {
        "schema": "tau.loop2_reliability_stress.v1",
        "status": "PASS" if len(results) == args.cases and not failed else "FAIL",
        "run_root": str(run_root),
        "summary_path": str(summary_path),
        "doctor_receipt": str(doctor_receipt),
        "scillm_key_source": key_source,
        "mocked": False,
        "live": True,
        "requested_cases": args.cases,
        "completed_cases": len(results),
        "passed_cases": sum(1 for item in results if item["ok"]),
        "failed_cases": len(failed),
        "stop_reason": "" if not failed else f"case_failed:{failed[0]['case_id']}",
        "cases": results,
    }
    summary_path.write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n")
    print(json.dumps({"summary_path": str(summary_path), "status": summary["status"]}, indent=2))
    return 0 if summary["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
