# DAG Terminal Contributions and Join Policies

Tau's `bounded-ready-queue` scheduler can resolve conditional fanout into a
declared virtual join without waiting forever on an unselected or failed
branch. Every incoming join edge receives one immutable
`tau.dag_terminal_contribution.v1` receipt before Tau writes the final
`tau.dag_join_decision.v1` receipt.

## Contract

Declare the policy on a virtual node with at least two incoming edges:

```json
{
  "id": "join",
  "agent": "join",
  "executor": "local",
  "join": {
    "schema": "tau.dag_join_policy.v1",
    "policy": "minimum_success_count",
    "required_successes": 2,
    "timeout_seconds": 30
  }
}
```

Join nodes cannot declare a command, provider, reviewer, route mode, requested
capability, or conditional outgoing edge. Put aggregation work in a separate
command-backed node after the virtual join.
Each direct join source must route exclusively to that join. This prevents one
join from consuming or suppressing a failure that also controls another path.
Tau's generic DAG validator rejects duplicate source-target edges before join
preflight, so one source cannot inflate count or quorum policies.

## Terminal States

The closed contribution states are:

| State | Meaning |
| --- | --- |
| `success` | The source completed with an admissible Tau PASS result. |
| `failed` | Bounded execution exhausted its attempts or failed operationally. |
| `blocked` | Tau rejected the source result or continuation. |
| `skipped` | Routing or upstream skip made the branch unreachable. |
| `cancelled` | An irreversible early join decision closed this edge. |
| `timed_out` | The join deadline closed an unresolved edge. |

Contributions are per edge and first-write-wins. A late source result cannot
replace `cancelled` or `timed_out`; Tau records it as an ignored late event.
Receipt creation uses an atomic no-overwrite filesystem operation, so concurrent
writers cannot replace the first persisted contribution.

## Policies

| Policy | Terminal decision |
| --- | --- |
| `all_success` | Release only when every input succeeds. |
| `all_terminal` | Release when every input is terminal, regardless of outcome. |
| `exact_success_count` | Release only with exactly `required_successes`. |
| `minimum_success_count` | Release when `required_successes` is reached. |
| `quorum` | Release when a rational fraction of declared edges succeeds. |
| `any_success` | Release on the first success. |
| `fail_fast` | Block on the first adverse input; skipped inputs are neutral. |
| `collect_failures` | Release only after complete inputs contain failures to collect. |

When every input is skipped, every policy returns `skip`. Count and quorum
policies can decide early when success or failure becomes mathematically
irreversible. Tau first writes `cancelled` contributions for unresolved inputs,
signals running local command process groups to terminate, then recomputes and
persists the terminal decision. Join deadlines apply the same cancellation path
before writing `timed_out` contributions.
A finalized join `skip` is a valid terminal settlement when no downstream work
is required, but it does not mark the terminal as successfully activated.

Quorum uses an explicit reduced fraction:

```json
{
  "schema": "tau.dag_join_policy.v1",
  "policy": "quorum",
  "quorum_fraction": {"numerator": 2, "denominator": 3},
  "timeout_seconds": 30
}
```

The success threshold is `ceil(incoming_edges * numerator / denominator)`.
Skipped edges do not shrink the declared quorum basis.

## Timeout

The scheduler arms a monotonic deadline before the first direct incoming source
is dispatched or virtually settled. A join therefore remains bounded even when
no source produces a contribution. At expiry Tau signals each unresolved local
source to terminate, writes the complete batch of `timed_out` contributions,
and evaluates the policy once. The pure evaluator never reads a clock, and a
final receipt never contains `wait`.
Sources whose join edge is cancelled or timed out before dispatch are marked
terminal and never launched. A queued worker that observes cancellation before
launch also returns without creating a subprocess.

## Receipts

Receipts are written under:

```text
<run-dir>/terminal-contributions/<join-node>/edge-<index>.json
<run-dir>/join-decisions/<join-node>.json
```

They bind the DAG ID, goal hash, ordered edge identities, normalized policy,
contribution payload hashes, terminal counts, decision, and deterministic
decision hash. The join decision is persisted before Tau settles the virtual join and
activates its outgoing edges.

## Boundaries

These receipts prove Tau applied the declared join contract to recorded
terminal states. They do not prove branch-result truth, provider/model quality,
termination of external/provider workloads outside the local command runner,
durable restart recovery, operating-system isolation, or arbitrary
nested-workflow correctness.
