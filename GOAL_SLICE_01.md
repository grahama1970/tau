# Tau Canonical Workflow Slice 01

**Status:** Active
**Owner:** Human
**Goal ID:** tau-canonical-workflow-slice-01
**Goal Version:** 1
**Goal Hash:** sha256:404b9693b0dd7697f6612da2881102e257d286773578a02f490cd4cf77cc1ce9

## Immutable Goal

From a clean Tau checkout, a human can discover and launch one built-in
canonical linear workflow named `repository-readiness` with one command,
watch its authoritative progress in the packaged React Flow viewer without
manual reload, and inspect its accepted repository-readiness result or exact
blocker.

## Completion Criteria

1. `tau workflows list --json` returns one available workflow with
   `workflow_id: repository-readiness` and `topology: LINEAR`.

2. The following command executes a three-node
   `inspect-repository -> validate-readiness -> publish-readiness` DagPlan
   without requiring the human to edit a DAG file:

   ```bash
   tau workflows run repository-readiness \
     --repo /path/to/repository \
     --goal "Determine whether this checkout is ready for focused work." \
     --require-clean \
     --run-dir /tmp/tau-repository-readiness \
     --open-viewer
   ```

3. A passing run writes these useful result artifacts:

   ```text
   <run-dir>/results/repository-readiness.json
   <run-dir>/results/repository-readiness.md
   ```

4. The materialized source DAG carries an explicit human-owned goal containing
   `goal_id`, `goal_version`, `goal_hash`, `summary`, and
   `completion_criteria`. The exact goal hash is carried through DagPlan and
   every node receipt.

5. The packaged React Flow viewer displays, from Tau-authored journal replay:

   * workflow title;
   * human goal summary;
   * currently active node;
   * accepted output summary;
   * exact blocker code;
   * final useful result.

6. A live browser trace observes the inspect, validate, and publish node
   transitions without reloading the page and records GET-only browser traffic.

7. A negative run against a dirty Git fixture with `--require-clean`:

   * blocks at `validate-readiness`;
   * records `dirty_repository`;
   * never executes `publish-readiness`;
   * displays the blocker in the viewer;
   * produces no final readiness report.

8. Focused backend tests, frontend typecheck/tests/build, installed-wheel
   verification, positive browser proof, and negative browser proof pass with:

   ```text
   mocked: false
   live: true
   provider_live: false
   ```

## Allowed Scope

* Add a packaged workflow catalog.
* Add the single `repository-readiness` workflow.
* Add optional full goal metadata to `tau.generic_dag_spec.v1` while preserving
  legacy `goal_hash` compatibility.
* Preserve accepted output from generic command receipts.
* Project workflow, goal, accepted output, and blocker information through the
  existing read-only DAG viewer.
* Add `workflows list`, `workflows describe`, and `workflows run` CLI commands.
* Update the `agent-skills/skills/tau` wrapper to expose those commands.
* Add focused tests, browser proofs, documentation, and project-knowledge
  records for this slice.

## Forbidden Scope

* No second canonical workflow.
* No provider, model, WebGPT, Herdr, tmux, Memory, GitHub mutation, or network
  dependency.
* No browser mutation endpoint.
* No approve, reject, retry, cancel, acknowledge, or DAG-edit controls.
* No scheduler, route, join, correction, receipt-admission, or SQLite authority
  changes.
* No cross-run dashboard, analytics, cost, token, or latency estimation.
* No rewrite of the React Flow application.
* No separate UX Lab implementation.
* No generalized plugin/workflow SDK beyond what `repository-readiness`
  requires.

## Stop Condition

Stop the slice when every completion criterion is proven.

Stop immediately when a required change would enter forbidden scope. Record the
blocker without broadening or rewriting this goal.
