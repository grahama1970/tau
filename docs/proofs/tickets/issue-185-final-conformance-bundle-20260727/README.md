# Issue #185 Final Conformance Bundle

This directory contains the final conformance and acceptance packet for the
issue #180 child-ticket sequence.

## Commands

```bash
PYTHONPATH=src uv run python -m py_compile src/tau_coding/issue_180_conformance.py src/tau_coding/cli.py
uv run ruff check src/tau_coding/issue_180_conformance.py src/tau_coding/cli.py
PYTHONPATH=src uv run tau issue-180-final-conformance-bundle --allow-live-github --allow-live-browser --output docs/proofs/tickets/issue-185-final-conformance-bundle-20260727/issue-180-final-conformance-bundle.json
jq '{status,mocked,live,provider_live,all_children_closed,acceptance_recorded_or_blocked,acceptance_status:.acceptance.status,artifact_error_count:(.artifact_errors|length),child_states:(.children|map({number,state,stateReason})),remote_main_sha,browser_screenshot:.browser_packet.screenshot}' docs/proofs/tickets/issue-185-final-conformance-bundle-20260727/issue-180-final-conformance-bundle.json
file docs/proofs/tickets/issue-185-final-conformance-bundle-20260727/issue-180-final-conformance-bundle.png
```

## Readback

```json
{
  "status": "PASS",
  "mocked": false,
  "live": true,
  "provider_live": false,
  "all_children_closed": true,
  "acceptance_recorded_or_blocked": true,
  "acceptance_status": "BLOCKED_NEEDS_HUMAN_ACCEPTANCE",
  "artifact_error_count": 0,
  "child_states": [
    {
      "number": 182,
      "state": "CLOSED",
      "stateReason": "COMPLETED"
    },
    {
      "number": 183,
      "state": "CLOSED",
      "stateReason": "COMPLETED"
    },
    {
      "number": 184,
      "state": "CLOSED",
      "stateReason": "COMPLETED"
    }
  ],
  "remote_main_sha": "4a0430c1c9ae09c8496641251fa2bd064967a946",
  "browser_screenshot": "/tmp/tau-issue185.CenDJ2/docs/proofs/tickets/issue-185-final-conformance-bundle-20260727/issue-180-final-conformance-bundle.png"
}
```

## Visual Inspection

`issue-180-final-conformance-bundle.png` is a 1440x1000 browser screenshot. It
visibly shows:

- #180 acceptance status as `BLOCKED_NEEDS_HUMAN_ACCEPTANCE`.
- #182, #183, and #184 as closed prerequisite children.
- Retained artifact rows for all three child receipts.
- Live artifact flags and screenshot counts.
- No artifact errors.

## Boundary

This packet proves that Tau can assemble the issue #180 closure evidence from
live GitHub state plus retained local receipts, and can render an inspectable
browser packet. It does not prove human acceptance of #180 because #180 remains
open.
