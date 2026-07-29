# Tau Acceptance Walkthrough

Run each rung from the CLEAN INSTALLED WHEEL (env -i, throwaway HOME).
For every beat: PREPARE (exact command), OBSERVE (truthful behavior),
FALSIFIER (what lying looks like - log as a defect, never tick), RECORD.

## Rung 1: repository-readiness (LINEAR)

- PREPARE: `tau workflows run repository-readiness --repo <repo> --run-dir <dir> --goal '<goal>'`
- OBSERVE: Terminal prints status PASS; the viewer shows exactly one node reaching green.
- FALSIFIER: Any node shows green before its receipt file exists on disk. Log as defect; do not tick.
- RECORD: Paste the run-dir and receipts/*.json paths.
- NEGATIVE PATH: Omit --goal: the command must EXIT NON-ZERO naming --goal as required.

## Rung 2: tau-operator-reference (MULTI_STEP_SEQUENTIAL)

- PREPARE: `tau workflows run tau-operator-reference --repo <tau-checkout> --run-dir <dir>`
- OBSERVE: The viewer shows a linear chain of >=2 nodes advancing in order; status PASS.
- FALSIFIER: A later node greens before its predecessor's receipt is admitted.
- RECORD: Paste the node receipt paths in order.

## Rung 3: repository-evidence-map (FAN_OUT_FAN_IN)

- PREPARE: `tau workflows run repository-evidence-map --repo <repo> --run-dir <dir> --goal '<goal>'`
- OBSERVE: The viewer shows a diamond: parallel nodes fan into one join; status PASS.
- FALSIFIER: The join greens while a sibling receipt is still absent.
- RECORD: Paste every sibling receipt and the join receipt.

## Rung 4: approved-release-bundle (MIXED_RETRY_APPROVAL)

- PREPARE: `tau workflows run approved-release-bundle --repo <repo> --run-dir <dir> --goal '<goal>' --publish-path <pub>`
- OBSERVE: The run HALTS at an approval gate showing the exact prompt; approving completes it.
- FALSIFIER: The publish path is written before you approve.
- RECORD: Paste the approval-gate receipt and the published bundle hash.
- NEGATIVE PATH: Type 'no' (or decline) at the approval gate: the run must ABORT with nothing published.

## Rung 5: durable-repository-qualification (DURABLE_MIXED_REPAIR_APPROVAL)

- PREPARE: `tau workflows run durable-repository-qualification --repo <repo> --run-dir <dir> --goal '<goal>' --publish-path <pub>`
- OBSERVE: The run BLOCKS at a deliberate failure; you watch it in the ALREADY-OPEN viewer, run --repair --resume, the resume ENTERS AT THE BLOCKED NODE (not step 0), and the published effect count stays 1 (not doubled).
- FALSIFIER: The blocked run silently reports PASS, or resume restarts from step 0, or the publish effect count doubles after repair.
- RECORD: Paste the blocked receipt, the repair attempt receipt, and the publication ledger (effect_count must read 1).

## Signing

When every positive observation is confirmed and every negative path
failed correctly, sign ACCEPTANCE.json with your own key:

    ssh-keygen -Y sign -n tau-acceptance -f ~/.ssh/id_ed25519 ACCEPTANCE.json

Then update #221 with the retained record path, its bundle_digest,
and the `ssh-keygen -Y verify` output. #180 closes only after parent
reconciliation confirms all children are closed.
