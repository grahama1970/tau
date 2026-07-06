# Tau Orchestration Reliability

`tau.orchestration_reliability_receipt.v1` is Tau's read-only summary of whether
one run stayed inside the declared orchestration controls.

It does not answer whether the agent was correct. It answers whether Tau had
enough receipts, gates, route evidence, and course-correction artifacts to treat
the orchestration as controlled.

## Command

```bash
uv run tau orchestration-reliability \
  --run-dir path/to/run \
  --out orchestration-reliability.json
```

The command exits non-zero when the run has an unhandled blocked state.

## Semantics

A clean `PASS` DAG run is reliable orchestration.

A `BLOCKED` DAG run can also be reliable orchestration when Tau stopped safely
and produced a `tau.dag_error.v1` or `tau.course_correction.v1` artifact that
names the next safe route.

The receipt blocks when:

- no DAG or run receipt is found;
- goal hash drift is reported;
- unexpected DAG routes are reported;
- retry budget is exceeded without handled correction;
- a Herdr observation gate blocks without an embedded course-correction payload;
- a run is blocked without a DAG error or course-correction artifact.

## Non-Claims

This receipt does not prove:

- agent truthfulness;
- semantic task correctness;
- provider/model quality;
- that course correction was executed;
- future route correctness.
