# Tau #221 Unsigned Human Acceptance Bundle

Ticket: `grahama1970/tau#221`

This is a prepared, unsigned human-acceptance bundle. It is not a closure
receipt for #221 because the ticket requires a human walkthrough and human-held
signature.

## Generated From

- source commit: `5e54cff6a773d56113f96fc0d675b088c27e43b7`
- retained wheel: `tau-0.1.0-py3-none-any.whl`
- wheel SHA-256:
  `sha256:f923748f0d419adec8d707dc2cb0382b1d91e6ab498d3859d98249e291648f7e`
- bundle digest:
  `sha256:404a46cfe52e4f3c3467063a260eaa108db19b2572b8a630ae40e84110c15ca8`

## Bound Evidence

`ACCEPTANCE.json` starts with `accepted: false`, `mocked: false`, `live: true`,
and no signature. Its `receipt_hashes` bind:

- #211 clean-wheel completion closure evidence;
- #211 campaign index;
- #223 live Chatterbox restart proof.

`OBSERVATIONS.json` starts with seven unchecked observations and `observed:
null` fields for the human to complete with run IDs, evidence paths, screenshot
or trace references, notes, and defect IDs.

## Verification

```bash
bash docs/proofs/acceptance/issue-221-human-acceptance-bundle-20260729T030743Z/verify_bundle.sh
```

Result when generated:

```text
bundle verified: all bound hashes match
```

## Remaining Human Gate

The human must run the five-rung walkthrough, fill observations, keep
`accepted=false` if any falsifier triggers, and sign the final acceptance record
with the documented human-held key. An agent must not sign or close #221 from
this unsigned bundle.
