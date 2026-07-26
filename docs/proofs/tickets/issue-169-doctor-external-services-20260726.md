# Issue #169 Proof: Tau Doctor External Service Checks

Issue: https://github.com/grahama1970/tau/issues/169

## Summary

`tau doctor` now reports external service health in the `external_services`
receipt field. Memory is treated as required and is probed through a read-only
`/health` request. Optional services are reported as `not_configured` when their
environment variables are absent and do not degrade the doctor result.

Required Memory unreachable state no longer produces a false `PASS`; the receipt
status becomes `DEGRADED` while local install readiness remains `ok: true`.

## Changed Files

- `src/tau_coding/cli.py`
- `tests/test_cli.py`

## Verification

mocked: no for the reachable and closed-port service checks; yes for the
optional-not-configured branch's Memory probe only, to keep that branch
independent of a developer's local Memory daemon.

live: yes for local CLI execution and localhost HTTP/socket probing.

Commands run from `/tmp/tau-main-issue-137.7nchbf`:

```text
$ uv run ruff check src/tau_coding/cli.py tests/test_cli.py
All checks passed!

$ uv run python -m py_compile src/tau_coding/cli.py tests/test_cli.py
exit 0

$ uv run pytest -q tests/test_cli.py::test_doctor_command_reports_read_only_runtime_preflight tests/test_cli.py::test_doctor_json_option_does_not_fall_through_to_tui tests/test_cli.py::test_doctor_reports_reachable_external_memory_service tests/test_cli.py::test_doctor_reports_unreachable_required_service_as_degraded tests/test_cli.py::test_doctor_reports_unconfigured_optional_services_without_degrading
.....                                                                    [100%]
5 passed in 2.07s

$ git diff --check
exit 0
```

## Coverage Against Ticket

- Reachable stub: `test_doctor_reports_reachable_external_memory_service` starts
  a real `ThreadingHTTPServer` bound to `127.0.0.1` and verifies doctor reports
  Memory as `reachable`.
- Closed port/unreachable: `test_doctor_reports_unreachable_required_service_as_degraded`
  reserves then closes a localhost TCP port and verifies doctor reports Memory
  as `unreachable` with `status: DEGRADED`.
- Optional not configured: `test_doctor_reports_unconfigured_optional_services_without_degrading`
  clears optional service env vars and verifies optional service state is
  `not_configured` without degrading the receipt.
- Receipt readback: reachable and unreachable tests write the doctor payload to
  `doctor.json`, read it back, parse it, and assert the external service states.

## Remaining Non-Claims

This proof does not validate semantic correctness of any external service beyond
HTTP reachability of the configured health endpoint. It does not make provider
or model calls.
