# Project Watchdog Live Dispatch Probe

Ticket: `grahama1970/tau#181`

The project-watchdog live probe was rerun against a clean Tau `main` worktree
using a temporary watchdog registry that pointed the Tau project entry at
`/tmp/tau-autocomplete-20260728`.

The accepted proof artifact is:

```text
docs/proofs/tickets/issue-181-watchdog-live-dispatch-20260728T233923Z/project-watchdog-receipt.json
```

Readback:

```json
{
  "status": "NEEDS_ATTENTION",
  "ok": false,
  "handled_count": 1,
  "handled_issues": [
    {
      "issue_number": 181,
      "action": "ticket_repair",
      "status": "NEEDS_ATTENTION",
      "summary": "$ask tau-dag failed for grahama1970/tau#181"
    }
  ]
}
```

The probe is intentionally not a successful repair claim. It proves the watchdog
no longer idles with an empty handled list for this ticket: it selected #181,
posted the watchdog lease, applied `agent-active`, ran the bounded repair lane,
captured the `$ask tau-dag` timeout as command result `exit_code=124`, removed
the active lease, applied `agent-blocked`, and persisted a receipt with
`handled_count=1`.

The timeout-to-receipt behavior required a shared `agent-skills` watchdog patch:

```text
grahama1970/agent-skills@9bd330ddb528652c9dfc4bf7fef91a47cc1ecc75
```
