"""Module entrypoint for Tau's TUI package."""

from __future__ import annotations

import sys

from tau_coding.tui.pty_proof_app import run_pty_proof_app
from tau_coding.tui.app import run_pty_proof_real_app


def main() -> None:
    """Run supported TUI module entrypoints."""

    if "--pty-proof-smoke" in sys.argv[1:]:
        run_pty_proof_app()
        return
    if "--pty-proof-real-app" in sys.argv[1:]:
        run_pty_proof_real_app()
        return
    raise SystemExit("Usage: python -m tau_coding.tui [--pty-proof-smoke|--pty-proof-real-app]")


if __name__ == "__main__":
    main()
