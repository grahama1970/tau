from __future__ import annotations

import runpy
import subprocess
from pathlib import Path


def test_wrong_session_probe_is_bounded_and_timeout_is_not_a_block_proof(
    monkeypatch,
) -> None:
    script = (
        Path(__file__).resolve().parents[1] / "scripts" / "run-herdr-runtime-smoke.py"
    )
    namespace = runpy.run_path(str(script))
    observed: dict[str, float] = {}

    def timeout_run(*_args, **kwargs):
        observed["timeout"] = kwargs["timeout"]
        raise subprocess.TimeoutExpired(cmd="herdr", timeout=kwargs["timeout"])

    monkeypatch.setattr(subprocess, "run", timeout_run)

    result = namespace["_probe_wrong_session"](
        herdr_bin="herdr",
        session="wrong-session",
        pane_id="w1:p1",
        timeout_seconds=0.25,
    )

    assert observed["timeout"] == 0.25
    assert result["returncode"] == 124
    assert result["timed_out"] is True
    assert result["blocked"] is False
