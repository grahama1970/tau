# Prompt improvements for the next Tau slice

1. State whether the next slice should enhance `goal-guardian` itself or only the bridge. This slice intentionally keeps `goal-guardian` minimal and hash-preserving.
2. When asking for `$create-architecture`, include exact active-goal fixture path if the slice should touch a durable goal capsule. This bridge avoids persistence by design.
3. Keep trusted-human routes explicit: every future goal-change prompt should name the trust boundary and expected fail-closed receipt fields.
4. Ask for one command name, one receipt schema id, and one focused pytest command. That kept this slice bounded.
5. Keep live GitHub mutation out of goal-change bridge prompts unless the intended acceptance gate explicitly requires `--apply` evidence.
6. Separate “start handoff validates” from “goal guardian reconciles the new goal.” They are different proof rungs and should remain separately testable.
