"""CLI adapter for Traycer commands."""

from __future__ import annotations

from pathlib import Path

from tau_coding.traycer.models import TraycerValidationOptions
from tau_coding.traycer.validate import validate_traycer_trace


def parse_traycer_validate_cli_args(args: list[str]) -> TraycerValidationOptions:
    """Parse `tau traycer validate` positional arguments."""

    values: dict[str, str | bool] = {}
    index = 0
    while index < len(args):
        arg = args[index]
        if arg == "--advisory-final-handoff-evidence":
            values["advisory_final_handoff_evidence"] = True
            index += 1
            continue
        if arg not in {
            "--trace",
            "--handoff",
            "--active-goal-hash",
            "--receipt",
            "--required-evidence",
            "--start-handoff",
        }:
            raise RuntimeError(_usage())
        if index + 1 >= len(args):
            raise RuntimeError(_usage())
        values[arg.removeprefix("--").replace("-", "_")] = args[index + 1]
        index += 2

    missing = [
        key
        for key in ("trace", "handoff", "active_goal_hash", "receipt")
        if key not in values
    ]
    if missing:
        raise RuntimeError(f"{_usage()}\nmissing required option(s): {', '.join(missing)}")

    return TraycerValidationOptions(
        trace_path=Path(str(values["trace"])),
        handoff_path=Path(str(values["handoff"])),
        active_goal_hash=str(values["active_goal_hash"]),
        receipt_path=Path(str(values["receipt"])),
        required_evidence_path=Path(str(values["required_evidence"]))
        if "required_evidence" in values
        else None,
        start_handoff_path=Path(str(values["start_handoff"]))
        if "start_handoff" in values
        else None,
        advisory_final_handoff_evidence=bool(
            values.get("advisory_final_handoff_evidence", False)
        ),
    )


def traycer_validate_command(options: TraycerValidationOptions) -> dict[str, object]:
    """Run Traycer validation and return the receipt payload."""

    return validate_traycer_trace(options)


def _usage() -> str:
    return (
        "Usage: tau traycer validate --trace <trace.jsonl> --handoff <final-handoff.json> "
        "--active-goal-hash <sha256:...> "
        "[--required-evidence <required-evidence.json> | --start-handoff <start-handoff.json>] "
        "[--advisory-final-handoff-evidence] --receipt <monitor-receipt.json>"
    )
